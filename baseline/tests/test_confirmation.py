"""Tests of review gates, inference boundaries and meaningful metric distinctions."""
import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from curve_audit.confirmation import (
    ABSOLUTE_TOLERANCE, CONDITIONS, FormalStudyBlocked, build_draft_tasks,
    build_fault_scenarios, build_reference_review_package, classify_numeric_claim,
    classify_verifier_scenario, content_hash, has_refusal_reason, number_equal,
    planned_runs, public_task, public_tasks, require_formal_ready, score_repair,
    score_report, validate_reference_review, write_reference_review_package,
)


def sample_references():
    return [
        {"task_id": "C01", "slot_id": "S1", "answerable": True,
         "value": "1.25", "unit": "USDT/USDC", "block_number": 123, "block_hash": "0x" + "a" * 64,
         "target_timestamp_utc": "2023-03-10T00:00:00Z"},
        {"task_id": "C01", "slot_id": "S2", "answerable": True, "value": "2", "unit": "percent"},
        {"task_id": "C01", "slot_id": "S3", "answerable": False, "value": None, "unit": "dimensionless"},
    ]


def numeric_claim(reference):
    result = {key: copy.deepcopy(value) for key, value in reference.items() if key not in ("answerable", "task_id")}
    result["status"] = "supported"
    return result


def sample_tasks():
    return [{"task_id": "C01", "question": "Requested components", "required_components": [
        {"slot_id": f"S{i}", "label": "component", "request": {"metric": "share_pct"}} for i in range(1, 4)
    ]}]


def record(package, identity="human_A"):
    rows = []
    for reference in package["candidate_references"]:
        rows.append({
            "task_id": reference["task_id"], "slot_id": reference["slot_id"], "verdict": "approve",
            "reference_sha256": content_hash(reference), "reviewed_at_utc": "2026-10-15T03:00:00Z",
            "checked_fields": ["answerability", "evidence_boundary", "value", "unit", "time", "block", "formula", "raw_evidence", "raw_to_normalized"],
            "raw_record_refs": ["test-fixture-record"], "calculation_note": "Synthetic test fixture; not an actual human review.",
        })
    return {"reviewer_id": identity, "reviewer_type": "human", "independent_review": True,
            "review_package_sha256": package["review_package_sha256"], "slots": rows}


class ComparisonRuleTests(unittest.TestCase):
    def test_tolerance_boundary_and_nonfinite_rejection(self):
        self.assertEqual("1E-8", str(ABSOLUTE_TOLERANCE))
        self.assertTrue(number_equal("1", "1.00000001"))
        self.assertFalse(number_equal("1", "1.00000001000001"))
        self.assertFalse(number_equal("1", "1.0000000100000000000000000000000000000000000001"))
        for value in ("NaN", "Infinity", "-Infinity", True, None, "not a number"):
            self.assertFalse(number_equal(value, value))

    def test_raw_integers_are_exact_canonical_and_do_not_use_tolerance(self):
        self.assertTrue(number_equal("1000000000000000001", "1000000000000000001", exact=True))
        self.assertFalse(number_equal("1000000000000000001", "1000000000000000000", exact=True))
        self.assertFalse(number_equal("01", "01", exact=True))
        self.assertFalse(number_equal("1.0", "1", exact=True))

    def test_unit_block_and_target_time_are_exact_even_for_correct_value(self):
        reference = sample_references()[0]
        for field, value, dimension in (("unit", "USD", "unit_error"), ("block_number", 124, "block_error"),
                                        ("block_hash", "0x" + "b" * 64, "block_error"),
                                        ("target_timestamp_utc", "2023-03-10T01:00:00Z", "time_error")):
            claim = numeric_claim(reference)
            claim[field] = value
            result = classify_numeric_claim(claim, reference)
            self.assertTrue(result[dimension])
            self.assertFalse(result["numeric_error"])
            self.assertFalse(result["correct"])

    def test_boolean_cannot_impersonate_exact_block_identifier(self):
        reference = sample_references()[0]
        reference["block_number"] = 1
        claim = numeric_claim(reference)
        claim["block_number"] = True
        self.assertTrue(classify_numeric_claim(claim, reference)["block_error"])

    def test_slot_id_does_not_excuse_wrong_window_direction_or_query(self):
        reference = sample_references()[1]
        reference["query"] = {"metric": "quote_rate", "operation": "last", "start": "a", "end": "b", "direction": "USDC_USDT", "size": 1000}
        claim = numeric_claim(reference)
        claim["query"]["start"] = "other"
        claim["query"]["direction"] = "USDT_USDC"
        result = classify_numeric_claim(claim, reference)
        self.assertTrue(result["time_error"])
        self.assertTrue(result["object_error"])
        self.assertFalse(result["correct"])

    def test_refusal_reason_accepts_field_or_body_and_only_checks_presence(self):
        self.assertTrue(has_refusal_reason({"explanation": "Missing sample"}, {}))
        self.assertTrue(has_refusal_reason({}, {"answer": "The evidence boundary prevents an answer."}))
        self.assertFalse(has_refusal_reason({"reason": "  "}, {"answer": None}))


