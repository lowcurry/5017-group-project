"""Independent historical acquisition from UTC tasks and public ABI configuration.

No Agent archive, metric implementation, saved block number, calldata or raw value
is an input. Historical ``get_dy`` is queried from the contract; this module does
not reimplement the StableSwap pricing formula. Different public RPC surfaces do
not establish independent backend operators. Offline replay establishes only that
saved RPC messages reproduce the saved decoding and source-selection result.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Callable

DEFAULT_CONFIG = {
    "version": "curve-3pool-public-abi-1",
    "chain_id": 1,
    "chain": "Ethereum Mainnet",
    "pool": "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7",
    "coin_order": ["DAI", "USDC", "USDT"],
    "decimals": {"DAI": 18, "USDC": 6, "USDT": 6},
    "quote_sizes": [1000, 10000, 100000],
    "directions": {"USDC_USDT": [1, 2], "USDT_USDC": [2, 1]},
    "selectors": {"balances(uint256)": "4903b0d1", "get_dy(int128,int128,uint256)": "5e0d443f"},
    "manual_review": {"status": "pending", "reviewers": [], "reviewed_at_utc": None},
}
DEFAULT_POLICY = {"attempts": 1, "timeout_seconds": 15, "retry_delay_seconds": 0,
                  "max_requests_per_provider": 2000, "max_targets": 1000, "location_strategy": "binary"}
_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_HEX = re.compile(r"0x[0-9a-fA-F]+\Z")


class SourceError(ValueError):
    """Invalid frozen task or ABI configuration; detected before network use."""


class SourceUnavailable(RuntimeError):
    """A sanitized acquisition failure, never a verdict that the Agent is wrong."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value):
    if not isinstance(value, str) or "T" not in value:
        raise SourceError("task timestamps must be explicit ISO UTC strings")
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SourceError("invalid UTC timestamp") from None
    if point.tzinfo is None or point.utcoffset() != timedelta(0) or point.microsecond:
        raise SourceError("task timestamps must use UTC and whole seconds")
    return point.astimezone(timezone.utc)


def _utc_text(point):
    return point.isoformat().replace("+00:00", "Z")


def _integer(value, minimum=0, maximum=2**256 - 1):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise SourceError("integer outside declared bounds")
    return value


def encode_call(selector: str, arguments: list[int]) -> str:
    """Encode nonnegative ABI integer words, including positive int128 indices."""
    if not isinstance(selector, str) or not re.fullmatch(r"[0-9a-fA-F]{8}", selector):
        raise SourceError("selector must contain exactly four bytes without 0x")
    if not isinstance(arguments, list):
        raise SourceError("ABI arguments must be a list")
    words = [_integer(argument).to_bytes(32, "big").hex() for argument in arguments]
    return "0x" + selector.lower() + "".join(words)


