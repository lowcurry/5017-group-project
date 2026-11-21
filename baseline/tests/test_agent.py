"""Model-free tests of tool execution, privacy boundaries and budget accounting."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from curve_audit.agent import normalized_query, parse_question, run_batch, run_task
from curve_audit.common import read_json, write_json
from curve_audit.data import load_dataset
from curve_audit.metrics import calculate
from curve_audit.provider import ProviderUnavailable, complete
from curve_audit.scoring import score_outputs

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = {
    "max_model_rounds": 4, "max_tool_calls": 4, "max_prompt_characters": 24000,
    "max_response_characters": 12000, "timeout_seconds_per_round": 120,
    "max_repair_rounds": 1,
}


def task_fixture():
    return {"task_id": "T_MODEL", "question": "计算 USDC 在指定采样时点的名义数量占比。",
            "query": {"metric": "share_pct", "operation": "first", "coin": "USDC",
                      "start": "2023-03-10T18:00:00Z", "end": "2023-03-10T18:00:00Z"},
            "split": "test", "family": "composition", "group": "historical",
            "_private": {"expected": {"value": "PRIVATE_ANSWER_SENTINEL"},
                         "fixture": {}, "secret": "PRIVATE_FIXTURE_SENTINEL"}}


def supported_report(dataset, task, *, value=None):
    result = calculate(dataset, task["query"])
    return {"task_id": task["task_id"], "answer": "按指定区块的名义数量计算。",
            "claims": [{"claim_id": "C1", "query": normalized_query(task["query"]),
                        "value": result["value"] if value is None else value, "unit": result["unit"],
                        "status": "supported", "evidence_ids": result["evidence_ids"],
                        "calculation_id": result["calculation_id"], "explanation": "确定性工具的结果。"}],
            "refusal_reason": None, "faults": []}


class MockModel:
    """A provider test double: does no inference and only returns supplied JSON."""
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []

    def __call__(self, prompt, schema, output_dir, model, timeout=120):
        self.prompts.append(prompt)
        if not self.payloads:
            raise AssertionError("Unexpected extra model round")
        value = self.payloads.pop(0)
        if isinstance(value, Exception):
            raise value
        metadata = {"inference_performed": True, "provider": "mock-for-tests",
                    "usage": {"input_tokens": 13, "output_tokens": 7}, "runtime_seconds": 0,
                    "native_tools_used": [], "return_code": 0}
        write_json(Path(output_dir) / "metadata.json", metadata)
        write_json(Path(output_dir) / "response.json", value)
        return copy.deepcopy(value), metadata


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(ROOT / "data/raw/pilot_archive.json")
        self.task = task_fixture()
        self.plan = {"plans": [{"task_id": self.task["task_id"], "query": normalized_query(self.task["query"])}]}

    def test_model_plan_executes_real_calculation_and_keeps_private_labels_out(self):
        report = supported_report(self.dataset, self.task)
        model = MockModel([self.plan, {"reports": [report]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            rows = run_batch(self.dataset, [self.task], "ordinary_agent", Path(tmp), budgets=BUDGETS)
            self.assertEqual(rows[0]["status"], "completed")
            self.assertEqual(rows[0]["tool_calls"][0]["tool"], "calculate")
            tool = rows[0]["tool_calls"][0]["result"]
            self.assertTrue(tool["ok"])
            self.assertEqual(tool["result"]["value"], calculate(self.dataset, self.task["query"])["value"])
            self.assertEqual(rows[0]["calculations"], [tool["result"]])
            metadata = read_json(Path(tmp) / "batch_metadata.json")
            self.assertEqual(metadata["actual_usage"], {"input_tokens": 26, "output_tokens": 14})
            self.assertEqual(rows[0]["metadata"]["model_rounds"], 2)
            self.assertEqual(rows[0]["metadata"]["repair_rounds"], 0)
            public = read_json(Path(tmp) / "public_input.json")[0]
            self.assertNotIn("_private", public)
        for prompt in model.prompts:
            self.assertNotIn("PRIVATE_ANSWER_SENTINEL", prompt)
            self.assertNotIn("PRIVATE_FIXTURE_SENTINEL", prompt)
            self.assertNotIn('"expected"', prompt)
            self.assertNotIn('"_synthetic_fixture"', prompt)

    def test_grounded_run_preserves_initial_error_and_budgeted_repair_feedback(self):
        wrong = supported_report(self.dataset, self.task, value="100")
        corrected = supported_report(self.dataset, self.task)
        model = MockModel([self.plan, {"reports": [wrong]}, {"reports": [corrected]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "evidence_grounded_agent", Path(tmp), budgets=BUDGETS)[0]
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["initial_report"]["claims"][0]["value"], "100")
            self.assertEqual(row["report"], corrected)
            self.assertEqual(row["metadata"]["model_rounds"], 3)
            self.assertEqual(row["metadata"]["repair_rounds"], 1)
            self.assertEqual(row["tool_calls"][1]["tool"], "independent_verifier")
            self.assertFalse(row["tool_calls"][1]["result"]["pass"])
        self.assertIn("verifier_feedback", model.prompts[2])
        self.assertIn("value differs", model.prompts[2])

    def test_zero_repair_budget_keeps_verifier_feedback_without_extra_model_round(self):
        report = supported_report(self.dataset, self.task)
        model = MockModel([self.plan, {"reports": [report]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "evidence_grounded_agent", Path(tmp),
                            budgets={**BUDGETS, "max_repair_rounds": 0})[0]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["metadata"]["model_rounds"], 2)
        self.assertEqual(row["metadata"]["repair_rounds"], 0)
        self.assertEqual(row["report"], row["initial_report"])
        self.assertEqual(row["tool_calls"][1]["tool"], "independent_verifier")
        self.assertTrue(row["tool_calls"][1]["result"]["pass"])

    def test_verifier_counts_against_same_tool_ceiling(self):
        report = supported_report(self.dataset, self.task)
        model = MockModel([self.plan, {"reports": [report]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "evidence_grounded_agent", Path(tmp),
                            budgets={**BUDGETS, "max_tool_calls": 1})[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(len(row["tool_calls"]), 1)
        self.assertEqual(row["tool_calls"][0]["tool"], "calculate")

    def test_missing_data_becomes_failed_tool_result_and_explicit_model_refusal(self):
        task = copy.deepcopy(self.task)
        task["_private"]["fixture"] = {"kind": "remove_balance", "target": task["query"]["start"], "coin": "USDC"}
        refusal = {"task_id": task["task_id"], "answer": "资料不完整，无法给出占比。", "refusal_reason": "余额缺失。", "faults": [],
                   "claims": [{"claim_id": "C1", "query": normalized_query(task["query"]), "value": None,
                               "unit": "percent", "status": "unsupported", "evidence_ids": [],
                               "calculation_id": "", "explanation": "余额缺失。"}]}
        model = MockModel([self.plan, {"reports": [refusal]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [task], "ordinary_agent", Path(tmp), budgets=BUDGETS)[0]
        self.assertEqual(row["status"], "completed")
        self.assertFalse(row["tool_calls"][0]["result"]["ok"])
        self.assertEqual(row["calculations"], [])
        self.assertIsNone(row["report"]["claims"][0]["value"])
        self.assertNotIn("remove_balance", model.prompts[1])
        self.assertNotIn("PRIVATE_FIXTURE_SENTINEL", model.prompts[1])

    def test_duplicate_or_missing_task_reports_are_not_success(self):
        model = MockModel([self.plan, {"reports": []}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "ordinary_agent", Path(tmp), budgets=BUDGETS)[0]
            self.assertEqual(row["status"], "failed")
            self.assertTrue(row["metadata"]["inference_performed"])
            self.assertTrue((Path(tmp) / "failure.json").exists())
            self.assertEqual(row["report"]["claims"], [])

    def test_prompt_limit_rejects_before_provider(self):
        model = MockModel([])
        budgets = {**BUDGETS, "max_prompt_characters": 1}
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "ordinary_agent", Path(tmp), budgets=budgets)[0]
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["metadata"]["inference_performed"])
        self.assertEqual(model.prompts, [])

    def test_response_limit_failure_retains_actual_tool_calls(self):
        report = supported_report(self.dataset, self.task)
        report["answer"] = "X" * 300
        model = MockModel([self.plan, {"reports": [report]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "ordinary_agent", Path(tmp),
                            budgets={**BUDGETS, "max_response_characters": 100})[0]
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["tool_calls"][0]["result"]["ok"])

    def test_model_round_ceiling_is_enforced(self):
        model = MockModel([self.plan, {"reports": [supported_report(self.dataset, self.task)]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "ordinary_agent", Path(tmp),
                            budgets={**BUDGETS, "max_model_rounds": 1})[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(len(model.prompts), 1)

    def test_tool_ceiling_zero_prevents_calculation(self):
        model = MockModel([self.plan, {"reports": [supported_report(self.dataset, self.task)]}])
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", model):
            row = run_batch(self.dataset, [self.task], "ordinary_agent", Path(tmp),
                            budgets={**BUDGETS, "max_tool_calls": 0})[0]
        self.assertEqual(row["status"], "failed")
        self.assertFalse(row["tool_calls"])

    def test_failed_provider_after_inference_preserves_usage_and_inference_flag(self):
        def rejected_after_inference(prompt, schema, output_dir, model, timeout):
            write_json(Path(output_dir) / "metadata.json", {
                "inference_performed": True, "usage": {"input_tokens": 99, "output_tokens": 42},
                "native_tools_used": ["command_execution"], "return_code": 0})
            raise ProviderUnavailable("Model used native tools outside experiment tool budget; run rejected")
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.agent.complete", rejected_after_inference):
            row = run_batch(self.dataset, [self.task], "ordinary_agent", Path(tmp), budgets=BUDGETS)[0]
            self.assertEqual(row["status"], "failed")
            self.assertTrue(row["metadata"]["inference_performed"])
            failure = read_json(Path(tmp) / "failure.json")
            self.assertEqual(failure["usage"], {"input_tokens": 99, "output_tokens": 42})

    def test_intent_parser_rejects_reserves_and_preserves_direction_size(self):
        with self.assertRaises(ValueError):
            parse_question("USDC发行人储备是否充足？", self.task["query"]["start"], self.task["query"]["end"])
        parsed = parse_question("USDT→USDC兑换100,000的报价变化", self.task["query"]["start"], self.task["query"]["end"])
        self.assertEqual((parsed["metric"], parsed["operation"], parsed["direction"], parsed["size"]),
                         ("quote_rate", "change", "USDT_USDC", 100000))
        with self.assertRaises(ValueError):
            run_task(self.dataset, self.task, "ordinary_agent", "replay", Path("unused"))

    def test_equivalent_utc_spellings_keep_calculation_and_scoring_identity(self):
        task = copy.deepcopy(self.task)
        expected = calculate(self.dataset, task["query"])
        task["_private"]["expected"] = {"answerable": True, "required_status": "supported",
                                          "required_numeric_slots": 1, "result": expected}
        report = supported_report(self.dataset, task)
        claim = report["claims"][0]
        claim["query"]["start"] = "2023-03-10T18:00:00+00:00"
        claim["query"]["end"] = "2023-03-10T18:00:00.000000+00:00"
        self.assertEqual(calculate(self.dataset, claim["query"])["calculation_id"], expected["calculation_id"])
        output = {"task_id": task["task_id"], "condition": "ordinary_agent", "repetition": 1,
                  "status": "completed", "report": report, "metadata": {}}
        with tempfile.TemporaryDirectory() as tmp:
            scored = score_outputs([task], [output], Path(tmp), dataset=self.dataset)["per_output"][0]
        self.assertEqual(scored["correct_numeric_slots"], 1)
        self.assertEqual(scored["completed_task_slots"], 1)
        self.assertEqual(scored["traceable_structured_claims"], 1)
        claim["query"]["end"] = "2023-03-10T18:00:01Z"
        with tempfile.TemporaryDirectory() as tmp:
            changed = score_outputs([task], [output], Path(tmp), dataset=self.dataset)["per_output"][0]
        self.assertEqual(changed["correct_numeric_slots"], 0)


class ProviderIsolationTests(unittest.TestCase):
    def _fake_process(self, items, captured):
        def run(command, **kwargs):
            captured.update({"command": command, **kwargs})
            result = Path(command[command.index("--output-last-message") + 1])
            result.write_text('{"ready":true}', encoding="utf-8")
            events = [{"type": "item.completed", "item": item} for item in items]
            events.append({"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}})
            return types.SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(e) for e in events), stderr="")
        return run

    def test_adapter_preserves_transcript_usage_and_uses_isolated_readonly_execution(self):
        captured = {}
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.provider.codex_binary", return_value="/mock/codex"), \
                patch("curve_audit.provider.subprocess.run", self._fake_process([{"type": "agent_message", "text": "ready"}], captured)):
            response, metadata = complete("Return ready", {"type": "object"}, Path(tmp))
            self.assertEqual(response, {"ready": True})
            self.assertEqual(metadata["usage"], {"input_tokens": 4, "output_tokens": 2})
            self.assertEqual(metadata["native_tools_used"], [])
            self.assertTrue((Path(tmp) / "transcript.jsonl").exists())
            self.assertIn("--ephemeral", captured["command"])
            self.assertEqual(captured["command"][captured["command"].index("--sandbox") + 1], "read-only")
            self.assertNotEqual(Path(captured["cwd"]), ROOT)

    def test_adapter_rejects_native_actions_including_file_changes(self):
        for action in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp, \
                    patch("curve_audit.provider.codex_binary", return_value="/mock/codex"), \
                    patch("curve_audit.provider.subprocess.run", self._fake_process([{"type": action}], {})):
                with self.assertRaises(ProviderUnavailable):
                    complete("Return ready", {"type": "object"}, Path(tmp))

    def test_adapter_does_not_inherit_rpc_secret(self):
        captured = {}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ETH_RPC_URL": "https://host/SECRET_KEY"}), \
                patch("curve_audit.provider.codex_binary", return_value="/mock/codex"), \
                patch("curve_audit.provider.subprocess.run", self._fake_process([], captured)):
            complete("Return ready", {"type": "object"}, Path(tmp))
            # Omitting env inherits the parent environment and therefore fails.
            self.assertIn("env", captured)
            self.assertNotIn("ETH_RPC_URL", captured["env"])
            self.assertNotIn("SECRET_KEY", json.dumps(captured["env"]))

    def test_adapter_timeout_never_fabricates_response(self):
        with tempfile.TemporaryDirectory() as tmp, patch("curve_audit.provider.codex_binary", return_value="/mock/codex"), \
                patch("curve_audit.provider.subprocess.run", side_effect=subprocess.TimeoutExpired(["/mock/codex"], 1)):
            with self.assertRaises(ProviderUnavailable):
                complete("Return ready", {"type": "object"}, Path(tmp), timeout=1)
            self.assertTrue((Path(tmp) / "failure.txt").exists())
            self.assertFalse((Path(tmp) / "response.json").exists())

    def test_adapter_never_accepts_previous_response_as_fresh_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "response.json").write_text('{"ready":true}', encoding="utf-8")
            process = types.SimpleNamespace(returncode=0, stdout='{"type":"turn.failed","error":{"message":"no output"}}', stderr="")
            with patch("curve_audit.provider.codex_binary", return_value="/mock/codex"), \
                    patch("curve_audit.provider.subprocess.run", return_value=process):
                with self.assertRaises(ProviderUnavailable):
                    complete("Return ready", {"type": "object"}, Path(tmp))

    def test_nonobject_transcript_events_do_not_escape_failure_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            def process(command, **kwargs):
                result = Path(command[command.index("--output-last-message") + 1])
                result.write_text('{"ready":true}', encoding="utf-8")
                return types.SimpleNamespace(returncode=0, stdout='null\n[]\n{"type":"turn.completed","usage":{"input_tokens":1}}', stderr="")
            with patch("curve_audit.provider.codex_binary", return_value="/mock/codex"), \
                    patch("curve_audit.provider.subprocess.run", process):
                response, metadata = complete("Return ready", {"type": "object"}, Path(tmp))
            self.assertEqual(response, {"ready": True})
            self.assertEqual(metadata["usage"]["input_tokens"], 1)


if __name__ == "__main__":
    unittest.main()
