"""Frozen V2 experiment, resume-safe execution and offline rescoring."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import re
import time

from .common import ROOT, canonical, now_utc, read_json, sha256_bytes, sha256_file, write_json
from .investigation import CONDITIONS, run_investigation_batch
from .investigation_tasks import apply_task_dataset, freeze_investigation_tasks, planned_run_manifest, public_task
from .investigation_scoring import score_investigation_outputs
from .metrics import calculate


def fixed_reference(dataset, task):
    """Strong structured-query reference, with no inference or private answers.

    It receives query recipes, unlike model conditions. Draft handling uses
    explicit domain rules on the public draft. This is an engineering reference,
    not a fair natural-language planning comparator.
    """
    variant = apply_task_dataset(dataset, task)
    calculations, logs, claims, narratives, faults = [], [], [], [], []
    first_peak = None
    draft = task.get("draft", {}).get("text", "")
    draft_quote = re.search(r"报价是\s*([-+]?\d+(?:\.\d+)?)\s*([A-Za-z/_]+)", draft)
    numerical_draft = draft_quote is not None
    draft_conflicting = False
    for index, recipe in enumerate(task["_private"]["recipes"], 1):
        query = copy.deepcopy(recipe)
        dependencies = []
        if task["_private"]["dependencies"] and index > 1:
            # A reference controller derives follow-up timestamps from the result
            # it just executed, not by copying an evaluator expected result.
            dependency = next(d for d in task["_private"]["dependencies"] if d["slot_id"] == f"S{index}")
            offset = timedelta(hours=dependency["offset_hours"])
            target = (datetime.fromisoformat(first_peak.replace("Z", "+00:00")) + offset).isoformat().replace("+00:00", "Z")
            query.update(start=target, end=target)
            dependencies = [f"{task['task_id']}-S01"]
        try:
            result = calculate(variant, query)
            calculations.append(result)
            if index == 1 and task["_private"]["dependencies"]:
                first_peak = result["target_timestamp_utc"]
            if numerical_draft and index == 1:
                draft_conflicting = abs(Decimal(draft_quote.group(1))-Decimal(result["value"])) > Decimal("1e-8") or draft_quote.group(2) != result["unit"]
            status = "conflicting" if draft_conflicting and index == 1 else "supported"
            explanation = "确定性工具结果；草稿数值或单位须以该结果纠正。" if status == "conflicting" else "由结构化查询独立执行得到。"
            claim = {"claim_id": f"{task['task_id']}-C{index}", "query": query, "value": result["value"], "unit": result["unit"], "status": status,
                     "evidence_ids": result["evidence_ids"], "calculation_id": result["calculation_id"], "explanation": explanation}
            response = {"ok": True, "result": result}
            if status == "conflicting":
                faults.append("草稿的报价数值或 USD 单位与实际代币报价不一致。")
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            response = {"ok": False, "error": str(exc)}
            claim = {"claim_id": f"{task['task_id']}-C{index}", "query": query, "value": None, "unit": "unknown", "status": "unsupported",
                     "evidence_ids": [], "calculation_id": "", "explanation": str(exc)}
        claims.append(claim)
        logs.append({"task_id": task["task_id"], "step_id": f"{task['task_id']}-S{index:02d}", "depends_on": dependencies,
                     "planning_round": 1 if index == 1 or not dependencies else 2, "tool": "calculate", "arguments": query, "result": response})
    if draft:
        if numerical_draft:
            kind, stance = "draft_consistency", "contradicted" if draft_conflicting else "supported"
        elif "恐慌" in draft:
            kind, stance = "causal_attribution", "unsupported"
        elif "储备" in draft or "偿付" in draft:
            kind, stance = "issuer_solvency", "unsupported"
        else:
            kind, stance = "continuous_peak", "unsupported"
        narratives.append({"kind": kind, "stance": stance, "explanation": "按限定的草稿/证据边界规则判断；具体正文解释仍需独立审核。", "evidence_ids": []})
    answer = "\n".join(f"{c['query']['metric']} / {c['query']['operation']}: {c['value']} {c['unit']} ({c['status']})" for c in claims)
    report = {"task_id": task["task_id"], "answer": answer, "claims": claims, "narrative_claims": narratives,
              "refusal_reason": "部分查询缺少完整证据，已逐项保留限制。" if any(c["value"] is None for c in claims) else None, "faults": faults}
    return {"task_id": task["task_id"], "condition": "fixed_template", "repetition": 1, "status": "completed", "report": report, "initial_report": copy.deepcopy(report),
            "calculations": calculations, "tool_calls": logs, "metadata": {"inference_performed": False,
            "baseline_access": "Receives structured query recipes and explicit narrow draft rules; no evaluator expected values/statuses. Strong engineering reference, not an equivalent language-planning baseline."}}


def freeze_settings(dataset, destination, repetitions, model, provider):
    tasks = freeze_investigation_tasks(dataset, destination / "frozen")
    settings = {"schema_version": "2.0", "provider": provider, "model": model, "repetitions": repetitions,
                "conditions": list(CONDITIONS), "budgets": read_json(ROOT / "config/investigation_budgets.json"),
                "generation_code_sha256": {p.name: sha256_file(p) for p in sorted((ROOT / "curve_audit").glob("*.py"))},
                "dataset_sha256": sha256_bytes(canonical({k: v for k, v in dataset.items() if not k.startswith("_")}).encode()),
                "notice": "New complex task formulations on a previously observed event. Fixed template receives structured recipes. AI conditions share public tasks, actual data variants and ceilings. Matched-round self-review versus evidence-grounded is the principal feedback contrast. Actual tokens need not be equal. Human prose review remains separate."}
    path = destination / "run_settings.json"
    if path.exists() and read_json(path) != settings:
        raise ValueError("Frozen settings/code differ; choose a new version rather than overwrite")
    if not path.exists():
        write_json(path, settings)
    plan = planned_run_manifest(tasks, repetitions, CONDITIONS) + planned_run_manifest(tasks, 1, ("fixed_template",))
    if (destination / "planned_runs.json").exists() and read_json(destination / "planned_runs.json") != plan:
        raise ValueError("Planned runs differ from frozen plan")
    write_json(destination / "planned_runs.json", plan)
    return tasks, settings, plan


def run_experiment(dataset, destination, repetitions=3, model="gpt-6-luna", provider="offline"):
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise ValueError("Repetitions must be a positive integer")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    tasks, settings, plan = freeze_settings(dataset, destination, repetitions, model, provider)
    outputs = [fixed_reference(dataset, task) for task in tasks]
    write_json(destination / "outputs.json", outputs)
    if provider == "codex-cli":
        for repetition in range(1, repetitions+1):
            # Rotate condition order across repetitions while retaining the same
            # 30-task batch layout, avoiding all repeats of one method first.
            order = list(CONDITIONS[repetition-1:]) + list(CONDITIONS[:repetition-1])
            for condition in order:
                run_dir = destination / "runs" / f"{condition}_r{repetition}"
                if (run_dir / "outputs.json").exists():
                    rows = read_json(run_dir / "outputs.json")
                    if (read_json(run_dir / "public_input.json") != [public_task(t) for t in tasks] or len(rows) != len(tasks)
                            or len({r["task_id"] for r in rows}) != len(tasks)
                            or {r["task_id"] for r in rows} != {t["task_id"] for t in tasks}
                            or any(r.get("condition") != condition for r in rows)):
                        raise ValueError("Retained run does not match frozen input")
                    # A failed completed attempt remains failed, never silently
                    # replaced by a successful retry.
                elif any(run_dir.glob("round_*")):
                    if read_json(run_dir / "public_input.json") != [public_task(t) for t in tasks]:
                        raise ValueError("Interrupted run input differs from frozen tasks")
                    # Recover an interrupted attempt as failures. Retain all
                    # observed usage and never silently retry its model calls.
                    calls = [read_json(p) for p in sorted(run_dir.glob("round_*/metadata.json"))]
                    checkpoint = read_json(run_dir / "tool_checkpoint.json") if (run_dir / "tool_checkpoint.json").exists() else {}
                    logs, calculations = checkpoint.get("logs", {}), checkpoint.get("calculations", {})
                    unknown_usage = any(p.is_dir() and not (p / "metadata.json").exists() for p in run_dir.glob("round_*"))
                    observed = [c.get("inference_performed") for c in calls]
                    performed = True if True in observed else None
                    rows = [{"task_id": t["task_id"], "condition": condition, "status": "failed", "report": {},
                             "tool_calls": logs.get(t["task_id"], []), "calculations": calculations.get(t["task_id"], []),
                             "metadata": {"inference_attempted": True, "inference_performed": performed,
                                          "error": "Interrupted retained attempt; no retry or fabricated completion", "usage_incomplete": unknown_usage}} for t in tasks]
                    write_json(run_dir / "outputs.json", rows)
                    write_json(run_dir / "batch_metadata.json", {"condition": condition, "status": "failed", "task_count": len(tasks), "model_calls": calls,
                               "actual_usage": {key: sum(c.get("usage", {}).get(key, 0) for c in calls) for key in ("input_tokens", "output_tokens")},
                               "wall_seconds": sum(c.get("runtime_seconds", 0) for c in calls), "runtime_scope": "Observed model-call time only for interrupted run; full batch wall time unavailable", "usage_incomplete": unknown_usage})
                    write_json(run_dir / "resume_recovery.json", {"created_at": now_utc(), "status": "retained_as_failure", "model_retried": False})
                else:
                    print(canonical({"starting": condition, "repetition": repetition, "tasks": len(tasks)}), flush=True)
                    rows = run_investigation_batch(dataset, tasks, condition, run_dir, model, settings["budgets"])
                for row in rows:
                    row["repetition"] = repetition
                    row["output_id"] = f"{row['task_id']}__{condition}__r{repetition}"
                outputs.extend(rows)
                write_json(destination / "outputs.json", outputs)
                print(canonical({"finished": condition, "repetition": repetition, "completed": sum(r["status"] == "completed" for r in rows), "failed": sum(r["status"] != "completed" for r in rows)}), flush=True)
    scoring = score_investigation_outputs(tasks, outputs, destination / "evaluation", dataset=dataset, planned_runs=plan)
    batches = [{"run": p.parent.name, **read_json(p)} for p in sorted((destination / "runs").glob("*/batch_metadata.json"))]
    costs = {}
    for condition in CONDITIONS:
        selected = [b for b in batches if b["condition"] == condition]
        costs[condition] = {"batches": len(selected), "model_calls": sum(len(b["model_calls"]) for b in selected),
                            "input_tokens": sum(b["actual_usage"].get("input_tokens", 0) for b in selected),
                            "output_tokens": sum(b["actual_usage"].get("output_tokens", 0) for b in selected),
                            "batch_wall_seconds": sum(b["wall_seconds"] for b in selected)}
    status = {"created_at": now_utc(), "provider": provider, "model": model, "planned_outputs": len(plan), "recorded_outputs": len(outputs),
              "failed_outputs": sum(r["status"] != "completed" for r in outputs), "missing_output_ids": scoring["missing_output_ids"],
              "all_planned_completed": provider == "codex-cli" and len(outputs) == len(plan) and not any(r["status"] != "completed" for r in outputs),
              "costs_by_condition": costs, "human_review": "PENDING two independent human reviewers; automated rules are not human scoring", "batches": batches}
    write_json(destination / "status.json", status)
    return status


def replay(dataset, destination, output_dir):
    destination = Path(destination)
    manifest = read_json(destination / "frozen/freeze_manifest.json")
    digest = sha256_bytes(canonical({k: v for k, v in dataset.items() if not k.startswith("_")}).encode())
    if manifest["dataset_sha256"] != digest:
        raise ValueError("Replay archive differs from frozen archive")
    tasks = read_json(destination / "frozen/tasks.private.json")["tasks"]
    return score_investigation_outputs(tasks, read_json(destination / "outputs.json"), output_dir, dataset, read_json(destination / "planned_runs.json"))
