"""Result-driven investigation regressions; all model responses are test doubles."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from curve_audit.common import read_json, write_json
from curve_audit.data import load_dataset
from curve_audit.investigation import run_investigation_batch
from curve_audit.provider import ProviderUnavailable
from curve_audit.verification import verify_claims

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = read_json(ROOT / "config/investigation_budgets.json")
START, END = "2023-03-10T18:00:00Z", "2023-03-11T18:00:00Z"


def task_fixture(tid="INV_TEST"):
    return {"task_id": tid, "question": "先定位失衡采样最大值，再查询该时点的USDC占比与USDC→USDT报价。",
            "window": {"start": START, "end": END}, "split": "test", "family": "test_only",
            "_private": {"recipes": ["PRIVATE_RECIPE_SENTINEL"],
                         "expected_claims": ["PRIVATE_ANSWER_SENTINEL"],
                         "fixture": {}, "secret": "PRIVATE_SECRET_SENTINEL"}}


def query(metric="imbalance_l1", operation="maximum", start=START, end=END, **changes):
    return {"metric": metric, "operation": operation, "start": start, "end": end,
            "coin": changes.get("coin", "USDC"), "direction": changes.get("direction", "USDC_USDT"),
            "size": changes.get("size", 1000)}


def request(q, dependencies=()):
    return {"query": q, "depends_on": list(dependencies), "rationale": "Use retained evidence."}


def plans(tid, requests=()):
    return {"plans": [{"task_id": tid, "requests": list(requests)}]}


def context_from_prompt(prompt):
    # Prompt prose may change. Decode the final JSON object rather than rely on
    # text at a particular byte offset.
    for index, char in enumerate(prompt):
        if char != "{":
            continue
        try:
            value = json.loads(prompt[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and "tool_history" in value:
            return value
    raise AssertionError("No retained context in model prompt")


def report_from_history(tid, context):
    claims = []
    for call in context["tool_history"][tid]:
        returned = call["result"]
        if returned["ok"]:
            result = returned["result"]
            claim = {key: copy.deepcopy(result[key]) for key in
                     ("query", "value", "unit", "evidence_ids", "calculation_id")}
            claim.update(status="supported", explanation="Retained tool evidence.")
        else:
            claim = {"query": copy.deepcopy(call["arguments"]), "value": None,
                     "unit": "", "evidence_ids": [], "calculation_id": "",
                     "status": "unsupported", "explanation": returned["error"]}
        claim["claim_id"] = "c" + str(len(claims)+1)
        claims.append(claim)
    return {"task_id": tid, "answer": "Supported parts and explicit evidence limits.",
            "claims": claims, "narrative_claims": [], "refusal_reason": None, "faults": []}


class AdaptiveModel:
    """A deterministic test double; it performs no model inference."""
    def __init__(self, handler):
        self.handler = handler
        self.prompts, self.names = [], []

    def __call__(self, prompt, schema, output_dir, model, timeout=120):
        name = Path(output_dir).name
        self.prompts.append(prompt)
        self.names.append(name)
        value = self.handler(name, context_from_prompt(prompt))
        if isinstance(value, Exception):
            raise value
        meta = {"inference_performed": True, "provider": "test-double",
                "usage": {"input_tokens": 11, "output_tokens": 5}, "return_code": 0}
        write_json(Path(output_dir) / "metadata.json", meta)
        write_json(Path(output_dir) / "response.json", value)
        return copy.deepcopy(value), meta


class MultiStepAgentTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(ROOT / "data/raw/pilot_archive.json")
        self.task = task_fixture()
        self.tid = self.task["task_id"]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def run_model(self, handler, condition="ordinary_agent", *, budgets=None, tasks=None, name="run"):
        model = AdaptiveModel(handler)
        directory = self.path / name
        with patch("curve_audit.investigation.complete", model):
            rows = run_investigation_batch(self.dataset, tasks or [self.task], condition, directory,
                                          budgets=budgets or BUDGETS)
        return rows, model, directory

    def basic_handler(self, name, context):
        if name == "round_1_plan":
            return plans(self.tid, [request(query())])
        if name in ("round_2_plan", "round_3_plan", "round_5_review_plan"):
            return plans(self.tid)
        return {"reports": [report_from_history(self.tid, context)]}

    def test_public_tasks_and_all_rounds_exclude_private_recipes_and_answers(self):
        rows, model, path = self.run_model(self.basic_handler)
        self.assertEqual("completed", rows[0]["status"])
        self.assertEqual({"task_id", "question", "window"}, set(read_json(path / "public_input.json")[0]))
        for prompt in model.prompts:
            for secret in ("PRIVATE_RECIPE_SENTINEL", "PRIVATE_ANSWER_SENTINEL",
                           "PRIVATE_SECRET_SENTINEL", '"_private"', '"recipes"', '"expected_claims"'):
                self.assertNotIn(secret, prompt)

    def test_peak_result_drives_second_stage_query_and_same_task_dependency(self):
        seen_peak = []

        def handler(name, context):
            history = context["tool_history"][self.tid]
            if name == "round_1_plan":
                self.assertEqual([], history)
                return plans(self.tid, [request(query())])
            if name == "round_2_plan":
                self.assertEqual(1, len(history))
                returned = history[0]["result"]["result"]
                peak_target = returned["target_timestamp_utc"]
                seen_peak.append(peak_target)
                self.assertNotEqual(peak_target, returned["observed_at"])
                return plans(self.tid, [request(query("share_pct", "last", peak_target, peak_target),
                                                [history[0]["step_id"]])])
            if name == "round_3_plan":
                return plans(self.tid)
            return {"reports": [report_from_history(self.tid, context)]}

        rows, model, _ = self.run_model(handler)
        row = rows[0]
        self.assertEqual("completed", row["status"])
        self.assertEqual(2, len(row["calculations"]))
        parent, child = row["tool_calls"]
        self.assertEqual([parent["step_id"]], child["depends_on"])
        self.assertEqual(parent["task_id"], child["task_id"])
        self.assertEqual(seen_peak[0], child["arguments"]["start"])
        self.assertTrue(verify_claims(self.dataset, row["report"])["pass"])
        self.assertEqual(4, len(model.prompts))

    def test_partial_missing_evidence_keeps_independently_supported_part(self):
        self.task["_private"]["fixture"] = {"kind": "remove_snapshot",
                                           "target": "2023-03-11T12:00:00Z"}
        early = query("quote_rate", "last", START, "2023-03-10T19:00:00Z")
        affected = query("share_pct", "change", "2023-03-11T11:00:00Z", "2023-03-11T13:00:00Z")

        def handler(name, context):
            if name == "round_1_plan":
                return plans(self.tid, [request(early), request(affected)])
            if name in ("round_2_plan", "round_3_plan"):
                return plans(self.tid)
            return {"reports": [report_from_history(self.tid, context)]}

        rows, _, _ = self.run_model(handler)
        row = rows[0]
        self.assertEqual("completed", row["status"])
        self.assertEqual([True, False], [c["result"]["ok"] for c in row["tool_calls"]])
        self.assertEqual(["supported", "unsupported"], [c["status"] for c in row["report"]["claims"]])
        self.assertIsNotNone(row["report"]["claims"][0]["value"])
        self.assertIsNone(row["report"]["claims"][1]["value"])
        self.assertEqual(1, len(row["calculations"]))

    def test_matched_review_conditions_use_six_rounds_with_distinct_feedback(self):
        for condition in ("ordinary_agent", "links_only_agent", "self_review_agent", "evidence_grounded_agent"):
            with self.subTest(condition=condition):
                rows, model, path = self.run_model(self.basic_handler, condition, name=condition)
                row = rows[0]
                self.assertEqual("completed", row["status"])
                expected_rounds = 6 if condition in ("self_review_agent", "evidence_grounded_agent") else 4
                self.assertEqual(expected_rounds, len(model.prompts))
                self.assertEqual(expected_rounds, row["metadata"]["model_rounds"])
                has_verifier = condition == "evidence_grounded_agent"
                self.assertEqual(has_verifier, any(c["tool"] == "independent_verifier" for c in row["tool_calls"]))
                if expected_rounds == 6:
                    for prompt in model.prompts[4:]:
                        ctx = context_from_prompt(prompt)
                        self.assertEqual(has_verifier, "independent_verifier_feedback" in ctx)
                        self.assertTrue(all(c["tool"] == "calculate" for c in ctx["tool_history"][self.tid]))
                self.assertEqual({"input_tokens": 11*expected_rounds, "output_tokens": 5*expected_rounds},
                                 read_json(path / "batch_metadata.json")["actual_usage"])

    def test_grounded_feedback_can_trigger_a_real_requery_before_final_report(self):
        def handler(name, context):
            if name == "round_4_report":
                report = report_from_history(self.tid, context)
                report["claims"][0]["value"] = "999"
                return {"reports": [report]}
            if name == "round_5_review_plan":
                feedback = context["independent_verifier_feedback"][self.tid]
                self.assertFalse(feedback["pass"])
                parent = context["tool_history"][self.tid][0]
                timestamp = parent["result"]["result"]["target_timestamp_utc"]
                return plans(self.tid, [request(query("quote_rate", "last", timestamp, timestamp),
                                                [parent["step_id"]])])
            return self.basic_handler(name, context)

        rows, model, _ = self.run_model(handler, "evidence_grounded_agent")
        row = rows[0]
        self.assertEqual("completed", row["status"])
        self.assertEqual(["calculate", "independent_verifier", "calculate"],
                         [c["tool"] for c in row["tool_calls"]])
        self.assertEqual("999", row["initial_report"]["claims"][0]["value"])
        self.assertTrue(verify_claims(self.dataset, row["report"])["pass"])
        self.assertEqual(2, len(row["calculations"]))
        self.assertEqual(6, len(model.prompts))

    def test_cross_task_dependency_fails_without_executing_the_invalid_call(self):
        def handler(name, context):
            if name == "round_1_plan":
                return plans(self.tid, [request(query(), ["OTHER_TASK-S01"])])
            return self.basic_handler(name, context)
        rows, model, path = self.run_model(handler)
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual([], rows[0]["tool_calls"])
        self.assertEqual(1, len(model.prompts))
        self.assertTrue((path / "failure.json").exists())

    def test_model_round_ceiling_preserves_executed_tools_and_initial_report(self):
        rows, model, path = self.run_model(self.basic_handler, "self_review_agent",
                                          budgets={**BUDGETS, "max_model_rounds": 4})
        row = rows[0]
        self.assertEqual("failed", row["status"])
        self.assertEqual(4, len(model.prompts))
        self.assertEqual(1, len(row["tool_calls"]))
        self.assertIsNotNone(row["initial_report"])
        self.assertEqual([], row["report"]["claims"])
        self.assertIn("ceiling", read_json(path / "failure.json")["error"])

    def test_verifier_counts_against_the_same_per_task_tool_ceiling(self):
        rows, _, _ = self.run_model(self.basic_handler, "evidence_grounded_agent",
                                    budgets={**BUDGETS, "max_tool_calls": 1})
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual(1, len(rows[0]["tool_calls"]))
        self.assertEqual("calculate", rows[0]["tool_calls"][0]["tool"])

    def test_planning_stage_request_limit_is_checked_at_application_boundary(self):
        def handler(name, context):
            if name == "round_1_plan":
                return plans(self.tid, [request(query()) for _ in range(4)])
            return self.basic_handler(name, context)
        rows, _, _ = self.run_model(handler)
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual([], rows[0]["tool_calls"])

    def test_provider_failure_retains_earlier_results_and_never_returns_success(self):
        def handler(name, context):
            if name == "round_2_plan":
                return ProviderUnavailable("simulated provider failure")
            return self.basic_handler(name, context)
        rows, model, path = self.run_model(handler)
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual(1, len(rows[0]["tool_calls"]))
        self.assertEqual(1, len(rows[0]["calculations"]))
        self.assertTrue(rows[0]["metadata"]["inference_performed"])
        self.assertEqual(2, len(model.prompts))
        self.assertEqual({"input_tokens": 11, "output_tokens": 5},
                         read_json(path / "batch_metadata.json")["actual_usage"])

    def test_failed_call_recorded_usage_is_retained_once(self):
        directory = self.path / "failed_usage"
        ordinary_model = AdaptiveModel(self.basic_handler)

        def provider(prompt, schema, output_dir, model, timeout):
            if Path(output_dir).name == "round_2_plan":
                write_json(Path(output_dir) / "metadata.json",
                           {"inference_performed": True, "usage": {"input_tokens": 22, "output_tokens": 7}})
                raise ProviderUnavailable("failed after recorded inference")
            return ordinary_model(prompt, schema, output_dir, model, timeout)

        with patch("curve_audit.investigation.complete", provider):
            rows = run_investigation_batch(self.dataset, [self.task], "ordinary_agent",
                                          directory, budgets=BUDGETS)
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual(2, rows[0]["metadata"]["model_rounds"])
        self.assertTrue(rows[0]["metadata"]["inference_performed"])
        metadata = read_json(directory / "batch_metadata.json")
        self.assertEqual({"input_tokens": 33, "output_tokens": 12}, metadata["actual_usage"])
        self.assertEqual(2, len(metadata["model_calls"]))

    def test_timeout_preserves_unknown_inference_status(self):
        directory = self.path / "timeout_unknown"

        def provider(prompt, schema, output_dir, model, timeout):
            write_json(Path(output_dir) / "metadata.json",
                       {"inference_attempted": True, "inference_performed": None,
                        "status": "timeout", "usage": {}})
            raise ProviderUnavailable("timeout with inference status unknown")

        with patch("curve_audit.investigation.complete", provider):
            rows = run_investigation_batch(self.dataset, [self.task], "ordinary_agent",
                                          directory, budgets=BUDGETS)
        self.assertEqual("failed", rows[0]["status"])
        self.assertIsNone(rows[0]["metadata"]["inference_performed"])
        self.assertEqual([], rows[0]["tool_calls"])
        self.assertEqual({"input_tokens": 0, "output_tokens": 0},
                         read_json(directory / "batch_metadata.json")["actual_usage"])

    def test_same_model_response_cannot_depend_on_a_result_not_yet_received(self):
        def handler(name, context):
            if name == "round_1_plan":
                return plans(self.tid, [request(query()),
                                       request(query("share_pct", "last"), [self.tid+"-S01"])])
            return self.basic_handler(name, context)
        rows, _, _ = self.run_model(handler)
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual(1, len(rows[0]["tool_calls"]))
        self.assertEqual([], rows[0]["tool_calls"][0]["depends_on"])

    def test_protocol_rejects_a_different_planning_stage_count_before_inference(self):
        with self.assertRaises(ValueError):
            self.run_model(self.basic_handler, budgets={**BUDGETS, "planning_rounds": 2})

    def test_missing_final_task_does_not_reuse_initial_as_success(self):
        def handler(name, context):
            if name == "round_6_final_report":
                return {"reports": []}
            return self.basic_handler(name, context)
        rows, _, _ = self.run_model(handler, "self_review_agent")
        self.assertEqual("failed", rows[0]["status"])
        self.assertIsNotNone(rows[0]["initial_report"])
        self.assertEqual([], rows[0]["report"]["claims"])
        self.assertEqual(6, rows[0]["metadata"]["model_rounds"])

    def test_prompt_and_report_character_limits_are_enforced(self):
        rows, model, _ = self.run_model(self.basic_handler,
                                        budgets={**BUDGETS, "max_prompt_characters_per_task": 1},
                                        name="prompt_limit")
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual([], model.prompts)
        rows, _, _ = self.run_model(self.basic_handler,
                                    budgets={**BUDGETS, "max_report_characters_per_task": 1},
                                    name="report_limit")
        self.assertEqual("failed", rows[0]["status"])
        self.assertEqual(1, len(rows[0]["calculations"]))

    def test_existing_run_cannot_be_overwritten_as_new_inference(self):
        self.run_model(self.basic_handler)
        with self.assertRaises(ValueError):
            self.run_model(self.basic_handler)


if __name__ == "__main__":
    unittest.main()