class TaskAndInferenceBoundaryTests(unittest.TestCase):
    def test_eight_draft_tasks_four_families_twenty_six_slots(self):
        tasks = build_draft_tasks()
        self.assertEqual(8, len(tasks))
        self.assertEqual(26, sum(len(task["required_components"]) for task in tasks))
        self.assertEqual([2, 2, 2, 2], sorted(Counter(task["family"] for task in tasks).values()))
        self.assertEqual({"usdc_march_2023"}, {task["event_id"] for task in tasks})
        self.assertTrue(all(task["status"].startswith("draft") for task in tasks))

    def test_locate_requests_keep_dynamic_dependency_without_resolved_time(self):
        for task in build_draft_tasks()[4:6]:
            followers = task["required_components"][1:]
            self.assertEqual([-1, 0, 1], [slot["request"]["offset_hours"] for slot in followers])
            self.assertTrue(all(slot["request"]["time_from_slot"] == "S1" for slot in followers))
            self.assertTrue(all("start" not in slot["request"] for slot in followers))

    def test_public_allowlist_removes_nested_reference_and_is_copy(self):
        task = build_draft_tasks()[0]
        task["_private"] = {"expected_value": "secret-private-value"}
        task["window"]["expected_value"] = "secret-private-value"
        task["required_components"][0]["candidate_reference"] = "secret-private-value"
        task["required_components"][0]["request"]["expected_value"] = "secret-private-value"
        task["allowed_evidence"]["reference_values"] = "secret-private-value"
        clean = public_task(task)
        self.assertNotIn("secret-private-value", json.dumps(clean))
        clean["window"]["start"] = "changed"
        self.assertNotEqual(clean["window"]["start"], task["window"]["start"])

    def test_matched_run_plan_has_shared_initials_and_no_duplicate_outputs(self):
        plan = planned_runs(build_draft_tasks())
        self.assertEqual((24, 72, 8), tuple(len(plan[key]) for key in ("initials", "finals", "fixed_references")))
        self.assertEqual(72, len({run["output_id"] for run in plan["finals"]}))
        for initial in plan["initials"]:
            branches = [run for run in plan["finals"] if run["initial_id"] == initial["initial_id"]]
            self.assertEqual(set(CONDITIONS), {run["condition"] for run in branches})

    def test_naive_or_reversed_event_windows_fail(self):
        with self.assertRaises(ValueError):
            build_draft_tasks([{"event_id": "x", "start": "2023-03-10T00:00:00", "end": "2023-03-10T06:00:00Z"}] * 2)
        with self.assertRaises(ValueError):
            build_draft_tasks([{"event_id": "x", "start": "2023-03-10T06:00:00Z", "end": "2023-03-10T00:00:00Z"}] * 2)


