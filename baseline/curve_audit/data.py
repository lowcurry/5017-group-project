"""Historical Curve 3pool data admission, collection and independent replication.

Only explicit RPC configuration is used. Raw integers, normalized quantities and
ABI calldata are cross-checked before data enter a calculation. Existing pilot
data lack the following-block header: their values remain usable, but the
nearest-prior-block assertion is explicitly marked unverified.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Callable

POOL = "0xbEbc44782C7dB0a1A60Cb6Fe97d0b483032FF1C7"
COINS = ("DAI", "USDC", "USDT")
DECIMALS = {"DAI": 18, "USDC": 6, "USDT": 6}
DIRECTIONS = {"USDC_USDT": (1, 2), "USDT_USDC": (2, 1)}
QUOTE_SIZES = (1000, 10000, 100000)
BALANCES_SELECTOR = "4903b0d1"
GET_DY_SELECTOR = "5e0d443f"
_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_UINT = re.compile(r"(?:0|[1-9][0-9]*)\Z")


class DatasetError(ValueError):
    """A dataset violates its declared chain, units, ABI or coverage."""


class RpcError(RuntimeError):
    """Sanitized RPC failure; never includes a URL, key or server error text."""


def parse_utc(value: str) -> datetime:
    """Parse an explicitly timezone-aware timestamp and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        raise DatasetError("Expected a valid timezone-aware ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DatasetError("Naive timestamps are not allowed; specify UTC explicitly")
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise DatasetError("Non-finite decimal quantity")
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def raw_decimal(value: str, decimals: int) -> Decimal:
    with localcontext() as context:
        context.prec = 100
        return Decimal(value) / Decimal(10) ** decimals


def calldata(selector: str, values: list[int]) -> str:
    return "0x" + selector + "".join(format(value, "064x") for value in values)


def _raw_uint(value: Any) -> str:
    if not isinstance(value, str) or not _UINT.fullmatch(value) or int(value) >= 2**256:
        raise DatasetError("Raw contract values must be canonical uint256 decimal strings")
    return value


def _normalized(value: Any, raw: str, decimals: int) -> None:
    if not isinstance(value, str):
        raise DatasetError("Normalized quantities must be decimal strings")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise DatasetError("Invalid normalized decimal quantity") from None
    if not amount.is_finite() or amount != raw_decimal(raw, decimals):
        raise DatasetError("Raw and normalized quantities disagree")


def coverage(dataset: dict) -> dict:
    """Describe actual target coverage; anchor extent is not hourly coverage."""
    snapshots = dataset["snapshots"]
    targets = sorted({parse_utc(row["target_timestamp_utc"]) for row in snapshots})
    hourly = sorted({parse_utc(row["target_timestamp_utc"]) for row in snapshots
                     if row.get("series") == "hourly"})
    step = dataset.get("sampling", {}).get("interval_minutes", 60)
    if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
        raise DatasetError("Invalid sampling interval")
    missing: list[str] = []
    if len(hourly) > 1:
        cursor = hourly[0]
        known = set(hourly)
        while cursor <= hourly[-1]:
            if cursor not in known:
                missing.append(utc_text(cursor))
            cursor += timedelta(minutes=step)
        if any((point - hourly[0]).total_seconds() % (step * 60) for point in hourly):
            raise DatasetError("Hourly targets do not align with the sampling interval")
    return {
        "target_start": utc_text(targets[0]),
        "target_end": utc_text(targets[-1]),
        "snapshot_count": len(snapshots),
        "target_count": len(targets),
        "distinct_block_count": len({row["block_hash"].lower() for row in snapshots}),
        "hourly_start": utc_text(hourly[0]) if hourly else None,
        "hourly_end": utc_text(hourly[-1]) if hourly else None,
        "interval_minutes": step,
        "missing_hourly_targets": missing,
        "isolated_targets": [utc_text(point) for point in targets if point not in set(hourly)],
        "nearest_prior_verified": all("next_block" in row for row in snapshots),
        "scope": "observed historical contract states; nominal token quantities, not USD values",
    }


def validate_dataset(dataset: dict) -> dict:
    """Validate in memory, without changing or rewriting the source dataset."""
    if not isinstance(dataset, dict):
        raise DatasetError("Dataset must be a JSON object")
    if type(dataset.get("chain_id")) is not int or dataset.get("chain_id") != 1:
        raise DatasetError("Only Ethereum Mainnet chain_id 1 is supported")
    if dataset.get("chain") != "Ethereum Mainnet":
        raise DatasetError("Unexpected chain label")
    if str(dataset.get("pool", "")).lower() != POOL.lower():
        raise DatasetError("Unexpected Curve pool contract")
    if (dataset.get("coin_order") != list(COINS) or dataset.get("decimals") != DECIMALS
            or any(type(value) is not int for value in dataset.get("decimals", {}).values())):
        raise DatasetError("Coin order or token decimals do not match Curve 3pool")
    if (dataset.get("quote_sizes") != list(QUOTE_SIZES)
            or any(type(value) is not int for value in dataset.get("quote_sizes", []))):
        raise DatasetError("Quote sizes must be 1000, 10000 and 100000 tokens")
    if dataset.get("balance_source", "pool_internal_balances") not in ("pool_internal_balances", "erc20_balance_of_pool"):
        raise DatasetError("Unsupported balance source convention")
    snapshots = dataset.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise DatasetError("Dataset has no snapshots")
    ids: set[str] = set()
    by_hash: dict[str, str] = {}
    by_number: dict[int, str] = {}
    by_target: dict[datetime, str] = {}
    for row in snapshots:
        try:
            if not isinstance(row, dict):
                raise DatasetError("Invalid snapshot object")
            identifier = row["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                raise DatasetError("Invalid snapshot identifier")
            if identifier in ids:
                raise DatasetError("Duplicate snapshot identifier")
            ids.add(identifier)
            target, observed = parse_utc(row["target_timestamp_utc"]), parse_utc(row["timestamp_utc"])
            if observed > target:
                raise DatasetError("Observed block is later than the target timestamp")
            number = row["block_number"]
            block_hash = row["block_hash"]
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                raise DatasetError("Invalid block number")
            if not isinstance(block_hash, str) or not _HASH.fullmatch(block_hash):
                raise DatasetError("Invalid block hash")
            if not _HASH.fullmatch(row["parent_hash"]):
                raise DatasetError("Invalid parent block hash")
            if row["contract_address"].lower() != POOL.lower():
                raise DatasetError("Snapshot contract does not match dataset pool")
            for field in ("raw_balances", "balances"):
                if set(row[field]) != set(COINS):
                    raise DatasetError("Missing or unexpected balance coins")
            for index, coin in enumerate(COINS):
                raw = _raw_uint(row["raw_balances"][coin])
                _normalized(row["balances"][coin], raw, DECIMALS[coin])
                expected_balance_call=calldata(BALANCES_SELECTOR,[index])
                if dataset.get('balance_source')=='erc20_balance_of_pool':
                    expected_tokens={'DAI':'0x6b175474e89094c44da98b954eedeac495271d0f','USDC':'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48','USDT':'0xdac17f958d2ee523a2206206994597c13d831ec7'}
                    if dataset.get('balance_contracts')!=expected_tokens:raise DatasetError('Wrong external balance token contracts')
                    expected_balance_call='0x70a08231'+POOL.lower()[2:].rjust(64,'0')
                if row["calldata"]["balances"][coin].lower() != expected_balance_call:
                    raise DatasetError("Balance calldata does not match the coin index")
            if not any(int(value) for value in row["raw_balances"].values()):
                raise DatasetError("Pool nominal total must be positive")
            for field in ("raw_quotes", "quotes"):
                if set(row[field]) != set(DIRECTIONS):
                    raise DatasetError("Missing or unexpected quote direction")
            for direction, (i, j) in DIRECTIONS.items():
                expected_sizes = {str(size) for size in QUOTE_SIZES}
                for field in ("raw_quotes", "quotes"):
                    if set(row[field][direction]) != expected_sizes:
                        raise DatasetError("Missing or unexpected quote sizes")
                for size in QUOTE_SIZES:
                    key = str(size)
                    raw = _raw_uint(row["raw_quotes"][direction][key])
                    _normalized(row["quotes"][direction][key], raw, DECIMALS[COINS[j]])
                    expected = calldata(GET_DY_SELECTOR, [i, j, size * 10**DECIMALS[COINS[i]]])
                    if row["calldata"]["quotes"][direction][key].lower() != expected:
                        raise DatasetError("Quote calldata does not match direction and input amount")
            if "next_block" in row:
                following = row["next_block"]
                if type(following["block_number"]) is not int or following["block_number"] != number + 1:
                    raise DatasetError("Following block is not consecutive")
                if not _HASH.fullmatch(following["block_hash"]):
                    raise DatasetError("Invalid following block hash")
                if following["parent_hash"].lower() != block_hash.lower():
                    raise DatasetError("Following block does not extend the selected block")
                if parse_utc(following["timestamp_utc"]) <= target:
                    raise DatasetError("Selected block is not the nearest prior block")
            # Aliases may differ only in labels, IDs, series and frozen targets.
            state = {key: row[key] for key in ("timestamp_utc", "block_number", "block_hash",
                     "parent_hash", "contract_address", "raw_balances", "balances", "raw_quotes", "quotes", "calldata")}
            fingerprint = json.dumps(state, sort_keys=True, separators=(",", ":")).lower()
            normalized_hash = block_hash.lower()
            if normalized_hash in by_hash and by_hash[normalized_hash] != fingerprint:
                raise DatasetError("Conflicting snapshots for one block hash")
            if number in by_number and by_number[number] != normalized_hash:
                raise DatasetError("Conflicting hashes for one block number")
            if target in by_target and by_target[target] != normalized_hash:
                raise DatasetError("Conflicting block selection for one target timestamp")
            by_hash[normalized_hash], by_number[number], by_target[target] = fingerprint, normalized_hash, normalized_hash
        except (KeyError, TypeError, AttributeError, InvalidOperation):
            raise DatasetError("Incomplete or malformed snapshot fields") from None
    return coverage(dataset)


def load_dataset(path: str | Path) -> dict:
    try:
        dataset = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DatasetError("Unable to read dataset JSON") from None
    # Derived metadata exist only in the returned in-memory object.
    dataset["_coverage"] = validate_dataset(dataset)
    return dataset


def select_snapshots(dataset: dict, start: str, end: str) -> list[dict]:
    meta = validate_dataset(dataset)
    start_time, end_time = parse_utc(start), parse_utc(end)
    if start_time > end_time:
        raise DatasetError("Window start is after window end")
    if start_time < parse_utc(meta["target_start"]) or end_time > parse_utc(meta["target_end"]):
        raise DatasetError("Requested window is outside observed target coverage")
    targets = {parse_utc(row["target_timestamp_utc"]) for row in dataset["snapshots"]}
    if start_time not in targets or end_time not in targets:
        raise DatasetError("Window endpoints must match collected target timestamps")
    if any(start_time <= parse_utc(value) <= end_time for value in meta["missing_hourly_targets"]):
        raise DatasetError("Requested window contains missing hourly observations")
    ordered = sorted((row for row in dataset["snapshots"]
                      if start_time <= parse_utc(row["target_timestamp_utc"]) <= end_time),
                     key=lambda row: (parse_utc(row["target_timestamp_utc"]), row.get("series") != "hourly", row["id"]))
    seen: set[str] = set()
    selected = []
    for row in ordered:
        key = row["block_hash"].lower()
        if key not in seen:
            selected.append(row)
            seen.add(key)
    if not selected:
        raise DatasetError("No collected observations in requested window")
    return selected


def read_rpc_url(env_file: str | Path | None = None) -> str:
    """Read ETH_RPC_URL from process environment or an explicitly named file."""
    value = os.environ.get("ETH_RPC_URL", "").strip()
    if not value and env_file is not None:
        try:
            for line in Path(env_file).read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("export "):
                    stripped = stripped[7:].lstrip()
                if stripped.startswith("ETH_RPC_URL="):
                    value = stripped.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            raise RpcError("Unable to read the explicitly configured environment file") from None
    if not value:
        raise RpcError("Set ETH_RPC_URL or supply an explicit environment file")
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError
    except ValueError:
        raise RpcError("RPC endpoint must be a valid HTTP or HTTPS URL") from None
    return value


class RpcClient:
    def __init__(self, url: str, minimum_interval: float = 0.06, timeout: float = 30):
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or any(ord(char) < 32 for char in url):
                raise ValueError
        except (ValueError, TypeError):
            raise RpcError("RPC endpoint must be a valid HTTP or HTTPS URL") from None
        self._url = url
        self.minimum_interval, self.timeout = minimum_interval, timeout
        self.last_call, self.request_count = 0.0, 0
        self._next_id = 1
        self._blocks: dict[int, dict] = {}

    def _send(self, payload: Any, retries: int = 3) -> Any:
        try:
            request = urllib.request.Request(self._url, data=json.dumps(payload).encode(),
                                            headers={"content-type": "application/json", "User-Agent": "curve-audit/1.0"})
        except (ValueError, TypeError):
            raise RpcError("Unable to prepare RPC request") from None
        for attempt in range(retries):
            delay = self.minimum_interval - (time.monotonic() - self.last_call)
            if delay > 0:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.load(response)
                self.last_call = time.monotonic()
                self.request_count += len(payload) if isinstance(payload, list) else 1
                return result
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                self.last_call = time.monotonic()
                if attempt + 1 == retries:
                    raise RpcError("RPC transport failed after bounded retries") from None
                time.sleep(0.25 * 2**attempt)
        raise RpcError("RPC transport failed")

    def call(self, method: str, params: list) -> Any:
        identifier = self._next_id
        self._next_id += 1
        result = self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        if not isinstance(result, dict) or result.get("id") != identifier or "error" in result or "result" not in result:
            raise RpcError("RPC response rejected or malformed")
        return result["result"]

    def batch(self, calls: list[tuple[str, list]]) -> list:
        payload = []
        for method, params in calls:
            payload.append({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
            self._next_id += 1
        result = self._send(payload)
        if not isinstance(result, list) or len(result) != len(payload):
            raise RpcError("RPC batch response rejected or malformed")
        try:
            by_id = {row["id"]: row for row in result}
            if len(by_id) != len(payload):
                raise ValueError
            ordered = [by_id[row["id"]] for row in payload]
            if any("error" in row or "result" not in row for row in ordered):
                raise ValueError
            return [row["result"] for row in ordered]
        except (KeyError, TypeError, ValueError):
            raise RpcError("RPC batch response rejected or malformed") from None

    def block(self, number: int) -> dict:
        if number not in self._blocks:
            result = self.call("eth_getBlockByNumber", [hex(number), False])
            try:
                if not isinstance(result, dict) or int(result["number"], 16) != number:
                    raise ValueError
                if not _HASH.fullmatch(result["hash"]) or not _HASH.fullmatch(result["parentHash"]):
                    raise ValueError
                int(result["timestamp"], 16)
            except (ValueError, KeyError, TypeError):
                raise RpcError("RPC returned an invalid or missing block header") from None
            self._blocks[number] = result
        return self._blocks[number]


def find_block_at_or_before(client: RpcClient, target_ts: int) -> tuple[int, dict, dict]:
    """Binary search, then prove selected.timestamp <= target < next.timestamp."""
    try:
        head = int(client.call("eth_blockNumber", []), 16)
        if int(client.block(head)["timestamp"], 16) <= target_ts:
            raise RpcError("Historical target must be earlier than the latest block")
        lo, hi = 0, head
        if target_ts < int(client.block(0)["timestamp"], 16):
            raise RpcError("Historical target predates the chain")
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if int(client.block(mid)["timestamp"], 16) <= target_ts:
                lo = mid
            else:
                hi = mid - 1
        header, following = client.block(lo), client.block(lo + 1)
        if not int(header["timestamp"], 16) <= target_ts < int(following["timestamp"], 16):
            raise RpcError("Nearest prior block invariant failed")
        if following["parentHash"].lower() != header["hash"].lower():
            raise RpcError("Selected block headers are not consecutive canonical headers")
        return lo, header, following
    except (ValueError, KeyError, TypeError):
        raise RpcError("RPC returned malformed block-selection data") from None


def _snapshot_calls(number: int) -> tuple[list[tuple[str, list]], dict]:
    balance_calls = {coin: calldata(BALANCES_SELECTOR, [index]) for index, coin in enumerate(COINS)}
    quote_calls = {direction: {str(size): calldata(GET_DY_SELECTOR, [i, j, size * 10**6])
                             for size in QUOTE_SIZES} for direction, (i, j) in DIRECTIONS.items()}
    all_data = list(balance_calls.values()) + [data for calls in quote_calls.values() for data in calls.values()]
    calls = [("eth_call", [{"to": POOL, "data": data}, hex(number)]) for data in all_data]
    return calls, {"balances": balance_calls, "quotes": quote_calls}


def _block_ref(number: int, header: dict) -> dict:
    return {"block_number": number, "block_hash": header["hash"], "parent_hash": header["parentHash"],
            "timestamp_utc": utc_text(datetime.fromtimestamp(int(header["timestamp"], 16), timezone.utc))}


def collect_snapshot(client: RpcClient, target: datetime, identifier: str) -> dict:
    number, header, following = find_block_at_or_before(client, int(target.timestamp()))
    calls, abi = _snapshot_calls(number)
    results = client.batch(calls)
    try:
        if any(not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value) for value in results):
            raise ValueError
        raw = [str(int(value, 16)) for value in results]
    except (TypeError, ValueError):
        raise RpcError("RPC returned invalid uint256 contract output") from None
    raw_balances = dict(zip(COINS, raw[:3]))
    balances = {coin: decimal_text(raw_decimal(value, DECIMALS[coin])) for coin, value in raw_balances.items()}
    raw_quotes, quotes, offset = {}, {}, 3
    for direction in DIRECTIONS:
        raw_quotes[direction] = {}
        quotes[direction] = {}
        for size in QUOTE_SIZES:
            raw_quotes[direction][str(size)] = raw[offset]
            quotes[direction][str(size)] = decimal_text(raw_decimal(raw[offset], 6))
            offset += 1
    return {"id": identifier, "label": "hourly_history", "series": "hourly",
            "target_timestamp_utc": utc_text(target), **_block_ref(number, header),
            "next_block": _block_ref(number + 1, following), "contract_address": POOL,
            "raw_balances": raw_balances, "balances": balances, "raw_quotes": raw_quotes,
            "quotes": quotes, "calldata": abi}


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def export_processed(dataset: dict, csv_path: str | Path, dictionary_path: str | Path) -> dict:
    """Write exact decimal metrics, one row per distinct observed block."""
    from .metrics import snapshot_metrics
    validate_dataset(dataset)
    fields = ["id", "target_timestamp_utc", "timestamp_utc", "block_number", "block_hash",
              *COINS, *(coin + "_share_pct" for coin in COINS), "imbalance_l1",
              *(f"{direction}_{size}_rate" for direction in DIRECTIONS for size in QUOTE_SIZES),
              *(f"{direction}_size_sensitivity_bps" for direction in DIRECTIONS)]
    destination = Path(csv_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    rows = []
    for snapshot in sorted(dataset["snapshots"], key=lambda row: (row["target_timestamp_utc"], row.get("series") != "hourly", row["id"])):
        if snapshot["block_hash"].lower() in seen:
            continue
        seen.add(snapshot["block_hash"].lower())
        metrics = snapshot_metrics(snapshot)
        row = {key: snapshot[key] for key in fields[:5]}
        row.update(metrics["balances"])
        row.update({coin + "_share_pct": metrics["share_pct"][coin] for coin in COINS})
        row["imbalance_l1"] = metrics["imbalance_l1"]
        row.update({f"{direction}_{size}_rate": metrics["quote_rates"][direction][str(size)]
                    for direction in DIRECTIONS for size in QUOTE_SIZES})
        row.update({f"{direction}_size_sensitivity_bps": metrics["size_sensitivity_bps"][direction]["100000"]
                    for direction in DIRECTIONS})
        rows.append(row)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(destination)
    dictionary = {"schema_version": "1.0", "one_row_per": "distinct observed block; aliases deduplicated",
                  "dataset": destination.name, "coverage": coverage(dataset),
                  "fields": {"id": "source snapshot identifier",
                             "target_timestamp_utc": "frozen sampling target, UTC",
                             "timestamp_utc": "selected block timestamp, UTC; may precede target",
                             "block_number": "Ethereum Mainnet block used for every call in row",
                             "block_hash": "selected block hash",
                             "DAI/USDC/USDT": "normalized nominal token quantities; not USD values",
                             "*_share_pct": "100 * nominal quantity / total nominal quantity, percent",
                             "imbalance_l1": "0.5 * sum(abs(token_share - 1/3)), dimensionless",
                             "*_rate": "historical get_dy output token units per input token unit; gas excluded",
                             "*_size_sensitivity_bps": "(rate(1000)-rate(100000))/rate(1000)*10000, basis points"},
                  "limitations": ["Contract quote is not an executed trade", "Hourly maximum is a sampled maximum",
                                  "Nominal token quantities do not measure dollar value or issuer reserves"]}
    _atomic_json(Path(dictionary_path), dictionary)
    return {"csv": str(destination), "dictionary": str(dictionary_path), "rows": len(rows)}


def collect(output_path: str | Path, start: str, end: str, rpc_url: str, *, interval_minutes: int = 60,
            resume: bool = True, progress_callback: Callable[[dict], None] | None = None,
            client: RpcClient | None = None) -> dict:
    """Collect inclusively; safely checkpoint each complete, validated snapshot.

    Existing output is never overwritten with a different sampling plan. A failed
    run leaves a credential-free checkpoint and does not replace admitted output.
    """
    start_time, end_time = parse_utc(start), parse_utc(end)
    if start_time > end_time or not isinstance(interval_minutes, int) or isinstance(interval_minutes, bool) or interval_minutes <= 0:
        raise DatasetError("Invalid collection window or interval")
    seconds = (end_time - start_time).total_seconds()
    if seconds % (60 * interval_minutes):
        raise DatasetError("Collection endpoints must align with the sampling interval")
    path = Path(output_path)
    checkpoint = path.with_suffix(path.suffix + ".checkpoint.json")
    plan = {"start_utc": utc_text(start_time), "end_utc": utc_text(end_time), "interval_minutes": interval_minutes}
    dataset = {"schema_version": "1.1", "fixture_status": "real_historical_archive_rpc", "chain_id": 1,
               "chain": "Ethereum Mainnet", "pool": POOL, "coin_order": list(COINS), "decimals": DECIMALS.copy(),
               "quote_sizes": list(QUOTE_SIZES), "sampling": plan,
               "block_selection": "latest block with timestamp <= target; following-block invariant verified",
               "retrieval_method": "eth_getBlockByNumber and block-pinned eth_call",
               "rpc_provider": "explicit user-configured archive endpoint (credential omitted)",
               "retrieved_at_utc": utc_text(datetime.now(timezone.utc)), "snapshots": []}
    if path.exists():
        previous = load_dataset(path)
        if previous.get("sampling") != plan:
            raise DatasetError("Output exists for a different sampling plan; use a new output path")
        dataset = {key: value for key, value in previous.items() if not key.startswith("_")}
    if resume and checkpoint.exists():
        previous = load_dataset(checkpoint)
        if previous.get("sampling") != plan:
            raise DatasetError("Checkpoint belongs to a different sampling plan")
        if len(previous["snapshots"]) >= len(dataset["snapshots"]):
            dataset = {key: value for key, value in previous.items() if not key.startswith("_")}
    endpoint = client or RpcClient(rpc_url)
    try:
        chain_id = int(endpoint.call("eth_chainId", []), 16)
    except (ValueError, TypeError):
        raise RpcError("Configured endpoint returned an invalid chain ID") from None
    if chain_id != 1:
        raise RpcError("Configured endpoint is not Ethereum Mainnet")
    existing = {parse_utc(row["target_timestamp_utc"]) for row in dataset["snapshots"]}
    cursor, index = start_time, 0
    while cursor <= end_time:
        if cursor not in existing:
            dataset["snapshots"].append(collect_snapshot(endpoint, cursor, f"H_{index:03d}"))
            dataset["snapshots"].sort(key=lambda row: row["target_timestamp_utc"])
            validate_dataset(dataset)
            dataset["request_count"] = endpoint.request_count
            _atomic_json(checkpoint, dataset)
            if progress_callback:
                progress_callback({"collected": len(dataset["snapshots"]), "target": utc_text(cursor),
                                   "block_number": dataset["snapshots"][-1]["block_number"]})
        cursor += timedelta(minutes=interval_minutes)
        index += 1
    select_snapshots(dataset, plan["start_utc"], plan["end_utc"])
    dataset["retrieval_completed_at_utc"] = utc_text(datetime.now(timezone.utc))
    dataset["request_count"] = endpoint.request_count
    _atomic_json(path, dataset)
    if checkpoint.exists():
        checkpoint.unlink()
    processed = path.parent.parent / "processed"
    exports = export_processed(dataset, processed / (path.stem + ".csv"), processed / (path.stem + "_dictionary.json"))
    return {"status": "PASS", "output": str(path), "snapshot_count": len(dataset["snapshots"]),
            "distinct_block_count": coverage(dataset)["distinct_block_count"], "request_count": endpoint.request_count,
            "coverage": coverage(dataset), "exports": exports}


def replicate(dataset: dict, rpc_url: str, sample_fraction: float = 0.1, *, client: RpcClient | None = None) -> dict:
    """Compare deterministic sampled raw calls and headers with a second endpoint.

    Use a genuinely independent provider operationally; endpoint independence
    cannot be inferred from a URL and is never claimed by this function.
    """
    validate_dataset(dataset)
    if not 0 < sample_fraction <= 1:
        raise DatasetError("Replication sample_fraction must be in (0,1]")
    unique = {}
    for row in dataset["snapshots"]:
        unique.setdefault(row["block_hash"].lower(), row)
    ordered = sorted(unique.values(), key=lambda row: hashlib.sha256(row["block_hash"].lower().encode()).hexdigest())
    import math
    selected = ordered[:max(1, math.ceil(len(ordered) * sample_fraction))]
    endpoint = client or RpcClient(rpc_url)
    try:
        chain_ok = int(endpoint.call("eth_chainId", []), 16) == 1
    except (ValueError, TypeError):
        raise RpcError("Replication endpoint returned an invalid chain ID") from None
    if not chain_ok:
        raise RpcError("Replication endpoint is not Ethereum Mainnet")
    checks = []
    for row in selected:
        errors = []
        try:
            header = endpoint.block(row["block_number"])
            if header["hash"].lower() != row["block_hash"].lower():
                errors.append("block_hash_mismatch")
            if header["parentHash"].lower() != row["parent_hash"].lower():
                errors.append("parent_hash_mismatch")
            observed_ts = datetime.fromtimestamp(int(header["timestamp"], 16), timezone.utc)
            if observed_ts != parse_utc(row["timestamp_utc"]):
                errors.append("block_timestamp_mismatch")
            following = endpoint.block(row["block_number"] + 1)
            if not observed_ts <= parse_utc(row["target_timestamp_utc"]) < datetime.fromtimestamp(int(following["timestamp"], 16), timezone.utc):
                errors.append("nearest_prior_invariant_failed")
            if following["parentHash"].lower() != row["block_hash"].lower():
                errors.append("following_parent_hash_mismatch")
            calls, _ = _snapshot_calls(row["block_number"])
            actual = endpoint.batch(calls)
            expected = [row["raw_balances"][coin] for coin in COINS] + [row["raw_quotes"][direction][str(size)] for direction in DIRECTIONS for size in QUOTE_SIZES]
            if len(actual) != len(expected) or any(str(int(value, 16)) != raw for value, raw in zip(actual, expected)):
                errors.append("raw_contract_state_mismatch")
        except (RpcError, ValueError, TypeError, KeyError):
            errors.append("replication_rpc_failure")
        checks.append({"snapshot_id": row["id"], "block_number": row["block_number"],
                       "block_hash": row["block_hash"], "status": "FAIL" if errors else "PASS", "errors": errors})
    return {"status": "PASS" if all(row["status"] == "PASS" for row in checks) else "FAIL",
            "checked_blocks": len(checks), "total_distinct_blocks": len(unique), "sample_fraction": sample_fraction,
            "sampling": "deterministic SHA256 ordering of block hashes; rounded up",
            "provider_independence": "must be documented by the operator; not established automatically",
            "checked_at_utc": utc_text(datetime.now(timezone.utc)), "checks": checks,
            "request_count": endpoint.request_count}
