"""Frozen multi-step investigations; model inputs use an explicit allowlist.

Private recipes provide evaluation slots, not executable model plans. They are
never copied into public/model task files. Synthetic deletions are confined to
in-memory copies, while the original historical archive remains unchanged.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .common import canonical, now_utc
from . import metrics
from .experiment import apply_task_dataset as _legacy_fixture, _hourly_times

TASK_VERSION = "curve-audit-investigations-v2.0"
CONDITIONS = ("ordinary_agent", "links_only_agent", "self_review_agent", "evidence_grounded_agent")


def public_task(task: dict) -> dict:
    """Only user-visible requests cross the inference boundary."""
    return {key: copy.deepcopy(task[key]) for key in ("task_id", "question", "window", "draft") if key in task}


def apply_task_dataset(dataset: dict, task: dict) -> dict:
    return _legacy_fixture(dataset, task)


def _query(metric, operation, start, end, **kwargs):
    return metrics.normalize_query({"metric": metric, "operation": operation, "start": start, "end": end, **kwargs})


def _shift(timestamp, hours):
    return (datetime.fromisoformat(timestamp.replace("Z", "+00:00")) + timedelta(hours=hours)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _task(identifier, family, start, end, question, queries, *, fixture=None, narrative=None, draft=None,
          conflicting=(), dependencies=None, split="test"):
    private = {"recipes": queries, "expected_narratives": narrative or [],
               "dependencies": dependencies or [], "minimum_tool_steps": len(queries)}
    if fixture:
        private["fixture"] = fixture
    task = {"task_id": identifier, "split": split, "family": family, "question": question,
            "window": {"start": start, "end": end}, "_private": private}
    if draft is not None:
        task["draft"] = draft
    private["conflicting_slots"] = list(conflicting)
    return task


def _resolve(dataset, task):
    fixture = apply_task_dataset(dataset, task)
    expected = []
    for index, query in enumerate(task["_private"]["recipes"], 1):
        slot_id = f"S{index}"
        item = {"slot_id": slot_id, "query": query,
                "required_status": "conflicting" if slot_id in task["_private"].get("conflicting_slots", []) else "supported"}
        try:
            item.update(answerable=True, result=metrics.calculate(fixture, query))
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            item.update(answerable=False, result=None, error=str(exc), required_status="unsupported")
        expected.append(item)
    task["_private"]["expected_claims"] = expected
    return task


def _multi(dataset, identifier, times, variant, *, split="test"):
    # Four genuinely different comparisons: asset composition, quote direction,
    # scale response, and sampled imbalance with endpoint context.
    offset = 12 + (variant % 4) * 24
    a, b, c, d = [times[index] for index in (offset, offset + 6, offset + 12, offset + 18)]
    mode = variant % 4
    if mode == 0:
        coin = "USDC" if variant < 4 else "DAI"
        queries = [_query("share_pct", "change", a, b, coin=coin),
                   _query("share_pct", "change", c, d, coin=coin),
                   _query("imbalance_l1", "maximum", a, d)]
        question = (f"比较 {a} 至 {b} 与 {c} 至 {d} 两段中 {coin} 名义代币占比的末减初变化，分别给出百分点，"
                    f"再给出完整 {a} 至 {d} 的采样失衡指标 imbalance_l1 最大值及其采样时点。解释这种对比能支持什么观察，避免因果推断。")
    elif mode == 1:
        direction = "USDC_USDT" if variant < 4 else "USDT_USDC"
        pretty = direction.replace("_", "→")
        queries = [_query("quote_rate", "first", a, b, direction=direction, size=1000),
                   _query("quote_rate", "last", a, b, direction=direction, size=100000),
                   _query("quote_rate", "change", c, d, direction=direction, size=10000),
                   _query("size_sensitivity_bps", "maximum", a, d, direction=direction, size=100000)]
        question = (f"调查 {pretty} 的不同规模与窗口：给出 {a} 至 {b} 首个采样点的 1000 输入报价、末个采样点的 100000 输入报价；"
                    f"给出 {c} 至 {d} 的 10000 输入报价末减初变化；最后给出 {a} 至 {d} 的 100000 相对 1000 输入规模敏感度采样最大值。各项保留方向和单位，不能把合约报价说成成交。")
    elif mode == 2:
        direction = "USDC_USDT" if variant < 4 else "USDT_USDC"
        pretty = direction.replace("_", "→")
        queries = [_query("size_sensitivity_bps", "first", a, b, direction=direction, size=10000),
                   _query("size_sensitivity_bps", "last", a, b, direction=direction, size=100000),
                   _query("size_sensitivity_bps", "change", c, d, direction=direction, size=100000)]
        question = (f"对 {pretty} 做规模调查：分别报告 {a} 至 {b} 首个采样点 10000 相对 1000 输入的规模敏感度，"
                    f"末个采样点 100000 相对 1000 的敏感度，以及 {c} 至 {d} 的 100000 相对 1000 敏感度末减初变化。每项单位为 basis_points，分别解释时间变化与规模对比。")
    else:
        coin = "USDT" if variant < 4 else "USDC"
        queries = [_query("imbalance_l1", "first", a, b), _query("imbalance_l1", "last", c, d),
                   _query("share_pct", "maximum", a, d, coin=coin),
                   _query("quote_rate", "last", c, d, direction="USDC_USDT", size=100000)]
        question = (f"给出早段 {a} 至 {b} 的首个采样失衡指标、晚段 {c} 至 {d} 的末个采样失衡指标；"
                    f"定位 {a} 至 {d} 内 {coin} 占比采样最大值；再给出晚段末个采样点 USDC→USDT、100000 输入报价。将四项联合用于描述池状态，别把名义占比解释成美元资产权重。")
    return _resolve(dataset, _task(identifier, "cross_window_scale", a, d, question, queries, split=split))


def _peak(dataset, identifier, times, variant, *, split="test"):
    offset = 12 + (variant % 4) * 24
    a, b = times[offset], times[offset + 18]
    mode = variant % 4
    if mode == 0:
        peak = _query("imbalance_l1", "maximum", a, b)
        description = "失衡指标 imbalance_l1"
    elif mode == 1:
        peak = _query("share_pct", "maximum", a, b, coin="USDC")
        description = "USDC 名义代币占比"
    elif mode == 2:
        peak = _query("size_sensitivity_bps", "maximum", a, b, direction="USDC_USDT", size=100000)
        description = "USDC→USDT、100000 相对 1000 输入的规模敏感度"
    else:
        peak = _query("size_sensitivity_bps", "maximum", a, b, direction="USDT_USDC", size=100000)
        description = "USDT→USDC、100000 相对 1000 输入的规模敏感度"
    result = metrics.calculate(dataset, peak)
    timestamp = result["target_timestamp_utc"]
    prior, after = _shift(timestamp, -1), _shift(timestamp, 1)
    direction = "USDC_USDT" if variant < 4 else "USDT_USDC"
    pretty = direction.replace("_", "→")
    queries = [peak] + [_query("quote_rate", "last", t, t, direction=direction, size=100000)
                        for t in (prior, timestamp, after)]
    queries.append(_query("share_pct", "last", timestamp, timestamp, coin="USDC"))
    question = (f"先在 {a} 至 {b} 内定位 {description} 的采样最大值及目标采样时点。取得时点后，再查询该时点前一小时、"
                f"该时点及后一小时的 {pretty}、100000 输入报价，并查询该峰值时点 USDC 名义占比。"
                "必须先定位再按实际返回时点展开，不能预设峰值日期；给出五项数值并说明小时采样的限制。")
    dependencies = [{"slot_id": f"S{i}", "after_slot_id": "S1", "rule": "query_time_from_peak_target", "offset_hours": offset}
                    for i, offset in zip(range(2, 6), (-1, 0, 1, 0))]
    return _resolve(dataset, _task(identifier, "locate_then_investigate", times[offset - 1], times[offset + 19],
                                 question, queries, dependencies=dependencies, split=split))


def _partial(dataset, identifier, times, variant, *, split="test"):
    offset = 12 + (variant % 4) * 24
    a, b, c, missing, d = [times[index] for index in (offset, offset + 3, offset + 7, offset + 10, offset + 14)]
    direction = "USDC_USDT" if variant % 2 == 0 else "USDT_USDC"
    coin = ("USDC", "DAI", "USDT")[variant % 3]
    queries = [_query("share_pct", "change", a, b, coin=coin),
               _query("quote_rate", "last", a, b, direction=direction, size=100000),
               _query("share_pct", "change", c, d, coin=coin),
               _query("quote_rate", "maximum", c, d, direction=direction, size=10000)]
    question = (f"使用当前提供的数据，分别调查完整早段 {a} 至 {b} 和晚段 {c} 至 {d}：报告早段 {coin} 占比末减初变化、"
                f"早段末个采样点 {direction.replace('_', '→')} 的 100000 输入报价，以及晚段 {coin} 占比末减初变化、"
                f"晚段同方向 10000 输入报价的采样最大值。检查每一段证据是否完整；若某段缺样，只拒答该段受影响的两项，仍保留可计算的两项，禁止插值或把缺失值当零。")
    return _resolve(dataset, _task(identifier, "partial_evidence", a, d, question, queries,
                                  fixture={"kind": "remove_snapshot", "target": missing}, split=split))


def _draft(dataset, identifier, times, variant, *, split="test"):
    offset = 12 + (variant % 4) * 24
    a, b = times[offset], times[offset + 12]
    direction = "USDC_USDT" if variant % 2 == 0 else "USDT_USDC"
    size = 100000 if variant >= 4 else 10000
    q1 = _query("quote_rate", "last", a, b, direction=direction, size=size)
    q2 = _query("share_pct", "change", a, b, coin="USDC")
    q3 = _query("imbalance_l1", "maximum", a, b)
    actual = metrics.calculate(dataset, q1)
    pretty = direction.replace("_", "→")
    narratives = []
    if variant in (0, 4):
        wrong = str(Decimal(actual["value"]) + Decimal("0.031"))
        draft = {"text": f"晚段末个采样点 {pretty}、{size} 输入报价是 {wrong} {actual['unit']}，且与数据完全一致。"}
        narratives.append({"slot_id": "N1", "kind": "draft_consistency", "expected_stance": "contradicted"})
        conflicting = ["S1"]
    elif variant in (1, 5):
        draft = {"text": f"末个采样点 {pretty}、{size} 输入报价是 {actual['value']} USD；这个美元单位没有问题。"}
        narratives.append({"slot_id": "N1", "kind": "draft_consistency", "expected_stance": "contradicted"})
        conflicting = ["S1"]
    elif variant == 2:
        draft = {"text": "USDC 占比变化证明所有资金变化都由恐慌抛售直接造成，池状态已经充分证明这条因果链。"}
        narratives.append({"slot_id": "N1", "kind": "causal_attribution", "expected_stance": "unsupported"})
        conflicting = []
    elif variant == 3:
        draft = {"text": "从该池的名义占比和报价，可以确认发行人储备充足且 USDC 绝不会失去偿付能力。"}
        narratives.append({"slot_id": "N1", "kind": "issuer_solvency", "expected_stance": "unsupported"})
        conflicting = []
    else:
        draft = {"text": "小时采样失衡最大值就是整个连续时间区间的真实最高值，不可能存在未采到的更高峰。"}
        narratives.append({"slot_id": "N1", "kind": "continuous_peak", "expected_stance": "unsupported"})
        conflicting = []
    question = (f"核查所给草稿，并提供 {a} 至 {b} 末个采样点 {pretty}、{size} 输入报价、USDC 名义占比末减初变化，"
                "以及该段失衡指标 imbalance_l1 的采样最大值。逐项注明单位，保留正确可答信息；指出草稿的数值、单位或证据边界问题，"
                "正文与解释不能一面纠正一面声称草稿完全一致。另用 narrative_claims 给出对应判断及解释。")
    return _resolve(dataset, _task(identifier, "draft_and_boundary_conflict", a, b, question, [q1, q2, q3],
                                  narrative=narratives, draft=draft, conflicting=conflicting, split=split))


def build_development_tasks(dataset: dict) -> list[dict]:
    """Two examples for interface debugging, never counted as formal test cases."""
    times = _hourly_times(dataset)
    if len(times) < 127:
        raise ValueError("V2 investigation development requires at least 127 hourly observations")
    return [_peak(dataset, "DEV2_01", times[6:], 0, split="development"),
            _partial(dataset, "DEV2_02", times[6:], 1, split="development")]


def build_formal_tasks(dataset: dict) -> list[dict]:
    times = _hourly_times(dataset)
    if len(times) < 127:
        raise ValueError("V2 investigation freeze requires at least 127 hourly observations")
    factories = [(_multi, 8), (_peak, 8), (_partial, 7), (_draft, 7)]
    tasks = []
    for factory, count in factories:
        for variant in range(count):
            tasks.append(factory(dataset, f"I{len(tasks) + 1:02d}", times, variant))
    return tasks


def freeze_investigation_tasks(dataset: dict, outdir) -> list[dict]:
    """Write an immutable 30-task key. Reuse is permitted only for exact content."""
    destination = Path(outdir)
    destination.mkdir(parents=True, exist_ok=True)
    tasks = build_formal_tasks(dataset)
    public = [public_task(task) for task in tasks]
    private = {"version": TASK_VERSION, "tasks": tasks}
    dataset_hash = hashlib.sha256(canonical({k: v for k, v in dataset.items() if not k.startswith("_")}).encode()).hexdigest()
    artifacts = {"tasks.public.json": public, "tasks.private.json": private}
    for name, content in artifacts.items():
        path = destination / name
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != content:
            raise FileExistsError(f"Frozen content differs: {path}; choose a new version directory")
    for name, content in artifacts.items():
        path = destination / name
        if not path.exists():
            path.write_text(json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest_path = destination / "freeze_manifest.json"
    if manifest_path.exists():
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous_manifest.get("dataset_sha256") != dataset_hash or previous_manifest.get("version") != TASK_VERSION:
            raise FileExistsError(f"Frozen dataset/version differs: {manifest_path}")
    if not manifest_path.exists():
        manifest = {"version": TASK_VERSION, "created_at_utc": now_utc(), "dataset_sha256": dataset_hash,
                    "public_tasks_sha256": hashlib.sha256(canonical(public).encode()).hexdigest(),
                    "private_tasks_sha256": hashlib.sha256(canonical(private).encode()).hexdigest(),
                    "formal_tasks": len(tasks), "family_counts": {family: sum(t["family"] == family for t in tasks) for family in sorted({t["family"] for t in tasks})},
                    "required_numeric_or_refusal_slots": sum(len(t["_private"]["expected_claims"]) for t in tasks),
                    "expected_narrative_slots": sum(len(t["_private"]["expected_narratives"]) for t in tasks),
                    "split_notice": "New task formulations on the previously used event, not an unseen-event evaluation. Development examples debug interfaces only. Formal tasks remain unchanged after first formal inference.",
                    "synthetic_notice": "Partial-evidence tasks delete one hourly snapshot in an isolated fixture; these are not newly observed historical gaps.",
                    "freeze_policy": "No overwrite or result-driven task selection. Prompt/budget changes after formal inference require a declared new experiment version."}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return tasks


def planned_run_manifest(tasks: list[dict], repetitions=1, conditions=CONDITIONS):
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise ValueError("repetitions must be a positive integer")
    return [{"output_id": f"{task['task_id']}__{condition}__r{repetition}", "task_id": task["task_id"],
             "condition": condition, "repetition": repetition}
            for task in tasks if task.get("split") == "test" for condition in conditions
            for repetition in range(1, repetitions + 1)]
