import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from curve_audit.common import read_json, write_json
from curve_audit.data import load_dataset
from curve_audit import metrics
from curve_audit.investigation_tasks import public_task
from curve_audit.investigation_tasks import build_formal_tasks
from curve_audit.investigation_experiment import fixed_reference, freeze_settings, replay, run_experiment
from curve_audit.investigation_scoring import score_investigation_outputs


class InvestigationExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(Path(__file__).resolve().parents[1] / "data/raw/expanded_archive.json")
        cls.tasks = build_formal_tasks(cls.dataset)

    def test_fixed_reference_never_reads_evaluator_answers_or_required_status(self):
        task = copy.deepcopy(self.tasks[-7])
        original = fixed_reference(self.dataset, task)
        task["_private"]["expected_claims"] = "poisoned answers"
        task["_private"]["expected_narratives"] = "poisoned narrative answers"
        task["_private"]["conflicting_slots"] = "poisoned status labels"
        altered = fixed_reference(self.dataset, task)
        self.assertEqual(original, altered)
        self.assertTrue(all(claim["value"] != "poisoned answers" for claim in altered["report"]["claims"]))

    def test_all_template_slots_are_recomputed_and_correct(self):
        outputs = [fixed_reference(self.dataset, task) for task in self.tasks]
        scores = score_investigation_outputs(self.tasks, outputs, dataset=self.dataset)
        summary = scores["aggregates"]["fixed_template/all"]
        self.assertEqual(117, summary["totals"]["required_slots"])
        self.assertEqual(117, summary["totals"]["completed_slots"])
        self.assertEqual(1, summary["task_mean"]["typed_narrative_accuracy"])
        self.assertTrue(all(output["metadata"]["inference_performed"] is False for output in outputs))

    def test_freeze_captures_390_outputs_and_rejects_model_or_budget_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            tasks, settings, plan = freeze_settings(self.dataset, destination, 3, "gpt-6-luna", "codex-cli")
            self.assertEqual(390, len(plan))
            self.assertEqual(390, len({item["output_id"] for item in plan}))
            self.assertEqual(30, sum(item["condition"] == "fixed_template" for item in plan))
            self.assertEqual(90, sum(item["condition"] == "self_review_agent" for item in plan))
            with self.assertRaises(ValueError):
                freeze_settings(self.dataset, destination, 3, "different-model", "codex-cli")
            altered_budgets = copy.deepcopy(settings["budgets"])
            altered_budgets["max_tool_calls"] += 1
            from curve_audit.investigation_experiment import read_json as actual_read_json
            def changed_read(path):
                if Path(path).name == "investigation_budgets.json":
                    return altered_budgets
                return actual_read_json(path)
            with patch("curve_audit.investigation_experiment.read_json", side_effect=changed_read):
                with self.assertRaises(ValueError):
                    freeze_settings(self.dataset, destination, 3, "gpt-6-luna", "codex-cli")

    def test_offline_mode_retains_all_unperformed_planned_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            status = run_experiment(self.dataset, directory, repetitions=1, provider="offline")
            self.assertEqual(150, status["planned_outputs"])
            self.assertEqual(30, status["recorded_outputs"])
            self.assertEqual(120, len(status["missing_output_ids"]))
            self.assertFalse(status["all_planned_completed"])
            scores = read_json(Path(directory) / "evaluation/scores.json")
            self.assertEqual(30, scores["aggregates"]["ordinary_agent/all"]["failed_outputs"])
            self.assertEqual(0, scores["aggregates"]["ordinary_agent/all"]["task_mean"]["required_slot_coverage"])
            replayed = replay(self.dataset, directory, Path(directory) / "replayed")
            self.assertEqual(scores["aggregates"], replayed["aggregates"])

    def test_replay_rejects_changed_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            run_experiment(self.dataset, directory, repetitions=1, provider="offline")
            changed = copy.deepcopy(self.dataset)
            changed["retrieved_at_utc"] = "changed"
            with self.assertRaises(ValueError):
                replay(changed, directory, Path(directory) / "replay")


    def test_correct_public_draft_is_not_misclassified_as_a_conflict(self):
        task = copy.deepcopy(self.tasks[-7])
        result = metrics.calculate(self.dataset, task["_private"]["recipes"][0])
        task["draft"] = {"text": f"该采样点报价是 {result['value']} {result['unit']}，与数据一致。"}
        output = fixed_reference(self.dataset, task)
        self.assertEqual("supported", output["report"]["claims"][0]["status"])
        self.assertEqual([], output["report"]["faults"])
        self.assertEqual("supported", output["report"]["narrative_claims"][0]["stance"])

    def test_reference_followups_derive_from_new_peak_result_not_frozen_dates(self):
        task = copy.deepcopy(self.tasks[8])
        changed = copy.deepcopy(self.dataset)
        new_peak = task["_private"]["recipes"][0]["start"]
        # Synthetic unit-test state changes only an in-memory source copy. It
        # cannot be admitted as new historical evidence or used in formal runs.
        for snapshot in changed["snapshots"]:
            if snapshot["target_timestamp_utc"] == new_peak:
                snapshot["raw_balances"]["USDC"] = str(10 ** 40)
                snapshot["balances"]["USDC"] = str(10 ** 34)
        actual = metrics.calculate(changed, task["_private"]["recipes"][0])
        self.assertEqual(new_peak, actual["target_timestamp_utc"])
        self.assertNotEqual(new_peak, task["_private"]["expected_claims"][0]["result"]["target_timestamp_utc"])
        output = fixed_reference(changed, task)
        self.assertEqual(new_peak, output["report"]["claims"][2]["query"]["start"])
        self.assertEqual(new_peak, output["report"]["claims"][4]["query"]["start"])
        self.assertTrue(all(call["planning_round"] == 2 for call in output["tool_calls"][1:]))

    @staticmethod
    def _write_failed_batch(dataset, tasks, condition, run_dir, model, budgets):
        rows = [{"task_id": task["task_id"], "condition": condition, "status": "failed", "report": {},
                 "metadata": {"error": "Simulated unit-test failure; no inference"}} for task in tasks]
        write_json(run_dir / "public_input.json", [public_task(task) for task in tasks])
        write_json(run_dir / "outputs.json", rows)
        write_json(run_dir / "batch_metadata.json", {"condition": condition, "status": "failed", "task_count": len(tasks),
                   "model_calls": [], "actual_usage": {"input_tokens": 0, "output_tokens": 0}, "wall_seconds": 0})
        return rows

    def test_retained_failed_attempt_is_never_silently_retried(self):
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            with patch("curve_audit.investigation_experiment.run_investigation_batch", side_effect=self._write_failed_batch) as run:
                first = run_experiment(self.dataset, directory, repetitions=1, provider="codex-cli")
                self.assertEqual(4, run.call_count)
            with patch("curve_audit.investigation_experiment.run_investigation_batch", side_effect=AssertionError("Must not retry")) as run:
                second = run_experiment(self.dataset, directory, repetitions=1, provider="codex-cli")
                self.assertEqual(0, run.call_count)
            self.assertEqual(120, first["failed_outputs"])
            self.assertEqual(first["failed_outputs"], second["failed_outputs"])
            self.assertEqual([], second["missing_output_ids"])

    def test_duplicate_retained_task_ids_are_rejected_before_inference(self):
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            destination = Path(directory)
            tasks, _, _ = freeze_settings(self.dataset, destination, 1, "gpt-6-luna", "codex-cli")
            run_dir = destination / "runs/ordinary_agent_r1"
            write_json(run_dir / "public_input.json", [public_task(task) for task in tasks])
            write_json(run_dir / "outputs.json", [{"task_id": tasks[0]["task_id"], "condition": "ordinary_agent", "status": "failed", "report": {}}] * 30)
            with patch("curve_audit.investigation_experiment.run_investigation_batch", side_effect=AssertionError("No inference")):
                with self.assertRaises(ValueError):
                    run_experiment(self.dataset, directory, repetitions=1, provider="codex-cli")

    def test_interrupted_response_is_preserved_as_failure_with_observed_usage(self):
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            destination = Path(directory)
            tasks, _, _ = freeze_settings(self.dataset, destination, 1, "gpt-6-luna", "codex-cli")
            run_dir = destination / "runs/ordinary_agent_r1"
            write_json(run_dir / "public_input.json", [public_task(task) for task in tasks])
            write_json(run_dir / "round_1_plan/response.json", {"plans": []})
            write_json(run_dir / "round_1_plan/metadata.json", {"inference_performed": True, "runtime_seconds": 1.5,
                       "usage": {"input_tokens": 11, "output_tokens": 7}})
            original_response = (run_dir / "round_1_plan/response.json").read_bytes()
            with patch("curve_audit.investigation_experiment.run_investigation_batch", side_effect=self._write_failed_batch) as run:
                status = run_experiment(self.dataset, directory, repetitions=1, provider="codex-cli")
                self.assertEqual(3, run.call_count)
                self.assertTrue(all(call.args[2] != "ordinary_agent" for call in run.call_args_list))
            self.assertEqual(original_response, (run_dir / "round_1_plan/response.json").read_bytes())
            self.assertEqual(11, status["costs_by_condition"]["ordinary_agent"]["input_tokens"])
            self.assertEqual(7, status["costs_by_condition"]["ordinary_agent"]["output_tokens"])
            recovery = read_json(run_dir / "resume_recovery.json")
            self.assertFalse(recovery["model_retried"])
            self.assertTrue(all(row["status"] == "failed" for row in read_json(run_dir / "outputs.json")))


if __name__ == "__main__":
    unittest.main()
