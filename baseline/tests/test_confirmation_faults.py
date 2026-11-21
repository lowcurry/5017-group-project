"""Engineering checks for the synthetic fault matrix and its actual accounting."""
import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from curve_audit.confirmation_faults import (
    assess_scenario, build_scenarios, replay_fault_matrix, run_fault_matrix, summarize,
)
from curve_audit.independent_source import SourceError, as_recompute_dataset, compare_source_records


def rehash(record):
    body = {key: value for key, value in record.items() if key != "content_sha256"}
    record["content_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ConfirmationFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = build_scenarios()
        cls.by_id = {scenario["id"]: scenario for scenario in cls.scenarios}
        cls.outcomes = [assess_scenario(scenario) for scenario in cls.scenarios]

    def test_scenario_counts_unique_ids_and_explicit_synthetic_scope(self):
        self.assertEqual(Counter(scenario["category"] for scenario in self.scenarios),
                         {"faulty": 12, "clean": 12, "refusal": 4, "unknown": 4})
        self.assertEqual(len(self.by_id), 32)
        for scenario in self.scenarios:
            self.assertEqual(scenario["fixture_status"], "synthetic_engineering_regression_fixture")
            self.assertFalse(scenario["source_record"]["operational_backend_independence_proven"])
        task_starts = {scenario["declared_task"]["required_claims"][0]["query"]["start"] for scenario in self.scenarios}
        self.assertGreater(len(task_starts), 12)

    def test_actual_faults_are_matched_to_corresponding_issue(self):
        summary = summarize(self.scenarios, self.outcomes)
        self.assertTrue(summary["pass"])
        self.assertEqual(summary["expected_behaviors_observed"], 32)
        self.assertEqual({key: summary["binary_detection"][key] for key in ("TP", "FN", "FP", "TN")},
                         {"TP": 12, "FN": 0, "FP": 0, "TN": 12})
        for scenario, outcome in zip(self.scenarios, self.outcomes):
            if scenario["category"] == "faulty":
                self.assertIn(scenario["expectation"]["required_issue_code"], outcome["issue_codes"])

    def test_differing_expected_labels_do_not_change_actual_verdict(self):
        scenario = copy.deepcopy(self.by_id["F01"])
        original = assess_scenario(scenario)
        scenario["category"] = "clean"
        scenario["expectation"] = {"verdict": "supported", "required_issue_code": None}
        self.assertEqual(assess_scenario(scenario), original)

    def test_unrelated_error_does_not_count_as_detection(self):
        scenario = self.by_id["F01"]
        outcome = copy.deepcopy(assess_scenario(scenario))
        outcome["issue_codes"] = ["unit_mismatch"]
        summary = summarize([scenario], [outcome])
        self.assertEqual(summary["binary_detection"]["TP"], 0)
        self.assertEqual(summary["binary_detection"]["FN"], 1)
        self.assertFalse(summary["pass"])

    def test_unknown_binary_cases_are_excluded_from_fp_fn_denominators(self):
        faulty, clean = self.by_id["F01"], self.by_id["C01"]
        faulty_outcome, clean_outcome = copy.deepcopy(assess_scenario(faulty)), copy.deepcopy(assess_scenario(clean))
        faulty_outcome["verdict"] = clean_outcome["verdict"] = "unknown"
        summary = summarize([faulty, clean], [faulty_outcome, clean_outcome])
        values = summary["binary_detection"]
        self.assertEqual(sum(values[key] for key in ("TP", "FN", "FP", "TN")), 0)
        self.assertEqual((values["unknown_faulty"], values["unknown_clean"]), (1, 1))
        self.assertEqual((values["evaluable_faulty"], values["evaluable_clean"]), (0, 0))
        self.assertIsNone(values["sensitivity"])
        self.assertIsNone(values["false_positive_rate"])

    def test_self_consistent_raw_errors_pass_same_archive_but_not_source_check(self):
        for identifier, code in (("F11", "source_raw_balance_mismatch"), ("F12", "source_raw_quote_mismatch")):
            with self.subTest(identifier=identifier):
                outcome = assess_scenario(self.by_id[identifier])
                self.assertTrue(outcome["same_archive_verification"]["pass"])
                self.assertEqual(outcome["verdict"], "error")
                self.assertIn(code, outcome["issue_codes"])
                self.assertTrue(outcome["source_replay"]["pass"])

    def test_task_window_and_direction_drift_are_not_hidden_by_valid_recomputation(self):
        for identifier, code in (("F07", "query_window_mismatch"), ("F09", "query_direction_mismatch")):
            with self.subTest(identifier=identifier):
                outcome = assess_scenario(self.by_id[identifier])
                self.assertTrue(outcome["same_archive_verification"]["pass"])
                self.assertIn(code, outcome["issue_codes"])

    def test_clean_precision_offset_inside_shared_tolerance_is_accepted(self):
        outcome = assess_scenario(self.by_id["C01"])
        self.assertTrue(outcome["same_archive_verification"]["pass"])
        self.assertEqual(outcome["verdict"], "supported")

    def test_legitimate_refusal_with_reason_in_answer_is_accepted(self):
        for identifier in ("R01", "R02", "R03", "R04"):
            with self.subTest(identifier=identifier):
                scenario = self.by_id[identifier]
                self.assertNotIn("explanation", scenario["report"]["claims"][0])
                self.assertNotIn("refusal_reason", scenario["report"])
                outcome = assess_scenario(scenario)
                self.assertEqual(outcome["verdict"], "refusal_supported")
                self.assertEqual(outcome["issues"], [])

    def test_four_source_failures_are_unknown_with_no_wrong_agent_verdict(self):
        for identifier in ("U01", "U02", "U03", "U04"):
            with self.subTest(identifier=identifier):
                outcome = assess_scenario(self.by_id[identifier])
                self.assertEqual(outcome["verdict"], "unknown")
                self.assertTrue(outcome["same_archive_verification"]["pass"])
                self.assertTrue(outcome["source_replay"]["pass"])
        self.assertIn("source_conflict_unknown", assess_scenario(self.by_id["U03"])["issue_codes"])

    def test_missing_required_slot_is_not_silently_accepted(self):
        scenario = copy.deepcopy(self.by_id["C02"])
        scenario["report"]["claims"].clear()
        outcome = assess_scenario(scenario)
        self.assertEqual(outcome["verdict"], "error")
        self.assertIn("required_claim_missing", outcome["issue_codes"])

    def test_rehashed_dataset_and_provider_snapshot_forgery_needs_transcript_replay(self):
        record = copy.deepcopy(self.by_id["C01"]["source_record"])
        for rows in (record["dataset"]["snapshots"], record["provider_attempts"][0]["snapshots"]):
            rows[0]["raw_balances"]["USDC"] = "1"
            rows[0]["balances"]["USDC"] = "0.000001"
        rehash(record)
        with self.assertRaisesRegex(SourceError, "reproduce"):
            as_recompute_dataset(record)
        with self.assertRaisesRegex(SourceError, "reproduce"):
            compare_source_records([record])
        scenario = copy.deepcopy(self.by_id["C01"])
        scenario["source_record"] = record
        outcome = assess_scenario(scenario)
        self.assertEqual(outcome["verdict"], "unknown")
        self.assertIn("source_record_invalid", outcome["issue_codes"])

    def test_saved_matrix_replays_all_actual_outcomes_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "new-matrix"
            summary = run_fault_matrix(destination)
            self.assertTrue(summary["pass"])
            self.assertEqual(replay_fault_matrix(destination), {
                "pass": True, "network_used": False, "model_calls": 0, "scenario_count": 32,
                "differences": [], "scope": "reproduction of saved synthetic engineering inputs and source messages"})
            with self.assertRaises(FileExistsError):
                run_fault_matrix(destination)
            protocol = json.loads((destination / "protocol.json").read_text())
            self.assertFalse(protocol["network_acquisition_performed"])
            self.assertFalse(protocol["human_checked_reference_answers"])
            self.assertFalse(protocol["held_out_evaluation"])

    def test_changed_saved_outcome_and_summary_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "matrix"
            run_fault_matrix(destination)
            result_path = destination / "results.json"
            results = json.loads(result_path.read_text())
            results["outcomes"][0]["verdict"] = "error"
            result_path.write_text(json.dumps(results))
            replayed = replay_fault_matrix(destination)
            self.assertFalse(replayed["pass"])
            self.assertIn("results_sha256", replayed["differences"])
            self.assertIn("reassessed_outcomes", replayed["differences"])


if __name__ == "__main__":
    unittest.main()
