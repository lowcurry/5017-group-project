import copy
import json
import tempfile
import unittest
from pathlib import Path

from curve_audit.experiment import (
    apply_task_dataset, fixed_template, freeze_tasks, planned_run_manifest, public_task,
)
from curve_audit.scoring import score_outputs


class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads((Path(__file__).resolve().parents[1] /
                                  "data/raw/pilot_archive.json").read_text())
        cls.temp = tempfile.TemporaryDirectory()
        cls.tasks = freeze_tasks(cls.dataset, cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_frozen_counts_and_168_real_run_manifest(self):
        self.assertEqual(30, len(self.tasks))
        test = [t for t in self.tasks if t["split"] == "test"]
        self.assertEqual(16, sum(t["group"] == "historical" for t in test))
        self.assertEqual(8, sum(t["group"] == "challenge" for t in test))
        manifest = planned_run_manifest(self.tasks)
        self.assertEqual(168, len(manifest))
        self.assertEqual(168, len({r["output_id"] for r in manifest}))
        self.assertEqual(24, sum(r["condition"] == "fixed_template" for r in manifest))
        self.assertEqual(240, len(planned_run_manifest(self.tasks, include_ablation=True)))

    def test_answerkey_and_fault_hints_never_reach_public_inputs(self):
        allowed = {"task_id", "question", "query", "draft"}
        for task in self.tasks:
            visible = public_task(task)
            self.assertLessEqual(set(visible), allowed)
            text = json.dumps(visible)
            for secret in ("_private", "expected", "fixture_status", "required_status", "missing_data"):
                self.assertNotIn(secret, text)
            visible["query"]["metric"] = "changed"
            self.assertNotEqual(task["query"]["metric"], "changed")
        public = json.loads((Path(self.temp.name) / "tasks.public.json").read_text())
        self.assertTrue(all(set(task) <= allowed for task in public))

    def test_no_development_target_is_a_test_target(self):
        dev = {t["query"][key] for t in self.tasks if t["split"] == "dev" for key in ("start", "end")}
        test = {t["query"][key] for t in self.tasks if t["split"] == "test" for key in ("start", "end")}
        self.assertFalse(dev & test)

    def test_same_freeze_is_reusable_but_changed_data_cannot_overwrite(self):
        self.assertEqual(self.tasks, freeze_tasks(self.dataset, self.temp.name))
        changed = copy.deepcopy(self.dataset)
        changed["retrieved_at_utc"] = "changed_metadata"
        with self.assertRaises(FileExistsError):
            freeze_tasks(changed, self.temp.name)

    def test_fixtures_are_copies_and_keep_synthetic_provenance(self):
        original = copy.deepcopy(self.dataset)
        challenge = next(t for t in self.tasks if t.get("_private", {}).get("fixture"))
        fixture = apply_task_dataset(self.dataset, challenge)
        self.assertEqual(original, self.dataset)
        self.assertEqual("synthetic_challenge_fixture", fixture["fixture_status"])
        self.assertTrue(fixture["_synthetic_fixture"]["synthetic"])
        self.assertNotEqual(self.dataset, fixture)

    def test_strong_template_correctly_handles_every_challenge(self):
        outputs = []
        for task in self.tasks:
            if task["split"] != "test":
                continue
            report = fixed_template(apply_task_dataset(self.dataset, task), public_task(task))
            outputs.append({"task_id": task["task_id"], "condition": "fixed_template",
                            "repetition": 1, "report": report})
        scores = score_outputs(self.tasks, outputs, dataset=self.dataset)
        self.assertEqual(1, scores["aggregates"]["fixed_template/historical"]["pooled"]["numeric_accuracy"])
        self.assertEqual(1, scores["aggregates"]["fixed_template/challenge"]["pooled"]["effective_answer_coverage"])
        self.assertEqual(1, scores["aggregates"]["fixed_template/challenge"]["pooled"]["fault_detection_rate"])
        self.assertEqual(0, scores["aggregates"]["fixed_template/historical"]["pooled"]["fault_false_positive_rate"])

    def test_empty_or_missing_numeric_answers_stay_in_denominator(self):
        task = next(t for t in self.tasks if t["split"] == "test" and t["group"] == "historical")
        output = {"task_id": task["task_id"], "condition": "ordinary", "report": {"claims": []}}
        scores = score_outputs(self.tasks, [output])
        row = scores["per_output"][0]
        self.assertEqual(1, row["required_numeric_slots"])
        self.assertEqual(0, row["correct_numeric_slots"])
        self.assertEqual(0, row["effective_answer_coverage"])
        self.assertIsNone(row["structured_claim_support"])
        output["report"] = {"claims": [{"query": task["query"], "value": None, "status": "unsupported"}],
                            "refusal_reason": "cannot answer"}
        row = score_outputs(self.tasks, [output])["per_output"][0]
        self.assertEqual(0, row["numeric_accuracy"])
        self.assertTrue(row["over_refusal"])

    def test_missing_reference_does_not_turn_correct_fact_into_hallucination(self):
        task = next(t for t in self.tasks if t["split"] == "test" and t["group"] == "historical")
        report = fixed_template(self.dataset, public_task(task))
        report["claims"][0]["evidence_ids"] = []
        report["claims"][0]["calculation_id"] = None
        row = score_outputs(self.tasks, [{"task_id": task["task_id"], "condition": "ordinary", "report": report}])["per_output"][0]
        self.assertEqual(1, row["structured_claim_support"])
        self.assertEqual(0, row["structured_unsupported_rate"])
        self.assertEqual(0, row["evidence_traceability"])
        self.assertEqual(1, row["numeric_accuracy"])

    def test_conflict_completion_requires_matching_flag_explanation_and_fault_details(self):
        task = next(t for t in self.tasks if t["split"] == "test" and
                    t["_private"]["expected"]["required_status"] == "conflicting")
        original = fixed_template(self.dataset, public_task(task))
        cases = []
        no_details = copy.deepcopy(original)
        no_details["faults"] = []
        cases.append(no_details)
        no_explanation = copy.deepcopy(original)
        no_explanation["claims"][0]["explanation"] = "   "
        cases.append(no_explanation)
        no_matching_flag = copy.deepcopy(original)
        no_matching_flag["claims"][0]["status"] = "supported"
        cases.append(no_matching_flag)
        empty_detail = copy.deepcopy(original)
        empty_detail["faults"] = ["  "]
        cases.append(empty_detail)
        for report in cases:
            row = score_outputs(self.tasks, [{"task_id": task["task_id"], "condition": "ordinary_agent", "report": report}])["per_output"][0]
            self.assertEqual(1, row["numeric_accuracy"])
            self.assertEqual(0, row["effective_answer_coverage"])
            self.assertEqual(0, row["fault_tp"])
            self.assertEqual(1, row["fault_fn"])
            self.assertFalse(row["fault_explanation_complete"])
        row = score_outputs(self.tasks, [{"task_id": task["task_id"], "condition": "ordinary_agent", "report": original}])["per_output"][0]
        self.assertEqual(1, row["effective_answer_coverage"])
        self.assertEqual(1, row["fault_tp"])
        self.assertTrue(row["fault_explanation_complete"])

    def test_full_model_schema_irrelevant_defaults_preserve_correctness(self):
        task = next(t for t in self.tasks if t["split"] == "test" and t["group"] == "historical")
        report = fixed_template(self.dataset, public_task(task))
        report["claims"][0]["query"].update({"direction": "USDC_USDT", "size": 1000})
        row = score_outputs(self.tasks, [{"task_id": task["task_id"], "condition": "ordinary_agent", "report": report}])["per_output"][0]
        self.assertEqual(1, row["numeric_accuracy"])
        self.assertEqual(1, row["evidence_traceability"])

    def test_failures_retained_and_blind_review_has_no_fabricated_scores(self):
        task = next(t for t in self.tasks if t["split"] == "test" and t["group"] == "historical")
        output = {"task_id": task["task_id"], "condition": "ordinary", "status": "timeout", "report": {}}
        with tempfile.TemporaryDirectory() as directory:
            result = score_outputs(self.tasks, [output], output_dir=directory)
            self.assertEqual(1, result["aggregates"]["ordinary/all"]["failed_runs"])
            self.assertEqual(0, result["per_output"][0]["numeric_accuracy"])
            self.assertEqual("not_performed", result["human_review_status"])
            self.assertIsNone(result["human_scores"])
            import csv
            with (Path(directory) / "blind_review.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertNotIn("condition", rows[0])
            self.assertEqual("", rows[0]["prose_supported_assertions"])
            self.assertEqual("", rows[0]["judge_id"])

    def test_repetitions_aggregate_per_task_without_independent_event_statistics(self):
        task = next(t for t in self.tasks if t["split"] == "test" and t["group"] == "historical")
        report = fixed_template(self.dataset, public_task(task))
        outputs = [{"task_id": task["task_id"], "condition": "ordinary", "repetition": i, "report": report}
                   for i in range(1, 4)]
        outputs.append({"task_id": task["task_id"], "condition": "evidence", "repetition": 1, "report": report})
        scores = score_outputs(self.tasks, outputs)
        pair = next(p for p in scores["paired_task_differences"] if p["group"] == "historical")
        self.assertEqual(1, pair["task_count"])
        self.assertEqual(0, pair["mean_difference"]["numeric_accuracy"])
        self.assertNotIn("p_value", pair)


if __name__ == "__main__":
    unittest.main()
