"""Required-slot evaluation for multi-claim investigations.

Numerical truth, evidence binding, partial abstention, structured boundary
judgments and unrestricted prose are separate quantities. Blank human-review
sheets are not completed human judgments. Repetitions are pooled within tasks
before condition comparisons; one historical event is not 30 independent events.
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
from .common import canonical
from .investigation_tasks import apply_task_dataset, public_task

ABSOLUTE_TOLERANCE = Decimal("0.00000001")
NUMERIC_METRICS = ("required_numeric_accuracy", "required_slot_coverage", "structured_factual_accuracy",
                   "structured_unsupported_rate", "citation_binding_accuracy", "partial_refusal_accuracy",
                   "typed_narrative_accuracy", "draft_conflict_identification", "dependency_order_compliance")


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _number_equal(left, right):
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and abs(a - b) <= ABSOLUTE_TOLERANCE
    except (InvalidOperation, TypeError, ValueError):
        return False


def _query_equal(left, right):
    try:
        a, b = metrics.normalize_query(left), metrics.normalize_query(right)
        if a == b:
            return True
        # First and last return one endpoint observation. Single-point
        # first/last/maximum are equivalent; single-point change is zero.
        def point(query):
            if query["operation"] == "first":
                return query["start"]
            if query["operation"] == "last":
                return query["end"]
            if query["operation"] == "maximum" and query["start"] == query["end"]:
                return query["start"]
            return None
        ap, bp = point(a), point(b)
        selectors_a = {key: value for key, value in a.items() if key not in ("start", "end", "operation")}
        selectors_b = {key: value for key, value in b.items() if key not in ("start", "end", "operation")}
        return ap is not None and ap == bp and selectors_a == selectors_b
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return False


def _claims(report):
    value = report.get("claims", [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _reference(claim, expected, fixture):
    for item in expected:
        if _query_equal(claim.get("query"), item["query"]):
            if fixture is not None and metrics.normalize_query(claim["query"]) != metrics.normalize_query(item["query"]):
                try:
                    return metrics.calculate(fixture, claim["query"])
                except (ValueError, KeyError, TypeError, ArithmeticError):
                    return None
            return item.get("result")
    if fixture is not None:
        try:
            return metrics.calculate(fixture, claim.get("query"))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            pass
    return None


def _correct(claim, reference):
    return bool(reference and claim.get("value") is not None and
                claim.get("status") in ("supported", "conflicting") and
                claim.get("unit") == reference["unit"] and _number_equal(claim.get("value"), reference["value"]))


def _bound(claim, reference):
    if not reference:
        return False
    evidence = claim.get("evidence_ids")
    return bool(isinstance(evidence, list) and all(isinstance(item, str) for item in evidence) and
                len(set(evidence)) == len(evidence) and set(evidence) == set(reference["evidence_ids"]) and
                claim.get("calculation_id") == reference["calculation_id"])


def _has_explanation(claim, report):
    # Refusal location is deliberately immaterial. This tests presence, not
    # semantic adequacy; the latter is assigned to the independent prose review.
    return any(isinstance(value, str) and bool(value.strip()) for value in
               (claim.get("explanation"), report.get("refusal_reason"), report.get("answer")))


def _dependency_diagnostics(task, output):
    entries = []
    for index, call in enumerate(output.get("tool_calls", [])):
        if not isinstance(call, dict) or call.get("tool") != "calculate":
            continue
        result = call.get("result", {})
        if isinstance(result, dict) and result.get("ok") is False:
            continue
        entries.append((index, call.get("arguments"), call.get("planning_round", call.get("phase"))))
    expected = {item["slot_id"]: item for item in task["_private"]["expected_claims"]}
    details = []
    for dependency in task["_private"].get("dependencies", []):
        parent = expected[dependency["after_slot_id"]]
        child = expected[dependency["slot_id"]]
        parents = [(index, phase) for index, query, phase in entries if _query_equal(query, parent["query"])]
        children = [(index, phase) for index, query, phase in entries if _query_equal(query, child["query"])]
        # Presence and chronology alone cannot establish that a model reasoned
        # from the returned value. This is an execution-order diagnostic only.
        valid = bool(parents and children and min(index for index, _ in parents) < min(index for index, _ in children))
        dynamic = any(isinstance(parent_phase, int) and isinstance(child_phase, int) and
                      parent_index < child_index and parent_phase < child_phase
                      for parent_index, parent_phase in parents for child_index, child_phase in children)
        details.append({**dependency, "valid_execution_order": valid, "valid_order": dynamic,
                        "planning_round_recorded": any(isinstance(phase, int) for _, phase in parents + children)})
    return details


def _score_output(task, output, fixture=None):
    expected = task.get("_private", {}).get("expected_claims")
    if not expected:
        raise ValueError("Scoring requires private frozen expected_claims")
    report = output.get("report") or {}
    if not isinstance(report, dict):
        report = {}
    claims = _claims(report)
    failed = output.get("status") in ("failed", "error", "timeout", "missing") or bool(output.get("error"))
    slot_details = []
    for item in expected:
        candidates = [claim for claim in claims if _query_equal(claim.get("query"), item["query"])]
        correct = any(_correct(claim, item.get("result")) for claim in candidates) if item["answerable"] else False
        abstained = (not item["answerable"] and any(claim.get("value") is None and
                     claim.get("status") == "unsupported" and _has_explanation(claim, report) for claim in candidates))
        # A simultaneous fabricated assertion is not a valid abstention.
        abstained = bool(abstained and not any(claim.get("value") is not None for claim in candidates))
        conflict = item["required_status"] == "conflicting"
        conflict_flagged = bool(conflict and any(claim.get("status") == "conflicting" and
                                 isinstance(claim.get("explanation"), str) and claim["explanation"].strip()
                                 for claim in candidates))
        slot_details.append({"slot_id": item["slot_id"], "answerable": item["answerable"],
                             "numeric_correct": bool(correct and not failed),
                             "valid_partial_refusal": bool(abstained and not failed),
                             "completed": bool((correct or abstained) and not failed),
                             "conflict_required": conflict, "conflict_identified": bool(conflict_flagged and not failed),
                             "matching_claim_count": len(candidates)})
    factual, correct_factual, bound = 0, 0, 0
    for claim in claims:
        if claim.get("value") is None:
            continue
        factual += 1
        reference = _reference(claim, expected, fixture)
        correct_factual += int(_correct(claim, reference))
        bound += int(_bound(claim, reference))
    narratives = report.get("narrative_claims", [])
    if not isinstance(narratives, list):
        narratives = []
    narrative_details = []
    for required in task["_private"].get("expected_narratives", []):
        candidates = [item for item in narratives if isinstance(item, dict) and item.get("kind") == required["kind"]]
        correct = bool(candidates and all(item.get("stance") == required["expected_stance"] for item in candidates) and
                       all(isinstance(item.get("explanation"), str) and item["explanation"].strip() for item in candidates))
        narrative_details.append({"slot_id": required["slot_id"], "kind": required["kind"],
                                  "correct_structured_stance": bool(correct and not failed)})
    dependencies = _dependency_diagnostics(task, output)
    counts = {
        "required_numeric_slots": sum(item["answerable"] for item in slot_details),
        "correct_numeric_slots": sum(item["numeric_correct"] for item in slot_details),
        "required_refusal_slots": sum(not item["answerable"] for item in slot_details),
        "correct_refusal_slots": sum(item["valid_partial_refusal"] for item in slot_details),
        "required_slots": len(slot_details), "completed_slots": sum(item["completed"] for item in slot_details),
        "structured_factual_claims": factual, "correct_structured_claims": correct_factual,
        "bound_structured_claims": bound,
        "required_narrative_slots": len(narrative_details),
        "correct_narrative_slots": sum(item["correct_structured_stance"] for item in narrative_details),
        "required_conflict_slots": sum(item["conflict_required"] for item in slot_details),
        "identified_conflict_slots": sum(item["conflict_identified"] for item in slot_details),
        "required_dependencies": len(dependencies),
        "ordered_dependencies": sum(item["valid_order"] for item in dependencies),
    }
    metadata = output.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    usage = metadata.get("usage") or {}
    if not isinstance(usage, dict) or metadata.get("batch_usage_reference"):
        # A batch usage reference belongs in a once-per-batch ledger, even if a
        # caller accidentally repeats the total in each output's usage object.
        usage = {}
    condition = output.get("condition", "unknown")
    repetition = output.get("repetition", 1)
    output_id = output.get("output_id") or f"{task['task_id']}__{condition}__r{repetition}"
    return {"output_id": output_id, "task_id": task["task_id"], "family": task["family"],
            "condition": condition, "repetition": repetition, "failed": bool(failed), **counts,
            "required_numeric_accuracy": _ratio(counts["correct_numeric_slots"], counts["required_numeric_slots"]),
            "required_slot_coverage": _ratio(counts["completed_slots"], counts["required_slots"]),
            "structured_factual_accuracy": _ratio(correct_factual, factual),
            "structured_unsupported_rate": _ratio(factual - correct_factual, factual),
            "citation_binding_accuracy": _ratio(bound, factual),
            "partial_refusal_accuracy": _ratio(counts["correct_refusal_slots"], counts["required_refusal_slots"]),
            "typed_narrative_accuracy": _ratio(counts["correct_narrative_slots"], counts["required_narrative_slots"]),
            "draft_conflict_identification": _ratio(counts["identified_conflict_slots"], counts["required_conflict_slots"]),
            "dependency_order_compliance": _ratio(counts["ordered_dependencies"], counts["required_dependencies"]),
            "all_required_slots_complete": bool(not failed and all(item["completed"] for item in slot_details)),
            "over_refused_slots": sum(item["answerable"] and any(claim.get("value") is None and claim.get("status") == "unsupported"
                    for claim in claims if _query_equal(claim.get("query"), expected[index]["query"])) for index, item in enumerate(slot_details)),
            "calculate_calls": sum(isinstance(call, dict) and call.get("tool") == "calculate" for call in output.get("tool_calls", [])),
            "minimum_tool_steps_diagnostic": task["_private"].get("minimum_tool_steps"),
            "slot_details": slot_details, "narrative_details": narrative_details, "dependency_details": dependencies,
            "input_tokens": metadata.get("input_tokens", usage.get("input_tokens")),
            "output_tokens": metadata.get("output_tokens", usage.get("output_tokens")),
            "elapsed_seconds": metadata.get("elapsed_seconds", metadata.get("runtime_seconds")),
            "runtime_scope": metadata.get("runtime_scope", "unknown"), "cost_usd": metadata.get("cost_usd")}


def _task_means(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["task_id"]].append(row)
    return [{"task_id": task_id, "repetitions": len(values),
             **{metric: mean(row[metric] for row in values if row[metric] is not None)
                if any(row[metric] is not None for row in values) else None for metric in NUMERIC_METRICS}}
            for task_id, values in sorted(grouped.items())]


def _summary(rows):
    task_values = _task_means(rows)
    count_keys = ("required_numeric_slots", "correct_numeric_slots", "required_refusal_slots", "correct_refusal_slots",
                  "required_slots", "completed_slots", "structured_factual_claims", "correct_structured_claims", "bound_structured_claims",
                  "required_narrative_slots", "correct_narrative_slots", "required_conflict_slots", "identified_conflict_slots",
                  "required_dependencies", "ordered_dependencies")
    totals = {key: sum(row[key] for row in rows) for key in count_keys}
    observed_costs = {}
    for key in ("input_tokens", "output_tokens", "elapsed_seconds", "cost_usd"):
        values = [row[key] for row in rows if isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)]
        observed_costs[key] = {"observed_outputs": len(values), "sum": sum(values) if values else None}
    return {"outputs": len(rows), "tasks": len(task_values), "failed_outputs": sum(row["failed"] for row in rows),
            "complete_outputs": sum(row["all_required_slots_complete"] for row in rows), "totals": totals,
            "task_mean": {metric: mean(row[metric] for row in task_values if row[metric] is not None)
                          if any(row[metric] is not None for row in task_values) else None for metric in NUMERIC_METRICS},
            "per_task": task_values, "observed_per_output_costs": observed_costs,
            "cost_scope": "Do not attach batch usage to every output. Batch tokens are aggregated once in a separate run ledger. Allocated batch time is not isolated task latency."}


def _paired(rows):
    conditions = sorted({row["condition"] for row in rows})
    lookup = {condition: {row["task_id"]: row for row in _task_means([r for r in rows if r["condition"] == condition])}
              for condition in conditions}
    pairs = []
    for index, first in enumerate(conditions):
        for second in conditions[index + 1:]:
            task_ids = sorted(set(lookup[first]) & set(lookup[second]))
            differences = [{"task_id": task_id, **{metric: lookup[first][task_id][metric] - lookup[second][task_id][metric]
                               if lookup[first][task_id][metric] is not None and lookup[second][task_id][metric] is not None else None
                               for metric in NUMERIC_METRICS}} for task_id in task_ids]
            pairs.append({"first": first, "second": second, "direction": "first minus second, task means across repetitions",
                          "paired_tasks": len(task_ids), "task_differences": differences,
                          "mean_difference": {metric: mean(row[metric] for row in differences if row[metric] is not None)
                              if any(row[metric] is not None for row in differences) else None for metric in NUMERIC_METRICS},
                          "inference": "Descriptive paired task differences only. Shared-event tasks and repeated generations are not independent historical replications."})
    return pairs


def _write_review_csv_once(path, fields, rows):
    """Preserve completed human labels, while rejecting stale output identities."""
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as stream:
            retained = list(csv.DictReader(stream))
        expected_ids = [str(row["blind_id"]) for row in rows]
        retained_ids = [row.get("blind_id") for row in retained]
        if len(retained_ids) != len(expected_ids) or set(retained_ids) != set(expected_ids):
            raise ValueError(f"Existing review sheet belongs to different outputs: {path}; choose a new evaluation directory")
        retained_lookup = {row["blind_id"]: row for row in retained}
        for expected in rows:
            actual = retained_lookup[str(expected["blind_id"]) ]
            # Generated identity/question/report columns are immutable. Human
            # labels are absent from expected rows and are never overwritten.
            if any(actual.get(key) != ("" if value is None else str(value)) for key, value in expected.items()):
                raise ValueError(f"Existing reviewed content differs: {path}; choose a new evaluation directory")
        return
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_double_review_templates(tasks, outputs, output_dir):
    """Export two independently completed blank sheets and an adjudication sheet."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lookup = {task["task_id"]: task for task in tasks}
    prepared = []
    for output in outputs:
        identity = output.get("output_id") or f"{output['task_id']}__{output.get('condition', 'unknown')}__r{output.get('repetition', 1)}"
        blind_id = "B2_" + hashlib.sha256(identity.encode()).hexdigest()[:14]
        prepared.append((blind_id, identity, output))
    prepared.sort(key=lambda item: item[0])
    fields = ("blind_id", "task_id", "question", "draft", "answer", "claims_and_explanations", "narrative_claims",
              "judge_id", "prose_factual_assertions", "prose_supported_assertions", "unsupported_assertions_text",
              "numerical_or_unit_contradictions", "draft_repair_semantically_correct", "evidence_boundary_respected",
              "partial_answer_adequate", "citation_correctness", "citation_completeness", "notes")
    rows = []
    for blind_id, _, output in prepared:
        task = lookup[output["task_id"]]
        report = output.get("report") or {}
        if not isinstance(report, dict):
            report = {}
        rows.append({"blind_id": blind_id, "task_id": task["task_id"], "question": task["question"],
                     "draft": canonical(task.get("draft")), "answer": report.get("answer", ""),
                     "claims_and_explanations": canonical(report.get("claims", [])),
                     "narrative_claims": canonical(report.get("narrative_claims", []))})
    for reviewer in ("A", "B"):
        _write_review_csv_once(directory / f"blind_review_{reviewer}.csv", fields, rows)
    key_rows = [{"blind_id": blind_id, "output_id": identity, "condition": output.get("condition"),
                 "repetition": output.get("repetition", 1)} for blind_id, identity, output in prepared]
    _write_review_csv_once(directory / "review_key.private.csv", ("blind_id", "output_id", "condition", "repetition"), key_rows)
    _write_review_csv_once(directory / "adjudication.csv",
        ("blind_id", "judge_A_frozen_at", "judge_B_frozen_at", "disputed_assertion", "adjudicated_label", "rationale"),
        [{"blind_id": blind_id} for blind_id, _, _ in prepared])
    references = [{"task": public_task(task), "family": task["family"],
                   "synthetic_fixture": task["_private"].get("fixture"),
                   "expected_claims": task["_private"]["expected_claims"],
                   "expected_narratives": task["_private"].get("expected_narratives", [])} for task in tasks]
    reference_path = directory / "review_reference.private.json"
    if reference_path.exists():
        if json.loads(reference_path.read_text(encoding="utf-8")) != references:
            raise ValueError("Existing review references differ; choose a new evaluation directory")
    else:
        reference_path.write_text(json.dumps(references, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    instructions = """两名审核者分别完成 blind_review_A/B.csv，在冻结各自标签前不得交换判断或读取 review_key.private.csv。
逐句审核 answer、claim.explanation 与 narrative_claim.explanation。将每个可核查事实、数值、方向、单位与采样峰值限定分成原子陈述，使用 review_reference.private.json 及数据/计算复核。
正确但无引用与错误但有引用必须区分。因果、发行人偿付与连续时间峰值主张不能由当前池状态直接支持。局部数据缺失应保留可答部分。
特别检查正文是否否认已在结构化字段承认的草稿冲突。typed narrative 得分只验证结构化 stance，不认证自由正文或解释语义。
人工主指标是每个输出中有依据事实数/全部可核查事实数，并报告引用正确率与完整率；无事实输出的事实支持率记NA，不记100%。对所有缺失/失败输出另计任务覆盖率。
两份标签冻结后先报告分歧率、共同完成的样本数与适合标签类型的一致性指标，再用 adjudication.csv 处理争议，最后合并方法标签。禁止将模型模拟评审称为人工审计。
所有评分栏当前为空；本次导出不代表已完成独立人工评价。方法风格可能泄露条件，盲审不保证无法猜测方法。
"""
    instructions_path = directory / "review_instructions.txt"
    if not instructions_path.exists():
        instructions_path.write_text(instructions, encoding="utf-8")


def score_investigation_outputs(tasks, outputs, output_dir=None, dataset=None, planned_runs=None):
    lookup = {task["task_id"]: task for task in tasks}
    supplied = list(outputs)
    identities = set()
    for output in supplied:
        identity = output.get("output_id") or f"{output['task_id']}__{output.get('condition', 'unknown')}__r{output.get('repetition', 1)}"
        if identity in identities:
            raise ValueError("Duplicate output identity")
        identities.add(identity)
    missing = []
    if planned_runs is not None:
        planned_ids = {item["output_id"] for item in planned_runs}
        if len(planned_ids) != len(planned_runs):
            raise ValueError("Duplicate planned output identity")
        if identities - planned_ids:
            raise ValueError("Unplanned supplied outputs")
        for item in planned_runs:
            if item["output_id"] not in identities:
                missing.append(item["output_id"])
                supplied.append({**item, "status": "missing", "report": {}, "metadata": {}})
    rows = []
    for output in supplied:
        if output.get("task_id") not in lookup:
            raise ValueError(f"Unknown task_id: {output.get('task_id')}")
        task = lookup[output["task_id"]]
        fixture = apply_task_dataset(dataset, task) if dataset is not None else None
        rows.append(_score_output(task, output, fixture))
    aggregates = {}
    for condition in sorted({row["condition"] for row in rows}):
        for family in sorted({row["family"] for row in rows}) + ["all"]:
            selected = [row for row in rows if row["condition"] == condition and (family == "all" or row["family"] == family)]
            aggregates[f"{condition}/{family}"] = _summary(selected)
    result = {"schema_version": "2.0", "scope": "Required-slot numerical truth, partial refusal, citation binding and typed boundary judgments. Free prose is outside automated certification.",
              "numerical_absolute_tolerance": str(ABSOLUTE_TOLERANCE),
              "primary_metrics": ["required_slot_coverage", "structured_factual_accuracy"],
              "separation_notice": "Evidence binding is scored independently of factual accuracy. Refusal text location does not affect valid partial refusal. Draft flags and typed narrative judgments are separate from numerical coverage.",
              "statistical_unit": "within-task mean across repetitions; related tasks share one event",
              "missing_run_policy": "Planned absent outputs are explicit failures with all required slots retained" if planned_runs is not None else "Supply planned_runs to enforce complete planned denominators; absent outputs otherwise not imputed",
              "planned_outputs": len(planned_runs) if planned_runs is not None else None, "missing_output_ids": missing,
              "per_output": rows, "aggregates": aggregates, "paired_task_differences": _paired(rows),
              "human_review_status": "not_performed", "human_scores": None,
              "dependency_scope": "Parent result queried in an earlier recorded planning round before dependent query; chronology alone is insufficient. This is not proof of reasoning or authentic execution",
              "extra_claim_scope": "all supported metric queries in fixture" if dataset is not None else "frozen expected queries only"}
    if output_dir is not None:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scores.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if rows:
            flat_rows = [{key: canonical(value) if isinstance(value, (list, dict)) else value for key, value in row.items()} for row in rows]
            with (directory / "per_output_scores.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=tuple(flat_rows[0]))
                writer.writeheader()
                writer.writerows(flat_rows)
        write_double_review_templates(tasks, supplied, directory)
    return result
