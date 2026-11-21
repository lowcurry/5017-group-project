"""Reproducible structured scoring, with separate blind prose review.

No automated score here certifies unrestricted narrative text. Missing references
reduce traceability, not factual support. Omitted numeric answers remain in the
required-slot denominator, and repeat runs are aggregated within tasks first.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import mean

from . import metrics
from .experiment import apply_task_dataset

ABSOLUTE_TOLERANCE = Decimal("0.00000001")


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _number_equal(left, right):
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and abs(a - b) <= ABSOLUTE_TOLERANCE
    except (InvalidOperation, TypeError, ValueError):
        return False


def _query_equal(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    try:
        return metrics.normalize_query(left) == metrics.normalize_query(right)
    except (ValueError, TypeError, KeyError):
        return False


def _claims(report):
    claims = report.get("claims")
    return [c for c in claims if isinstance(c, dict)] if isinstance(claims, list) else []


def _flagged(report):
    return bool(report.get("faults") or report.get("refusal_reason") or any(
        c.get("status") in ("unsupported", "conflicting") for c in _claims(report)))


def _conflict_flagged(report, query):
    """Check a complete structured conflict flag, without judging prose meaning."""
    faults = report.get("faults")
    details_present = isinstance(faults, list) and any(
        (isinstance(item, str) and bool(item.strip())) or
        (isinstance(item, dict) and bool(item)) for item in faults)
    return bool(details_present and any(
        claim.get("status") == "conflicting" and
        _query_equal(claim.get("query"), query) and
        isinstance(claim.get("explanation"), str) and bool(claim["explanation"].strip())
        for claim in _claims(report)))


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _result_for_claim(task, claim, task_dataset):
    frozen = task.get("_private", {}).get("expected", {})
    if _query_equal(claim.get("query"), task.get("query")):
        return frozen.get("result")
    if task_dataset is None:
        return None
    try:
        return metrics.calculate(task_dataset, claim.get("query", {}))
    except (ValueError, KeyError, TypeError):
        return None


def _output_score(task, output, task_dataset=None):
    expected = task.get("_private", {}).get("expected")
    if not expected:
        raise ValueError("Scoring requires evaluator-private frozen answer keys")
    report = output.get("report") or {}
    if not isinstance(report, dict):
        report = {}
    claims = report.get("claims", [])
    if not isinstance(claims, list):
        claims = []
    claims = [c for c in claims if isinstance(c, dict)]
    numeric_correct = False
    supported, traceable, factual = 0, 0, 0
    for claim in claims:
        # Null unsupported claims are abstentions, not new factual assertions.
        if claim.get("value") is None:
            continue
        factual += 1
        reference = _result_for_claim(task, claim, task_dataset)
        correct = bool(reference and claim.get("unit") == reference.get("unit") and
                       _number_equal(claim.get("value"), reference.get("value")) and
                       claim.get("status") in ("supported", "conflicting"))
        if correct:
            supported += 1
            # References are scored independently of the factual value.
            refs = claim.get("evidence_ids")
            expected_refs = reference.get("evidence_ids", [])
            valid_refs = (isinstance(refs, list) and all(isinstance(ref, str) for ref in refs)
                          and len(refs) == len(set(refs)) and set(refs) == set(expected_refs))
            if valid_refs and claim.get("calculation_id") == reference.get("calculation_id"):
                traceable += 1
            if _query_equal(claim.get("query"), task.get("query")):
                numeric_correct = True
    answerable = expected["answerable"]
    refusal = bool(report.get("refusal_reason")) and not any(c.get("value") is not None for c in claims)
    valid_refusal = bool(not answerable and refusal and any(
        c.get("status") == "unsupported" and c.get("value") is None and
        _query_equal(c.get("query"), task.get("query")) for c in claims))
    conflict_required = expected["required_status"] == "conflicting"
    conflict_detected = _conflict_flagged(report, task.get("query"))
    completed = bool((answerable and numeric_correct and (not conflict_required or conflict_detected)) or valid_refusal)
    expected_fault = bool(not answerable or conflict_required)
    flagged = _flagged(report)
    detection_complete = conflict_detected if conflict_required else valid_refusal if not answerable else flagged
    metadata = output.get("metadata") or {}
    usage = metadata.get("usage", {}) if isinstance(metadata, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    if not isinstance(metadata, dict):
        metadata = {}
    condition = output.get("condition", "unknown")
    repetition = output.get("repetition", 1)
    output_id = output.get("output_id") or f"{task['task_id']}__{condition}__r{repetition}"
    failed = output.get("status") in ("failed", "error", "timeout") or bool(output.get("error"))
    return {
        "output_id": output_id, "task_id": task["task_id"], "condition": condition,
        "repetition": repetition, "group": task["group"], "family": task["family"],
        "failed": bool(failed), "required_numeric_slots": int(answerable),
        "correct_numeric_slots": int(answerable and numeric_correct and not failed),
        "required_task_slots": 1, "completed_task_slots": int(completed and not failed),
        "structured_factual_claims": factual, "supported_structured_claims": supported,
        "traceable_structured_claims": traceable,
        "numeric_accuracy": _ratio(int(answerable and numeric_correct and not failed), int(answerable)),
        "effective_answer_coverage": int(completed and not failed),
        "structured_claim_support": _ratio(supported, factual),
        "structured_unsupported_rate": _ratio(factual - supported, factual),
        "evidence_traceability": _ratio(traceable, factual),
        "valid_refusal": valid_refusal, "over_refusal": bool(answerable and refusal),
        "fault_expected": expected_fault, "fault_flagged": flagged,
        "fault_explanation_complete": bool(detection_complete),
        "fault_tp": int(expected_fault and detection_complete and not failed),
        "fault_fn": int(expected_fault and (not detection_complete or failed)),
        "fault_fp": int(not expected_fault and flagged),
        "fault_tn": int(not expected_fault and not flagged),
        "conflict_required": conflict_required, "conflict_detected": conflict_detected,
        "elapsed_seconds": metadata.get("elapsed_seconds", metadata.get("runtime_seconds", output.get("elapsed_seconds"))),
        "runtime_scope": metadata.get("runtime_scope", "per-run if recorded; batch allocation is not individual latency"),
        "input_tokens": metadata.get("input_tokens", usage.get("input_tokens", usage.get("prompt_tokens"))),
        "output_tokens": metadata.get("output_tokens", usage.get("output_tokens", usage.get("completion_tokens"))),
        "cost_usd": metadata.get("cost_usd"),
    }


def _summarize(rows):
    totals = {key: sum(row[key] for row in rows) for key in (
        "required_numeric_slots", "correct_numeric_slots", "required_task_slots", "completed_task_slots",
        "structured_factual_claims", "supported_structured_claims", "traceable_structured_claims",
        "fault_tp", "fault_fn", "fault_fp", "fault_tn")}
    pooled = {
        "numeric_accuracy": _ratio(totals["correct_numeric_slots"], totals["required_numeric_slots"]),
        "effective_answer_coverage": _ratio(totals["completed_task_slots"], totals["required_task_slots"]),
        "structured_claim_support": _ratio(totals["supported_structured_claims"], totals["structured_factual_claims"]),
        "structured_unsupported_rate": _ratio(totals["structured_factual_claims"] - totals["supported_structured_claims"], totals["structured_factual_claims"]),
        "evidence_traceability": _ratio(totals["traceable_structured_claims"], totals["structured_factual_claims"]),
        "fault_detection_rate": _ratio(totals["fault_tp"], totals["fault_tp"] + totals["fault_fn"]),
        "fault_false_positive_rate": _ratio(totals["fault_fp"], totals["fault_fp"] + totals["fault_tn"]),
    }
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(row)
    task_means = {}
    for metric in ("numeric_accuracy", "effective_answer_coverage", "structured_claim_support",
                   "structured_unsupported_rate", "evidence_traceability"):
        values = [mean(row[metric] for row in repetitions if row[metric] is not None)
                  for repetitions in by_task.values()
                  if any(row[metric] is not None for row in repetitions)]
        task_means[metric] = mean(values) if values else None
    costs = {}
    for key in ("elapsed_seconds", "input_tokens", "output_tokens", "cost_usd"):
        values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
        costs[key] = {"observed_runs": len(values), "sum": sum(values) if values else None,
                      "mean": mean(values) if values else None}
    return {"runs": len(rows), "tasks": len(by_task), "failed_runs": sum(row["failed"] for row in rows),
            "over_refusals": sum(row["over_refusal"] for row in rows), "totals": totals,
            "pooled": pooled, "task_mean": task_means, "observed_costs": costs,
            "cost_scope_notice": "Only per-output numeric usage is summed; batch token costs must be recorded once outside per-output metadata. Allocated batch runtime is not measured individual latency."}


def _paired(rows):
    values = defaultdict(list)
    for row in rows:
        values[(row["condition"], row["group"], row["task_id"])].append(row)
    pairs = []
    methods = sorted({row["condition"] for row in rows})
    for first_index, first in enumerate(methods):
        for second in methods[first_index + 1:]:
            for group in ("historical", "challenge"):
                deltas = []
                task_ids = sorted({row["task_id"] for row in rows if row["group"] == group})
                for task_id in task_ids:
                    a, b = values.get((first, group, task_id), []), values.get((second, group, task_id), [])
                    if not a or not b:
                        continue
                    row = {"task_id": task_id}
                    for metric in ("numeric_accuracy", "effective_answer_coverage", "structured_claim_support", "evidence_traceability"):
                        av, bv = [r[metric] for r in a if r[metric] is not None], [r[metric] for r in b if r[metric] is not None]
                        row[metric] = mean(av) - mean(bv) if av and bv else None
                    deltas.append(row)
                pairs.append({"first": first, "second": second, "group": group,
                              "direction": "first minus second, within-task means across repetitions",
                              "task_count": len(deltas), "task_differences": deltas,
                              "mean_difference": {metric: (mean(r[metric] for r in deltas if r[metric] is not None)
                                  if any(r[metric] is not None for r in deltas) else None)
                                  for metric in ("numeric_accuracy", "effective_answer_coverage", "structured_claim_support", "evidence_traceability")},
                              "inference": "Descriptive only; no independent-event claim or repetition-level significance test."})
    return pairs


def write_review_templates(tasks, outputs, output_dir):
    """Create a blind narrative review sheet, with method identities kept separate."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lookup = {task["task_id"]: task for task in tasks}
    prepared = []
    for output in outputs:
        identity = output.get("output_id") or f"{output['task_id']}__{output.get('condition', 'unknown')}__r{output.get('repetition', 1)}"
        blind_id = "B" + hashlib.sha256(identity.encode()).hexdigest()[:14]
        prepared.append((blind_id, identity, output))
    prepared.sort(key=lambda item: item[0])
    fields = ("blind_id", "task_id", "question", "draft", "answer", "claims", "refusal_reason",
              "judge_id", "prose_factual_assertions", "prose_supported_assertions", "unsupported_assertions_text",
              "coverage_adequate", "evidence_links_adequate", "over_refusal", "notes")
    with (directory / "blind_review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for blind_id, _, output in prepared:
            task = lookup[output["task_id"]]
            report = output.get("report") or {}
            if not isinstance(report, dict):
                report = {}
            writer.writerow({"blind_id": blind_id, "task_id": task["task_id"], "question": task["question"],
                             "draft": json.dumps(task.get("draft"), ensure_ascii=False),
                             "answer": report.get("answer", ""),
                             "claims": json.dumps(report.get("claims", []), ensure_ascii=False),
                             "refusal_reason": report.get("refusal_reason") or ""})
    with (directory / "review_key.private.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("blind_id", "output_id", "condition", "repetition"))
        writer.writeheader()
        for blind_id, identity, output in prepared:
            writer.writerow({"blind_id": blind_id, "output_id": identity,
                             "condition": output.get("condition"), "repetition": output.get("repetition", 1)})
    instructions = (
        "盲审表不包含方法标签；review_key.private.csv 由评估负责人保管。\n"
        "逐句审核 answer 和解释中的事实陈述；根据每题数据、计算和来源判断，而非以有无引用判断事实真伪。\n"
        "记录完整性、无依据陈述及过度拒答；禁止仅按字数评分。先冻结各审核者评分，再合并方法标签。\n"
        "表格只提供空白评分栏；当前没有生成或声称任何人工评分。\n"
        "结构化输出风格仍可能透露方法，故该盲审不保证审核者无法推断方法。\n")
    (directory / "review_instructions.txt").write_text(instructions, encoding="utf-8")


def score_outputs(tasks, outputs, output_dir=None, dataset=None):
    """Score all supplied runs, including errors; do not drop missing numeric slots.

    Tasks must be the private frozen specifications. Give ``dataset`` to validate
    legitimate additional queries; without it only the required frozen query is
    within the factual support scoring scope.
    """
    lookup = {task["task_id"]: task for task in tasks}
    rows, seen = [], set()
    for output in outputs:
        task_id = output.get("task_id")
        if task_id not in lookup:
            raise ValueError(f"Unknown task_id in output: {task_id}")
        task = lookup[task_id]
        fixture = apply_task_dataset(dataset, task) if dataset is not None else None
        row = _output_score(task, output, fixture)
        if row["output_id"] in seen:
            raise ValueError("Duplicate output_id; repetitions must be retained separately")
        seen.add(row["output_id"])
        rows.append(row)
    aggregate = {}
    for condition in sorted({row["condition"] for row in rows}):
        for group in ("historical", "challenge", "all"):
            chosen = [row for row in rows if row["condition"] == condition and
                      (group == "all" or row["group"] == group)]
            aggregate[f"{condition}/{group}"] = _summarize(chosen)
    result = {"schema_version": "1.0", "scope": "Automated structured-claim scoring; unrestricted prose requires blind human review.",
              "numerical_absolute_tolerance": str(ABSOLUTE_TOLERANCE),
              "fault_detection_rule": "Structured flags and explanation completeness only: unanswerable tasks require a matching unsupported null claim and refusal reason; draft conflicts require a matching conflicting claim, nonempty explanation and nonempty fault details. This does not certify the semantic correctness of an explanation.",
              "extra_claim_support_scope": "all valid metric queries" if dataset is not None else "required frozen query only",
              "missing_run_policy": "Supplied failures retained. Compare supplied count against the separate planned-run manifest; absent runs are not silently imputed.",
              "statistical_unit": "task; related tasks share one historical event; repetitions are not independent events",
              "per_output": rows, "aggregates": aggregate, "paired_task_differences": _paired(rows),
              "human_review_status": "not_performed", "human_scores": None}
    if output_dir is not None:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scores.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if rows:
            with (directory / "per_output_scores.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        write_review_templates(tasks, outputs, directory)
    return result
