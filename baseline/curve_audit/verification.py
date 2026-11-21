"""Independent, fail-closed checks against archived raw integers.

This module intentionally imports no metrics implementation. A successful result
certifies structured arithmetic and provenance, never arbitrary prose or chain truth.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
import re
from .confirmation import ABSOLUTE_TOLERANCE, has_refusal_reason, number_equal

COINS = ("DAI", "USDC", "USDT")
DECIMALS = {"DAI": 18, "USDC": 6, "USDT": 6}
POOL = "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7"
DIRECTIONS = ("USDC_USDT", "USDT_USDC")
TOLERANCE = ABSOLUTE_TOLERANCE


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO UTC string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("timestamps must explicitly use UTC")
    return result.astimezone(timezone.utc)


def _decimal(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("number must be a decimal string or integer")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("non-finite decimal")
    if number and abs(number.adjusted()) > 1000:
        raise ValueError("decimal magnitude exceeds supported audit bounds")
    return number


def _raw(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise ValueError("raw amount must be a canonical nonnegative integer string")
    if int(value) >= 2**256:
        raise ValueError("raw amount exceeds uint256")
    return Decimal(value)


def _word(value):
    return f"{int(value):064x}"


def _canonical_number(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def validate_dataset(dataset):
    """Check internal source consistency; this is not a second RPC attestation."""
    errors = []
    if not isinstance(dataset, dict):
        return ["dataset must be an object"]
    if dataset.get("chain_id") != 1 or isinstance(dataset.get("chain_id"), bool):
        errors.append("unexpected chain_id (expected Ethereum Mainnet = 1)")
    if dataset.get("chain") != "Ethereum Mainnet":
        errors.append("unexpected chain label")
    if str(dataset.get("pool", "")).lower() != POOL:
        errors.append("unexpected pool contract")
    if dataset.get("coin_order") != list(COINS):
        errors.append("coin order must be DAI, USDC, USDT")
    if dataset.get("decimals") != DECIMALS:
        errors.append("token decimals disagree with fixed 3pool configuration")
    sizes = dataset.get("quote_sizes")
    if (not isinstance(sizes, list) or not sizes or
            any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in sizes)):
        errors.append("invalid quote_sizes")
        sizes = []
    if len(sizes) != len(set(sizes)):
        errors.append("duplicate quote_sizes")
    if sizes != [1000, 10000, 100000]:
        errors.append("unexpected fixed quote size configuration")
    if dataset.get("balance_source", "pool_internal_balances") not in ("pool_internal_balances", "erc20_balance_of_pool"):
        errors.append("unsupported balance source convention")
    snapshots = dataset.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        return errors + ["dataset has no snapshots"]
    seen_ids, blocks, by_number, by_target = set(), {}, {}, {}
    for position, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            errors.append(f"snapshot[{position}] must be an object")
            continue
        sid = snapshot.get("id", f"index_{position}")
        prefix = str(sid)
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or sid in seen_ids:
            errors.append(f"{prefix}: missing or duplicate snapshot id")
        seen_ids.add(str(sid))
        try:
            target = _time(snapshot["target_timestamp_utc"])
            actual = _time(snapshot["timestamp_utc"])
            if actual > target:
                errors.append(f"{prefix}: block timestamp exceeds target")
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"{prefix}: invalid timestamp: {exc}")
        block = snapshot.get("block_number")
        if not isinstance(block, int) or isinstance(block, bool) or block <= 0:
            errors.append(f"{prefix}: invalid block_number")
        for key in ("block_hash", "parent_hash"):
            if not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(snapshot.get(key, ""))):
                errors.append(f"{prefix}: invalid {key}")
        if str(snapshot.get("contract_address", "")).lower() != POOL:
            errors.append(f"{prefix}: wrong snapshot contract")
        for key in ("balances", "raw_balances"):
            if not isinstance(snapshot.get(key), dict) or set(snapshot[key]) != set(COINS):
                errors.append(f"{prefix}: {key} must contain exactly the three pool coins")
        for key in ("quotes", "raw_quotes"):
            if not isinstance(snapshot.get(key), dict) or set(snapshot[key]) != set(DIRECTIONS):
                errors.append(f"{prefix}: {key} must contain exactly the two quote directions")
            else:
                for direction in DIRECTIONS:
                    if (not isinstance(snapshot[key][direction], dict) or
                            set(snapshot[key][direction]) != {str(x) for x in sizes}):
                        errors.append(f"{prefix}: {key} quote sizes disagree")
        for i, coin in enumerate(COINS):
            try:
                with localcontext() as context:
                    context.prec = 90
                    raw = _raw(snapshot["raw_balances"][coin])
                    if not isinstance(snapshot["balances"][coin], str):
                        raise ValueError("normalized balance must be a decimal string")
                    normal = _decimal(snapshot["balances"][coin])
                    if raw / (Decimal(10) ** DECIMALS[coin]) != normal:
                        errors.append(f"{prefix}: raw/normalized balance mismatch {coin}")
                expected = "0x4903b0d1" + _word(i)
                if dataset.get('balance_source')=='erc20_balance_of_pool':
                    token_contracts={'DAI':'0x6b175474e89094c44da98b954eedeac495271d0f','USDC':'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48','USDT':'0xdac17f958d2ee523a2206206994597c13d831ec7'}
                    if dataset.get('balance_contracts')!=token_contracts:errors.append('incorrect ERC20 balance source contracts')
                    expected='0x70a08231'+POOL[2:].rjust(64,'0')
                if snapshot["calldata"]["balances"][coin].lower() != expected:
                    errors.append(f"{prefix}: calldata balance index mismatch {coin}")
            except (KeyError, ValueError, TypeError, AttributeError, InvalidOperation) as exc:
                errors.append(f"{prefix}: invalid balance {coin}: {exc}")
        try:
            if not any(_raw(snapshot["raw_balances"][coin]) for coin in COINS):
                errors.append(f"{prefix}: zero total nominal pool balance")
        except (KeyError, ValueError, TypeError, InvalidOperation):
            pass  # Per-coin diagnostics above already report malformed balances.
        for direction in DIRECTIONS:
            source, dest = direction.split("_")
            for size in sizes:
                try:
                    with localcontext() as context:
                        context.prec = 90
                        raw = _raw(snapshot["raw_quotes"][direction][str(size)])
                        if not isinstance(snapshot["quotes"][direction][str(size)], str):
                            raise ValueError("normalized quote must be a decimal string")
                        normal = _decimal(snapshot["quotes"][direction][str(size)])
                        if raw / (Decimal(10) ** DECIMALS[dest]) != normal:
                            errors.append(f"{prefix}: raw/normalized quote mismatch {direction}:{size}")
                    expected = ("0x5e0d443f" + _word(COINS.index(source)) +
                                _word(COINS.index(dest)) + _word(size * 10 ** DECIMALS[source]))
                    if snapshot["calldata"]["quotes"][direction][str(size)].lower() != expected:
                        errors.append(f"{prefix}: calldata direction/amount mismatch {direction}:{size}")
                except (KeyError, ValueError, TypeError, AttributeError, InvalidOperation) as exc:
                    errors.append(f"{prefix}: invalid quote {direction}:{size}: {exc}")
        # Aliases for one block are allowed only when their source state agrees.
        identity = str(snapshot.get("block_hash", "")).lower()
        source_state = {k: snapshot.get(k) for k in (
            "timestamp_utc", "block_number", "parent_hash", "contract_address",
            "raw_balances", "balances", "raw_quotes", "quotes", "calldata")}
        if identity in blocks and blocks[identity] != source_state:
            errors.append(f"{prefix}: aliases disagree about the same block")
        blocks[identity] = source_state
        if str(block) in by_number and by_number[str(block)] != identity:
            errors.append(f"{prefix}: conflicting block hashes for one block number")
        by_number[str(block)] = identity
        try:
            target_key = _time(snapshot["target_timestamp_utc"])
            if target_key in by_target and by_target[target_key] != identity:
                errors.append(f"{prefix}: conflicting block selection for one target")
            by_target[target_key] = identity
            if "next_block" in snapshot:
                following = snapshot["next_block"]
                if following["block_number"] != block + 1:
                    errors.append(f"{prefix}: following block is not consecutive")
                if following["parent_hash"].lower() != identity:
                    errors.append(f"{prefix}: following block parent hash mismatch")
                if not re.fullmatch(r"0x[0-9a-fA-F]{64}", following["block_hash"]):
                    errors.append(f"{prefix}: invalid following block hash")
                if _time(following["timestamp_utc"]) <= target_key:
                    errors.append(f"{prefix}: selected block is not nearest prior block")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            errors.append(f"{prefix}: invalid source block selection: {exc}")
    return errors


def normalize_query(query):
    if not isinstance(query, dict):
        raise ValueError("query must be an object")
    allowed = {"metric", "operation", "start", "end", "coin", "direction", "size"}
    if set(query) - allowed:
        raise ValueError("unexpected query arguments: " + ", ".join(sorted(set(query) - allowed)))
    metric = query.get("metric")
    if metric not in ("share_pct", "quote_rate", "size_sensitivity_bps", "imbalance_l1"):
        raise ValueError("unsupported metric")
    operation = query.get("operation")
    if operation not in ("first", "last", "change", "maximum"):
        raise ValueError("unsupported operation")
    start, end = _time(query.get("start")), _time(query.get("end"))
    if start > end:
        raise ValueError("window start exceeds end")
    coin, direction = query.get("coin", "USDC"), query.get("direction", "USDC_USDT")
    if metric == "share_pct" and coin not in COINS:
        raise ValueError("unsupported share coin")
    if metric in ("quote_rate", "size_sensitivity_bps") and direction not in DIRECTIONS:
        raise ValueError("unsupported quote direction")
    size = query.get("size", 100000 if metric == "size_sensitivity_bps" else 1000)
    try:
        if isinstance(size, bool) or str(int(size)) != str(size):
            raise ValueError("size must be a positive integer")
        size = int(size)
        if size <= 0:
            raise ValueError("size must be positive")
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid quote size") from exc
    normalized = dict(metric=metric, operation=operation,
                      start=start.isoformat().replace("+00:00", "Z"),
                      end=end.isoformat().replace("+00:00", "Z"))
    if metric == "share_pct":
        normalized["coin"] = coin
    if metric in ("quote_rate", "size_sensitivity_bps"):
        if size not in (1000, 10000, 100000):
            raise ValueError("unsupported quote size")
        normalized.update(direction=direction, size=size)
    return normalized


def _selected(dataset, query):
    start, end = _time(query["start"]), _time(query["end"])
    snapshots = dataset.get("snapshots", [])
    if not snapshots:
        raise ValueError("no snapshots")
    all_times = [_time(s["target_timestamp_utc"]) for s in snapshots]
    if start < min(all_times) or end > max(all_times):
        raise ValueError("requested window extends beyond available historical coverage")
    if start not in all_times or end not in all_times:
        raise ValueError("window endpoints must match archived target timestamps")
    hourly = sorted({_time(s["target_timestamp_utc"]) for s in snapshots if s.get("series") == "hourly"})
    step = dataset.get("sampling", {}).get("interval_minutes", 60)
    if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
        raise ValueError("invalid sampling interval")
    if hourly:
        cursor = hourly[0]
        known = set(hourly)
        while cursor <= hourly[-1]:
            if start <= cursor <= end and cursor not in known:
                raise ValueError("requested window contains missing hourly observations")
            cursor += timedelta(minutes=step)
    # Prefer hourly IDs over anchor aliases, then choose deterministically by ID.
    ordered = sorted(snapshots, key=lambda s: (
        _time(s["target_timestamp_utc"]), 0 if s.get("series") == "hourly" else 1, s["id"]))
    selected, seen = [], set()
    for snapshot in ordered:
        if start <= _time(snapshot["target_timestamp_utc"]) <= end:
            block = snapshot["block_hash"].lower()
            if block not in seen:
                selected.append(snapshot)
                seen.add(block)
    if not selected:
        raise ValueError("window has no available samples")
    return selected


def _metric(snapshot, query):
    metric = query["metric"]
    with localcontext() as context:
        context.prec = 80
        if metric in ("share_pct", "imbalance_l1"):
            balances = {c: _raw(snapshot["raw_balances"][c]) / Decimal(10) ** DECIMALS[c]
                        for c in COINS}
            total = sum(balances.values())
            if total <= 0:
                raise ValueError("zero total pool balance")
            if metric == "share_pct":
                return balances[query["coin"]] / total * 100
            return sum(abs(value / total - Decimal(1) / 3) for value in balances.values()) / 2
        direction = query["direction"]
        _, dest = direction.split("_")
        def rate(size):
            return _raw(snapshot["raw_quotes"][direction][str(size)]) / Decimal(10) ** DECIMALS[dest] / size
        if metric == "quote_rate":
            return rate(query["size"])
        baseline = rate(1000)
        if baseline <= 0:
            raise ValueError("zero baseline quote rate")
        return (baseline - rate(query["size"])) / baseline * 10000


def recompute(dataset, query):
    """Return the independent expected structured claim, or raise ValueError."""
    try:
        query = normalize_query(query)
        selected = _selected(dataset, query)
        operation = query["operation"]
        metric = query["metric"]
        if metric == "share_pct":
            unit = "percentage_points" if operation == "change" else "percent"
        elif metric == "quote_rate":
            source, dest = query["direction"].split("_")
            unit = dest + "/" + source
        elif metric == "size_sensitivity_bps":
            unit = "basis_points"
        else:
            unit = "dimensionless"
        with localcontext() as context:
            context.prec = 80
            if operation == "first":
                observed, references = selected[0], [selected[0]]
                value = _metric(observed, query)
            elif operation == "last":
                observed, references = selected[-1], [selected[-1]]
                value = _metric(observed, query)
            elif operation == "change":
                observed = None
                references = [selected[0]] if len(selected) == 1 else [selected[0], selected[-1]]
                value = _metric(selected[-1], query) - _metric(selected[0], query)
            else:
                observed = max(selected, key=lambda s: _metric(s, query))
                references = selected
                value = _metric(observed, query)
        result = {"query": query, "value": _canonical_number(value), "unit": unit,
                  "sample_count": len(selected),
                  "evidence_ids": ["E_" + s["id"] for s in references],
                  "blocks": [{"snapshot_id": s["id"], "block_number": s["block_number"],
                              "block_hash": s["block_hash"]} for s in references]}
        if observed:
            result.update(observed_at=observed["timestamp_utc"],
                          target_timestamp_utc=observed["target_timestamp_utc"],
                          snapshot_id=observed["id"],
                          block_number=observed["block_number"], block_hash=observed["block_hash"])
        return result
    except (KeyError, TypeError, InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError("missing or invalid source data: " + str(exc)) from exc


def verify_claims(dataset, report):
    dataset_errors = validate_dataset(dataset)
    errors = list(dataset_errors)
    checks, results = [], []
    if not isinstance(dataset, dict):
        return {"pass": False, "errors": errors, "checks": [], "claim_results": [],
                "scope": "structured claims only"}
    if not isinstance(report, dict):
        return {"pass": False, "errors": errors + ["report must be an object"],
                "checks": [], "claim_results": [], "scope": "structured claims only"}
    if not isinstance(report.get("task_id"), str) or not report.get("task_id"):
        errors.append("missing report task_id")
    if not isinstance(report.get("answer"), str):
        errors.append("report answer must be text")
    claims = report.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("report must include at least one checkable or explicit unsupported claim")
        claims = []
    seen = set()
    for i, claim in enumerate(claims):
        claim_errors = []
        if not isinstance(claim, dict):
            results.append({"claim_id": f"index_{i}", "pass": False, "errors": ["claim must be an object"]})
            errors.append(f"claim[{i}]: claim must be an object")
            continue
        cid = claim.get("claim_id", f"index_{i}")
        if not isinstance(cid, str) or not cid or cid in seen:
            claim_errors.append("missing or duplicate claim_id")
        seen.add(str(cid))
        status = claim.get("status")
        if status not in ("supported", "unsupported", "refused", "conflicting"):
            claim_errors.append("invalid claim status")
        expected, inability = None, None
        try:
            expected = recompute(dataset, claim.get("query"))
        except (ValueError, TypeError) as exc:
            inability = str(exc)
        if status in ("unsupported", "refused"):
            if claim.get("value") is not None:
                claim_errors.append("unsupported claim must not assert a numeric value")
            if not has_refusal_reason(claim, report):
                claim_errors.append("unsupported claim requires a refusal explanation")
            if expected is not None and not dataset_errors:
                claim_errors.append("query is computable; unsupported status is an unnecessary refusal")
            refs = claim.get("evidence_ids", [])
            if not isinstance(refs, list):
                claim_errors.append("evidence_ids must be a list")
            elif refs and any(not isinstance(r, str) or r not in {
                    "E_" + str(s.get("id")) for s in dataset.get("snapshots", []) if isinstance(s, dict)}
                              for r in refs):
                claim_errors.append("unsupported claim references unknown evidence")
        elif status in ("supported", "conflicting"):
            if expected is None:
                claim_errors.append("claim cannot be supported: " + str(inability))
            else:
                try:
                    if not isinstance(claim.get("value"), str):
                        raise ValueError("claim value must be a decimal string")
                    if not number_equal(str(_decimal(claim["value"])), expected["value"]):
                        claim_errors.append("value differs from independent raw-integer recomputation")
                except (ValueError, DecimalException) as exc:
                    claim_errors.append(str(exc))
                if claim.get("unit") != expected["unit"]:
                    claim_errors.append("unit disagrees with metric/operation/direction")
                if claim.get("evidence_ids") != expected["evidence_ids"]:
                    claim_errors.append("evidence does not exactly support the selected window/operation")
                if not isinstance(claim.get("calculation_id"), str) or not claim["calculation_id"]:
                    claim_errors.append("missing calculation reference")
                for key in ("observed_at", "block_number", "block_hash", "target_timestamp_utc", "snapshot_id"):
                    if key in claim and claim[key] != expected.get(key):
                        claim_errors.append(key + " does not match selected source block")
            if status == "conflicting" and not report.get("faults"):
                claim_errors.append("conflicting status requires recorded conflict details")
        errors.extend(f"{cid}: {error}" for error in claim_errors)
        results.append({"claim_id": cid, "pass": not claim_errors,
                        "errors": claim_errors, "expected": expected, "unavailability": inability})
    checks.extend(["raw integers versus normalized amounts", "calldata token indices and raw input amounts",
                   "fixed chain/pool/decimals", "window coverage and exact operation evidence",
                   "independent arithmetic and units", "claim status and explicit refusal"])
    return {"pass": not errors, "errors": errors, "checks": checks, "claim_results": results,
            "scope": "Structured claims and internal source consistency only; arbitrary narrative and blockchain authenticity are not certified."}
