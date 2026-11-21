import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from curve_audit import metrics
from curve_audit.data import load_dataset
from curve_audit.investigation_tasks import (
    build_development_tasks, build_formal_tasks, freeze_investigation_tasks,
    apply_task_dataset, planned_run_manifest, public_task,
)
from curve_audit.investigation_scoring import score_investigation_outputs


class InvestigationScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(Path(__file__).resolve().parents[1] / "data/raw/expanded_archive.json")
        cls.tasks = build_formal_tasks(cls.dataset)

    def _task(self, family):
        return copy.deepcopy(next(task for task in self.tasks if task["family"] == family))

    def _gold(self, task, condition="test", repetition=1):
        claims = []
        tools = []
        for index, slot in enumerate(task["_private"]["expected_claims"]):
            result = slot.get("result")
            claims.append({"claim_id": f"claim{index}", "query": copy.deepcopy(slot["query"]),
                           "value": result["value"] if result else None, "unit": result["unit"] if result else "",
                           "status": slot["required_status"], "evidence_ids": result["evidence_ids"] if result else [],
                           "calculation_id": result["calculation_id"] if result else "", "explanation": "可复算；缺样部分无法回答。"})
            tools.append({"tool": "calculate", "arguments": copy.deepcopy(slot["query"]),
                          "planning_round": 1 if index == 0 else 2,
                          "result": {"ok": bool(result), "result": result}})
        narratives = [{"kind": slot["kind"], "stance": slot["expected_stance"], "explanation": "仅能由现有池状态判断证据边界。", "evidence_ids": []}
                      for slot in task["_private"].get("expected_narratives", [])]
        return {"task_id": task["task_id"], "condition": condition, "repetition": repetition, "status": "completed",
                "report": {"task_id": task["task_id"], "answer": "保留可答部分；缺少证据的部分不能回答。", "claims": claims,
                           "narrative_claims": narratives, "refusal_reason": None, "faults": []}, "tool_calls": tools}

    def _score(self, task, output):
        return score_investigation_outputs([task], [output], dataset=self.dataset)["per_output"][0]

    def test_distinct_task_families_and_real_required_work(self):
        self.assertEqual(30, len(self.tasks))
        self.assertEqual(117, sum(len(task["_private"]["expected_claims"]) for task in self.tasks))
        self.assertEqual([7, 7, 8, 8], sorted(sum(task["family"] == family for task in self.tasks)
                                         for family in {task["family"] for task in self.tasks}))
        formal_questions = {(task["question"], json.dumps(task.get("draft"))) for task in self.tasks}
        self.assertEqual(30, len(formal_questions))
        dev = build_development_tasks(self.dataset)
        self.assertEqual(2, len(dev))
        self.assertFalse(formal_questions & {(task["question"], json.dumps(task.get("draft"))) for task in dev})
        self.assertTrue(all(len(task["_private"]["expected_claims"]) >= 3 for task in self.tasks))

    def test_public_allowlist_never_transmits_queries_or_answer_keys(self):
        for task in self.tasks:
            clean = public_task(task)
            self.assertLessEqual(set(clean), {"task_id", "question", "window", "draft"})
            self.assertNotIn("query", json.dumps(clean))
            self.assertNotIn("expected_claims", json.dumps(clean))
            clean["window"]["start"] = "changed"
            self.assertNotEqual(clean["window"], task["window"])

    def test_freeze_is_immutable_including_data_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            first = freeze_investigation_tasks(self.dataset, directory)
            self.assertEqual(first, freeze_investigation_tasks(self.dataset, directory))
            changed = copy.deepcopy(self.dataset)
            changed["retrieved_at_utc"] = "changed"
            with self.assertRaises(FileExistsError):
                freeze_investigation_tasks(changed, directory)
            manifest = json.loads((Path(directory) / "freeze_manifest.json").read_text())
            self.assertEqual(117, manifest["required_numeric_or_refusal_slots"])

    def test_partial_fixture_preserves_original_and_two_answerable_slots(self):
        task = self._task("partial_evidence")
        original = copy.deepcopy(self.dataset)
        fixture = apply_task_dataset(self.dataset, task)
        self.assertEqual(original, self.dataset)
        self.assertEqual(len(self.dataset["snapshots"]) - 1, len(fixture["snapshots"]))
        self.assertEqual(2, sum(slot["answerable"] for slot in task["_private"]["expected_claims"]))
        self.assertTrue(fixture["_synthetic_fixture"]["synthetic"])

    def test_partial_refusals_do_not_require_a_dedicated_report_field(self):
        task = self._task("partial_evidence")
        output = self._gold(task)
        row = self._score(task, output)
        self.assertIsNone(output["report"]["refusal_reason"])
        self.assertEqual(1, row["required_slot_coverage"])
        self.assertEqual(1, row["partial_refusal_accuracy"])
        self.assertEqual(2, row["required_numeric_slots"])
        output["report"]["claims"] = output["report"]["claims"][:2]
        row = self._score(task, output)
        self.assertEqual(.5, row["required_slot_coverage"])
        self.assertEqual(1, row["required_numeric_accuracy"])

    def test_fabricated_value_cannot_also_be_a_valid_refusal(self):
        task = self._task("partial_evidence")
        output = self._gold(task)
        bad = copy.deepcopy(output["report"]["claims"][2])
        bad.update(value="0", status="supported")
        output["report"]["claims"].append(bad)
        row = self._score(task, output)
        self.assertEqual(.75, row["required_slot_coverage"])
        self.assertGreater(row["structured_unsupported_rate"], 0)

    def test_missing_answers_remain_in_required_slot_denominator(self):
        task = self._task("cross_window_scale")
        output = self._gold(task)
        output["report"]["claims"] = output["report"]["claims"][:1]
        row = self._score(task, output)
        self.assertEqual(3, row["required_numeric_slots"])
        self.assertAlmostEqual(1 / 3, row["required_numeric_accuracy"])
        self.assertEqual(1, row["structured_factual_accuracy"])

    def test_truth_and_citations_are_separate_even_when_number_is_wrong(self):
        task = self._task("cross_window_scale")
        output = self._gold(task)
        for claim in output["report"]["claims"]:
            claim["evidence_ids"] = []
            claim["calculation_id"] = ""
        row = self._score(task, output)
        self.assertEqual(1, row["structured_factual_accuracy"])
        self.assertEqual(0, row["citation_binding_accuracy"])
        output = self._gold(task)
        output["report"]["claims"][0]["value"] = "999999"
        row = self._score(task, output)
        self.assertLess(row["structured_factual_accuracy"], 1)
        self.assertEqual(1, row["citation_binding_accuracy"])

    def test_endpoint_alias_is_valid_but_continuous_window_maximum_is_not(self):
        task = self._task("locate_then_investigate")
        output = self._gold(task)
        claim = output["report"]["claims"][1]
        claim["query"]["operation"] = "first"
        result = metrics.calculate(self.dataset, claim["query"])
        claim.update(value=result["value"], unit=result["unit"], calculation_id=result["calculation_id"], evidence_ids=result["evidence_ids"])
        self.assertEqual(1, self._score(task, output)["required_slot_coverage"])
        peak = output["report"]["claims"][0]
        timestamp = task["_private"]["expected_claims"][0]["result"]["target_timestamp_utc"]
        peak["query"].update(start=timestamp, end=timestamp)
        self.assertLess(self._score(task, output)["required_slot_coverage"], 1)

    def test_endpoint_only_query_can_answer_requested_window_endpoint(self):
        task = self._task("cross_window_scale")
        task = copy.deepcopy(next(task for task in self.tasks if task["task_id"] == "I02"))
        output = self._gold(task)
        claim = output["report"]["claims"][0]
        claim["query"]["end"] = claim["query"]["start"]
        claim["query"]["operation"] = "last"
        result = metrics.calculate(self.dataset, claim["query"])
        claim.update(value=result["value"], unit=result["unit"], calculation_id=result["calculation_id"], evidence_ids=result["evidence_ids"])
        row = self._score(task, output)
        self.assertEqual(1, row["required_slot_coverage"])
        self.assertEqual(1, row["citation_binding_accuracy"])

    def test_draft_flags_are_separate_from_correct_numerical_coverage(self):
        task = self._task("draft_and_boundary_conflict")
        output = self._gold(task)
        output["report"]["claims"][0]["status"] = "supported"
        row = self._score(task, output)
        self.assertEqual(1, row["required_slot_coverage"])
        self.assertEqual(0, row["draft_conflict_identification"])

    def test_typed_judgment_does_not_certify_unrestricted_prose(self):
        task = self._task("draft_and_boundary_conflict")
        output = self._gold(task)
        output["report"]["answer"] = "这个小时采样值证明连续时间内绝无更高峰值。"
        scores = score_investigation_outputs([task], [output], dataset=self.dataset)
        self.assertEqual(1, scores["per_output"][0]["typed_narrative_accuracy"])
        self.assertEqual("not_performed", scores["human_review_status"])
        self.assertIsNone(scores["human_scores"])
        output["report"]["narrative_claims"][0]["stance"] = "supported"
        self.assertEqual(0, self._score(task, output)["typed_narrative_accuracy"])

    def test_peak_dependency_requires_an_earlier_planning_round(self):
        task = self._task("locate_then_investigate")
        output = self._gold(task)
        self.assertEqual(1, self._score(task, output)["dependency_order_compliance"])
        for call in output["tool_calls"]:
            call["planning_round"] = 1
        row = self._score(task, output)
        self.assertEqual(0, row["dependency_order_compliance"])
        self.assertTrue(all(item["valid_execution_order"] for item in row["dependency_details"]))

    def test_missing_planned_run_is_an_explicit_failure(self):
        task = self._task("partial_evidence")
        plan = planned_run_manifest([task], repetitions=2, conditions=("test",))
        scores = score_investigation_outputs([task], [self._gold(task)], dataset=self.dataset, planned_runs=plan)
        aggregate = scores["aggregates"]["test/all"]
        self.assertEqual(1, aggregate["failed_outputs"])
        self.assertEqual(8, aggregate["totals"]["required_slots"])
        self.assertEqual(.5, aggregate["task_mean"]["required_slot_coverage"])
        self.assertEqual(1, len(scores["missing_output_ids"]))

    def test_repetitions_are_averaged_before_pairing_tasks(self):
        a, b = self.tasks[:2]
        outputs = [self._gold(a, "methodA", repetition) for repetition in (1, 2, 3)]
        bad = self._gold(b, "methodA")
        bad["report"]["claims"] = []
        outputs += [bad, self._gold(a, "methodB"), self._gold(b, "methodB")]
        scores = score_investigation_outputs([a, b], outputs, dataset=self.dataset)
        self.assertEqual(.5, scores["aggregates"]["methodA/all"]["task_mean"]["required_slot_coverage"])
        pair = scores["paired_task_differences"][0]
        self.assertEqual(2, pair["paired_tasks"])
        self.assertEqual(-.5, pair["mean_difference"]["required_slot_coverage"])

    def test_batch_usage_is_not_repeated_as_per_output_cost(self):
        task = self.tasks[0]
        output = self._gold(task)
        output["metadata"] = {"batch_usage_reference": "batch_metadata.json", "usage": {"input_tokens": 100, "output_tokens": 20}}
        row = self._score(task, output)
        self.assertIsNone(row["input_tokens"])
        self.assertIsNone(row["output_tokens"])

    def test_double_review_sheets_are_blank_and_method_key_is_separate(self):
        task = self.tasks[0]
        with tempfile.TemporaryDirectory() as directory:
            score_investigation_outputs([task], [self._gold(task)], directory, self.dataset)
            for reviewer in ("A", "B"):
                with (Path(directory) / f"blind_review_{reviewer}.csv").open(encoding="utf-8-sig") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(1, len(rows))
                self.assertEqual("", rows[0]["judge_id"])
                self.assertEqual("", rows[0]["prose_supported_assertions"])
                self.assertNotIn("condition", rows[0])
            self.assertTrue((Path(directory) / "review_key.private.csv").exists())
            self.assertTrue((Path(directory) / "review_reference.private.json").exists())


    def test_rescoring_preserves_completed_human_reviews_and_adjudication(self):
        task = self.tasks[0]
        output = self._gold(task)
        with tempfile.TemporaryDirectory() as directory:
            score_investigation_outputs([task], [output], directory, self.dataset)
            for name, label_column, label in (("blind_review_A.csv", "judge_id", "Human A"),
                                              ("blind_review_B.csv", "prose_supported_assertions", "7"),
                                              ("adjudication.csv", "adjudicated_label", "supported")):
                path = Path(directory) / name
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    fields, rows = reader.fieldnames, list(reader)
                rows[0][label_column] = label
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
            before = {path.name: path.read_bytes() for path in Path(directory).glob("*.csv") if path.name != "per_output_scores.csv"}
            score_investigation_outputs([task], [output], directory, self.dataset)
            after = {path.name: path.read_bytes() for path in Path(directory).glob("*.csv") if path.name != "per_output_scores.csv"}
            self.assertEqual(before, after)
            output["report"]["answer"] = "Changed report must not inherit prior human judgments."
            with self.assertRaises(ValueError):
                score_investigation_outputs([task], [output], directory, self.dataset)
            self.assertEqual(before, {path.name: path.read_bytes() for path in Path(directory).glob("*.csv") if path.name != "per_output_scores.csv"})


if __name__ == "__main__":
    unittest.main()