class HumanReferenceGateTests(unittest.TestCase):
    def setUp(self):
        self.package = build_reference_review_package(sample_tasks(), sample_references())
        self.a, self.b = record(self.package, "human_A"), record(self.package, "human_B")

    def test_blank_package_is_pending_and_cannot_start_formal(self):
        blank = build_reference_review_package(build_draft_tasks())
        result = validate_reference_review(blank)
        self.assertFalse(result["pass"])
        self.assertEqual("pending_external_records", result["status"])
        self.assertIsNone(result["frozen_reference_sha256"])
        self.assertTrue(all(row["verdict"] == "" for row in blank["reviewer_a_blank_rows"]))

    def test_two_complete_distinct_records_freeze_hash_bound_reference(self):
        result = validate_reference_review(self.package, self.a, self.b)
        self.assertTrue(result["pass"], result["errors"])
        self.assertEqual(content_hash(result["frozen_reference"]), result["frozen_reference_sha256"])
        self.assertEqual("supplied_record_integrity_only", result["identity_assurance"])

    def test_duplicate_reviewer_incomplete_checks_and_wrong_binding_fail(self):
        self.b["reviewer_id"] = "human_A"
        self.b["slots"][0]["checked_fields"].remove("block")
        self.b["slots"][1]["reference_sha256"] = "0" * 64
        result = validate_reference_review(self.package, self.a, self.b)
        self.assertFalse(result["pass"])
        self.assertIn("two_distinct_human_reviewers_required", result["errors"])
        self.assertTrue(any("checks_incomplete" in error for error in result["errors"]))
        self.assertTrue(any("binding_mismatch" in error for error in result["errors"]))

    def test_candidate_package_tampering_is_not_accepted(self):
        self.package["candidate_references"][0]["value"] = "99"
        result = validate_reference_review(self.package, self.a, self.b)
        self.assertFalse(result["pass"])
        self.assertIn("review_package_hash_mismatch", result["errors"])

    def test_dynamic_reference_must_have_a_resolved_point_query_before_freeze(self):
        references = sample_references()
        references[0]["query"] = {"time_from_slot": "S1", "offset_hours": -1}
        package = build_reference_review_package(sample_tasks(), references)
        self.assertFalse(validate_reference_review(package, record(package, "A"), record(package, "B"))["pass"])
        references[0]["resolved_query"] = {"start": "2023-03-10T00:00:00Z", "end": "2023-03-10T00:00:00Z"}
        package = build_reference_review_package(sample_tasks(), references)
        self.assertTrue(validate_reference_review(package, record(package, "A"), record(package, "B"))["pass"])

    def test_disagreement_requires_third_bound_resolution(self):
        revised = copy.deepcopy(self.package["candidate_references"][0])
        revised["value"] = "1.26"
        self.b["slots"][0].update(verdict="revise", proposed_reference=revised)
        result = validate_reference_review(self.package, self.a, self.b)
        self.assertFalse(result["pass"])
        arbitration = {"reviewer_id": "human_C", "reviewer_type": "human", "review_package_sha256": self.package["review_package_sha256"], "slots": [{
            "task_id": "C01", "slot_id": "S1", "reviewed_at_utc": "2026-10-15T04:00:00Z",
            "resolution_note": "Synthetic arbitration fixture, not a real human label.", "resolved_reference": revised,
        }]}
        final = validate_reference_review(self.package, self.a, self.b, arbitration)
        self.assertTrue(final["pass"], final["errors"])
        self.assertEqual("1.26", final["frozen_reference"]["references"][0]["value"])
        arbitration["reviewer_id"] = "human_A"
        self.assertFalse(validate_reference_review(self.package, self.a, self.b, arbitration)["pass"])

    def test_missing_or_duplicate_review_rows_are_rejected(self):
        self.b["slots"][1] = copy.deepcopy(self.b["slots"][0])
        result = validate_reference_review(self.package, self.a, self.b)
        self.assertFalse(result["pass"])
        self.assertIn("human_review_B_slot_coverage_incomplete", result["errors"])

    def test_ai_notes_do_not_satisfy_human_reference_gate(self):
        self.b["reviewer_type"] = "ai"
        self.assertFalse(validate_reference_review(self.package, self.a, self.b)["pass"])

    def test_formal_gate_requires_all_four_conditions_and_hash_binding(self):
        review = validate_reference_review(self.package, self.a, self.b)
        kwargs = {
            "task_manifest": {"status": "frozen", "tasks_sha256": self.package["tasks_sha256"], "event_scope": "single_event"},
            "source_status": {"pass": True, "independent_path_verified": True},
            "budget_status": {"pass": True, "enforcement": "hard", "equal_condition_caps": True},
        }
        self.assertTrue(require_formal_ready(review, **kwargs)["pass"])
        for key, field, bad in (("source_status", "independent_path_verified", False), ("budget_status", "enforcement", "estimated"),
                                 ("budget_status", "equal_condition_caps", False), ("task_manifest", "tasks_sha256", "f" * 64)):
            broken = copy.deepcopy(kwargs)
            broken[key][field] = bad
            with self.assertRaises(FormalStudyBlocked):
                require_formal_ready(review, **broken)

    def test_written_public_tasks_exclude_private_candidates_and_blanks_stay_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            write_reference_review_package(directory, sample_tasks(), sample_references())
            public = (Path(directory) / "tasks.public.json").read_text()
            self.assertNotIn("1.25", public)
            self.assertNotIn("candidate_references", public)
            self.assertIn("reference_sha256", (Path(directory) / "reviewer_a_blank.csv").read_text())

    def test_review_writer_never_overwrites_any_existing_human_file(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "reviewer_a_blank.csv"
            existing.write_text("actual supplied record must survive", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_reference_review_package(directory, sample_tasks(), sample_references())
            self.assertEqual("actual supplied record must survive", existing.read_text())
            self.assertFalse((Path(directory) / "reference_review_package.private.json").exists())


class ScopedEvaluationTests(unittest.TestCase):
    def test_missing_wrong_numeric_and_refusal_use_different_denominators(self):
        references = sample_references()
        report = {"claims": [numeric_claim(references[0]), {"slot_id": "S3", "value": None, "status": "unsupported"}], "answer": "Cannot infer the continuous peak."}
        row = score_report(report, references)
        self.assertEqual(0.5, row["metrics"]["correct_answerable_coverage"]["value"])
        self.assertEqual(0, row["metrics"]["reported_numeric_error_rate"]["value"])
        self.assertEqual(0.5, row["metrics"]["omission_rate"]["value"])
        self.assertEqual(1, row["metrics"]["justified_refusal_rate"]["value"])
        self.assertEqual(1, row["metrics"]["reported_numeric_error_rate"]["denominator"])
        self.assertEqual(2, row["metrics"]["correct_answerable_coverage"]["denominator"])

    def test_duplicate_correct_and_wrong_value_cannot_game_coverage(self):
        references = sample_references()[:1]
        correct = numeric_claim(references[0])
        wrong = copy.deepcopy(correct)
        wrong["value"] = "50"
        result = score_report({"claims": [correct, wrong]}, references)
        self.assertEqual(0, result["metrics"]["correct_answerable_coverage"]["value"])
        self.assertEqual(0.5, result["metrics"]["reported_numeric_error_rate"]["value"])

    def test_simultaneous_fabrication_is_not_a_justified_refusal(self):
        reference = sample_references()[2]
        report = {"answer": "Unavailable", "claims": [{"slot_id": "S3", "value": None, "status": "unsupported"}, {"slot_id": "S3", "value": "2", "unit": "dimensionless", "status": "supported"}]}
        row = score_report(report, [reference])
        self.assertEqual(0, row["metrics"]["justified_refusal_rate"]["value"])
        self.assertEqual(1, row["metrics"]["unsupported_structured_rate"]["value"])

    def test_over_refusal_is_not_omission_and_prose_is_not_automatically_scored(self):
        reference = sample_references()[1]
        row = score_report({"answer": "Unsupported causal narrative; missing data.", "claims": [{"slot_id": "S2", "value": None, "status": "unsupported"}]}, [reference])
        self.assertEqual(1, row["metrics"]["over_refusal_rate"]["value"])
        self.assertEqual(0, row["metrics"]["omission_rate"]["value"])
        self.assertIsNone(row["metrics"]["reported_numeric_error_rate"]["value"])
        self.assertEqual("report.claims_only", row["scope"])

    def test_repair_distinguishes_wrong_value_correction_from_missing_fill(self):
        refs = sample_references()[:2]
        wrong = numeric_claim(refs[0])
        wrong["value"] = "9"
        row = score_repair({"claims": [wrong]}, {"claims": [numeric_claim(ref) for ref in refs]}, refs)
        self.assertEqual(1, row["counts"]["corrected_reported_error_slots"])
        self.assertEqual(1, row["counts"]["missing_values_filled"])
        self.assertEqual(0, row["counts"]["correct_answers_lost"])

    def test_repair_counts_correct_answer_harmed_by_review(self):
        refs = sample_references()[:1]
        claim = numeric_claim(refs[0])
        wrong = copy.deepcopy(claim)
        wrong["unit"] = "USD"
        row = score_repair({"claims": [claim]}, {"claims": [wrong]}, refs)
        self.assertEqual(1, row["counts"]["correct_answers_lost"])
        self.assertEqual(1, row["counts"]["new_reported_error_slots"])

    def test_failed_initial_execution_is_not_reported_as_a_successful_omission_repair(self):
        refs = sample_references()[:1]
        row = score_repair({}, {"claims": [numeric_claim(refs[0])]}, refs, initial_status="timeout")
        self.assertEqual(1, row["counts"]["execution_failure_pairs"])
        self.assertEqual(0, row["counts"]["missing_values_filled"])

    def test_scores_do_not_expose_reference_values_and_failures_stay_in_slot_denominator(self):
        refs = sample_references()
        row = score_report({"claims": [numeric_claim(refs[0])]}, refs, execution_status="timeout")
        self.assertEqual(0, row["metrics"]["correct_answerable_coverage"]["value"])
        self.assertEqual(2, row["metrics"]["correct_answerable_coverage"]["denominator"])
        self.assertNotIn("1.25", json.dumps(row))


class FaultSpecificationTests(unittest.TestCase):
    def test_thirty_two_scenarios_are_declared_not_executed(self):
        scenarios = build_fault_scenarios()
        self.assertEqual(32, len(scenarios))
        self.assertEqual({"known_error": 12, "clean_control": 12, "valid_refusal": 4, "source_unknown": 4}, dict(Counter(item["class"] for item in scenarios)))
        self.assertEqual({"specification_not_executed"}, {item["status"] for item in scenarios})
        self.assertEqual(32, len({item["scenario_id"] for item in scenarios}))

    def test_detection_requires_correct_injected_issue_and_unknown_does_not_blame_agent(self):
        scenarios = build_fault_scenarios()
        bad = next(item for item in scenarios if item["class"] == "known_error")
        self.assertTrue(classify_verifier_scenario(bad, {"status": "contradicted", "detected_issues": [bad["issue"]]})["pass"])
        self.assertEqual("wrong_issue", classify_verifier_scenario(bad, {"status": "contradicted", "detected_issues": ["other"]})["classification"])
        unknown = next(item for item in scenarios if item["class"] == "source_unknown")
        self.assertTrue(classify_verifier_scenario(unknown, {"status": "unverifiable"})["pass"])
        self.assertFalse(classify_verifier_scenario(unknown, {"status": "unverifiable", "agent_error": True})["pass"])

    def test_clean_control_false_positive_is_separate_from_source_inconclusive(self):
        clean = next(item for item in build_fault_scenarios() if item["class"] == "clean_control")
        self.assertEqual("false_positive", classify_verifier_scenario(clean, {"status": "contradicted"})["classification"])
        self.assertEqual("inconclusive", classify_verifier_scenario(clean, {"status": "unverifiable"})["classification"])


if __name__ == "__main__":
    unittest.main()
