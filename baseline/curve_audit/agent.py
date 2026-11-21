"""A bounded model-selected metric tool loop, with retained verifier feedback."""
from __future__ import annotations

import copy
import time
from pathlib import Path

from .common import ROOT, canonical, read_json, write_json
from .provider import complete, ProviderUnavailable


def query_schema() -> dict:
    properties = {
        "metric": {"type": "string", "enum": ["share_pct", "quote_rate", "size_sensitivity_bps", "imbalance_l1"]},
        "operation": {"type": "string", "enum": ["first", "last", "change", "maximum"]},
        "start": {"type": "string"}, "end": {"type": "string"},
        "coin": {"type": "string", "enum": ["DAI", "USDC", "USDT"]},
        "direction": {"type": "string", "enum": ["USDC_USDT", "USDT_USDC"]},
        "size": {"type": "integer", "enum": [1000, 10000, 100000]},
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def report_schema() -> dict:
    claim = {"type": "object", "properties": {
        "claim_id": {"type": "string"}, "query": query_schema(),
        "value": {"type": ["string", "null"]}, "unit": {"type": "string"},
        "status": {"type": "string", "enum": ["supported", "unsupported", "conflicting"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "calculation_id": {"type": "string"}, "explanation": {"type": "string"},
    }, "additionalProperties": False}
    claim["required"] = list(claim["properties"])
    report = {"type": "object", "properties": {
        "task_id": {"type": "string"}, "answer": {"type": "string"},
        "claims": {"type": "array", "items": claim},
        "refusal_reason": {"type": ["string", "null"]},
        "faults": {"type": "array", "items": {"type": "string"}},
    }, "additionalProperties": False}
    report["required"] = list(report["properties"])
    return report


def normalized_query(query: dict) -> dict:
    return {"metric": query.get("metric", "share_pct"), "operation": query.get("operation", "last"),
            "start": query["start"], "end": query["end"], "coin": query.get("coin", "USDC"),
            "direction": query.get("direction", "USDC_USDT"),
            "size": int(query.get("size", 100000 if query.get("metric") == "size_sensitivity_bps" else 1000))}


def model_task(task: dict) -> dict:
    """The model derives tool arguments from the question, not an answer-key query."""
    value = {"task_id": task["task_id"], "question": task["question"],
             "window": {"start": task["query"]["start"], "end": task["query"]["end"]}}
    if "draft" in task:
        value["draft"] = copy.deepcopy(task["draft"])
    return value


def parse_question(question: str, start: str, end: str) -> dict:
    """Deliberately bounded intent parser; unknown requests do not get invented answers."""
    text = question.lower()
    if any(s in text for s in ["偿付", "破产", "储备", "insolven", "reserve", "预测", "profit", "盈利", "未来"]):
        raise ValueError("Question exceeds pool-state evidence: issuer reserves/future profit cannot be inferred")
    metric = None
    if any(s in text for s in ["规模", "sensitivity", "大额", "滑点"]):
        metric = "size_sensitivity_bps"
    elif any(s in text for s in ["报价", "quote", "汇率", "兑换", "exchange"]):
        metric = "quote_rate"
    elif any(s in text for s in ["失衡", "imbalance"]):
        metric = "imbalance_l1"
    elif any(s in text for s in ["占比", "share", "组成", "composition"]):
        metric = "share_pct"
    if metric is None:
        raise ValueError("Supported intents: composition/share, quotes, size sensitivity, imbalance")
    operation = "change" if any(s in text for s in ["变化", "change", "比较", "compare"]) else "last"
    if any(s in text for s in ["最高", "最大", "峰值", "maximum", "peak"]):
        operation = "maximum"
    elif any(s in text for s in ["最初", "起始", "首个", "第一个", "first"]):
        operation = "first"
    direction = "USDT_USDC" if any(s in text for s in ["usdt→usdc", "usdt->usdc", "usdt to usdc", "usdt兑换usdc", "usdt_usdc"]) else "USDC_USDT"
    coin = "DAI" if "dai" in text else ("USDT" if "usdt" in text and "usdc" not in text else "USDC")
    size = 100000 if metric == "size_sensitivity_bps" else 1000
    for amount in [100000, 10000, 1000]:
        if str(amount) in text.replace(",", ""):
            size = amount
            break
    return normalized_query({"metric": metric, "operation": operation, "start": start, "end": end,
                             "coin": coin, "direction": direction, "size": size})


def run_batch(dataset: dict, tasks: list[dict], condition: str, output_dir: Path,
              model: str = "gpt-6-luna", budgets: dict | None = None) -> list[dict]:
    from .experiment import apply_task_dataset, public_task
    from .metrics import calculate
    from .verification import verify_claims
    budgets = budgets or read_json(ROOT / "config/budgets.json")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    public = [model_task(t) for t in tasks]
    write_json(out / "public_input.json", public)
    records, calculations, tool_logs = {}, {}, {}
    totals = {"input_tokens": 0, "output_tokens": 0}
    inference_calls = []
    inference_attempted = False
    repair_rounds = 0
    native_boundary = "Do not use native tools, shell, files, browsing, or memory. Only use the supplied JSON. "
    try:
        task_ids = [task["task_id"] for task in tasks]
        plan_item = {"type": "object", "properties": {"task_id": {"type": "string", "enum": task_ids}, "query": query_schema()},
                     "required": ["task_id", "query"], "additionalProperties": False}
        plan_schema = {"type": "object", "properties": {"plans": {"type": "array", "items": plan_item, "minItems": len(tasks), "maxItems": len(tasks)}},
                       "required": ["plans"], "additionalProperties": False}
        prompt = native_boundary + "Return completed JSON only, with no preface. Select exactly one calculate tool request for EACH of these task IDs: " + ", ".join(task_ids) + ". The plans array must have " + str(len(tasks)) + " entries. Infer the intended metric, operation, coin, direction and size from each question, preserving the provided window. Tool metrics: share_pct=nominal token share in percent; quote_rate=output token units per input token; size_sensitivity_bps=rate decline relative to 1000 input; imbalance_l1=half L1 distance from equal shares. Operations first=first observation,last=last observation,change=last minus first,maximum=sampled maximum. Coin defaults USDC; direction defaults USDC_USDT; size defaults1000 except sensitivity100000. Supply all schema keys; irrelevant defaults do not affect computation. No answer values are available yet. Input tasks:\n" + canonical(public)

        def call_model(prompt_value: str, schema: dict, name: str):
            nonlocal inference_attempted
            # The same per-task application ceiling applies to all conditions.
            if len(inference_calls) >= budgets["max_model_rounds"]:
                raise ProviderUnavailable("Model round budget exceeded")
            if (out / name / "response.json").exists():
                raise ProviderUnavailable("Existing inference response preserved; choose a fresh run directory")
            if len(prompt_value) > budgets["max_prompt_characters"] * len(tasks):
                raise ProviderUnavailable("Prompt character budget exceeded")
            inference_attempted = True
            try:
                payload, meta = complete(prompt_value, schema, out / name, model,
                                         timeout=budgets["timeout_seconds_per_round"])
            except (ProviderUnavailable, OSError, ValueError):
                metadata_path = out / name / "metadata.json"
                if metadata_path.exists():
                    meta = read_json(metadata_path)
                    inference_calls.append(meta)
                    for key in totals:
                        totals[key] += int(meta.get("usage", {}).get(key, 0))
                raise
            inference_calls.append(meta)
            for key in totals:
                totals[key] += int(meta.get("usage", {}).get(key, 0))
            return payload

        plans = call_model(prompt, plan_schema, "round_1_plan")["plans"]
        if len(plans) != len(tasks) or {p["task_id"] for p in plans} != {t["task_id"] for t in tasks}:
            raise ProviderUnavailable("Plan must include every task exactly once")
        mapping = {t["task_id"]: t for t in tasks}
        for plan in plans:
            tid, query = plan["task_id"], plan["query"]
            if budgets["max_tool_calls"] < 1:
                raise ProviderUnavailable("Tool call budget exceeded before calculate")
            task_data = apply_task_dataset(dataset, mapping[tid])
            before = time.monotonic()
            try:
                result = calculate(task_data, query)
                calculations[tid] = [result]
                tool_result = {"ok": True, "result": result}
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                calculations[tid] = []
                tool_result = {"ok": False, "error": str(exc)}
            tool_logs[tid] = [{"tool": "calculate", "arguments": query, "result": tool_result,
                               "runtime_seconds": time.monotonic() - before}]
            records[tid] = tool_result
        protocol = "Answer all tasks in a reports array, exactly once each. Use only tool results. State explicit units and do not invent missing values. Decimal values must be strings. At most one material numerical claim per task. Unknown data: null value, unsupported, reason. If supplied draft disagrees with data, state conflicting with corrected value and nonempty faults. Do not assert continuous peaks, issuer solvency, dollar value, or actual fills. "
        if condition in {"evidence_grounded_agent", "links_only_agent", "self_review_agent"}:
            protocol += "Every material claim must bind exact evidence_ids and calculation_id from the tool, with the exact full query. Classify supported/unsupported/conflicting honestly. "
        report_envelope = {"type": "object", "properties": {"reports": {"type": "array", "items": report_schema(), "minItems": len(tasks), "maxItems": len(tasks)}},
                           "required": ["reports"], "additionalProperties": False}
        context = {"tasks": public, "tool_results": records}
        first = call_model(native_boundary + protocol + canonical(context), report_envelope, "round_2_report")["reports"]
        if len(first) != len(tasks) or {r["task_id"] for r in first} != set(mapping):
            raise ProviderUnavailable("Reports must include every task exactly once")
        final = copy.deepcopy(first)
        if condition == "evidence_grounded_agent":
            feedback = {}
            for report in first:
                tid = report["task_id"]
                if len(tool_logs[tid]) >= budgets["max_tool_calls"]:
                    raise ProviderUnavailable("Tool call budget exceeded before verifier")
                before = time.monotonic()
                feedback[tid] = verify_claims(apply_task_dataset(dataset, mapping[tid]), report)
                tool_logs[tid].append({"tool": "independent_verifier", "arguments": {"report": report},
                                       "result": feedback[tid], "runtime_seconds": time.monotonic() - before})
            # One budgeted repair round, retaining the initial report and all failures.
            if budgets["max_repair_rounds"] > 0:
                final = call_model(native_boundary + protocol + "Review independent verifier feedback. Repair at most once; keep evidence limits. Return all task reports, even those already passing.\n" + canonical({**context, "initial_reports": first, "verifier_feedback": feedback}), report_envelope, "round_3_repair")["reports"]
                repair_rounds = 1
            if len(final) != len(tasks) or {r["task_id"] for r in final} != set(mapping):
                raise ProviderUnavailable("Repaired reports must include every task exactly once")
        elif condition == "self_review_agent" and budgets["max_repair_rounds"] > 0:
            final = call_model(native_boundary + protocol + "Review your initial reports using the same supplied tool results. No independent verifier feedback is supplied in this control. Correct inconsistencies at most once and return all reports.\n" + canonical({**context, "initial_reports": first}), report_envelope, "round_3_self_review")["reports"]
            repair_rounds = 1
            if len(final) != len(tasks) or {r["task_id"] for r in final} != set(mapping):
                raise ProviderUnavailable("Self-reviewed reports must include every task exactly once")
        first_by = {r["task_id"]: r for r in first}
        rows = []
        for report in final:
            tid = report["task_id"]
            if len(canonical(report)) > budgets["max_response_characters"]:
                raise ProviderUnavailable("Per-task response budget exceeded")
            row = {"task_id": tid, "condition": condition, "status": "completed", "report": report,
                   "initial_report": first_by[tid], "tool_calls": tool_logs[tid], "calculations": calculations[tid],
                   "metadata": {"inference_performed": True, "provider": "codex-cli", "model": model,
                                "model_rounds": len(inference_calls), "tool_call_count": len(tool_logs[tid]),
                                "runtime_seconds": (time.monotonic() - started) / len(tasks),
                                "runtime_scope": "equal allocation of batch wall time, not isolated latency",
                                "usage": {}, "batch_usage_reference": "batch_metadata.json",
                                "repair_rounds": repair_rounds}}
            rows.append(row)
        write_json(out / "batch_metadata.json", {"condition": condition, "model": model, "task_count": len(tasks),
                   "actual_usage": totals, "model_calls": inference_calls,
                   "wall_seconds": time.monotonic() - started, "inference_performed": True})
        write_json(out / "outputs.json", rows)
        return rows
    except (ProviderUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
        rows = [{"task_id": t["task_id"], "condition": condition, "status": "failed",
                 "report": {"task_id": t["task_id"], "answer": "", "claims": [], "refusal_reason": None, "faults": []},
                 "metadata": {"inference_attempted": inference_attempted,
                              "inference_performed": any(m.get("inference_performed") for m in inference_calls), "error": str(exc),
                              "model": model, "runtime_seconds": (time.monotonic() - started) / len(tasks)},
                 "tool_calls": tool_logs.get(t["task_id"], []), "calculations": calculations.get(t["task_id"], [])}
                for t in tasks]
        write_json(out / "failure.json", {"error": str(exc), "completed_model_calls": inference_calls, "usage": totals})
        write_json(out / "batch_metadata.json", {"condition": condition, "model": model, "task_count": len(tasks),
                   "actual_usage": totals, "model_calls": inference_calls, "wall_seconds": time.monotonic() - started,
                   "inference_attempted": inference_attempted, "status": "failed"})
        write_json(out / "outputs.json", rows)
        return rows


def run_task(dataset: dict, task: dict, condition: str, provider: str, output_dir: Path, **kwargs) -> dict:
    if provider != "codex-cli":
        raise ValueError("Actual inference provider supported here: codex-cli")
    return run_batch(dataset, [task], condition, output_dir, **kwargs)[0]
