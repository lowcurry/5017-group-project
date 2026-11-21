"""Adversarial verifier tests use fixed archive numbers, not metrics-generated truths."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from curve_audit.evidence import build_package, render_report, verify_package
from curve_audit.faults import run_fault_suite
from curve_audit.verification import recompute, validate_dataset, verify_claims

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return json.loads((ROOT / "data/raw/pilot_archive.json").read_text())


def report_fixture():
    query = {"metric": "quote_rate", "operation": "first",
             "start": "2023-03-10T18:00:00Z", "end": "2023-03-10T19:00:00Z",
             "direction": "USDC_USDT", "size": 1000}
    claim = {"claim_id": "claim_quote", "query": query, "value": "0.999817946",
             "unit": "USDT/USDC", "status": "supported", "evidence_ids": ["E_H_00"],
             "calculation_id": "C_fixture_quote", "explanation": "1000 USDC 历史报价得到 999.817946 USDT。"}
    report = {"task_id": "fixture", "answer": "历史合约报价见结构化主张。", "claims": [claim],
              "refusal_reason": None, "faults": []}
    calculation = {key: copy.deepcopy(claim[key]) for key in ("query", "value", "unit", "evidence_ids", "calculation_id")}
    return report, [calculation]


class ClaimVerificationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = fixture()
        self.report, self.calculations = report_fixture()

    def test_clean_raw_quote_exact_value(self):
        self.assertEqual(validate_dataset(self.dataset), [])
        expected = recompute(self.dataset, self.report["claims"][0]["query"])
        self.assertEqual(expected["value"], "0.999817946")
        self.assertEqual(expected["observed_at"], "2023-03-10T17:59:59Z")
        self.assertTrue(verify_claims(self.dataset, self.report)["pass"])

    def test_unit_value_evidence_and_nonfinite_rejected(self):
        for key, value in (("unit", "USD"), ("value", "1.999817946"),
                           ("evidence_ids", ["E_H_01"]), ("value", "NaN"), ("value", "Infinity")):
            report = copy.deepcopy(self.report)
            report["claims"][0][key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(verify_claims(self.dataset, report)["pass"])

    def test_boundary_and_interior_missing_data_fail(self):
        for end in ("2099-01-01T00:00:00Z", "2023-03-10T18:30:00Z"):
            report = copy.deepcopy(self.report)
            report["claims"][0]["query"]["end"] = end
            self.assertFalse(verify_claims(self.dataset, report)["pass"])
        self.dataset["snapshots"] = [s for s in self.dataset["snapshots"] if s["id"] != "H_01"]
        query = self.report["claims"][0]["query"]
        query["end"] = "2023-03-10T20:00:00Z"
        self.assertFalse(verify_claims(self.dataset, self.report)["pass"])

    def test_unsupported_requires_reason_and_real_unavailability(self):
        claim = self.report["claims"][0]
        claim.update(status="unsupported", value=None, evidence_ids=[], calculation_id="")
        self.assertFalse(verify_claims(self.dataset, self.report)["pass"])
        claim["query"]["end"] = "2099-01-01T00:00:00Z"
        self.assertTrue(verify_claims(self.dataset, self.report)["pass"])
        claim["explanation"] = ""
        self.report["answer"] = ""
        self.assertFalse(verify_claims(self.dataset, self.report)["pass"])

    def test_safe_refusal_does_not_certify_corrupt_source(self):
        self.dataset["snapshots"][1]["balances"]["USDC"] = "1"
        claim = self.report["claims"][0]
        claim.update(status="unsupported", value=None, evidence_ids=[], calculation_id="",
                     explanation="数据内部不一致，保留结论。")
        checked = verify_claims(self.dataset, self.report)
        self.assertFalse(checked["pass"])
        self.assertTrue(checked["claim_results"][0]["pass"])

    def test_conflicting_requires_record_and_correct_value(self):
        self.report["claims"][0]["status"] = "conflicting"
        self.assertFalse(verify_claims(self.dataset, self.report)["pass"])
        self.report["faults"] = ["草稿数值与原始数据冲突；本表提供复算值。"]
        self.assertTrue(verify_claims(self.dataset, self.report)["pass"])
        self.report["claims"][0]["value"] = "99"
        self.assertFalse(verify_claims(self.dataset, self.report)["pass"])

    def test_maximum_requires_all_samples_and_alias_dedup(self):
        claim = self.report["claims"][0]
        claim["query"].update(operation="maximum", start="2023-03-11T11:00:00Z", end="2023-03-11T13:00:00Z")
        expected = recompute(self.dataset, claim["query"])
        self.assertEqual(expected["evidence_ids"], ["E_H_17", "E_H_18", "E_H_19"])
        claim.update({key: expected[key] for key in ("value", "unit", "evidence_ids")})
        self.assertTrue(verify_claims(self.dataset, self.report)["pass"])
        claim["evidence_ids"] = ["E_H_18"]
        self.assertFalse(verify_claims(self.dataset, self.report)["pass"])

    def test_raw_normalized_calldata_and_block_alias_checks(self):
        mutations = [
            lambda s: s["balances"].update(USDC="1"),
            lambda s: s["raw_balances"].update(USDC="0" + s["raw_balances"]["USDC"]),
            lambda s: s["calldata"]["balances"].update(USDC=s["calldata"]["balances"]["USDT"]),
            lambda s: s["calldata"]["quotes"]["USDC_USDT"].update({"1000": s["calldata"]["quotes"]["USDT_USDC"]["1000"]}),
            lambda s: s.update(block_hash="0x00"),
        ]
        for mutate in mutations:
            dataset = copy.deepcopy(self.dataset)
            mutate(dataset["snapshots"][1])
            self.assertTrue(validate_dataset(dataset))
        alias = next(s for s in self.dataset["snapshots"] if s["id"] == "S_STRESS")
        alias["raw_quotes"]["USDC_USDT"]["1000"] = "1"
        self.assertFalse(verify_claims(self.dataset, self.report)["pass"])

    def test_malformed_or_empty_reports_never_certified(self):
        for report in (None, {}, {"task_id": "x", "answer": "x", "claims": []},
                       {"task_id": "x", "answer": "x", "claims": [None]}):
            self.assertFalse(verify_claims(self.dataset, report)["pass"])


class PackageVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "package"
        self.report, self.calculations = report_fixture()
        self.tools = [{"tool": "calculate", "arguments": copy.deepcopy(self.calculations[0]["query"]),
                       "result": copy.deepcopy(self.calculations[0])}]
        self.built = build_package(self.directory, fixture(), self.report, self.tools, self.calculations,
                                   {"run_kind": "verification_test", "prose_certified": False})

    def test_clean_package_hash_and_deterministic_rendering(self):
        self.assertTrue(self.built["verification"]["pass"], self.built["verification"]["errors"])
        manifest_bytes = (self.directory / "manifest.json").read_bytes()
        self.assertEqual(self.built["package_hash"], hashlib.sha256(manifest_bytes).hexdigest())
        self.assertNotIn("manifest.json", json.loads(manifest_bytes)["files"])
        self.assertFalse(manifest_bytes.endswith(b"\n"))
        self.assertEqual((self.directory / "report.md").read_text(), render_report(self.report))

    def test_semantic_mutations_even_after_rehash_and_clean_controls(self):
        original = (self.directory / "manifest.json").read_bytes()
        results = run_fault_suite(self.directory, Path(self.temp.name) / "faults")
        self.assertTrue(results["pass"], results)
        self.assertEqual(results["detected_count"], results["fault_count"])
        self.assertEqual(results["clean_control_passed"], 2)
        self.assertEqual(original, (self.directory / "manifest.json").read_bytes())
        self.assertTrue(verify_package(self.directory)["pass"])

    def test_unresolved_calculation_reference(self):
        self.report["claims"][0]["calculation_id"] = "C_different"
        result = build_package(self.directory, fixture(), self.report, self.tools, self.calculations, {})
        self.assertFalse(result["verification"]["pass"])
        self.assertTrue(any("unresolved calculation" in e for e in result["verification"]["errors"]))

    def test_full_query_defaults_semantically_match_calculation(self):
        self.report["claims"][0]["query"].update(coin="USDC")
        result = build_package(self.directory, fixture(), self.report, self.tools, self.calculations, {})
        self.assertTrue(result["verification"]["pass"], result["verification"]["errors"])

    def test_anchor_binds_registered_readback_and_package(self):
        anchor = Path(self.temp.name) / "anchor.json"
        record = {"package_hash": self.built["package_hash"], "registered_hash": self.built["package_hash"],
                  "read_back_hash": self.built["package_hash"], "chain_id": 1337}
        anchor.write_text(json.dumps(record))
        self.assertTrue(verify_package(self.directory, anchor)["pass"])
        record["read_back_hash"] = "0" * 64
        anchor.write_text(json.dumps(record))
        self.assertFalse(verify_package(self.directory, anchor)["pass"])


if __name__ == "__main__":
    unittest.main()
