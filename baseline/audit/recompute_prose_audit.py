"""Rebuild the prose evidence audit from frozen records. Standard library only."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re


# Numerical assertions in S1/S2/S3 order, transcribed from the anonymous prose.
# '-' denotes a scope-limited refusal rather than a numerical claim.
NUMERICAL_ASSERTIONS = """
P01 29.68 2.579 -
P02 29.6796 2.5792 -
P05 33.712746 0.999897263 0.001928
P06 30.878165451717635 1.000037 0.002924
P07 33.7127 0.999897 0.00193
P08 30.878165 1.000036999 0.002924
P09 34.5109 1.1693 -
P10 -18.38 0.3479 1.00122
P12 29.6796 2.5792 -
P13 33.71 0.999897 0.0019
P14 +18.4205 0.27770 0.98978
P15 +18.4205 0.27770 0.98978
P16 -18.38 0.3479 1.00122
P17 18.4205 0.27770 0.9897830738
P18 -18.38 0.3479 1.0012227852
P19 34.51 1.169 -
P20 18.4205 0.2777 0.9897830738
P21 -18.38 0.3479 1.001223
P22 33.7127 0.999897 0.00193
P23 29.6796 2.5792 -
P24 34.51 1.17 -
P25 29.6796 2.5792 -
P26 34.51 1.17 -
P28 33.712746 0.999897263 0.001928
P29 -18.38 0.348 1.001223
P30 34.5109 1.1693 -
P32 30.88 1.000037 0.0029
P34 33.71 0.999897 0.0019
P35 +18.4205 0.27770 0.98978
P36 30.88 1.000037 0.0029
P37 30.8782 1.000037 0.002924
P38 -18.3839 0.3479 1.0012227852
P41 -18.42 0.2777 0.98978
P42 29.68 2.58 -
"""

# Material additional assertions beyond the three task bundles. Direct restatements
# of a bundle are counted once. The decisions below are review annotations.
EXTRAS = [
    ("P05", "about_1bp_discount", "约1bp折价", "supported", "The 1,000 USDC quote is below parity by 1.02737 basis points."),
    ("P05", "liquidity_depth", "流动性仍充裕", "unsupported", "Two quote sizes supply no benchmark for sufficient liquidity."),
    ("P06", "material_dai_shortfall", "DAI 份额明显偏低", "ambiguous", "The share is below one third, but the comparison threshold for ‘materially low’ is unspecified."),
    ("P06", "discount_direction", "USDC 相对 USDT 的轻微折价", "unsupported", "Contradiction. At the quoted size, 1.000036999 USDT per USDC is above one, not below."),
    ("P07", "rough_three_way_balance", "三币大致均衡", "supported", "The three nominal shares at this snapshot are each within one percentage point of one third."),
    ("P07", "liquidity_depth", "流动性深度对 100000 规模仍较充裕", "unsupported", "The two direction-specific quotes give a rate difference but no benchmark for sufficient depth."),
    ("P09", "block_and_observed_time", "快照15928753，观测于11-08T23:59:59Z", "supported", "Frozen block 15928753 has UTC timestamp 2022-11-08T23:59:59Z."),
    ("P09", "sample_as_lower_bound", "小时点只能给出下界采样值", "supported", "The sampled maximum is a lower bound on the maximum over all times in the same interval."),
    ("P10", "token_flow_attribution", "资金显著流出USDC侧", "unsupported", "Share change does not identify transaction flow or its cause."),
    ("P12", "sampling_upper_bound", "采样只能给出离散时点的上下界", "unsupported", "Sampled observations cannot upper-bound an inter-sample peak without a further model."),
    ("P13", "dispersed_structure", "池内结构仍偏分散", "ambiguous", "The frozen nominal shares are available, but the relevant dispersion threshold is undefined."),
    ("P13", "liquidity_depth", "深度充足", "unsupported", "Two single-direction quotes at one instant do not establish adequate depth."),
    ("P13", "material_imbalance", "尚未显现明显失衡", "ambiguous", "The degree of imbalance can be calculated but no threshold for ‘material’ is specified."),
    ("P16", "token_flow_attribution", "资金明显流出USDC侧", "unsupported", "Share change does not identify transaction flow or its cause."),
    ("P16", "market_pressure", "符合USDC承压的方向性解读", "unsupported", "A direction-specific pool quote above one does not substantiate market pressure."),
    ("P18", "token_flow_attribution", "资金从USDC侧流出", "unsupported", "Share change does not identify transaction flow or its cause."),
    ("P20", "token_flow_attribution", "资金向USDC集中", "unsupported", "The frozen observations support higher nominal share, not an attribution to capital movements."),
    ("P21", "token_flow_attribution", "FTX 事件期间资金撤离 USDC 的显著失衡", "unsupported", "The event is a window label. A pool composition change does not identify withdrawals or their causes."),
    ("P22", "slippage_meaning", "几乎无滑点偏离", "ambiguous", "The quote is near parity, but the passage gives no execution or reference-price definition of slippage."),
    ("P22", "liquidity_depth", "该时点池内流动性充足", "unsupported", "The available two-size quote comparison gives no sufficient-depth benchmark."),
    ("P22", "market_calm", "市场处于相对平静状态", "unsupported", "A one-pool snapshot does not measure broader market activity or volatility."),
    ("P23", "peak_direction_meaning", "峰值方向与连续时点仍未确定", "ambiguous", "‘Peak direction’ could refer to the observed increase or the unidentified continuous-time peak."),
    ("P24", "missing_20_to_21", "20:00至21:00Z及21:00至23:00Z存在缺口", "unsupported", "Contradiction for 20:00 to 21:00. Both points are permitted. 22:00 is the excluded point."),
    ("P25", "inside_interval_observation", "小时点只能给出采样间隔内的观测", "ambiguous", "An hourly snapshot is one block observation. The wording leaves observation within the interval unclear."),
    ("P28", "price_deviation_meaning", "池内USDC与USDT价格偏差极小", "ambiguous", "The directional small-size quote is close to one. A two-size rate difference alone does not define a pool mid-price or market-price deviation."),
    ("P32", "slippage_meaning", "大额交易滑点极小", "ambiguous", "The data measure a two-size quote-rate difference. Execution slippage requires a stated reference."),
    ("P34", "three_pool_balance_meaning", "三池接近均衡", "ambiguous", "The observations concern the three coins in one pool. The phrase could be a shorthand or a claim about three pools."),
    ("P34", "slippage_meaning", "滑点极小", "ambiguous", "A directional quote close to one is not itself an execution slippage measure."),
    ("P34", "liquidity_depth_and_curve", "池子流动性深度充足、曲线平缓", "unsupported", "Two same-direction quote sizes do not establish a depth threshold or the shape of the full curve."),
    ("P34", "event_impact", "FTX 事件冲击尚未在 3pool 定价与结构上显现", "unsupported", "A single point supplies neither event attribution nor a suitable pre-event comparison."),
    ("P36", "slippage_meaning", "几乎无滑点", "ambiguous", "A quote near one establishes deviation from parity, not execution slippage without a baseline."),
    ("P36", "liquidity_depth", "USDC/USDT兑换深度充足", "unsupported", "The quote comparison gives no sufficient-depth benchmark."),
    ("P36", "no_depeg_pressure", "尚未显现后续脱锚压力", "unsupported", "A single directional quote and pool composition cannot establish later depeg pressure."),
    ("P37", "pair_balance_meaning", "池内 USDC/USDT 段仍高度均衡", "ambiguous", "Balance could refer to composition or quote parity. The scope and threshold are unstated."),
    ("P37", "no_depeg_pressure", "兑换价格尚未出现显著脱锚压力", "unsupported", "One point and direction do not measure price pressure over time."),
    ("P38", "relative_tightness", "可能反映该时点USDT相对偏紧的池内状态", "unsupported", "The hedge is retained, but the direction-specific quote alone does not identify relative scarcity."),
    ("P41", "token_flow_attribution", "资金快速撤离", "unsupported", "The reported share direction is reversed. The frozen pool state does not identify withdrawal transactions."),
    ("P41", "market_pressure", "低于1显示USDC承压", "unsupported", "The quote is below one at that size and direction. It does not establish market pressure."),
]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utc(timestamp):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def stamp(time):
    return time.isoformat().replace("+00:00", "Z")


def exact_metrics(dataset, tasks):
    """Recompute from raw balances and quotes using exact rational arithmetic."""
    by_time = {row["target_timestamp_utc"]: row for row in dataset["snapshots"]}
    decimals = dataset["decimals"]

    def shares(row):
        units = {
            coin: Fraction(int(value), 10 ** decimals[coin])
            for coin, value in row["raw_balances"].items()
        }
        total = sum(units.values())
        return {coin: value / total for coin, value in units.items()}

    def evaluate(row, query):
        metric = query["metric"]
        if metric == "share_pct":
            return 100 * shares(row)[query["coin"]]
        if metric == "imbalance_l1":
            return sum(abs(v - Fraction(1, 3)) for v in shares(row).values()) / 2
        direction = query["direction"]
        output = direction.split("_")[1]
        quotes = row["raw_quotes"][direction]
        size = query["size"]
        rate = Fraction(int(quotes[str(size)]), size * 10 ** decimals[output])
        if metric == "quote_rate":
            return rate
        assert metric == "size_sensitivity_bps"
        small = Fraction(int(quotes["1000"]), 1000 * 10 ** decimals[output])
        return (small - rate) * 10000 / small

    records = {}
    for task in tasks:
        forbidden = set(task["allowed_evidence"]["excluded_targets"])
        resolved = {}
        for component in task["required_components"]:
            slot = component["slot_id"]
            query = dict(component["request"])
            if "time_from_slot" in query:
                previous = resolved[query.pop("time_from_slot")]["target"]
                point = stamp(utc(previous) + timedelta(hours=query.pop("offset_hours")))
                query.update(start=point, end=point)
            if query["metric"] == "continuous_imbalance_peak":
                result = {"value": None, "evidence_ids": [], "target": None}
            else:
                selected = sorted(
                    t for t in by_time if query["start"] <= t <= query["end"] and t not in forbidden
                )
                values = {t: evaluate(by_time[t], query) for t in selected}
                target = interval = None
                operation = query["operation"]
                if operation == "last":
                    target = selected[-1]
                    value, evidence_times = values[target], [target]
                elif operation == "change":
                    value = values[query["end"]] - values[query["start"]]
                    evidence_times = [query["start"], query["end"]]
                elif operation == "maximum":
                    target = max(values, key=values.get)
                    value, evidence_times = values[target], selected
                else:
                    assert operation == "max_hourly_change"
                    pairs = [
                        (a, b) for a, b in zip(selected, selected[1:])
                        if utc(b) - utc(a) == timedelta(hours=1)
                    ]
                    interval = max(pairs, key=lambda pair: values[pair[1]] - values[pair[0]])
                    value = values[interval[1]] - values[interval[0]]
                    evidence_times = sorted({t for pair in pairs for t in pair})
                result = {
                    "value": value, "target": target, "interval": interval,
                    "evidence_ids": ["E_" + by_time[t]["id"] for t in evidence_times],
                }
            resolved[slot] = result
            records[(task["task_id"], slot)] = result
    return records


def decimal_value(value):
    with localcontext() as context:
        context.prec = 70
        return Decimal(value.numerator) / Decimal(value.denominator)


def quote_in_answer(value, answer):
    """The only normalized prose token is Chinese prose stating a decline."""
    if value in answer:
        return True
    return value.startswith("-") and value[1:] in answer and "下降" in answer


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "ratio": numerator / denominator if denominator else None}


def build(root, output):
    formal = root / "experiments/formal_v1"
    prep = root / "results/current_preparation"
    paths = {
        "anonymous_records": formal / "prose_review_input.json",
        "group_key": formal / "prose_review_key.json",
        "frozen_tasks": prep / "tasks.json",
        "frozen_snapshots": prep / "dataset.json",
        "frozen_references": prep / "references.automatic.private.json",
    }
    packet, key, tasks, data, refs = [read_json(path) for path in paths.values()]
    assert len(packet) == len(key) == 42 and {r["review_id"] for r in packet} == set(key)
    exact = exact_metrics(data, tasks)
    assert len(exact) == len(refs) == 18
    for ref in refs:
        result = exact[(ref["task_id"], ref["slot_id"])]
        if ref["answerable"]:
            tolerance = Decimal("0.00000001")
            assert abs(decimal_value(result["value"]) - Decimal(ref["result"]["value"])) <= tolerance
            assert result["evidence_ids"] == ref["result"]["evidence_ids"]
            assert result["target"] == ref["result"].get("target_timestamp_utc")
            if result["interval"]:
                assert result["interval"] == (ref["result"]["interval_start"], ref["result"]["interval_end"])
        else:
            assert result["value"] is None and ref["reason"] == "sampling_resolution_insufficient"

    transcribed = {parts[0]: parts[1:] for line in NUMERICAL_ASSERTIONS.strip().splitlines()
                   if (parts := line.split())}
    extras_by_report = {}
    for review_id, code, excerpt, verdict, reason in EXTRAS:
        extras_by_report.setdefault(review_id, []).append({
            "assertion_id": code, "excerpt": excerpt, "support": verdict, "basis": reason,
        })

    # Verify decisions about explicit quote signs, sampling gaps and block time.
    for task_id, expected_sign in [("R01", 1), ("R02", -1), ("R04", -1), ("R05", 1)]:
        quote_slot = "S2" if task_id in ("R01", "R04") else "S3"
        rate = exact[task_id, quote_slot]["value"]
        assert (rate - 1) * expected_sign > 0
    assert (1 - exact["R04", "S2"]["value"]) * 10000 == Fraction(102737, 100000)
    points = {row["target_timestamp_utc"]: row for row in data["snapshots"]}
    block = points["2022-11-09T00:00:00Z"]
    assert block["block_number"] == 15928753 and block["timestamp_utc"] == "2022-11-08T23:59:59Z"
    assert {"E_UTC_20221109T200000Z", "E_UTC_20221109T210000Z"} <= set(exact["R06", "S2"]["evidence_ids"])
    assert "E_UTC_20221109T220000Z" not in exact["R06", "S2"]["evidence_ids"]

    records = []
    for item in packet:
        review_id, task_id, answer = item["review_id"], item["task_id"], item["answer"]
        run_id = key[review_id]
        group = "T" if run_id.endswith("_T") else run_id.split("_")[-2]
        assert group in {"T", "G", "E"}
        record = {
            "review_id": review_id, "task_id": task_id, "condition": group,
            "scope": "template_stub" if group == "T" else "submitted_prose" if answer.strip() else "budget_stop",
            "task_bundles": [], "additional_assertions": extras_by_report.get(review_id, []),
        }
        if record["scope"] != "submitted_prose":
            assert review_id not in transcribed and not record["additional_assertions"]
            records.append(record)
            continue

        assert review_id in transcribed
        structured = {claim["slot_id"]: claim for claim in item["claims"]}
        for slot_num, reported in enumerate(transcribed[review_id], 1):
            slot = f"S{slot_num}"
            result = exact[task_id, slot]
            if reported == "-":
                assert result["value"] is None
                assert re.search("小时.*采样|采样.*小时", answer) and "峰值" in answer
                bundle = {
                    "slot": slot, "type": "evidence_limit_refusal", "support": "supported",
                    "basis": "An hourly grid does not identify the continuous-time maximum.",
                }
            else:
                assert result["value"] is not None and quote_in_answer(reported, answer), (review_id, slot)
                claimed = Decimal(reported)
                ulp = Decimal(1).scaleb(claimed.as_tuple().exponent)
                wanted = decimal_value(result["value"])
                matches = abs(wanted - claimed) <= ulp / 2
                required = set(result["evidence_ids"])
                cited = set(structured[slot].get("evidence_ids") or [])
                bundle = {
                    "slot": slot, "type": "numerical", "prose_value": reported,
                    "support": "supported" if matches else "unsupported",
                    "basis": "Prose value, scope, direction and unit agree with the frozen task at reported precision."
                    if matches else "Contradiction. Prose states that USDC share fell from about 31% to 12.6%, but the frozen endpoint share change is positive.",
                    "recomputed_value": str(wanted), "target_utc": result["target"],
                    "comparison_interval_utc": result["interval"],
                    "required_evidence_ids": sorted(required), "original_cited_evidence_ids": sorted(cited),
                    "missing_required_evidence_ids": sorted(required - cited),
                    "original_citation_coverage": "complete" if required <= cited else "incomplete",
                }
            record["task_bundles"].append(bundle)
        for extra in record["additional_assertions"]:
            assert extra["excerpt"] in answer, (review_id, extra["excerpt"])
        records.append(record)

    assert set(transcribed) == {r["review_id"] for r in records if r["scope"] == "submitted_prose"}
    assert Counter(r["scope"] for r in records) == {
        "submitted_prose": 34, "template_stub": 6, "budget_stop": 2,
    }

    def group_summary(selected):
        prose = [r for r in selected if r["scope"] == "submitted_prose"]
        bundles = [b for r in prose for b in r["task_bundles"]]
        numeric = [b for b in bundles if b["type"] == "numerical"]
        extra = [a for r in prose for a in r["additional_assertions"]]
        categories = Counter(a["support"] for a in extra)
        unsupported_reports = sum(
            any(a["support"] == "unsupported" for a in r["task_bundles"] + r["additional_assertions"])
            for r in prose
        )
        return {
            "scheduled_records": len(selected),
            "submitted_prose": len(prose),
            "template_stubs_excluded_from_prose_denominators": sum(r["scope"] == "template_stub" for r in selected),
            "budget_stops_excluded_from_prose_denominators": sum(r["scope"] == "budget_stop" for r in selected),
            "numerical_prose_matches": ratio(sum(b["support"] == "supported" for b in numeric), len(numeric)),
            "numerical_bundles_with_incomplete_original_citations": ratio(
                sum(b["original_citation_coverage"] == "incomplete" for b in numeric), len(numeric)
            ),
            "supported_evidence_limit_refusals_in_prose": ratio(
                sum(b["type"] == "evidence_limit_refusal" and b["support"] == "supported" for b in bundles),
                len(bundles) - len(numeric)
            ),
            "additional_assertions": {k: categories[k] for k in ("supported", "unsupported", "ambiguous")},
            "reports_with_at_least_one_unsupported_prose_assertion": ratio(unsupported_reports, len(prose)),
        }

    summary = {group: group_summary([r for r in records if r["condition"] == group])
               for group in ("G", "E", "T")}
    summary["all"] = group_summary(records)
    sources = {label: {"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for label, path in paths.items()}
    write_json(output / "prose_audit_rows.json", records)
    write_json(output / "prose_audit_summary.json", {
        "sources": sources,
        "scope": "Submitted prose, with numerical data agreement and original citation coverage recorded separately.",
        "method": "Initial rule-based review with exact frozen-data recomputation. The later annotation and adjudication workbooks are documented separately in audit/prose_review_20261110/REVIEW.md.",
        "by_condition": summary,
    })
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print(json.dumps(build(args.project_root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
