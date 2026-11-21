"""Result-driven, bounded multi-step investigations and a matched-round control.

Private recipes are never used to select a model's tools. Every model request,
successful or failed tool response, initial report and final report is retained.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

from .agent import query_schema, report_schema
from .common import ROOT, canonical, now_utc, read_json, write_json
from .metrics import calculate
from .provider import ProviderUnavailable, complete
from .verification import verify_claims

CONDITIONS = ("ordinary_agent", "links_only_agent", "self_review_agent", "evidence_grounded_agent")
NATIVE_BOUNDARY = "Do not use native tools, shell, files, browsing or memory. Use only the supplied JSON and return schema-compliant JSON without a preface. "
TOOL_GUIDE = """Available application tool: calculate(query). Metrics: share_pct (coin DAI/USDC/USDT, nominal percent), quote_rate (direction USDC_USDT or USDT_USDC, size 1000/10000/100000; output-token units per input token), size_sensitivity_bps (relative quote-rate decline from size1000, default size100000), imbalance_l1 (half L1 distance from equal nominal shares). Operations: first, last, change(last minus first), maximum(sampled maximum only). All seven query keys are required; irrelevant defaults coin=USDC,direction=USDC_USDT,size=1000 do not alter the calculation. Window endpoints must be exact UTC hourly targets. For a request at one exact target t, set start=end=t and operation=last. For one hour before/after a peak, subtract/add one hour to that returned target and use the resulting SINGLE target as BOTH endpoints. A last query over [t,t+1h] returns the later endpoint, not the value at t. A peak result includes target_timestamp_utc; use that target, rather than observed_at one second earlier, in subsequent queries. Never guess a peak timestamp before receiving a tool result. Do not infer issuer solvency, off-chain reserves, continuous peaks or causal mechanisms from pool states. Missing data produces an error; preserve that limitation but answer other supported parts. No expected answers or hidden fixture labels are provided."""


def investigation_report_schema():
    schema = copy.deepcopy(report_schema())
    narrative = {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["causal_attribution", "issuer_solvency", "continuous_peak", "draft_consistency"]},
        "stance": {"type": "string", "enum": ["supported", "unsupported", "contradicted"]},
        "explanation": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    }, "required": ["kind", "stance", "explanation", "evidence_ids"], "additionalProperties": False}
    schema["properties"]["narrative_claims"] = {"type": "array", "items": narrative}
    schema["required"].append("narrative_claims")
    return schema


def planning_schema(task_ids, limit):
    request = {"type": "object", "properties": {
        "query": query_schema(), "depends_on": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"}}, "required": ["query", "depends_on", "rationale"], "additionalProperties": False}
    item = {"type": "object", "properties": {
        "task_id": {"type": "string", "enum": task_ids},
        "requests": {"type": "array", "items": request, "minItems": 0, "maxItems": limit}},
        "required": ["task_id", "requests"], "additionalProperties": False}
    return {"type": "object", "properties": {"plans": {"type": "array", "items": item, "minItems": len(task_ids), "maxItems": len(task_ids)}}, "required": ["plans"], "additionalProperties": False}


def _compact_call(call):
    # Full raw results remain on disk; only redundant observation arrays are omitted
    # from the model context. Values and ALL required evidence IDs are preserved.
    value = copy.deepcopy(call)
    value.pop("runtime_seconds", None)
    result = value.get("result", {}).get("result")
    if isinstance(result, dict):
        result.pop("observations", None)
    return value


def run_investigation_batch(dataset, tasks, condition, output_dir, model="gpt-6-luna", budgets=None):
    from .investigation_tasks import public_task, apply_task_dataset
    if condition not in CONDITIONS:
        raise ValueError("Unknown investigation condition")
    if not tasks or len({t["task_id"] for t in tasks}) != len(tasks):
        raise ValueError("Investigation tasks must be nonempty with unique task IDs")
    budgets = budgets or read_json(ROOT / "config/investigation_budgets.json")
    if budgets["planning_rounds"] != 3:
        raise ValueError("This frozen protocol requires exactly three planning stages")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "outputs.json").exists() or any(out.glob("round_*/response.json")):
        raise ValueError("Existing inference preserved; choose a new run directory")
    started = time.monotonic()
    public = [public_task(t) for t in tasks]
    write_json(out / "public_input.json", public)
    task_ids = [t["task_id"] for t in tasks]
    mapping = {t["task_id"]: t for t in tasks}
    logs, calculations = {tid: [] for tid in task_ids}, {tid: [] for tid in task_ids}
    model_calls = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    initial = {}
    inference_attempted = False

    def invoke(prompt, schema, name):
        nonlocal inference_attempted
        if len(model_calls) >= budgets["max_model_rounds"]:
            raise ProviderUnavailable("Model round ceiling exceeded")
        if len(prompt) > budgets["max_prompt_characters_per_task"] * len(tasks):
            raise ProviderUnavailable("Prompt character ceiling exceeded")
        try:
            inference_attempted = True
            payload, metadata = complete(prompt, schema, out / name, model, budgets["timeout_seconds_per_round"])
        except (ProviderUnavailable, OSError, ValueError):
            path = out / name / "metadata.json"
            if path.exists():
                metadata = read_json(path)
                model_calls.append(metadata)
                for key in usage:
                    usage[key] += int(metadata.get("usage", {}).get(key, 0))
            raise
        model_calls.append(metadata)
        for key in usage:
            usage[key] += int(metadata.get("usage", {}).get(key, 0))
        return payload

    def context():
        return {"tasks": public, "tool_history": {tid: [_compact_call(c) for c in logs[tid] if c["tool"] == "calculate"] for tid in task_ids}}

    def execute(plans, request_limit, planning_round):
        if len(plans) != len(tasks) or {p["task_id"] for p in plans} != set(task_ids):
            raise ProviderUnavailable("Every planning stage must include each task exactly once")
        for plan in plans:
            tid = plan["task_id"]
            if not isinstance(plan["requests"], list) or len(plan["requests"]) > request_limit:
                raise ProviderUnavailable("Per-stage request ceiling exceeded")
            known = {c["step_id"] for c in logs[tid]}
            for request in plan["requests"]:
                if len(logs[tid]) >= budgets["max_tool_calls"]:
                    raise ProviderUnavailable("Tool ceiling exceeded")
                dependencies = request["depends_on"]
                if len(set(dependencies)) != len(dependencies) or not set(dependencies).issubset(known):
                    raise ProviderUnavailable("Invalid or cross-task step dependency")
                query = request["query"]
                before = time.monotonic()
                try:
                    calculation = calculate(apply_task_dataset(dataset, mapping[tid]), query)
                    result = {"ok": True, "result": calculation}
                    calculations[tid].append(calculation)
                except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                    result = {"ok": False, "error": str(exc)}
                logs[tid].append({"task_id": tid, "step_id": f"{tid}-S{len(logs[tid])+1:02d}", "depends_on": dependencies,
                                  "planning_round": planning_round,
                                  "tool": "calculate", "arguments": query, "result": result,
                                  "rationale": request["rationale"], "runtime_seconds": time.monotonic()-before})
        write_json(out / "tool_checkpoint.json", {"logs": logs, "calculations": calculations})

    def report_prompt(extra=""):
        protocol = "Write one report per task with all requested numerical parts, allowing multiple material claims. Copy full decimal values, normalized queries, explicit units and available calculation IDs. For a failed requested query, include value=null,status=unsupported, the attempted query, no invented calculation_id or references, and a specific explanation. Keep supported parts when another part is unavailable. A missing dedicated refusal_reason alone does not determine factual correctness. For a supplied draft, state actual conflicts in answer, claims and faults consistently. Address each requested non-numeric boundary using narrative_claims: kind causal_attribution/issuer_solvency/continuous_peak/draft_consistency, stance supported/unsupported/contradicted. Do not claim a continuous maximum from hourly observations. Distinguish an incorrect draft from a lack of evidence for causal or reserve assertions. Answer and explanations must agree with numeric claims. Keep prose concise and do not repeat long reference lists in answer. "
        if condition != "ordinary_agent":
            protocol += "Bind every factual claim to exact evidence_ids and calculation_id of its supporting tool result. Classify supported/unsupported/conflicting honestly. "
        return NATIVE_BOUNDARY + TOOL_GUIDE + protocol + extra

    envelope = {"type": "object", "properties": {"reports": {"type": "array", "items": investigation_report_schema(), "minItems": len(tasks), "maxItems": len(tasks)}}, "required": ["reports"], "additionalProperties": False}
    try:
        for stage in range(1, budgets["planning_rounds"]+1):
            prompt = (NATIVE_BOUNDARY + TOOL_GUIDE + f" Planning stage {stage}/3. Return exactly one plans item per task. Select zero to three useful calculate requests per task. Plan only what the question requires. In the first stage locate any requested sampled peak before using it. In later stages use actual preceding results to choose follow-up windows, directions and sizes, or investigate prior errors. depends_on must list relevant earlier step_ids from the SAME task; independent requests use []. Do not repeat already successful identical queries, do not manufacture calls merely to fill stages, and use [] when enough evidence is collected.\n" + canonical(context()))
            execute(invoke(prompt, planning_schema(task_ids, budgets["max_requests_per_planning_round"]), f"round_{stage}_plan")["plans"], budgets["max_requests_per_planning_round"], stage)
            write_json(out / f"tools_after_round_{stage}.json", logs)
        reports = invoke(report_prompt() + canonical(context()), envelope, "round_4_report")["reports"]
        if len(reports) != len(tasks) or {r["task_id"] for r in reports} != set(task_ids):
            raise ProviderUnavailable("Initial reports must include every task exactly once")
        initial = {r["task_id"]: r for r in reports}
        feedback = {}
        if condition == "evidence_grounded_agent":
            for tid, report in initial.items():
                if len(logs[tid]) >= budgets["max_tool_calls"]:
                    raise ProviderUnavailable("Tool ceiling exceeded before independent verifier")
                before = time.monotonic()
                feedback[tid] = verify_claims(apply_task_dataset(dataset, mapping[tid]), report)
                logs[tid].append({"task_id": tid, "step_id": f"{tid}-S{len(logs[tid])+1:02d}",
                                  "planning_round": 4,
                                  "depends_on": [c["step_id"] for c in logs[tid]], "tool": "independent_verifier",
                                  "arguments": {"report": report}, "result": feedback[tid],
                                  "runtime_seconds": time.monotonic()-before})
            write_json(out / "tool_checkpoint.json", {"logs": logs, "calculations": calculations})
        final = reports
        if condition in ("self_review_agent", "evidence_grounded_agent"):
            review_context = {**context(), "initial_reports": reports}
            if condition == "evidence_grounded_agent":
                review_context["independent_verifier_feedback"] = feedback
            prompt = NATIVE_BOUNDARY + TOOL_GUIDE + " Review-planning stage: inspect your initial reports and retained results. Select zero to two additional calculate requests per task only when a requery resolves an error or missing evidence; preserve limitations when data truly is missing. Use prior step IDs for dependencies. The matched self-review control receives no independent verifier feedback. Return all task IDs, including tasks requiring no requery.\n" + canonical(review_context)
            execute(invoke(prompt, planning_schema(task_ids, budgets["max_review_requests"]), "round_5_review_plan")["plans"], budgets["max_review_requests"], 5)
            final_context = {**context(), "initial_reports": reports}
            if condition == "evidence_grounded_agent":
                final_context["independent_verifier_feedback"] = feedback
            final = invoke(report_prompt("Review and revise each report once using the newly retained evidence. Return all tasks, preserving supported parts and explicit limitations. ") + canonical(final_context), envelope, "round_6_final_report")["reports"]
        if len(final) != len(tasks) or {r["task_id"] for r in final} != set(task_ids):
            raise ProviderUnavailable("Final reports must include every task exactly once")
        rows = []
        for report in final:
            tid = report["task_id"]
            if len(canonical(report)) > budgets["max_report_characters_per_task"]:
                raise ProviderUnavailable("Report character ceiling exceeded")
            rows.append({"task_id": tid, "condition": condition, "status": "completed", "report": report,
                         "initial_report": initial[tid], "calculations": calculations[tid], "tool_calls": logs[tid],
                         "metadata": {"inference_performed": True, "model": model, "provider": "codex-cli", "trajectory_schema": "2.0",
                                      "model_rounds": len(model_calls), "tool_call_count": len(logs[tid]),
                                      "runtime_seconds": (time.monotonic()-started)/len(tasks),
                                      "runtime_scope": "equal allocation of batch wall time, not isolated latency"}})
        status = "completed"
    except (ProviderUnavailable, OSError, ValueError, TypeError, KeyError) as exc:
        observed_inference = [m.get("inference_performed") for m in model_calls]
        inference_status = True if True in observed_inference else None if inference_attempted and (None in observed_inference or not model_calls) else False
        rows = [{"task_id": tid, "condition": condition, "status": "failed", "report": {"task_id": tid, "answer": "", "claims": [], "narrative_claims": [], "refusal_reason": None, "faults": []},
                 "initial_report": initial.get(tid), "calculations": calculations[tid], "tool_calls": logs[tid],
                 "metadata": {"inference_attempted": inference_attempted, "inference_performed": inference_status, "error": str(exc), "model_rounds": len(model_calls)}} for tid in task_ids]
        write_json(out / "failure.json", {"error": str(exc), "created_at": now_utc()})
        status = "failed"
    metadata = {"condition": condition, "status": status, "task_count": len(tasks), "actual_usage": usage,
                "model_calls": model_calls, "wall_seconds": time.monotonic()-started, "budgets": budgets,
                "usage_scope": "actual batch total, not a per-task estimate; unavailable failed-call usage is unknown"}
    write_json(out / "batch_metadata.json", metadata)
    write_json(out / "outputs.json", rows)
    return rows
