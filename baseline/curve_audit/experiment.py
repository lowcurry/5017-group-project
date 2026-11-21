"""Frozen investigation tasks and a deterministic, fully informed baseline.

The evaluator-only ``_private`` field contains fixture controls and answer keys.
Always pass ``public_task(task)`` to an LLM. Public task files never contain keys.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import metrics

TASK_VERSION = "curve-audit-frozen-v1"
CONDITIONS = ("fixed_template", "ordinary_agent", "evidence_grounded_agent")


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def public_task(task):
    """Explicit allowlist: never give labels, fixture controls or answers to models."""
    return {key: copy.deepcopy(task[key]) for key in
            ("task_id", "question", "query", "draft") if key in task}


def _time(snapshot):
    return snapshot.get("target_timestamp_utc", snapshot.get("timestamp_utc"))


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hourly_times(dataset):
    """Use the longest continuous hourly series; aliases are the same observation."""
    snapshots = dataset.get("snapshots", [])
    hourly = [s for s in snapshots if s.get("series") == "hourly"]
    times = sorted({_time(s) for s in (hourly or snapshots)})
    chunks = []
    for value in times:
        if not chunks or _dt(value) - _dt(chunks[-1][-1]) != timedelta(hours=1):
            chunks.append([])
        chunks[-1].append(value)
    result = max(chunks, key=len, default=[])
    if len(result) < 13:
        raise ValueError("Freeze requires at least 13 consecutive hourly target snapshots")
    return result


def apply_task_dataset(dataset, task):
    """Return an isolated fixture; the archive itself is never changed.

    Missing records are real absences in the fixture, not secret fault labels for
    the model. Synthetic provenance is saved for audit outside model inputs.
    """
    result = copy.deepcopy(dataset)
    mutation = task.get("_private", {}).get("fixture", {})
    if not mutation:
        return result
    kind = mutation["kind"]
    targets = [s for s in result["snapshots"] if _time(s) == mutation["target"]]
    block_hashes = {s.get("block_hash") for s in targets}
    selected = [s for s in result["snapshots"]
                if _time(s) == mutation["target"] or s.get("block_hash") in block_hashes]
    if kind == "remove_snapshot":
        result["snapshots"] = [s for s in result["snapshots"] if s not in selected]
    elif kind == "remove_quote":
        for snapshot in selected:
            for field in ("raw_quotes", "quotes"):
                snapshot.get(field, {}).get(mutation["direction"], {}).pop(str(mutation["size"]), None)
    elif kind == "remove_balance":
        for snapshot in selected:
            for field in ("raw_balances", "balances"):
                snapshot.get(field, {}).pop(mutation["coin"], None)
    else:
        raise ValueError("Unknown evaluator fixture control")
    result["fixture_status"] = "synthetic_challenge_fixture"
    result["_synthetic_fixture"] = {
        "synthetic": True, "base_sha256": _digest(dataset),
        "task_id": task["task_id"], "control": copy.deepcopy(mutation),
        "notice": "Deleted observations for an evaluation fixture; never historical evidence.",
    }
    return result


def _draft_disagrees(draft, result):
    if not draft:
        return False
    if draft.get("unit") != result.get("unit"):
        return True
    try:
        return abs(Decimal(str(draft["value"])) - Decimal(str(result["value"]))) > Decimal("1e-8")
    except (InvalidOperation, KeyError, TypeError):
        return True


def fixed_template(dataset, task):
    """Strong baseline: uses ordinary tools and handles missing/conflicting input.

    This function does not inspect evaluator private fields or expected answers.
    It must receive the already-selected task dataset, just as the Agents do.
    """
    clean = public_task(task)
    try:
        result = metrics.calculate(dataset, clean["query"])
    except (ValueError, KeyError, TypeError) as exc:
        reason = "现有资料无法支持此查询：" + str(exc)
        return {"task_id": clean["task_id"], "answer": reason,
                "claims": [{"claim_id": "C1", "query": clean["query"],
                            "value": None, "unit": None, "status": "unsupported",
                            "evidence_ids": [], "calculation_id": None,
                            "explanation": reason}], "refusal_reason": reason, "faults": []}
    conflict = _draft_disagrees(clean.get("draft"), result)
    explanation = "按冻结资料和确定性公式计算；最大值仅指采样时点。"
    if conflict:
        explanation += "所给草稿与计算结果不一致，应以可复算结果为准。"
    claim = {"claim_id": "C1", "query": copy.deepcopy(clean["query"]),
             "value": result["value"], "unit": result["unit"],
             "status": "conflicting" if conflict else "supported",
             "evidence_ids": result["evidence_ids"],
             "calculation_id": result["calculation_id"], "explanation": explanation}
    return {"task_id": clean["task_id"], "answer":
            f"{result['value']} {result['unit']}。{explanation}",
            "claims": [claim], "refusal_reason": None,
            "faults": ([{"type": "draft_conflict", "claim_id": "C1",
                         "explanation": "用户提供的草稿数值或单位与确定性计算不符。"}]
                       if conflict else [])}


def _expected(dataset, task):
    try:
        result = metrics.calculate(apply_task_dataset(dataset, task), task["query"])
    except (ValueError, KeyError, TypeError) as exc:
        return {"answerable": False, "required_status": "unsupported",
                "required_numeric_slots": 0, "reason": str(exc), "result": None}
    return {"answerable": True, "required_status":
            "conflicting" if _draft_disagrees(task.get("draft"), result) else "supported",
            "required_numeric_slots": 1, "result": result}


def _question(query):
    metric = query["metric"]
    labels = {"share_pct": f"{query.get('coin')} 名义数量占比",
              "quote_rate": f"{query.get('direction')}、输入 {query.get('size')} 的报价汇率",
              "size_sensitivity_bps": f"{query.get('direction')}、输入 {query.get('size')} 相对 1,000 的规模敏感性",
              "imbalance_l1": "池组成失衡 L1 指标"}
    operations = {"first": "首个采样时点的值", "last": "最后采样时点的值",
                  "change": "末值减首值的变化", "maximum": "采样时点中的最大值"}
    return (f"请计算 {query['start']} 至 {query['end']} 的 {labels[metric]}："
            f"{operations[query['operation']]}。报告数值、单位及证据；资料不足时明确说明。")


def freeze_tasks(dataset, outdir):
    """Freeze six development and 24 test tasks, preserving all existing versions.

    The historical groups share one event; held-out windows reduce prompt tuning
    overlap but do not make these 24 tasks independent market events.
    """
    times = _hourly_times(dataset)
    dev_end = max(4, len(times) // 4)
    dev_times, heldout = times[:dev_end], times[dev_end:]
    tasks = []

    def add(split, group, family, query, fixture=None, draft=None):
        prefix = "D" if split == "dev" else "T"
        count = sum(t["split"] == split for t in tasks) + 1
        task = {"task_id": f"{prefix}{count:02d}", "split": split, "group": group,
                "family": family, "question": _question(query), "query": query,
                "_private": {"fixture": fixture or {}}}
        if draft is not None:
            task["draft"] = draft
            task["question"] += "请同时核查附带草稿，指出任何不一致，并给出计算结果。"
        task["_private"]["expected"] = _expected(dataset, task)
        tasks.append(task)
        return task

    def query(metric, operation, start, end=None, **params):
        return {"metric": metric, "operation": operation, "start": start,
                "end": end or start, **params}

    add("dev", "historical", "composition", query("share_pct", "first", dev_times[0], coin="USDC"))
    add("dev", "historical", "composition", query("share_pct", "change", dev_times[0], dev_times[-1], coin="DAI"))
    add("dev", "historical", "quotes", query("quote_rate", "last", dev_times[-1], direction="USDC_USDT", size=1000))
    add("dev", "historical", "size", query("size_sensitivity_bps", "first", dev_times[1], direction="USDT_USDC", size=100000))
    add("dev", "historical", "windowmax", query("imbalance_l1", "maximum", dev_times[0], dev_times[-1]))
    dev_q = query("quote_rate", "first", dev_times[2], direction="USDC_USDT", size=10000)
    dev_r = metrics.calculate(dataset, dev_q)
    add("dev", "challenge", "draft_check", dev_q,
        draft={"value": str(Decimal(dev_r["value"]) + 1), "unit": dev_r["unit"]})

    # Four held-out windows; no dev query or target timestamp is reused.
    windows = []
    for index in range(4):
        lo = index * len(heldout) // 4
        hi = (index + 1) * len(heldout) // 4 - 1
        windows.append((heldout[lo], heldout[max(lo, hi)]))
    for index, (start, end) in enumerate(windows):
        add("test", "historical", "composition", query("share_pct", "change", start, end,
                                                        coin=("DAI", "USDC", "USDT", "USDC")[index]))
    for index, (start, end) in enumerate(windows):
        add("test", "historical", "quotes", query("quote_rate", ("first", "last", "change", "last")[index], start, end,
                                                   direction=("USDC_USDT", "USDT_USDC")[index % 2],
                                                   size=(1000, 10000, 100000, 10000)[index]))
    for index, (start, end) in enumerate(windows):
        add("test", "historical", "size", query("size_sensitivity_bps", ("first", "last", "change", "maximum")[index], start, end,
                                                 direction=("USDT_USDC", "USDC_USDT")[index % 2], size=100000))
    for index, (start, end) in enumerate(windows):
        add("test", "historical", "windowmax", query(("share_pct", "imbalance_l1", "share_pct", "imbalance_l1")[index],
                                                      "maximum", start, end,
                                                      **({"coin": ("USDC", "", "USDT", "")[index]} if index % 2 == 0 else {})))

    for index, direction in enumerate(("USDC_USDT", "USDT_USDC")):
        target = heldout[index + 1]
        q = query("quote_rate", "first", target, direction=direction, size=10000)
        add("test", "challenge", "missing_data", q,
            fixture={"kind": "remove_quote", "target": target, "direction": direction, "size": 10000})
    target = heldout[3]
    add("test", "challenge", "missing_data", query("share_pct", "first", target, coin="USDC"),
        fixture={"kind": "remove_balance", "target": target, "coin": "USDC"})
    target = heldout[4]
    add("test", "challenge", "missing_data", query("imbalance_l1", "first", target),
        fixture={"kind": "remove_snapshot", "target": target})
    outside = (_iso(_dt(min(_time(s) for s in dataset["snapshots"])) - timedelta(days=1)),
               _iso(_dt(max(_time(s) for s in dataset["snapshots"])) + timedelta(days=1)))
    for target in outside:
        add("test", "challenge", "range", query("share_pct", "first", target, coin="USDC"))
    for index, (start, end) in enumerate(windows[-2:]):
        q = query("quote_rate", "last", start, end, direction="USDC_USDT", size=100000)
        r = metrics.calculate(dataset, q)
        draft = {"value": str(Decimal(r["value"]) + Decimal("0.03")), "unit": r["unit"]} if index == 0 else {
            "value": r["value"], "unit": "USD"}
        add("test", "challenge", "draft_check", q, draft=draft)

    public = [public_task(task) for task in tasks]
    manifest = {"task_version": TASK_VERSION, "dataset_sha256": _digest(dataset),
                "private_tasks_sha256": _digest(tasks), "public_tasks_sha256": _digest(public),
                "counts": {"dev": 6, "test": 24, "test_historical": 16, "test_challenge": 8},
                "split_policy": "Disjoint development/test target windows; related templates and one event.",
                "independence_notice": "Tasks and repeats are not independent historical events.",
                "freeze_policy": "An existing different dataset/task version is never silently overwritten."}
    destination = Path(outdir)
    destination.mkdir(parents=True, exist_ok=True)
    artifacts = {"manifest.json": manifest, "tasks.public.json": public, "tasks.private.json": tasks}
    for name, content in artifacts.items():
        path = destination / name
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != content:
            raise FileExistsError(f"Frozen artifact differs: {path}; choose a new version directory")
    for name, content in artifacts.items():
        path = destination / name
        if not path.exists():
            path.write_text(json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return tasks


def planned_run_manifest(tasks, repetitions=3, include_ablation=False):
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    manifest = []
    for task in tasks:
        if task.get("split") != "test":
            continue
        for condition in CONDITIONS + (("links_only_agent",) if include_ablation else ()):
            for repetition in range(1, (1 if condition == "fixed_template" else repetitions) + 1):
                manifest.append({"output_id": f"{task['task_id']}__{condition}__r{repetition}",
                                 "task_id": task["task_id"], "condition": condition,
                                 "repetition": repetition})
    return manifest