def decode_uint256(value: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise SourceUnavailable("contract return is not one uint256 ABI word")
    return str(int.from_bytes(bytes.fromhex(value[2:]), "big"))


def _quantity(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise SourceUnavailable("RPC quantity is malformed")
    number = int(value, 16)
    if number >= 2**64:
        raise SourceUnavailable("RPC block quantity exceeds supported bounds")
    return number


def _decimal_text(raw, precision):
    with localcontext() as context:
        context.prec = 100
        value = Decimal(raw) / (Decimal(10) ** precision)
        text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def validate_config(config):
    """Validate declared public configuration, without claiming human approval."""
    if not isinstance(config, dict):
        raise SourceError("configuration must be an object")
    result = copy.deepcopy(config)
    required = set(DEFAULT_CONFIG) - {"manual_review"}
    if set(result) - set(DEFAULT_CONFIG) or required - set(result):
        raise SourceError("configuration has missing or unexpected fields")
    if not isinstance(result["version"], str) or not result["version"]:
        raise SourceError("configuration version is required")
    _integer(result["chain_id"], 1, 2**32 - 1)
    if not isinstance(result["chain"], str) or not result["chain"] or not _ADDRESS.fullmatch(str(result["pool"])):
        raise SourceError("invalid chain or contract address")
    result["pool"] = result["pool"].lower()
    coins = result["coin_order"]
    if (not isinstance(coins, list) or len(coins) < 2 or len(coins) != len(set(coins)) or
            any(not isinstance(coin, str) or not re.fullmatch(r"[A-Z][A-Z0-9]*", coin) for coin in coins)):
        raise SourceError("coin order must contain distinct token identifiers")
    if not isinstance(result["decimals"], dict) or set(result["decimals"]) != set(coins):
        raise SourceError("decimals must match coin order")
    for value in result["decimals"].values():
        _integer(value, 0, 77)
    sizes = result["quote_sizes"]
    if not isinstance(sizes, list) or not sizes or len(sizes) != len(set(sizes)):
        raise SourceError("quote sizes must be a nonempty distinct list")
    for size in sizes:
        _integer(size, 1)
    directions = result["directions"]
    if not isinstance(directions, dict) or not directions:
        raise SourceError("quote directions are required")
    for name, pair in directions.items():
        if not isinstance(pair, list) or len(pair) != 2:
            raise SourceError("direction indices must be pairs")
        i, j = (_integer(value, 0, len(coins) - 1) for value in pair)
        if i == j or name != coins[i] + "_" + coins[j]:
            raise SourceError("direction name and token indices disagree")
        for size in sizes:
            _integer(size * 10 ** result["decimals"][coins[i]])
    signatures = {"balances(uint256)", "get_dy(int128,int128,uint256)"}
    if not isinstance(result["selectors"], dict) or set(result["selectors"]) != signatures:
        raise SourceError("declared ABI signatures must match the supported query interface")
    for selector in result["selectors"].values():
        encode_call(selector, [])
    return result


def _policy(policy):
    value = dict(DEFAULT_POLICY)
    if policy is not None:
        if not isinstance(policy, dict) or set(policy) - set(value):
            raise SourceError("unknown acquisition policy field")
        value.update(policy)
    if value["location_strategy"] not in ("binary", "sequential_bracket_600_v1"):
        raise SourceError("unsupported location strategy")
    _integer(value["attempts"], 1, 3)
    _integer(value["max_requests_per_provider"], 1, 100000)
    _integer(value["max_targets"], 1, 10000)
    for key, maximum in (("timeout_seconds", 60), ("retry_delay_seconds", 5)):
        if (not isinstance(value[key], (int, float)) or isinstance(value[key], bool) or
                not 0 <= value[key] <= maximum or (key == "timeout_seconds" and value[key] == 0)):
            raise SourceError("invalid bounded acquisition timing")
    return value


def plan_task(task, *, max_targets=1000):
    """Expand the entire declared UTC grid; restrictions never fetch excluded rows."""
    permitted = {"id", "start", "end", "interval_minutes", "allowed_targets"}
    if not isinstance(task, dict) or set(task) - permitted or not {"start", "end"} <= set(task):
        raise SourceError("task accepts only UTC bounds, interval, id and allowed targets")
    start, end = _utc(task["start"]), _utc(task["end"])
    step = _integer(task.get("interval_minutes", 60), 1, 10080)
    seconds = int((end - start).total_seconds())
    if seconds < 0 or seconds % (step * 60):
        raise SourceError("bounds must form a complete aligned sampling grid")
    count = seconds // (step * 60) + 1
    if count > max_targets:
        raise SourceError("task exceeds the frozen target budget")
    targets = [_utc_text(start + timedelta(minutes=step * index)) for index in range(count)]
    allowed = task.get("allowed_targets", targets)
    if not isinstance(allowed, list):
        raise SourceError("allowed targets must be a list")
    allowed = [_utc_text(_utc(value)) for value in allowed]
    if len(allowed) != len(set(allowed)) or set(allowed) - set(targets):
        raise SourceError("allowed targets must be distinct members of the frozen grid")
    identifier = task.get("id", "independent_source")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise SourceError("invalid source task id")
    return {"id": identifier, "start": targets[0], "end": targets[-1],
            "interval_minutes": step, "requested_targets": targets,
            "allowed_targets": [value for value in targets if value in set(allowed)],
            "excluded_targets": [value for value in targets if value not in set(allowed)]}


def _provider(provider):
    if (not isinstance(provider, dict) or set(provider) - {"name", "url", "documentation"} or
            not isinstance(provider.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", provider["name"])):
        raise SourceError("provider requires a stable public name")
    try:
        url = urllib.parse.urlsplit(provider["url"])
        if (url.scheme != "https" or not url.hostname or url.username or url.password or
                url.query or url.fragment or any(ord(char) < 32 for char in provider["url"])):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise SourceError("provider URL must be public HTTPS without credentials or query parameters") from None
    if "documentation" in provider and not isinstance(provider["documentation"], str):
        raise SourceError("provider documentation must be a string")
    return copy.deepcopy(provider)


class HttpTransport:
    """Explicit public URL only; no environment-based endpoint discovery."""
    def __init__(self, provider, timeout=15):
        self.provider, self.timeout = _provider(provider), timeout

    def __call__(self, payload):
        request = urllib.request.Request(self.provider["url"], data=_canonical(payload).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "curve-independent-source/1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise SourceUnavailable("RPC response exceeds response-size budget")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            # Preserve the public server's response even when HTTP status is an error.
            body = exc.read(2_000_000).decode("utf-8", errors="replace")
            return {"http_status": exc.code, "raw_body": body}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None)
            raise SourceUnavailable("RPC transport unavailable: " + type(exc).__name__ + "/" + type(reason).__name__) from None


class RecordingRpc:
    def __init__(self, transport: Callable, policy=None):
        self.transport, self.policy = transport, _policy(policy)
        self.transcript, self._headers, self._next_id = [], {}, 1

    def call(self, method, params):
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": copy.deepcopy(params)}
        self._next_id += 1
        last_reason = "RPC unavailable"
        for attempt in range(1, self.policy["attempts"] + 1):
            if len(self.transcript) >= self.policy["max_requests_per_provider"]:
                raise SourceUnavailable("frozen RPC request budget exhausted")
            entry = {"request": copy.deepcopy(payload), "attempt": attempt}
            started = time.monotonic()
            try:
                response = self.transport(copy.deepcopy(payload))
                _canonical(response)  # Only JSON-safe evidence can be saved and replayed.
                entry["raw_response"] = copy.deepcopy(response)
                if (not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or
                        type(response.get("id")) is not int or response.get("id") != payload["id"] or
                        "error" in response or "result" not in response):
                    raise SourceUnavailable("RPC response rejected or malformed")
                return copy.deepcopy(response["result"])
            except (SourceUnavailable, TimeoutError, OSError, ValueError, TypeError) as exc:
                last_reason = str(exc) if isinstance(exc, SourceUnavailable) else "RPC transport unavailable"
                entry["failure"] = last_reason
            finally:
                entry["elapsed_seconds"] = round(time.monotonic() - started, 6)
                self.transcript.append(entry)
            if attempt < self.policy["attempts"] and self.policy["retry_delay_seconds"]:
                time.sleep(self.policy["retry_delay_seconds"])
        raise SourceUnavailable(last_reason)

    def header(self, number):
        _integer(number, 0, 2**64 - 1)
        if number not in self._headers:
            raw = self.call("eth_getBlockByNumber", [hex(number), False])
            try:
                if not isinstance(raw, dict) or _quantity(raw["number"]) != number:
                    raise SourceUnavailable("RPC header has the wrong block number")
                if not _HASH.fullmatch(raw["hash"]) or not _HASH.fullmatch(raw["parentHash"]):
                    raise SourceUnavailable("RPC header hash is malformed")
                _quantity(raw["timestamp"])
            except (KeyError, TypeError):
                raise SourceUnavailable("RPC header is missing required fields") from None
            self._headers[number] = copy.deepcopy(raw)
        return self._headers[number]


def _locate_binary(rpc: RecordingRpc, target_utc: str):
    """Binary-search without Agent hints, then prove nearest-prior and ancestry."""
    target = int(_utc(target_utc).timestamp())
    head = _quantity(rpc.call("eth_blockNumber", []))
    first, last = rpc.header(0), rpc.header(head)
    if target < _quantity(first["timestamp"]) or target >= _quantity(last["timestamp"]):
        raise SourceUnavailable("target is outside provable historical chain bounds")
    low, high = 0, head
    while low < high:
        middle = low + (high - low + 1) // 2
        if _quantity(rpc.header(middle)["timestamp"]) <= target:
            low = middle
        else:
            high = middle - 1
    selected, following = rpc.header(low), rpc.header(low + 1)
    if not _quantity(selected["timestamp"]) <= target < _quantity(following["timestamp"]):
        raise SourceUnavailable("nearest-prior timestamp invariant failed")
    if following["parentHash"].lower() != selected["hash"].lower():
        raise SourceUnavailable("selected and following headers disagree on ancestry")
    return low, selected, following


def locate_block(rpc: RecordingRpc, target_utc: str):
    if rpc.policy.get("location_strategy", "binary") != "sequential_bracket_600_v1":
        return _locate_binary(rpc, target_utc)
    if not hasattr(rpc, "last_selected"):
        result = _locate_binary(rpc, target_utc)
    else:
        goal = int(_utc(target_utc).timestamp())
        lo, hi = rpc.last_selected, rpc.last_selected + 600
        if _quantity(rpc.header(lo)["timestamp"]) > goal:
            raise SourceUnavailable("sequential acquisition targets must be ascending")
        while _quantity(rpc.header(hi)["timestamp"]) <= goal:
            lo, hi = hi, hi + 600
        while hi - lo > 1:
            middle = (lo + hi) // 2
            if _quantity(rpc.header(middle)["timestamp"]) <= goal:
                lo = middle
            else:
                hi = middle
        result = (lo, rpc.header(lo), rpc.header(lo + 1))
    rpc.last_selected = result[0]
    return result


def _block_reference(header):
    return {"block_number": _quantity(header["number"]), "block_hash": header["hash"].lower(),
            "parent_hash": header["parentHash"].lower(),
            "timestamp_utc": _utc_text(datetime.fromtimestamp(_quantity(header["timestamp"]), timezone.utc))}


def acquire_snapshot(rpc, target_utc, identifier, config):
    """Construct every ABI call from the reviewed public config, then decode raw."""
    number, selected, following = locate_block(rpc, target_utc)
    coins, decimals = config["coin_order"], config["decimals"]
    balances, quotes, raw_balances, raw_quotes = {}, {}, {}, {}
    calldata = {"balances": {}, "quotes": {}}
    selectors = config["selectors"]
    for index, coin in enumerate(coins):
        encoded = encode_call(selectors["balances(uint256)"], [index])
        raw = decode_uint256(rpc.call("eth_call", [{"to": config["pool"], "data": encoded}, hex(number)]))
        calldata["balances"][coin] = encoded
        raw_balances[coin], balances[coin] = raw, _decimal_text(raw, decimals[coin])
    for direction, (i, j) in config["directions"].items():
        calldata["quotes"][direction], raw_quotes[direction], quotes[direction] = {}, {}, {}
        for size in config["quote_sizes"]:
            encoded = encode_call(selectors["get_dy(int128,int128,uint256)"], [i, j, size * 10 ** decimals[coins[i]]])
            raw = decode_uint256(rpc.call("eth_call", [{"to": config["pool"], "data": encoded}, hex(number)]))
            key = str(size)
            calldata["quotes"][direction][key] = encoded
            raw_quotes[direction][key], quotes[direction][key] = raw, _decimal_text(raw, decimals[coins[j]])
    # Re-fetch selected+following headers without the cache. This catches ordinary
    # provider changes during acquisition; it is not an eth_getProof state proof.
    for original in (selected, following):
        actual = rpc.call("eth_getBlockByNumber", [original["number"], False])
        if actual != original:
            raise SourceUnavailable("source changed selected headers during acquisition")
    return {"id": identifier, "label": "independent_utc_grid", "series": "hourly",
            "target_timestamp_utc": target_utc, **_block_reference(selected),
            "next_block": _block_reference(following), "contract_address": config["pool"],
            "raw_balances": raw_balances, "balances": balances, "raw_quotes": raw_quotes,
            "quotes": quotes, "calldata": calldata}


def _run_provider(plan, config, provider, policy, transport):
    rpc = RecordingRpc(transport, policy)
    result = {"provider": provider, "snapshots": [], "unknown_targets": [], "errors": []}
    try:
        if _quantity(rpc.call("eth_chainId", [])) != config["chain_id"]:
            raise SourceUnavailable("provider returned the wrong chain ID")
    except SourceUnavailable as exc:
        result["errors"].append({"stage": "chain_id", "reason": str(exc)})
        result["unknown_targets"] = list(plan["allowed_targets"])
    else:
        for index, target in enumerate(plan["requested_targets"]):
            if target not in plan["allowed_targets"]:
                continue
            try:
                result["snapshots"].append(acquire_snapshot(rpc, target, f"SRC_{index:04d}", config))
            except SourceUnavailable as exc:
                result["unknown_targets"].append(target)
                result["errors"].append({"stage": "snapshot", "target_timestamp_utc": target, "reason": str(exc)})
    result["status"] = "unknown" if result["unknown_targets"] else "available"
    result["rpc_attempt_count"] = len(rpc.transcript)
    result["transcript"] = rpc.transcript
    return result


def _merge_attempts(plan, config, attempts):
    snapshots, conflicts, unknown = [], [], []
    for target in plan["allowed_targets"]:
        available = [(attempt["provider"]["name"], row) for attempt in attempts for row in attempt["snapshots"]
                     if row["target_timestamp_utc"] == target]
        if not available:
            unknown.append(target)
        elif any(row != available[0][1] for _, row in available[1:]):
            conflicts.append({"target_timestamp_utc": target, "provider_names": [name for name, _ in available],
                              "reason": "independently retrieved source snapshots disagree"})
            unknown.append(target)
        else:
            snapshots.append(copy.deepcopy(available[0][1]))
    dataset = {"schema_version": "1.1", "fixture_status": "independent_historical_rpc_record",
               "chain_id": config["chain_id"], "chain": config["chain"], "pool": config["pool"],
               "coin_order": config["coin_order"], "decimals": config["decimals"], "quote_sizes": config["quote_sizes"],
               "sampling": {"start_utc": plan["start"], "end_utc": plan["end"], "interval_minutes": plan["interval_minutes"]},
               "snapshots": snapshots, "block_selection": "independent UTC binary search; selected <= target < following",
               "retrieval_method": "independently encoded block-pinned eth_call; raw RPC transcript retained"}
    return {"status": "unknown" if unknown else "available", "dataset": dataset,
            "unknown_targets": unknown, "source_conflicts": conflicts,
            "complete_allowed_grid": not unknown,
            "complete_requested_grid": not unknown and not plan["excluded_targets"]}


def collect_independent(task, config=None, providers=None, *, policy=None, transports=None):
    """Acquire all allowed grid rows from frozen providers; never substitutes tasks.

    ``transports`` maps provider names to injected JSON-RPC request callables for
    offline tests. All providers are attempted, so disagreement cannot be hidden
    by selecting a preferred successful response. Unavailable providers remain in
    the record; one available public source can supply a row, without a quorum or
    backend-independence claim. Conflicting available rows are excluded as unknown.
    """
    config, policy = validate_config(DEFAULT_CONFIG if config is None else config), _policy(policy)
    plan = plan_task(task, max_targets=policy["max_targets"])
    if not isinstance(providers, list) or not providers:
        raise SourceError("an explicit frozen list of public providers is required")
    providers = [_provider(provider) for provider in providers]
    names = [provider["name"] for provider in providers]
    if len(names) != len(set(names)) or len({provider["url"] for provider in providers}) != len(providers) or len(providers) > 4:
        raise SourceError("provider names and public URLs must be distinct; provider count bounded by four")
    if transports is not None and set(transports) != set(names):
        raise SourceError("injected transports must exactly cover the frozen providers")
    attempts = []
    for provider in providers:
        transport = transports[provider["name"]] if transports is not None else HttpTransport(provider, policy["timeout_seconds"])
        attempts.append(_run_provider(plan, config, provider, policy, transport))
    record = {"schema_version": "independent-source-1", "task": copy.deepcopy(task), "plan": plan,
              "config": config, "policy": policy, "providers": providers, "provider_attempts": attempts,
              **_merge_attempts(plan, config, attempts),
              "scope": "source acquisition and decoding; no arbitrary prose certification or local StableSwap formula",
              "operational_backend_independence_proven": False,
              "human_configuration_review_status": config.get("manual_review", {}).get("status", "not_recorded")}
    record["acquisition_mode"] = "injected_transport" if transports is not None else "public_https"
    record["dataset"]["fixture_status"] = ("injected_transport_record" if transports is not None
                                             else "independent_historical_rpc_record")
    record["content_sha256"] = _digest(record)
    return record


def _check_integrity(record):
    if not isinstance(record, dict) or record.get("schema_version") != "independent-source-1":
        raise SourceError("unsupported independent source record")
    body = {key: value for key, value in record.items() if key != "content_sha256"}
    if record.get("content_sha256") != _digest(body):
        raise SourceError("independent source record content hash mismatch")
    providers, attempts = record.get("providers"), record.get("provider_attempts")
    if not isinstance(providers, list) or not providers or not isinstance(attempts, list) or len(attempts) != len(providers):
        raise SourceError("provider attempts must exactly cover the declared providers")
    normalized = [_provider(provider) for provider in providers]
    if len({p["name"] for p in normalized}) != len(normalized) or len({p["url"] for p in normalized}) != len(normalized):
        raise SourceError("duplicate provider identity")
    for provider, attempt in zip(normalized, attempts):
        if not isinstance(attempt, dict) or attempt.get("provider") != provider:
            raise SourceError("provider attempts must preserve declared order and identities")
        if not isinstance(attempt.get("transcript"), list) or attempt.get("rpc_attempt_count") != len(attempt["transcript"]):
            raise SourceError("provider transcript count mismatch")



def compare_source_records(records):
    """Compare independently acquired records for one frozen task/configuration.

    This is not a majority vote: any disagreement between available records makes
    that target unknown. Different tasks or ABI configurations cannot be combined.
    """
    if not isinstance(records, list) or not records:
        raise SourceError("source comparison requires at least one record")
    for record in records:
        _check_integrity(record)
        if not replay_record(record)["pass"]:
            raise SourceError("source record does not reproduce from saved RPC transcript")
    baseline = records[0]
    if any(record["plan"] != baseline["plan"] or record["config"] != baseline["config"] for record in records[1:]):
        raise SourceError("source records must share the frozen task and public configuration")
    attempts = [attempt for record in records for attempt in record["provider_attempts"]]
    return {**_merge_attempts(baseline["plan"], baseline["config"], attempts),
            "source_record_sha256s": [record["content_sha256"] for record in records],
            "operational_backend_independence_proven": False}


def as_recompute_dataset(record):
    """Return only successful, nonconflicting rows; unavailable rows stay missing."""
    _check_integrity(record)
    if not replay_record(record)["pass"]:
        raise SourceError("source record does not reproduce from saved RPC transcript")
    return copy.deepcopy(record["dataset"])


class ReplayTransport:
    def __init__(self, transcript):
        self.transcript, self.position = transcript, 0
        self.mismatches = []

    def __call__(self, payload):
        if self.position >= len(self.transcript):
            self.mismatches.append("replay transcript exhausted")
            raise SourceUnavailable("replay transcript exhausted")
        entry = self.transcript[self.position]
        self.position += 1
        if entry.get("request") != payload:
            self.mismatches.append("replay request differs from saved RPC request")
            raise SourceUnavailable("replay request differs from saved RPC request")
        if "raw_response" in entry:
            return copy.deepcopy(entry["raw_response"])
        raise SourceUnavailable(entry.get("failure", "saved RPC transport unavailable"))


def replay_record(record):
    """Reexecute task planning, search, ABI construction and decoding offline."""
    _check_integrity(record)
    transports = {attempt["provider"]["name"]: ReplayTransport(attempt["transcript"])
                  for attempt in record["provider_attempts"]}
    replayed = collect_independent(record["task"], record["config"], record["providers"],
                                   policy=record["policy"], transports=transports)
    # Replay uses an injected transport internally; preserve the saved origin label
    # while still recomputing every request, decoded response and derived row.
    replayed["dataset"]["fixture_status"] = record["dataset"].get("fixture_status")
    keys = ("plan", "status", "dataset", "unknown_targets", "source_conflicts", "complete_allowed_grid", "complete_requested_grid")
    differences = [key for key in keys if replayed[key] != record[key]]
    for expected, actual in zip(record["provider_attempts"], replayed["provider_attempts"]):
        for key in ("provider", "status", "snapshots", "unknown_targets", "errors", "rpc_attempt_count"):
            if expected[key] != actual[key]:
                differences.append(expected["provider"]["name"] + ":" + key)
        cursor = transports[expected["provider"]["name"]]
        if cursor.mismatches:
            differences.append(expected["provider"]["name"] + ":replay_request_mismatch")
        if cursor.position != len(cursor.transcript):
            differences.append(expected["provider"]["name"] + ":unused_transcript_entries")
    return {"pass": not differences, "network_used": False, "differences": differences,
            "source_record_sha256": record["content_sha256"], "replayed_status": replayed["status"],
            "snapshot_count": len(replayed["dataset"]["snapshots"]),
            "scope": "saved messages reproduce source selection/decoding; not fresh chain attestation"}


def save_record(path, record):
    """Write one immutable record; replacing a previous run is rejected."""
    _check_integrity(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
