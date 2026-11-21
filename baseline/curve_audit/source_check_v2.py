"""Frozen, credential-free historical source checks against documented public RPCs.

Freeze selection BEFORE making requests. Public-provider agreement is not a proof
of operational backend independence, especially when the original URL is redacted.
All network requests are read-only; no wallet, credentials or configured private
endpoint is discovered or loaded by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from .common import canonical, now_utc, read_json, sha256_file, write_json
from .data import COINS, DIRECTIONS, QUOTE_SIZES, POOL, RpcClient, RpcError, load_dataset
from .metrics import snapshot_metrics

PUBLIC_PROVIDERS = {
    "publicnode": {"url": "https://ethereum-rpc.publicnode.com",
                   "documentation": "https://ethereum.publicnode.com/"},
    "1rpc": {"url": "https://public.1rpc.io/eth",
             "documentation": "https://docs.1rpc.io/overview/supported-networks"},
    "drpc": {"url": "https://eth.drpc.org/",
             "documentation": "https://drpc.org/docs/ethereum-api"},
    "cloudflare_trial": {"url": "https://web3-trial.cloudflare-eth.com/v1/mainnet",
                         "documentation": "https://developers.cloudflare.com/web3/how-to/use-ethereum-gateway/"},
}
SEED = "curve-audit-v2-source-check-20261011"


def selection(dataset: dict) -> tuple[list[dict], dict]:
    """Five deterministic risk anchors plus 10% of all rows by hash rank."""
    from decimal import Decimal
    rows = sorted(dataset["snapshots"], key=lambda r: r["target_timestamp_utc"])
    metrics = {r["id"]: snapshot_metrics(r) for r in rows}
    anchor_selectors = {
        "first_target": rows[0], "last_target": rows[-1],
        "maximum_usdc_nominal_share": max(rows, key=lambda r: Decimal(metrics[r["id"]]["share_pct"]["USDC"])),
        "minimum_usdc_usdt_100000_rate": min(rows, key=lambda r: Decimal(metrics[r["id"]]["quote_rates"]["USDC_USDT"]["100000"])),
        "maximum_usdc_usdt_size_sensitivity": max(rows, key=lambda r: Decimal(metrics[r["id"]]["size_sensitivity_bps"]["USDC_USDT"]["100000"])),
    }
    sample_count = math.ceil(len(rows) * 0.10)
    ranked = sorted(rows, key=lambda r: hashlib.sha256((SEED + ":" + r["id"]).encode()).hexdigest())
    selected_ids = {r["id"] for r in ranked[:sample_count]} | {r["id"] for r in anchor_selectors.values()}
    selected = [r for r in rows if r["id"] in selected_ids]
    return selected, {"seed": SEED, "total_snapshot_count": len(rows),
                      "random_rank_sample_count": sample_count,
                      "anchor_selectors": {k: v["id"] for k, v in anchor_selectors.items()},
                      "selected_count_after_deduplication": len(selected)}


def freeze_protocol(dataset_path: Path, output_dir: Path) -> dict:
    dataset = load_dataset(dataset_path)
    rows, design = selection(dataset)
    protocol = {"schema_version": "2.0", "created_at_utc": now_utc(),
                "dataset_relative_path": "data/raw/expanded_archive.json",
                "dataset_sha256": sha256_file(dataset_path), "selection": design,
                "selected_snapshots": [{k: r[k] for k in ("id", "target_timestamp_utc", "block_number", "block_hash")} for r in rows],
                "providers": PUBLIC_PROVIDERS,
                "comparisons": ["selected block hash/parent hash/timestamp", "next block header", "three raw balances", "six raw quote outputs"],
                "failure_policy": "Probe chain/header/archive balance once per provider; if unavailable, skip repeated equivalent calls and record unattempted fields. No replacement snapshots selected after network outcomes.",
                "interpretation": "Agreement is field-level for selected states only. Different public provider surfaces do not prove disjoint upstream operators; original configured endpoint is redacted. No full-dataset or cryptographic state proof is claimed."}
    destination = output_dir / "protocol.json"
    if destination.exists():
        old = read_json(destination)
        if old["dataset_sha256"] != protocol["dataset_sha256"] or old["selected_snapshots"] != protocol["selected_snapshots"] or old["providers"] != protocol["providers"]:
            raise ValueError("Frozen protocol differs; use a new directory")
        return old
    write_json(destination, protocol)
    return protocol


def _header_result(expected: dict, actual: dict) -> dict:
    from datetime import datetime, timezone
    checks = {"block_hash": actual["hash"].lower() == expected["block_hash"].lower(),
              "parent_hash": actual["parentHash"].lower() == expected["parent_hash"].lower(),
              "timestamp": datetime.fromtimestamp(int(actual["timestamp"], 16), timezone.utc).isoformat().replace("+00:00", "Z") == expected["timestamp_utc"]}
    return {"status": "agree" if all(checks.values()) else "disagree", "checks": checks,
            "observed": {"hash": actual["hash"], "parent_hash": actual["parentHash"], "timestamp_hex": actual["timestamp"]}}


def _uint_call(client: RpcClient, block: int, data: str) -> str:
    value = client.call("eth_call", [{"to": POOL, "data": data}, hex(block)])
    if not isinstance(value, str) or len(value) != 66 or not value.startswith("0x"):
        raise RpcError("Invalid uint256 contract return")
    try:
        return str(int(value, 16))
    except ValueError:
        raise RpcError("Invalid uint256 contract return") from None


def check_provider(dataset: dict, protocol: dict, provider_name: str, *, client=None) -> dict:
    """Injectable client makes disagreement/failure semantics testable offline."""
    if provider_name not in protocol["providers"]:
        raise ValueError("Provider is not in frozen public-provider allowlist")
    provider = protocol["providers"][provider_name]
    client = client or RpcClient(provider["url"], minimum_interval=0.15, timeout=8)
    by_id = {r["id"]: r for r in dataset["snapshots"]}
    rows = [by_id[r["id"]] for r in protocol["selected_snapshots"]]
    output = {"provider": provider_name, "public_endpoint": provider["url"],
              "documentation": provider["documentation"], "started_at_utc": now_utc(),
              "network_mode": "read_only_public_RPC", "protocol_selection_preserved": True,
              "status": "unavailable", "snapshots": [], "errors": []}
    try:
        chain = client.call("eth_chainId", [])
        if chain != "0x1":
            raise RpcError("Endpoint did not return Ethereum Mainnet chain ID")
        output["chain_id"] = 1
    except (RpcError, ValueError, TypeError) as exc:
        output["errors"].append({"stage": "chain_id", "reason": str(exc)})
        output["not_attempted"] = "All selected headers and state calls skipped after chain-ID failure"
        output["counts"] = {"selected_snapshot_count": len(rows), "visited_snapshot_count": 0,
                            "header_objects_agree": 0, "header_objects_disagree": 0,
                            "state_fields_agree": 0, "state_fields_disagree": 0,
                            "state_fields_unavailable": 0, "state_fields_not_attempted": len(rows) * 9}
        output["completed_at_utc"] = now_utc()
        return output
    archive_available, headers_available = None, True
    for row in rows:
        record = {"snapshot_id": row["id"], "block_number": row["block_number"], "headers": {}, "state": []}
        if headers_available:
            try:
                record["headers"]["selected"] = _header_result(row, client.block(row["block_number"]))
                record["headers"]["next"] = _header_result(row["next_block"], client.block(row["block_number"] + 1))
            except (RpcError, ValueError, KeyError, TypeError) as exc:
                record["headers"] = {"status": "unavailable", "reason": str(exc)}
                headers_available = False
        else:
            record["headers"] = {"status": "not_attempted", "reason": "Equivalent header probe failed"}
        if archive_available is not False:
            calls = [("balance:" + coin, row["calldata"]["balances"][coin], row["raw_balances"][coin]) for coin in COINS]
            calls += [(f"quote:{direction}:{size}", row["calldata"]["quotes"][direction][str(size)], row["raw_quotes"][direction][str(size)]) for direction in DIRECTIONS for size in QUOTE_SIZES]
            for name, data, expected in calls:
                try:
                    actual = _uint_call(client, row["block_number"], data)
                    record["state"].append({"field": name, "status": "agree" if actual == expected else "disagree", "expected_raw": expected, "observed_raw": actual})
                    archive_available = True
                except RpcError as exc:
                    record["state"].append({"field": name, "status": "unavailable", "reason": str(exc)})
                    # Only the initial archive probe gates equivalent later calls.
                    if archive_available is None:
                        archive_available = False
                        break
        if archive_available is False:
            record["state_skipped_reason"] = "Initial block-pinned historical eth_call unavailable; remaining calls not attempted"
        output["snapshots"].append(record)
        if not headers_available and archive_available is False:
            output["not_attempted"] = f"Remaining {len(rows) - len(output['snapshots'])} selected snapshots skipped after both header and initial archive failures"
            break
    state = [x for r in output["snapshots"] for x in r["state"]]
    headers = [x for r in output["snapshots"] for x in r["headers"].values() if isinstance(x, dict) and "checks" in x]
    output["counts"] = {"selected_snapshot_count": len(rows), "visited_snapshot_count": len(output["snapshots"]),
                        "header_objects_agree": sum(x["status"] == "agree" for x in headers),
                        "header_objects_disagree": sum(x["status"] == "disagree" for x in headers),
                        "state_fields_agree": sum(x["status"] == "agree" for x in state),
                        "state_fields_disagree": sum(x["status"] == "disagree" for x in state),
                        "state_fields_unavailable": sum(x["status"] == "unavailable" for x in state),
                        "state_fields_not_attempted": len(rows) * 9 - len(state)}
    counts = output["counts"]
    if counts["header_objects_disagree"] or counts["state_fields_disagree"]:
        output["status"] = "disagreement"
    elif counts["header_objects_agree"] == len(rows) * 2 and counts["state_fields_agree"] == len(rows) * 9:
        output["status"] = "selected_fields_agree"
    elif counts["header_objects_agree"] or counts["state_fields_agree"]:
        output["status"] = "partial_agreement"
    output["request_count"] = client.request_count
    output["request_count_scope"] = "JSON-RPC messages receiving decodable transport responses, as counted by existing safe RpcClient; excludes transport failures"
    output["completed_at_utc"] = now_utc()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run", "summarize"])
    parser.add_argument("--dataset", type=Path, default=Path("data/raw/expanded_archive.json"))
    parser.add_argument("--out", type=Path, default=Path("results/source_check_v2"))
    parser.add_argument("--provider", choices=list(PUBLIC_PROVIDERS))
    args = parser.parse_args()
    if args.action == "freeze":
        protocol = freeze_protocol(args.dataset, args.out)
        print(json.dumps(protocol["selection"], ensure_ascii=False))
        return
    protocol = read_json(args.out / "protocol.json")
    if args.action == "run":
        if sha256_file(args.dataset) != protocol["dataset_sha256"]:
            raise ValueError("Dataset changed after protocol freeze")
        if not args.provider:
            parser.error("--provider required for run")
        destination = args.out / (args.provider + ".json")
        if destination.exists():
            raise ValueError("Existing network result preserved; choose a fresh protocol directory")
        result = check_provider(load_dataset(args.dataset), protocol, args.provider)
        write_json(destination, result)
        print(json.dumps({k: result.get(k) for k in ("provider", "status", "counts", "errors")}, ensure_ascii=False))
    else:
        attempts = [read_json(args.out / (key + ".json")) for key in protocol["providers"] if (args.out / (key + ".json")).exists()]
        summary = {"schema_version": "2.0", "protocol_sha256": sha256_file(args.out / "protocol.json"),
                   "providers_attempted": len(attempts), "selected_snapshot_count": len(protocol["selected_snapshots"]),
                   "successful_selected_field_agreement": any(x["status"] == "selected_fields_agree" for x in attempts),
                   "provider_statuses": {x["provider"]: x["status"] for x in attempts},
                   "field_counts_by_provider": {x["provider"]: x.get("counts") for x in attempts},
                   "result_sha256_by_provider": {x["provider"]: sha256_file(args.out / (x["provider"] + ".json")) for x in attempts},
                   "full_dataset_second_source_checked": False, "operational_backend_independence_proven": False,
                   "limitations": [protocol["interpretation"], "Unavailable responses do not validate or refute archived numeric values."]}
        write_json(args.out / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
