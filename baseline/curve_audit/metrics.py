"""Deterministic nominal-liquidity calculations, using raw uint256 values only."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, localcontext

from .data import COINS, DECIMALS, DIRECTIONS, QUOTE_SIZES, DatasetError, decimal_text, parse_utc, raw_decimal, select_snapshots, utc_text

METRICS_VERSION = "1.0"


def snapshot_metrics(snapshot: dict) -> dict:
    with localcontext() as context:
        context.prec = 80
        balances = {coin: raw_decimal(snapshot["raw_balances"][coin], DECIMALS[coin]) for coin in COINS}
        total = sum(balances.values(), Decimal(0))
        if total <= 0:
            raise DatasetError("Pool nominal total must be positive")
        shares = {coin: amount / total for coin, amount in balances.items()}
        rates = {direction: {str(size): raw_decimal(snapshot["raw_quotes"][direction][str(size)], 6) / Decimal(size)
                             for size in QUOTE_SIZES} for direction in DIRECTIONS}
        sensitivities = {}
        for direction in DIRECTIONS:
            baseline = rates[direction]["1000"]
            if baseline <= 0:
                raise DatasetError("Small quote baseline must be positive for size sensitivity")
            sensitivities[direction] = {str(size): decimal_text((baseline - rates[direction][str(size)]) / baseline * Decimal(10000))
                                        for size in QUOTE_SIZES}
        imbalance = sum((abs(value - Decimal(1) / Decimal(3)) for value in shares.values()), Decimal(0)) / 2
        return {"block_number": snapshot["block_number"], "block_hash": snapshot["block_hash"],
                "timestamp_utc": snapshot["timestamp_utc"],
                "balances": {key: decimal_text(value) for key, value in balances.items()},
                "total_nominal_tokens": decimal_text(total),
                "shares": {key: decimal_text(value) for key, value in shares.items()},
                "share_pct": {key: decimal_text(value * 100) for key, value in shares.items()},
                "quote_rates": {direction: {size: decimal_text(rate) for size, rate in values.items()} for direction, values in rates.items()},
                "imbalance_l1": decimal_text(imbalance), "size_sensitivity_bps": sensitivities,
                "usdc_usdt_size_sensitivity_bps": sensitivities["USDC_USDT"]["100000"],
                "usdt_usdc_size_sensitivity_bps": sensitivities["USDT_USDC"]["100000"]}


def normalize_query(query: dict) -> dict:
    if not isinstance(query, dict):
        raise DatasetError("Calculation query must be an object")
    if set(query) - {"metric", "operation", "start", "end", "coin", "direction", "size"}:
        raise DatasetError("Unexpected calculation query parameters")
    metric = query.get("metric")
    operation = query.get("operation")
    if metric not in ("share_pct", "quote_rate", "size_sensitivity_bps", "imbalance_l1"):
        raise DatasetError("Unsupported metric")
    if operation not in ("first", "last", "change", "maximum"):
        raise DatasetError("Unsupported window operation")
    normalized = {"metric": metric, "operation": operation,
                  "start": utc_text(parse_utc(query.get("start"))), "end": utc_text(parse_utc(query.get("end")))}
    if metric == "share_pct":
        coin = query.get("coin", "USDC")
        if coin not in COINS:
            raise DatasetError("Unsupported share coin")
        normalized["coin"] = coin
    if metric in ("quote_rate", "size_sensitivity_bps"):
        direction = query.get("direction", "USDC_USDT")
        size = query.get("size", 100000 if metric == "size_sensitivity_bps" else 1000)
        # String size is accepted for JSON tool interfaces, but fractions/bools are not.
        if isinstance(size, bool) or str(size) not in {str(value) for value in QUOTE_SIZES}:
            raise DatasetError("Unsupported quote size")
        if direction not in DIRECTIONS:
            raise DatasetError("Unsupported quote direction")
        normalized.update({"direction": direction, "size": int(size)})
    return normalized


def _metric_value(snapshot: dict, query: dict) -> tuple[Decimal, str]:
    metrics = snapshot_metrics(snapshot)
    metric = query["metric"]
    if metric == "share_pct":
        return Decimal(metrics["share_pct"][query["coin"]]), "percent"
    if metric == "quote_rate":
        unit = "USDT/USDC" if query["direction"] == "USDC_USDT" else "USDC/USDT"
        return Decimal(metrics["quote_rates"][query["direction"]][str(query["size"])]), unit
    if metric == "size_sensitivity_bps":
        return Decimal(metrics["size_sensitivity_bps"][query["direction"]][str(query["size"])]), "basis_points"
    return Decimal(metrics["imbalance_l1"]), "dimensionless"


def _observation(snapshot: dict) -> dict:
    return {"snapshot_id": snapshot["id"], "target_timestamp_utc": snapshot["target_timestamp_utc"],
            "observed_at": snapshot["timestamp_utc"], "block_number": snapshot["block_number"], "block_hash": snapshot["block_hash"]}


def calculate(dataset: dict, query: dict) -> dict:
    normalized = normalize_query(query)
    selected = select_snapshots(dataset, normalized["start"], normalized["end"])
    with localcontext() as context:
        context.prec = 80
        values = [_metric_value(snapshot, normalized) for snapshot in selected]
        operation = normalized["operation"]
        unit = values[0][1]
        focus = None
        if operation == "first":
            used, chosen = [selected[0]], [values[0][0]]
            value, focus = chosen[0], selected[0]
        elif operation == "last":
            used, chosen = [selected[-1]], [values[-1][0]]
            value, focus = chosen[0], selected[-1]
        elif operation == "change":
            used = [selected[0]] if len(selected) == 1 else [selected[0], selected[-1]]
            chosen = [values[0][0]] if len(selected) == 1 else [values[0][0], values[-1][0]]
            value = values[-1][0] - values[0][0]
            if normalized["metric"] == "share_pct":
                unit = "percentage_points"
        else:
            used, chosen = selected, [item[0] for item in values]
            peak_index = max(range(len(values)), key=lambda index: values[index][0])
            value, focus = values[peak_index][0], selected[peak_index]
        signature = {"version": METRICS_VERSION, "query": normalized,
                     "observations": [{"id": snapshot["id"], "block_hash": snapshot["block_hash"], "value": decimal_text(number)}
                                      for snapshot, number in zip(used, chosen)]}
        identifier = "C_" + hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:16]
        result = {"value": decimal_text(value), "unit": unit, "evidence_ids": ["E_" + snapshot["id"] for snapshot in used],
                  "calculation_id": identifier, "query": normalized, "metrics_version": METRICS_VERSION,
                  "sample_count": len(selected)}
        if focus is not None:
            result.update(_observation(focus))
        else:
            result["observations"] = [_observation(snapshot) for snapshot in used]
        if operation == "maximum":
            result["scope"] = "maximum among collected target observations, not a continuous-time maximum"
        return result


def all_metrics(dataset: dict) -> dict:
    return {snapshot["id"]: snapshot_metrics(snapshot) for snapshot in dataset["snapshots"]}


def evidence_catalog(dataset: dict) -> dict:
    return {"E_" + snapshot["id"]: {"snapshot_id": snapshot["id"], "block_number": snapshot["block_number"],
            "block_hash": snapshot["block_hash"], "timestamp_utc": snapshot["timestamp_utc"],
            "target_timestamp_utc": snapshot["target_timestamp_utc"], "source": "raw_dataset.json",
            "source_type": dataset["fixture_status"]} for snapshot in dataset["snapshots"]}
