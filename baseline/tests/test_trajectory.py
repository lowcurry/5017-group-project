"""Trajectory regressions rehash copied packages to isolate semantic checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from curve_audit.evidence import _canonical, _hash, build_package, render_report, verify_package
from curve_audit.verification import recompute, verify_claims

ROOT = Path(__file__).resolve().parents[1]


def fixtures():
    dataset = json.loads((ROOT / "data/raw/pilot_archive.json").read_text())
    query = {"metric": "quote_rate", "operation": "first",
             "start": "2023-03-10T18:00:00Z", "end": "2023-03-10T19:00:00Z",
             "direction": "USDC_USDT", "size": 1000}
    expected = recompute(dataset, query)
    calculation = {k: copy.deepcopy(expected[k]) for k in
                   ("query", "value", "unit", "evidence_ids", "sample_count",
                    "observed_at", "target_timestamp_utc", "snapshot_id",
                    "block_number", "block_hash")}
    calculation["calculation_id"] = "C_independent_fixture"
    claim = {k: copy.deepcopy(calculation[k]) for k in
             ("query", "value", "unit", "evidence_ids", "calculation_id")}
    claim.update(claim_id="c1", status="supported", explanation="固定原始整数的独立复算。")
    report = {"task_id": "task_A", "answer": "历史报价见结构化主张。", "claims": [claim],
              "refusal_reason": None, "faults": []}
    initial = copy.deepcopy(report)
    initial["claims"][0]["value"] = "999"
    calls = [{"tool": "calculate", "task_id": "task_A", "step_id": "task_A_s1",
              "depends_on": [], "arguments": copy.deepcopy(query),
              "result": {"ok": True, "result": copy.deepcopy(calculation)}},
             {"tool": "independent_verifier", "task_id": "task_A", "step_id": "task_A_v1",
              "depends_on": ["task_A_s1"], "arguments": {"report": copy.deepcopy(initial)},
              "result": verify_claims(dataset, initial)}]
    metadata = {"trajectory_schema": "2.0", "initial_reports": [initial]}
    return dataset, report, [calculation], calls, metadata


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "evidence"
        self.dataset, self.report, self.calculations, self.calls, self.metadata = fixtures()
        self.built = self.build()
        self.assertTrue(self.built["verification"]["pass"], self.built["verification"]["errors"])

    def build(self):
        return build_package(self.path, self.dataset, self.report, self.calls,
                             self.calculations, self.metadata)

    def replace_and_rehash(self, name, value):
        (self.path / name).write_bytes(_canonical(value))
        manifest = json.loads((self.path / "manifest.json").read_text())
        manifest["files"][name] = _hash((self.path / name).read_bytes())
        (self.path / "manifest.json").write_bytes(_canonical(manifest))

    def assert_trajectory_rejected(self, text):
        checked = verify_package(self.path)
        self.assertFalse(checked["pass"], checked)
        self.assertTrue(any(text in e for e in checked["errors"]), checked["errors"])
        self.assertFalse(any("hash mismatch" in e for e in checked["errors"]), checked["errors"])

    def test_initial_report_need_not_equal_repaired_final(self):
        self.assertNotEqual(self.report, self.metadata["initial_reports"][0])
        self.assertFalse(self.calls[1]["result"]["pass"])
        self.assertTrue(verify_package(self.path)["pass"])

    def test_rehashed_empty_history_has_no_calculation_producer(self):
        self.replace_and_rehash("tool_calls.json", [])
        self.assert_trajectory_rejected("nonempty")

    def test_rehashed_calculate_return_value_cannot_hide_behind_manifest(self):
        self.calls[0]["result"]["result"]["value"] = "999999999"
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("calculate return disagrees")

    def test_rehashed_calculate_argument_query_must_match_return(self):
        self.calls[0]["arguments"]["direction"] = "USDT_USDC"
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("arguments disagree")

    def test_rehashed_verifier_task_swap_is_rejected_even_with_replayed_feedback(self):
        self.calls[1]["arguments"]["report"]["task_id"] = "task_B"
        self.calls[1]["result"] = verify_claims(self.dataset, self.calls[1]["arguments"]["report"])
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("report task_id disagrees")

    def test_rehashed_initial_report_change_cannot_retain_other_draft_feedback(self):
        self.calls[1]["arguments"]["report"]["claims"][0]["value"] = "888"
        self.calls[1]["result"] = verify_claims(self.dataset, self.calls[1]["arguments"]["report"])
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("retained initial_reports")

    def test_rehashed_verifier_feedback_is_independently_replayed(self):
        self.calls[1]["result"]["pass"] = True
        self.calls[1]["result"]["errors"] = []
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("feedback differs")

    def test_missing_calculate_call_rejected_even_if_claims_still_reproduce(self):
        self.replace_and_rehash("tool_calls.json", [self.calls[1]])
        self.assert_trajectory_rejected("no successful calculate producer")

    def test_false_calculate_failure_is_not_accepted_as_unavailable(self):
        self.calls[0]["result"] = {"ok": False, "error": "invented outage"}
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("independently computable")

    def test_true_unavailable_call_and_partial_answer_preserve_successful_steps(self):
        missing_query = copy.deepcopy(self.calls[0]["arguments"])
        missing_query["end"] = "2099-01-01T00:00:00Z"
        self.calls.insert(1, {"tool": "calculate", "task_id": "task_A",
                             "step_id": "task_A_s2", "depends_on": ["task_A_s1"],
                             "arguments": missing_query,
                             "result": {"ok": False, "error": "coverage unavailable"}})
        self.calls[-1]["depends_on"].append("task_A_s2")
        self.report["claims"].append({"claim_id": "c2", "query": missing_query, "value": None,
                                      "unit": "", "status": "unsupported", "evidence_ids": [],
                                      "calculation_id": "", "explanation": "该窗口超出归档数据。"})
        checked = self.build()["verification"]
        self.assertTrue(checked["pass"], checked["errors"])

    def test_graph_rejects_forward_duplicate_and_cross_task_dependencies(self):
        variants = [
            ("forward", lambda calls: calls[0]["depends_on"].append("task_A_v1")),
            ("duplicate_step", lambda calls: calls[1].update(step_id="task_A_s1")),
            ("omitted_calc", lambda calls: calls[1].update(depends_on=[])),
            ("cross_task", lambda calls: calls[0].update(task_id="task_B")),
        ]
        for name, mutation in variants:
            with self.subTest(name=name):
                _, _, _, calls, _ = fixtures()
                mutation(calls)
                self.replace_and_rehash("tool_calls.json", calls)
                self.assertFalse(verify_package(self.path)["pass"])

    def test_final_claim_cannot_borrow_another_task_calculation(self):
        self.calls[0]["task_id"] = "task_B"
        self.calls[1]["depends_on"] = []
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("not produced by its task")

    def test_rehashing_does_not_bypass_existing_external_commitment(self):
        anchor = Path(self.temp.name) / "anchor.json"
        original = self.built["package_hash"]
        anchor.write_text(json.dumps({key: original for key in
                                      ("package_hash", "registered_hash", "read_back_hash")}))
        self.metadata["note"] = "A consistent but newly repackaged artifact."
        self.replace_and_rehash("metadata.json", self.metadata)
        self.assertTrue(verify_package(self.path)["pass"])
        checked = verify_package(self.path, anchor)
        self.assertFalse(checked["pass"])
        self.assertTrue(any("anchor mismatch" in e for e in checked["errors"]))

    def test_numerical_replay_never_uses_production_calculator(self):
        with patch("curve_audit.metrics.calculate", side_effect=AssertionError("production oracle used")):
            self.assertTrue(verify_package(self.path)["pass"])

    def test_legacy_direct_return_and_ai_envelope_remain_supported(self):
        self.calls = [{k: v for k, v in c.items() if k not in
                       ("task_id", "step_id", "depends_on")} for c in self.calls]
        self.calls[0]["result"] = copy.deepcopy(self.calculations[0])
        self.metadata = {}
        self.build()
        checked = verify_package(self.path)
        self.assertTrue(checked["pass"], checked["errors"])
        self.assertEqual("legacy", checked["trajectory_verification"]["schema"])

    def test_legacy_swapped_verifier_report_task_rejected_after_rehash(self):
        self.calls = [{k: v for k, v in c.items() if k not in
                       ("task_id", "step_id", "depends_on")} for c in self.calls]
        self.metadata = {}
        self.assertTrue(self.build()["verification"]["pass"])
        self.calls[1]["arguments"]["report"]["task_id"] = "unrelated_task"
        self.calls[1]["result"] = verify_claims(self.dataset, self.calls[1]["arguments"]["report"])
        self.replace_and_rehash("tool_calls.json", self.calls)
        self.assert_trajectory_rejected("legacy verifier task")

    def test_boundary_rendering_discloses_narrative_scope(self):
        self.report["narrative_claims"] = [{"kind": "issuer_solvency", "stance": "unsupported",
                                           "explanation": "池状态不能证明发行人偿付能力。",
                                           "evidence_ids": []}]
        text = render_report(self.report)
        self.assertIn("结构化边界判断，正文仍需审核", text)
        self.assertIn("issuer_solvency", text)
        self.assertIn("不由数值验证器认证", text)
        self.assertTrue(self.build()["verification"]["pass"])


if __name__ == "__main__":
    unittest.main()
