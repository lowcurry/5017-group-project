"""Confirmatory study specification, human-reference gate and scoped scoring.

This module does not compute financial metrics, call a model or fetch data. The
public decision rules may be shared by independent implementations; reference
values and calculation code must not cross the inference boundary. Blank review
forms are explicitly pending external records, never completed human judgments.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path

PROTOCOL_VERSION = "curve-confirmation-20261015.1"
ABSOLUTE_TOLERANCE = Decimal("0.00000001")
CONDITIONS = ("self_review", "archive_recompute", "source_recompute")
REPETITIONS = 3
REFUSAL_STATUSES = frozenset(("unsupported", "refused"))
ASSERTION_STATUSES = frozenset(("supported", "conflicting"))
_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def number_equal(left, right, *, exact=False):
    """Finite decimal comparison; exact applies to raw integers/identifiers."""
    if isinstance(left, bool) or isinstance(right, bool) or left is None or right is None:
        return False
    if exact:
        return bool(_INTEGER.fullmatch(str(left)) and _INTEGER.fullmatch(str(right)) and str(left) == str(right))
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        if not a.is_finite() or not b.is_finite():
            return False
        with localcontext() as context:
            context.prec = max(80, len(a.as_tuple().digits), len(b.as_tuple().digits)) + 5
            return abs(a - b) <= ABSOLUTE_TOLERANCE
    except (DecimalException, TypeError, ValueError):
        return False


def has_refusal_reason(claim, report):
    """Accept either field or prose location; presence is not semantic approval."""
    return any(isinstance(value, str) and bool(value.strip()) for value in (
        claim.get("explanation"), claim.get("refusal_reason"), claim.get("reason"),
        report.get("refusal_reason"), report.get("answer"),
    ))


def public_protocol():
    return {
        "version": PROTOCOL_VERSION,
        "derived_decimal_absolute_tolerance": str(ABSOLUTE_TOLERANCE),
        "relative_tolerance": "0",
        "exact_fields": ["raw_integer", "unit", "block_number", "block_hash", "target_timestamp_utc", "coin", "direction", "size"],
        "accepted_assertion_statuses": sorted(ASSERTION_STATUSES),
        "accepted_refusal_statuses": sorted(REFUSAL_STATUSES),
        "refusal_reason_locations": ["claim.explanation", "claim.refusal_reason", "claim.reason", "report.refusal_reason", "report.answer"],
        "refusal_reason_check": "presence_only_semantic_adequacy_requires_review",
        "automatic_scope": "report.claims_only_not_unrestricted_prose",
        "conditions": list(CONDITIONS), "repetitions": REPETITIONS,
        "study_design": "shared_initial_with_three_matched_review_branches",
        "planned_final_model_answers": 72, "planned_fixed_references": 8,
        "draft_required_slots": 26, "draft_answerable_slots": 22, "draft_refusal_slots": 4,
        "public_inputs_exclude": ["candidate_references", "expected_claims", "reference_values", "review_records", "_private"],
        "human_review_status": "pending_external_records",
        "budget_status": "pending_hard_cap_adapter",
        "event_scope": "single_event_draft_second_event_not_selected",
        "formal_start_requirements": ["frozen_tasks", "validated_human_reference_records", "verified_independent_source_path", "enforced_equal_hard_token_caps"],
    }


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("Expected an explicit UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Timestamps must explicitly use UTC")
    return parsed.astimezone(timezone.utc)


def _shift(value, hours):
    return (_utc(value) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def build_draft_tasks(event_windows=None):
    """Eight tasks/26 slots. No private answer is computed or exposed here.

    The default is explicitly a single-event draft. A second event may replace a
    window only after availability and human-record alignment are documented.
    Public query specifications describe requested work, not hidden answer keys.
    """
    windows = event_windows or [
        {"event_id": "usdc_march_2023", "start": "2023-03-10T00:00:00Z", "end": "2023-03-10T06:00:00Z"},
        {"event_id": "usdc_march_2023", "start": "2023-03-11T00:00:00Z", "end": "2023-03-11T06:00:00Z"},
    ]
    if len(windows) != 2:
        raise ValueError("Provide exactly two short study windows")
    for window in windows:
        if _utc(window["end"]) - _utc(window["start"]) < timedelta(hours=4):
            raise ValueError("Each draft window requires at least four hourly intervals")
        if not isinstance(window.get("event_id"), str) or not window["event_id"]:
            raise ValueError("Each window requires an explicit event identifier")
    tasks = []
    for family in ("single_point_and_units", "window_and_scale", "locate_then_query", "partial_evidence_and_boundary"):
        for variant, window in enumerate(windows):
            start, end = window["start"], window["end"]
            middle = _shift(start, 2)
            base = {"start": start, "end": end}
            if family == "single_point_and_units":
                requests = [
                    ("DAI nominal token share; retain raw-to-normalized audit", {"metric": "share_pct", "operation": "last", "start": middle, "end": middle, "coin": "DAI"}),
                    ("USDC nominal token share", {"metric": "share_pct", "operation": "last", "start": middle, "end": middle, "coin": "USDC"}),
                    ("USDC→USDT quote, 1000 input", {"metric": "quote_rate", "operation": "last", "start": middle, "end": middle, "direction": "USDC_USDT", "size": 1000}),
                ]
                question = "报告所列单点指标。保留原始整数到归一化的单位关系、报价方向与目标区块；合约报价不可称为成交价。"
            elif family == "window_and_scale":
                requests = [
                    ("USDC share endpoint change", {"metric": "share_pct", "operation": "change", **base, "coin": "USDC"}),
                    ("100000-input USDC→USDT quote at last target", {"metric": "quote_rate", "operation": "last", **base, "direction": "USDC_USDT", "size": 100000}),
                    ("sampled maximum size sensitivity, 100000 relative to 1000", {"metric": "size_sensitivity_bps", "operation": "maximum", **base, "direction": "USDC_USDT", "size": 100000}),
                ]
                question = "比较端点变化和输入规模。末减初变化与规模敏感度分别报告；最大值须检查完整采样网格，并说明小时采样的限制。"
            elif family == "locate_then_query":
                requests = [("sampled imbalance maximum and returned target", {"metric": "imbalance_l1", "operation": "maximum", **base})]
                for offset in (-1, 0, 1):
                    requests.append((f"USDC→USDT quote at returned peak {offset:+d} hour", {
                        "metric": "quote_rate", "operation": "last", "direction": "USDC_USDT", "size": 100000,
                        "time_from_slot": "S1", "offset_hours": offset,
                    }))
                question = "先定位窗口的采样失衡最大值，再以实际返回时点查询前一小时、该小时及后一小时报价。必须保存依赖顺序，不能预设峰值时点。"
            else:
                requests = [
                    ("USDC share at the permitted first target", {"metric": "share_pct", "operation": "first", "start": start, "end": start, "coin": "USDC"}),
                    ("share change across a deliberately missing interior sample", {"metric": "share_pct", "operation": "change", **base, "coin": "USDC"}),
                    ("continuous-time global imbalance peak", {"metric": "continuous_imbalance_peak", **base}),
                ]
                question = "仅使用限定证据：保留可答的首点占比；内部缺样时拒答完整窗口变化；小时采样不足以确定连续时间真实峰值。不得补回被题目排除的样本。"
            task = {
                "task_id": f"C{len(tasks)+1:02d}", "family": family, "event_id": window["event_id"],
                "status": "draft_awaiting_source_and_human_records", "window": copy.deepcopy(window),
                "question": question,
                "required_components": [{"slot_id": f"S{i}", "label": label, "request": request} for i, (label, request) in enumerate(requests, 1)],
                "allowed_evidence": {"policy": "specified_hourly_targets_only", "interval_minutes": 60},
            }
            if family == "locate_then_query":
                task["allowed_evidence"]["followup_padding_hours"] = 1
            if family == "partial_evidence_and_boundary":
                task["allowed_evidence"]["excluded_target_timestamp_utc"] = _shift(start, 3)
            tasks.append(task)
    return tasks


def public_task(task):
    allow = ("task_id", "family", "event_id", "window", "question", "required_components", "allowed_evidence")
    clean = {key: copy.deepcopy(task[key]) for key in allow if key in task}
    # Even a malformed caller cannot smuggle private values into a component.
    clean["window"] = {key: copy.deepcopy(task.get("window", {})[key]) for key in ("start", "end", "event_id") if key in task.get("window", {})}
    query_fields = ("metric", "operation", "start", "end", "coin", "direction", "size", "time_from_slot", "offset_hours")
    clean["required_components"] = [{
        **{key: copy.deepcopy(item[key]) for key in ("slot_id", "label") if key in item},
        "request": {key: copy.deepcopy(item.get("request", {})[key]) for key in query_fields if key in item.get("request", {})},
    } for item in task.get("required_components", [])]
    clean["allowed_evidence"] = {key: copy.deepcopy(task.get("allowed_evidence", {})[key]) for key in
                                 ("policy", "interval_minutes", "followup_padding_hours", "excluded_target_timestamp_utc")
                                 if key in task.get("allowed_evidence", {})}
    return clean


def public_tasks(tasks):
    return [public_task(task) for task in tasks]


def planned_runs(tasks):
    return {
        "initials": [{"initial_id": f"{task['task_id']}__r{rep}", "task_id": task["task_id"], "repetition": rep}
                     for task in tasks for rep in range(1, REPETITIONS + 1)],
        "finals": [{"output_id": f"{task['task_id']}__{condition}__r{rep}", "initial_id": f"{task['task_id']}__r{rep}",
                    "task_id": task["task_id"], "condition": condition, "repetition": rep}
                   for task in tasks for rep in range(1, REPETITIONS + 1) for condition in CONDITIONS],
        "fixed_references": [{"output_id": f"{task['task_id']}__fixed_reference", "task_id": task["task_id"]} for task in tasks],
    }


def _slot_keys(tasks):
    keys = [(task["task_id"], item["slot_id"]) for task in tasks for item in task.get("required_components", [])]
    if len(keys) != len(set(keys)) or not keys:
        raise ValueError("Tasks require unique, nonempty slot identifiers")
    return keys


def build_reference_review_package(tasks, candidate_references=None, *, dataset_fingerprint=None):
    """Return private candidates and blank A/B/arbitration rows, not human labels."""
    keys = _slot_keys(tasks)
    candidates = copy.deepcopy(candidate_references or [])
    if candidates and (len(candidates) != len(keys) or any(not isinstance(item, dict) for item in candidates)
                       or {(item.get("task_id"), item.get("slot_id")) for item in candidates} != set(keys)):
        raise ValueError("Candidate references must cover exactly the task slots")
    lookup = {(item["task_id"], item["slot_id"]): item for item in candidates}
    rows = [{"task_id": task_id, "slot_id": slot_id, "reviewer_id": "", "reviewed_at_utc": "", "verdict": "",
             "reference_sha256": content_hash(lookup[(task_id, slot_id)]) if (task_id, slot_id) in lookup else "",
             "raw_record_refs": "", "calculation_note": "", "checked_fields": "", "proposed_reference": ""}
            for task_id, slot_id in keys]
    package = {
        "schema_version": 1, "protocol_version": PROTOCOL_VERSION, "status": "pending_external_records",
        "human_identity_scope": "record_integrity_only_identity_and_independence_not_automatically_proven",
        "tasks_sha256": content_hash(public_tasks(tasks)), "dataset_fingerprint": dataset_fingerprint,
        "slot_keys": [list(key) for key in keys], "candidate_references": candidates,
        "reference_template": [{"task_id": task["task_id"], "slot_id": item["slot_id"],
                                "answerable": None, "query": copy.deepcopy(item.get("request")),
                                "resolved_query": None, "value": None, "unit": "", "source_refs": []}
                               for task in tasks for item in task.get("required_components", [])],
        "candidate_references_sha256": content_hash(candidates),
        "reviewer_a_blank_rows": copy.deepcopy(rows), "reviewer_b_blank_rows": copy.deepcopy(rows),
        "arbitration_blank_rows": [{"task_id": a, "slot_id": b, "reviewer_id": "", "reviewed_at_utc": "", "resolution_note": "", "resolved_reference": ""} for a, b in keys],
    }
    package["review_package_sha256"] = content_hash(package)
    return package


def write_reference_review_package(directory, tasks, candidate_references=None, *, dataset_fingerprint=None):
    package = build_reference_review_package(tasks, candidate_references, dataset_fingerprint=dataset_fingerprint)
    path = Path(directory)
    names = ("reference_review_package.private.json", "tasks.public.json", "reviewer_a_blank.csv", "reviewer_b_blank.csv", "arbitration_blank.csv")
    if any((path / name).exists() for name in names):
        raise FileExistsError("Review output already exists; preserve supplied or filled human records")
    path.mkdir(parents=True, exist_ok=True)
    for name, value in ((names[0], package), (names[1], public_tasks(tasks))):
        with (path / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    for label in ("reviewer_a", "reviewer_b", "arbitration"):
        rows = package[f"{label}_blank_rows"]
        with (path / f"{label}_blank.csv").open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return package


def _reference_result(reference):
    return reference.get("result") if isinstance(reference.get("result"), dict) else reference


def _exact_equal(left, right):
    try:
        return type(left) is type(right) and _canonical(left) == _canonical(right)
    except (TypeError, ValueError):
        return False


def _valid_reference(reference, key):
    if not isinstance(reference, dict) or (reference.get("task_id"), reference.get("slot_id")) != key:
        return False
    if not isinstance(reference.get("answerable"), bool):
        return False
    result = _reference_result(reference)
    if reference["answerable"]:
        return bool(number_equal(result.get("value"), result.get("value")) and isinstance(result.get("unit"), str) and result["unit"].strip())
    return result.get("value") is None


def validate_reference_review(package, reviewer_a=None, reviewer_b=None, arbitration=None):
    """Validate imported human records, then return a hash-bound frozen key.

    No reviewer is synthesized. This validates completeness and binding of supplied
    records, not that a named individual actually performed the stated work.
    """
    errors = []
    package_body = {key: value for key, value in package.items() if key != "review_package_sha256"}
    package_hash = package.get("review_package_sha256")
    if package_hash != content_hash(package_body):
        errors.append("review_package_hash_mismatch")
    if package.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("review_protocol_version_mismatch")
    candidates = package.get("candidate_references", [])
    keys = [tuple(item) for item in package.get("slot_keys", [])]
    lookup = {(item.get("task_id"), item.get("slot_id")): item for item in candidates if isinstance(item, dict)}
    if len(keys) != len(set(keys)) or len(lookup) != len(candidates) or set(lookup) != set(keys):
        errors.append("candidate_reference_coverage_incomplete")
    if package.get("candidate_references_sha256") != content_hash(candidates):
        errors.append("candidate_reference_hash_mismatch")
    for key, reference in lookup.items():
        if not _valid_reference(reference, key):
            errors.append(f"invalid_candidate_reference:{key[0]}:{key[1]}")
        query = reference.get("query")
        if isinstance(query, dict) and "time_from_slot" in query:
            resolved = reference.get("resolved_query")
            if not isinstance(resolved, dict) or "time_from_slot" in resolved:
                errors.append(f"dynamic_reference_query_unresolved:{key[0]}:{key[1]}")
            else:
                try:
                    if _utc(resolved.get("start")) != _utc(resolved.get("end")):
                        raise ValueError("Expected a resolved point query")
                except (ValueError, TypeError):
                    errors.append(f"dynamic_reference_query_unresolved:{key[0]}:{key[1]}")
    review_maps = []
    reviewer_ids = []
    for label, record in (("A", reviewer_a), ("B", reviewer_b)):
        if not isinstance(record, dict):
            errors.append(f"human_review_{label}_missing")
            review_maps.append({})
            continue
        reviewer_id = record.get("reviewer_id")
        if record.get("reviewer_type") != "human" or record.get("independent_review") is not True:
            errors.append(f"human_review_{label}_human_independence_attestation_missing")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            errors.append(f"human_reviewer_{label}_identity_missing")
        else:
            reviewer_ids.append(reviewer_id.strip())
        if record.get("review_package_sha256") != package_hash:
            errors.append(f"human_review_{label}_package_binding_mismatch")
        rows = record.get("slots", [])
        if not isinstance(rows, list):
            rows = []
        row_map = {(row.get("task_id"), row.get("slot_id")): row for row in rows if isinstance(row, dict)}
        if len(row_map) != len(rows) or set(row_map) != set(keys):
            errors.append(f"human_review_{label}_slot_coverage_incomplete")
        checked = {}
        for key, row in row_map.items():
            candidate = lookup.get(key)
            if candidate is None:
                continue
            if row.get("reference_sha256") != content_hash(candidate):
                errors.append(f"human_review_{label}_reference_binding_mismatch:{key[0]}:{key[1]}")
            try:
                _utc(row.get("reviewed_at_utc"))
            except (ValueError, TypeError):
                errors.append(f"human_review_{label}_timestamp_missing:{key[0]}:{key[1]}")
            required = {"answerability", "evidence_boundary"}
            if candidate.get("answerable") is True:
                required |= {"value", "unit", "time", "block", "formula", "raw_evidence", "raw_to_normalized"}
            if not isinstance(row.get("checked_fields"), list) or not required.issubset(set(row["checked_fields"])):
                errors.append(f"human_review_{label}_checks_incomplete:{key[0]}:{key[1]}")
            if not isinstance(row.get("raw_record_refs"), list) or not row["raw_record_refs"] or not all(isinstance(item, str) and item.strip() for item in row["raw_record_refs"]):
                errors.append(f"human_review_{label}_evidence_refs_missing:{key[0]}:{key[1]}")
            if not isinstance(row.get("calculation_note"), str) or not row["calculation_note"].strip():
                errors.append(f"human_review_{label}_calculation_note_missing:{key[0]}:{key[1]}")
            if row.get("verdict") == "approve":
                checked[key] = candidate
            elif row.get("verdict") == "revise" and _valid_reference(row.get("proposed_reference"), key):
                checked[key] = row["proposed_reference"]
            else:
                errors.append(f"human_review_{label}_verdict_missing_or_invalid:{key[0]}:{key[1]}")
        review_maps.append(checked)
    if len(reviewer_ids) != 2 or len(set(reviewer_ids)) != 2:
        errors.append("two_distinct_human_reviewers_required")
    disagreements = [key for key in keys if key in review_maps[0] and key in review_maps[1] and review_maps[0][key] != review_maps[1][key]]
    resolutions = {}
    if disagreements:
        if not isinstance(arbitration, dict) or arbitration.get("review_package_sha256") != package_hash:
            errors.append("bound_arbitration_required_for_disagreements")
        else:
            arbitrator = arbitration.get("reviewer_id")
            if arbitration.get("reviewer_type") != "human":
                errors.append("human_arbitrator_attestation_missing")
            if not isinstance(arbitrator, str) or not arbitrator.strip() or arbitrator.strip() in reviewer_ids:
                errors.append("distinct_third_arbitrator_required")
            rows = arbitration.get("slots", [])
            if not isinstance(rows, list):
                rows = []
            row_map = {(row.get("task_id"), row.get("slot_id")): row for row in rows if isinstance(row, dict)}
            if len(row_map) != len(rows) or set(row_map) != set(disagreements):
                errors.append("arbitration_disagreement_coverage_mismatch")
            for key, row in row_map.items():
                try:
                    _utc(row.get("reviewed_at_utc"))
                except (ValueError, TypeError):
                    errors.append(f"arbitration_timestamp_missing:{key[0]}:{key[1]}")
                if not isinstance(row.get("resolution_note"), str) or not row["resolution_note"].strip() or not _valid_reference(row.get("resolved_reference"), key):
                    errors.append(f"arbitration_resolution_invalid:{key[0]}:{key[1]}")
                else:
                    resolutions[key] = row["resolved_reference"]
    valid = not errors
    frozen = None
    if valid:
        frozen = {
            "protocol_version": PROTOCOL_VERSION, "tasks_sha256": package["tasks_sha256"],
            "dataset_fingerprint": package.get("dataset_fingerprint"), "review_package_sha256": package_hash,
            "review_record_hashes": {"A": content_hash(reviewer_a), "B": content_hash(reviewer_b), "arbitration": content_hash(arbitration) if disagreements else None},
            "references": [copy.deepcopy(resolutions.get(key, review_maps[0][key])) for key in keys],
        }
    return {
        "pass": valid, "status": "validated_supplied_human_records" if valid else "pending_external_records",
        "errors": errors, "required_slots": len(keys), "disputed_slots": [list(key) for key in disagreements],
        "identity_assurance": "supplied_record_integrity_only",
        "frozen_reference": frozen, "frozen_reference_sha256": content_hash(frozen) if frozen else None,
    }


class FormalStudyBlocked(ValueError):
    """A confirmatory experiment cannot run until its declared gates pass."""


def require_formal_ready(reference_review, *, task_manifest, source_status, budget_status):
    blockers = []
    frozen = reference_review.get("frozen_reference")
    if reference_review.get("pass") is not True or not frozen or reference_review.get("frozen_reference_sha256") != content_hash(frozen):
        blockers.append("validated_human_reference_records_missing")
    if task_manifest.get("status") != "frozen" or not _HASH.fullmatch(str(task_manifest.get("tasks_sha256", ""))):
        blockers.append("tasks_not_frozen")
    elif frozen and frozen.get("tasks_sha256") != task_manifest["tasks_sha256"]:
        blockers.append("reference_task_binding_mismatch")
    if source_status.get("pass") is not True or source_status.get("independent_path_verified") is not True:
        blockers.append("independent_source_path_not_verified")
    if budget_status.get("pass") is not True or budget_status.get("enforcement") != "hard" or budget_status.get("equal_condition_caps") is not True:
        blockers.append("equal_hard_token_caps_not_enforced")
    if blockers:
        raise FormalStudyBlocked("; ".join(blockers))
    return {"pass": True, "status": "formal_ready", "scope": task_manifest.get("event_scope", "unspecified")}


def classify_numeric_claim(claim, reference):
    """Compare one structured numerical assertion without exposing the key."""
    result = _reference_result(reference)
    known = reference.get("answerable") is True
    numeric = claim.get("value") is not None
    detail = {"reported_numeric": numeric, "reference_answerable": known,
              "numeric_error": False, "unit_error": False, "block_error": False,
              "time_error": False, "object_error": False, "unsupported": False, "correct": False}
    if not numeric:
        return detail
    if not known:
        detail["unsupported"] = True
        return detail
    detail["numeric_error"] = not number_equal(claim.get("value"), result.get("value"), exact=result.get("value_kind") == "raw_integer")
    detail["unit_error"] = claim.get("unit") != result.get("unit")
    for field in ("block_number", "block_hash", "block_identifiers"):
        if field in result and not _exact_equal(claim.get(field), result[field]):
            detail["block_error"] = True
    if "target_timestamp_utc" in result and claim.get("target_timestamp_utc") != result["target_timestamp_utc"]:
        detail["time_error"] = True
    expected_query = reference.get("resolved_query", reference.get("query", {}))
    expected_query = expected_query if isinstance(expected_query, dict) else {}
    actual_query = claim.get("query", {})
    actual_query = actual_query if isinstance(actual_query, dict) else {}
    for field in ("coin", "direction", "size"):
        expected = result.get(field, expected_query.get(field))
        actual = claim.get(field, actual_query.get(field))
        if expected is not None and (type(actual) is not type(expected) or actual != expected):
            detail["object_error"] = True
    for field in ("start", "end"):
        if field in expected_query and actual_query.get(field) != expected_query[field]:
            detail["time_error"] = True
    for field in ("metric", "operation"):
        if field in expected_query and actual_query.get(field) != expected_query[field]:
            detail["object_error"] = True
    detail["unsupported"] = claim.get("status") not in ASSERTION_STATUSES
    detail["correct"] = not any(detail[field] for field in ("numeric_error", "unit_error", "block_error", "time_error", "object_error", "unsupported"))
    return detail


def _matches(claim, reference):
    if claim.get("slot_id") is not None:
        return claim.get("slot_id") == reference.get("slot_id")
    return isinstance(claim.get("query"), dict) and claim["query"] == reference.get("resolved_query", reference.get("query"))


def _metric(numerator, denominator, scope):
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None, "scope": scope}


def score_report(report, references, *, execution_status="completed"):
    """Slot completeness and reported-claim errors have different denominators.

    A contradictory duplicate prevents slot completion. Claims outside required
    slots are marked unadjudicated/unsupported in this closed reference scope,
    rather than being asserted false in the world. Free prose is not scored.
    """
    if len({item.get("slot_id") for item in references}) != len(references):
        raise ValueError("Score one task at a time with unique reference slot_ids")
    claims = report.get("claims", []) if isinstance(report, dict) else []
    claims = [item for item in claims if isinstance(item, dict)] if isinstance(claims, list) else []
    report = report if isinstance(report, dict) else {}
    failed = execution_status not in ("completed", "success", "ok")
    details, claim_details, used = [], [], set()
    for reference in references:
        matches = [(index, claim) for index, claim in enumerate(claims) if _matches(claim, reference)]
        used.update(index for index, _ in matches)
        numeric = []
        for index, claim in matches:
            value = classify_numeric_claim(claim, reference)
            if value["reported_numeric"]:
                value.update(claim_index=index, slot_id=reference["slot_id"])
                claim_details.append(value)
                numeric.append(value)
        refusals = [claim for _, claim in matches if claim.get("value") is None and claim.get("status") in REFUSAL_STATUSES]
        correct = bool(not failed and reference["answerable"] and numeric and all(item["correct"] for item in numeric) and not refusals)
        valid_refusal = bool(not failed and not reference["answerable"] and refusals and not numeric and all(has_refusal_reason(claim, report) for claim in refusals))
        if reference["answerable"]:
            state = "correct" if correct else "reported_error" if numeric else "over_refusal" if refusals else "omission"
        else:
            state = "justified_refusal" if valid_refusal else "unsupported_assertion" if numeric else "missing_or_invalid_refusal"
        details.append({"slot_id": reference["slot_id"], "answerable": reference["answerable"], "state": state,
                        "correct": correct, "justified_refusal": valid_refusal, "matching_claims": len(matches),
                        "execution_failed": failed})
    for index, claim in enumerate(claims):
        if index not in used and claim.get("value") is not None:
            detail = classify_numeric_claim(claim, {"answerable": False})
            detail.update(claim_index=index, slot_id=claim.get("slot_id"), unadjudicated_outside_reference=True)
            claim_details.append(detail)
    states = Counter(item["state"] for item in details)
    counts = {"answerable_slots": sum(item["answerable"] for item in references),
              "unanswerable_slots": sum(not item["answerable"] for item in references),
              "correct_answerable_slots": states["correct"], "omitted_answerable_slots": states["omission"],
              "over_refused_answerable_slots": states["over_refusal"], "justified_refusal_slots": states["justified_refusal"],
              "reported_numeric_claims": len(claim_details),
              **{field + "s": sum(item[field] for item in claim_details) for field in ("numeric_error", "unit_error", "block_error", "time_error", "object_error")},
              "unsupported_structured_claims": sum(item["unsupported"] for item in claim_details),
              "reported_claims_with_any_error": sum(not item["correct"] for item in claim_details)}
    metrics = {
        "correct_answerable_coverage": _metric(counts["correct_answerable_slots"], counts["answerable_slots"], "required_answerable_slots"),
        "omission_rate": _metric(counts["omitted_answerable_slots"], counts["answerable_slots"], "required_answerable_slots_no_numeric_assertion_or_explicit_refusal"),
        "over_refusal_rate": _metric(counts["over_refused_answerable_slots"], counts["answerable_slots"], "required_answerable_slots"),
        "justified_refusal_rate": _metric(counts["justified_refusal_slots"], counts["unanswerable_slots"], "required_unanswerable_slots_reason_presence_only"),
        "unsupported_structured_rate": _metric(counts["unsupported_structured_claims"], counts["reported_numeric_claims"], "reported_numeric_claims_closed_reference_scope"),
        "reported_error_rate": _metric(counts["reported_claims_with_any_error"], counts["reported_numeric_claims"], "reported_numeric_claims_any_value_unit_block_time_object_status_error"),
    }
    for dimension in ("numeric", "unit", "block", "time", "object"):
        metrics[f"reported_{dimension}_error_rate"] = _metric(counts[f"{dimension}_errors"], counts["reported_numeric_claims"], "all_reported_numeric_claims_unknown_references_tracked_separately")
    return {"scope": "report.claims_only", "refusal_reason_scope": "presence_only", "failed": failed,
            "counts": counts, "metrics": metrics, "slot_details": details, "claim_details": claim_details}


def score_repair(initial_report, final_report, references, *, initial_status="completed", final_status="completed"):
    initial = score_report(initial_report, references, execution_status=initial_status)
    final = score_report(final_report, references, execution_status=final_status)
    pairs = list(zip(initial["slot_details"], final["slot_details"]))
    eligible = not initial["failed"] and not final["failed"]
    answerable = [(a, b) for a, b in pairs if a["answerable"] and eligible]
    counts = {
        "initial_reported_error_slots": sum(a["state"] == "reported_error" for a, _ in answerable),
        "corrected_reported_error_slots": sum(a["state"] == "reported_error" and b["correct"] for a, b in answerable),
        "initial_omitted_slots": sum(a["state"] == "omission" for a, _ in answerable),
        "missing_values_filled": sum(a["state"] == "omission" and b["correct"] for a, b in answerable),
        "initial_over_refused_slots": sum(a["state"] == "over_refusal" for a, _ in answerable),
        "over_refusals_recovered": sum(a["state"] == "over_refusal" and b["correct"] for a, b in answerable),
        "initial_correct_slots": sum(a["correct"] for a, _ in answerable),
        "correct_answers_lost": sum(a["correct"] and not b["correct"] for a, b in answerable),
        "new_reported_error_slots": sum(a["state"] != "reported_error" and b["state"] == "reported_error" for a, b in answerable),
        "initial_nonerror_answerable_slots": sum(a["state"] != "reported_error" for a, _ in answerable),
        "execution_failure_pairs": int(not eligible),
    }
    return {"scope": "structured_answerable_slots_not_prose_hallucination", "counts": counts, "metrics": {
        "reported_error_correction_rate": _metric(counts["corrected_reported_error_slots"], counts["initial_reported_error_slots"], "initial_reported_error_slots"),
        "omission_fill_rate": _metric(counts["missing_values_filled"], counts["initial_omitted_slots"], "initial_omitted_slots"),
        "over_refusal_recovery_rate": _metric(counts["over_refusals_recovered"], counts["initial_over_refused_slots"], "initial_over_refused_slots"),
        "correct_answer_loss_rate": _metric(counts["correct_answers_lost"], counts["initial_correct_slots"], "initial_correct_slots"),
        "new_reported_error_rate": _metric(counts["new_reported_error_slots"], counts["initial_nonerror_answerable_slots"], "initial_nonerror_answerable_slots"),
    }}


def build_fault_scenarios():
    """A specification, not a claim that these scenarios have been executed."""
    issues = ["value_up", "value_down", "unit_currency", "unit_direction", "block_number", "block_hash",
              "window_shift", "missing_grid_sample", "direction_reversed", "input_size_changed",
              "coherent_balance_archive_tamper", "coherent_quote_archive_tamper"]
    scenarios = []
    for index, issue in enumerate(issues, 1):
        pair = f"P{index:02d}"
        scenarios.extend([
            {"scenario_id": f"BAD{index:02d}", "pair_id": pair, "class": "known_error", "issue": issue, "expected_status": "contradicted", "status": "specification_not_executed"},
            {"scenario_id": f"CLEAN{index:02d}", "pair_id": pair, "class": "clean_control", "issue": None, "expected_status": "supported", "status": "specification_not_executed"},
        ])
    for index, issue in enumerate(("missing_interior_sample", "excluded_quote_target", "empty_permitted_window", "continuous_peak_not_identifiable"), 1):
        scenarios.append({"scenario_id": f"REFUSE{index:02d}", "class": "valid_refusal", "issue": issue, "expected_status": "unsupported", "status": "specification_not_executed"})
    for index, issue in enumerate(("rpc_timeout", "historical_state_unavailable", "source_conflict", "malformed_rpc_response"), 1):
        scenarios.append({"scenario_id": f"UNKNOWN{index:02d}", "class": "source_unknown", "issue": issue, "expected_status": "unverifiable", "status": "specification_not_executed"})
    return scenarios


def classify_verifier_scenario(scenario, observed):
    kind, status = scenario["class"], observed.get("status")
    if kind == "known_error":
        detected = status == "contradicted" and scenario["issue"] in observed.get("detected_issues", [])
        label = "correctly_detected" if detected else "inconclusive" if status == "unverifiable" else "wrong_issue" if status == "contradicted" else "missed_error"
    elif kind == "clean_control":
        label = "correctly_passed" if status == "supported" else "inconclusive" if status == "unverifiable" else "false_positive"
    elif kind == "valid_refusal":
        label = "correct_refusal" if status in REFUSAL_STATUSES and has_refusal_reason(observed, observed) else "invalid_refusal_classification"
    elif kind == "source_unknown":
        label = "correct_unknown" if status == "unverifiable" and observed.get("agent_error") is not True else "source_failure_misattributed"
    else:
        raise ValueError("Unknown verifier scenario class")
    return {"scenario_id": scenario["scenario_id"], "class": kind, "classification": label,
            "pass": label in ("correctly_detected", "correctly_passed", "correct_refusal", "correct_unknown")}
