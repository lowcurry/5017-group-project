"""Thirty-two deterministic engineering fault scenarios, with offline replay.

The chain, raw values and RPC responses here are SYNTHETIC program fixtures.
These are neither newly collected blockchain observations, model experiment
outputs, nor human-checked reference answers. The suite is an engineering
regression matrix whose scenarios are known during implementation, not a held-out
scientific evaluation. A saved transcript is reproducible evidence, not a
cryptographic authentication of independently retrieved Ethereum state.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

from .independent_source import (DEFAULT_CONFIG, SourceError, as_recompute_dataset,
                                 collect_independent, encode_call, replay_record)
from .verification import normalize_query, recompute, verify_claims

_BASE = 1_678_406_400  # Only a synthetic fixture clock; not actual historical state.
_PROVIDER = {"name": "synthetic_fixture_a", "url": "https://example.com/offline-rpc",
             "documentation": "Offline injected transport; this URL is never contacted"}
_PROVIDER_B = {"name": "synthetic_fixture_b", "url": "https://example.org/offline-rpc",
               "documentation": "Offline injected transport; this URL is never contacted"}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _time(number):
    return datetime.fromtimestamp(_BASE + number * 12, timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(number):
    return "0x" + hashlib.sha256(f"confirmation-synthetic-block:{number}".encode()).hexdigest()


def _decimal(raw, decimals):
    with localcontext() as context:
        context.prec = 100
        text = format(Decimal(raw) / Decimal(10) ** decimals, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class _FixtureTransport:
    """Self-contained state generator, unrelated to any old experiment record."""
    def __init__(self, *, raw_offset=0, fail_transport=False, fail_archive=False, change_header=False):
        self.raw_offset, self.fail_transport = raw_offset, fail_transport
        self.fail_archive, self.change_header = fail_archive, change_header
        self.header_requests = {}

    def __call__(self, request):
        if self.fail_transport:
            raise TimeoutError("synthetic timeout")
        method, params = request["method"], request["params"]
        if method == "eth_chainId":
            value = "0x1"
        elif method == "eth_blockNumber":
            value = hex(2000)
        elif method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            value = {"number": hex(number), "hash": _hash(number), "parentHash": _hash(number - 1),
                     "timestamp": hex(_BASE + number * 12)}
            self.header_requests[number] = self.header_requests.get(number, 0) + 1
            if self.change_header and self.header_requests[number] > 1:
                value["hash"] = "0x" + "a" * 64
        elif method == "eth_call":
            if self.fail_archive:
                return {"jsonrpc": "2.0", "id": request["id"],
                        "error": {"code": -32000, "message": "synthetic historical state unavailable"}}
            number = int(params[1], 16)
            data = params[0]["data"]
            arguments = [int(data[position:position + 64], 16) for position in range(10, len(data), 64)]
            if data[2:10] == "4903b0d1":
                coin = arguments[0]
                nominal = (coin + 1) * 1000 + (number % (11 + coin)) * (10 + coin)
                raw = nominal * 10 ** (18 if coin == 0 else 6)
            elif data[2:10] == "5e0d443f":
                i, j, raw_input = arguments
                coefficient = 998000 + number % 97 + i * 30 + j * 7 - raw_input // 10**10
                raw = raw_input * coefficient // 10**6
            else:
                raise AssertionError("fixture received an undeclared selector")
            value = "0x" + (raw + self.raw_offset).to_bytes(32, "big").hex()
        else:
            raise AssertionError("fixture received an undeclared RPC method")
        return {"jsonrpc": "2.0", "id": request["id"], "result": value}


def _source(first_block, *, allowed=None, mode="available"):
    task = {"id": f"fixture_grid_{first_block}", "start": _time(first_block), "end": _time(first_block + 10), "interval_minutes": 1}
    if allowed is not None:
        task["allowed_targets"] = [_time(first_block + offset) for offset in allowed]
    providers, transports = [_PROVIDER], {_PROVIDER["name"]: _FixtureTransport()}
    if mode == "transport_timeout":
        transports[_PROVIDER["name"]] = _FixtureTransport(fail_transport=True)
    elif mode == "historical_state_unavailable":
        transports[_PROVIDER["name"]] = _FixtureTransport(fail_archive=True)
    elif mode == "source_conflict":
        providers = [_PROVIDER, _PROVIDER_B]
        transports[_PROVIDER_B["name"]] = _FixtureTransport(raw_offset=1)
    elif mode == "source_header_change":
        transports[_PROVIDER["name"]] = _FixtureTransport(change_header=True)
    elif mode != "available":
        raise ValueError("unknown synthetic source mode")
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["version"] = "synthetic-engineering-fixture-abi-1"
    config["manual_review"] = {"status": "not_human_reviewed_synthetic_fixture", "reviewers": [], "reviewed_at_utc": None}
    return collect_independent(task, config, providers, transports=transports)


def _query(first_block, *, metric="share_pct", operation="first", window=False, direction="USDC_USDT"):
    query = {"metric": metric, "operation": operation,
             "start": _time(first_block), "end": _time(first_block + 10 if window else first_block)}
    if metric == "share_pct":
        query["coin"] = "USDC"
    elif metric in ("quote_rate", "size_sensitivity_bps"):
        query.update(direction=direction, size=100000)
    return query


def _supported_report(dataset, query, task_id):
    expected = recompute(dataset, query)
    return {"task_id": task_id, "answer": "Synthetic engineering fixture result; not a real chain observation.",
            "claims": [{"claim_id": "C1", "status": "supported", "calculation_id": "fixture_calculation_C1", **expected}]}


def _refusal_report(query, task_id):
    return {"task_id": task_id,
            "answer": "The declared evidence scope has no complete observations for this query, so no numeric value is asserted.",
            "claims": [{"claim_id": "C1", "status": "unsupported", "query": query,
                        "value": None, "evidence_ids": []}]}


def _case(identifier, category, family, query, dataset, report, source, *, issue=None, injection=None):
    verdict = {"faulty": "error", "clean": "supported", "refusal": "refusal_supported", "unknown": "unknown"}[category]
    return {"id": identifier, "category": category, "family": family,
            "fixture_status": "synthetic_engineering_regression_fixture",
            "declared_task": {"task_id": report["task_id"], "required_claims": [{"claim_id": "C1", "query": query}]},
            "archive_dataset": dataset, "report": report, "source_record": source,
            "expectation": {"verdict": verdict, "required_issue_code": issue},
            "injection": injection}


def build_scenarios():
    """Return twelve error/clean pairs, four refusals and four unknown cases."""
    families = ["numeric_level", "numeric_change", "share_unit", "quote_unit", "block_number", "block_hash",
                "window_specification", "window_missing_sample", "quote_direction", "quote_calldata_direction",
                "self_consistent_raw_balance", "self_consistent_raw_quote"]
    required = ["value_mismatch", "value_mismatch", "unit_mismatch", "unit_mismatch", "block_number_mismatch",
                "block_hash_mismatch", "query_window_mismatch", "window_gap", "query_direction_mismatch",
                "calldata_direction_mismatch", "source_raw_balance_mismatch", "source_raw_quote_mismatch"]
    scenarios = []
    for index, (family, issue) in enumerate(zip(families, required)):
        first = 100 + index * 20
        source = _source(first)
        dataset = as_recompute_dataset(source)
        if index in (1, 6):
            query = _query(first, operation="change", window=True)
        elif index == 7:
            query = _query(first, metric="imbalance_l1", operation="maximum", window=True)
        elif index in (3, 8, 9, 11):
            query = _query(first, metric="quote_rate")
        else:
            query = _query(first)
        task_id = f"fixture_task_{index + 1:02d}"
        clean_report = _supported_report(dataset, query, task_id)
        if index == 0:
            # Matching clean case verifies an allowed precision offset below 1e-8.
            clean_report["claims"][0]["value"] = str(Decimal(clean_report["claims"][0]["value"]) + Decimal("0.000000005"))
        scenarios.append(_case(f"C{index + 1:02d}", "clean", family, query, copy.deepcopy(dataset),
                               copy.deepcopy(clean_report), copy.deepcopy(source)))
        faulty_dataset, faulty_report = copy.deepcopy(dataset), copy.deepcopy(clean_report)
        claim = faulty_report["claims"][0]
        injection = {"family": family, "changed_path": None, "before": None, "after": None}
        if index in (0, 1):
            injection.update(changed_path="report.claims[0].value", before=claim["value"])
            claim["value"] = str(Decimal(claim["value"]) + (Decimal("0.1") if index == 0 else Decimal("-1")))
            injection["after"] = claim["value"]
        elif index in (2, 3):
            injection.update(changed_path="report.claims[0].unit", before=claim["unit"])
            claim["unit"] = "dimensionless" if index == 2 else "USDC/USDT"
            injection["after"] = claim["unit"]
        elif index in (4, 5):
            key = "block_number" if index == 4 else "block_hash"
            injection.update(changed_path="report.claims[0]." + key, before=claim[key])
            claim[key] = claim[key] + 1 if index == 4 else "0x" + "f" * 64
            injection["after"] = claim[key]
        elif index == 6:
            changed_query = {**query, "end": _time(first + 5)}
            injection.update(changed_path="report.claims[0].query.end", before=query["end"], after=changed_query["end"])
            faulty_report = _supported_report(faulty_dataset, changed_query, task_id)
        elif index == 7:
            removed = faulty_dataset["snapshots"].pop(1)
            injection.update(changed_path="archive_dataset.snapshots[1]", before=removed, after=None)
        elif index == 8:
            changed_query = {**query, "direction": "USDT_USDC"}
            injection.update(changed_path="report.claims[0].query.direction", before=query["direction"], after=changed_query["direction"])
            faulty_report = _supported_report(faulty_dataset, changed_query, task_id)
        elif index == 9:
            row = faulty_dataset["snapshots"][0]
            injection.update(changed_path="archive_dataset.snapshots[0].calldata.quotes.USDC_USDT.100000",
                             before=row["calldata"]["quotes"]["USDC_USDT"]["100000"])
            row["calldata"]["quotes"]["USDC_USDT"]["100000"] = encode_call("5e0d443f", [2, 1, 100000 * 10**6])
            injection["after"] = row["calldata"]["quotes"]["USDC_USDT"]["100000"]
        elif index == 10:
            row = faulty_dataset["snapshots"][0]
            injection.update(changed_path="archive_dataset.snapshots[0].raw_balances.USDC+balances.USDC",
                             before={"raw": row["raw_balances"]["USDC"], "normalized": row["balances"]["USDC"]})
            row["raw_balances"]["USDC"] = str(int(row["raw_balances"]["USDC"]) + 10**12)
            row["balances"]["USDC"] = _decimal(row["raw_balances"]["USDC"], 6)
            injection["after"] = {"raw": row["raw_balances"]["USDC"], "normalized": row["balances"]["USDC"]}
            faulty_report = _supported_report(faulty_dataset, query, task_id)
        else:
            row = faulty_dataset["snapshots"][0]
            injection.update(changed_path="archive_dataset.snapshots[0].raw_quotes.USDC_USDT.100000+quotes.USDC_USDT.100000",
                             before={"raw": row["raw_quotes"]["USDC_USDT"]["100000"], "normalized": row["quotes"]["USDC_USDT"]["100000"]})
            row["raw_quotes"]["USDC_USDT"]["100000"] = str(int(row["raw_quotes"]["USDC_USDT"]["100000"]) + 100 * 10**6)
            row["quotes"]["USDC_USDT"]["100000"] = _decimal(row["raw_quotes"]["USDC_USDT"]["100000"], 6)
            injection["after"] = {"raw": row["raw_quotes"]["USDC_USDT"]["100000"], "normalized": row["quotes"]["USDC_USDT"]["100000"]}
            faulty_report = _supported_report(faulty_dataset, query, task_id)
        scenarios.append(_case(f"F{index + 1:02d}", "faulty", family, query, faulty_dataset, faulty_report,
                               copy.deepcopy(source), issue=issue, injection=injection))
    for index in range(4):
        first = 500 + index * 20
        allowed = [0, 10] if index == 2 else ([5, 10] if index == 3 else None)
        source = _source(first, allowed=allowed)
        dataset = as_recompute_dataset(source)
        if index == 0:
            query = _query(first - 5)
        elif index == 1:
            query = _query(first + 15)
        elif index == 2:
            query = _query(first, metric="imbalance_l1", operation="maximum", window=True)
        else:
            query = _query(first)
        report = _refusal_report(query, f"refusal_task_{index + 1}")
        scenarios.append(_case(f"R{index + 1:02d}", "refusal", ["before_coverage", "after_coverage", "scope_internal_gap", "scope_excluded_endpoint"][index],
                               query, dataset, report, source))
    for index, mode in enumerate(("transport_timeout", "historical_state_unavailable", "source_conflict", "source_header_change")):
        first = 700 + index * 20
        baseline = _source(first)
        dataset = as_recompute_dataset(baseline)
        query = _query(first)
        report = _supported_report(dataset, query, f"unknown_task_{index + 1}")
        scenarios.append(_case(f"U{index + 1:02d}", "unknown", mode, query, dataset, report, _source(first, mode=mode)))
    return scenarios


def _error_code(message):
    tests = [("missing hourly observations", "window_gap"), ("calldata direction/amount mismatch", "calldata_direction_mismatch"),
             ("value differs", "value_mismatch"), ("unit disagrees", "unit_mismatch"),
             ("block_number does not match", "block_number_mismatch"), ("block_hash does not match", "block_hash_mismatch"),
             ("unnecessary refusal", "unnecessary_refusal"), ("evidence does not exactly support", "evidence_window_mismatch")]
    return next((code for needle, code in tests if needle in message), "structured_validation_error")


def _task_issues(declared, report):
    issues = []
    if report.get("task_id") != declared["task_id"]:
        issues.append({"code": "task_id_mismatch", "reason": "report task id differs from the frozen task"})
    claims = {claim.get("claim_id"): claim for claim in report.get("claims", []) if isinstance(claim, dict)}
    for slot in declared["required_claims"]:
        claim = claims.get(slot["claim_id"])
        if claim is None:
            issues.append({"code": "required_claim_missing", "reason": slot["claim_id"] + " is missing"})
            continue
        try:
            expected, actual = normalize_query(slot["query"]), normalize_query(claim.get("query"))
        except (ValueError, TypeError):
            issues.append({"code": "task_query_invalid", "reason": "required query is not a supported frozen query"})
            continue
        differing = sorted(key for key in set(expected) | set(actual) if expected.get(key) != actual.get(key))
        if differing:
            code = "query_window_mismatch" if any(key in differing for key in ("start", "end")) else ("query_direction_mismatch" if "direction" in differing else "query_specification_mismatch")
            issues.append({"code": code, "reason": "claim query differs from frozen task fields: " + ", ".join(differing)})
    return issues


def _source_issues(archive, independent):
    issues = []
    rows = {row["target_timestamp_utc"]: row for row in archive.get("snapshots", [])}
    for source_row in independent.get("snapshots", []):
        target = source_row["target_timestamp_utc"]
        row = rows.get(target)
        if row is None:
            issues.append({"code": "source_snapshot_missing", "reason": "archive lacks independently acquired target " + target})
            continue
        for key in ("block_number", "block_hash", "parent_hash", "timestamp_utc", "next_block", "contract_address"):
            if row.get(key) != source_row.get(key):
                issues.append({"code": "source_" + key + "_mismatch", "reason": target + " differs in " + key})
        for key, code in (("raw_balances", "source_raw_balance_mismatch"), ("raw_quotes", "source_raw_quote_mismatch")):
            if row.get(key) != source_row.get(key):
                issues.append({"code": code, "reason": target + " archived " + key + " differs from independently decoded response"})
    return issues


def assess_scenario(scenario):
    """Assess actual inputs; expected labels are never used to choose a verdict."""
    report, archive = scenario["report"], scenario["archive_dataset"]
    same_archive = verify_claims(archive, report)
    issues = _task_issues(scenario["declared_task"], report)
    issues.extend({"code": _error_code(message), "reason": message} for message in same_archive["errors"])
    try:
        source = scenario["source_record"]
        replay = replay_record(source)
        if not replay["pass"]:
            raise SourceError("source transcript does not reproduce the saved source data")
        independent = as_recompute_dataset(source)
    except (SourceError, ValueError, KeyError, TypeError) as exc:
        return {"verdict": "unknown", "issues": [{"code": "source_record_invalid", "reason": str(exc)}],
                "issue_codes": ["source_record_invalid"], "same_archive_verification": same_archive,
                "source_replay": {"pass": False, "network_used": False}}
    if source["status"] != "available":
        code = "source_conflict_unknown" if source["source_conflicts"] else "source_unavailable_unknown"
        return {"verdict": "unknown", "issues": [{"code": code, "reason": "source failure/conflict cannot establish an Agent error"}],
                "issue_codes": [code], "same_archive_verification": same_archive, "source_replay": replay}
    issues.extend(_source_issues(archive, independent))
    claims = report.get("claims", [])
    verdict = "error" if issues else ("refusal_supported" if claims and all(claim.get("status") == "unsupported" for claim in claims) else "supported")
    return {"verdict": verdict, "issues": issues, "issue_codes": sorted({issue["code"] for issue in issues}),
            "same_archive_verification": same_archive, "source_replay": replay}


def summarize(scenarios, outcomes):
    """Unknown binary cases are listed separately, never forced into FP or FN."""
    if len(scenarios) != len(outcomes):
        raise ValueError("scenario/outcome count differs")
    binary = {key: 0 for key in ("TP", "FN", "FP", "TN", "unknown_faulty", "unknown_clean", "expected_faulty", "expected_clean")}
    refusal = {"expected": 0, "correct": 0, "incorrect": 0, "unknown": 0}
    unknown = {"expected": 0, "correct": 0, "incorrect": 0}
    checks = []
    for scenario, outcome in zip(scenarios, outcomes):
        category, expected = scenario["category"], scenario["expectation"]
        verdict = outcome["verdict"]
        corresponding = expected["required_issue_code"] is None or expected["required_issue_code"] in outcome["issue_codes"]
        matched = verdict == expected["verdict"] and corresponding
        checks.append({"id": scenario["id"], "category": category, "family": scenario["family"],
                       "expected_verdict": expected["verdict"], "actual_verdict": verdict,
                       "required_issue_code": expected["required_issue_code"], "corresponding_issue_detected": corresponding,
                       "expected_behavior_observed": matched,
                       "same_archive_pass": outcome["same_archive_verification"]["pass"]})
        if category == "faulty":
            binary["expected_faulty"] += 1
            binary["unknown_faulty" if verdict == "unknown" else ("TP" if verdict == "error" and corresponding else "FN")] += 1
        elif category == "clean":
            binary["expected_clean"] += 1
            binary["unknown_clean" if verdict == "unknown" else ("FP" if verdict == "error" else "TN")] += 1
        elif category == "refusal":
            refusal["expected"] += 1
            refusal["unknown" if verdict == "unknown" else ("correct" if matched else "incorrect")] += 1
        elif category == "unknown":
            unknown["expected"] += 1
            unknown["correct" if matched else "incorrect"] += 1
        else:
            raise ValueError("unexpected scenario category")
    positives, negatives = binary["TP"] + binary["FN"], binary["FP"] + binary["TN"]
    binary.update(evaluable_faulty=positives, evaluable_clean=negatives,
                  sensitivity=binary["TP"] / positives if positives else None,
                  false_positive_rate=binary["FP"] / negatives if negatives else None)
    return {"scenario_count": len(scenarios), "pass": all(check["expected_behavior_observed"] for check in checks),
            "expected_behaviors_observed": sum(check["expected_behavior_observed"] for check in checks),
            "binary_detection": binary, "legal_refusals": refusal, "source_unknowns": unknown, "scenario_checks": checks,
            "scope": "synthetic engineering regression matrix; no model experiment, new chain acquisition or human reference validation",
            "limitations": ["Cases were known during engineering; this is not held-out generalization evidence.",
                            "Source-transcript replay detects inconsistent saved artifacts, not coordinated forgery of all raw responses and hashes.",
                            "Unknown acquisition cases are reported separately from false positives and false negatives."]}


def _write_new(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _code_hashes():
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("confirmation_faults.py", "independent_source.py", "verification.py")}


def run_fault_matrix(output_dir):
    """Run all actual checks and save fresh inputs/results; refuse any overwrite."""
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError("fault output already exists; a completed or partial run is never overwritten")
    scenarios = build_scenarios()
    outcomes = [assess_scenario(scenario) for scenario in scenarios]
    inputs = {"schema_version": "confirmation-fault-inputs-1", "scenarios": scenarios}
    protocol = {"schema_version": "confirmation-fault-protocol-1", "fixture_status": "synthetic_engineering_regression_fixture",
                "planned_counts": {"faulty": 12, "clean": 12, "refusal": 4, "unknown": 4},
                "scenario_inputs_sha256": _digest(inputs), "implementation_sha256": _code_hashes(),
                "failure_policy": "save actual outcomes; do not replace failures or count unrelated errors as injection detection",
                "human_checked_reference_answers": False, "network_acquisition_performed": False, "model_calls": 0,
                "held_out_evaluation": False}
    results = {"schema_version": "confirmation-fault-results-1", "protocol_sha256": _digest(protocol),
               "scenario_inputs_sha256": _digest(inputs),
               "outcomes": [{"id": scenario["id"], **outcome} for scenario, outcome in zip(scenarios, outcomes)]}
    summary = {"schema_version": "confirmation-fault-summary-1", **summarize(scenarios, outcomes),
               "protocol_sha256": _digest(protocol), "scenario_inputs_sha256": _digest(inputs), "results_sha256": _digest(results)}
    output.mkdir(parents=True)
    for name, artifact in (("protocol.json", protocol), ("scenarios.json", inputs), ("results.json", results), ("summary.json", summary)):
        _write_new(output / name, artifact)
    return summary


def replay_fault_matrix(output_dir):
    """Reassess saved scenario inputs, including replay of every synthetic source."""
    folder = Path(output_dir)
    documents = {name: json.loads((folder / (name + ".json")).read_text()) for name in ("protocol", "scenarios", "results", "summary")}
    protocol, inputs, saved, summary = (documents[name] for name in ("protocol", "scenarios", "results", "summary"))
    differences = []
    if protocol["implementation_sha256"] != _code_hashes():
        differences.append("implementation_sha256")
    if protocol["scenario_inputs_sha256"] != _digest(inputs) or saved["scenario_inputs_sha256"] != _digest(inputs):
        differences.append("scenario_inputs_sha256")
    if saved["protocol_sha256"] != _digest(protocol) or summary["protocol_sha256"] != _digest(protocol):
        differences.append("protocol_sha256")
    if summary["results_sha256"] != _digest(saved):
        differences.append("results_sha256")
    scenarios = inputs["scenarios"]
    outcomes = [assess_scenario(scenario) for scenario in scenarios]
    actual = [{"id": scenario["id"], **outcome} for scenario, outcome in zip(scenarios, outcomes)]
    if actual != saved["outcomes"]:
        differences.append("reassessed_outcomes")
    replayed_summary = summarize(scenarios, outcomes)
    if any(summary.get(key) != value for key, value in replayed_summary.items()):
        differences.append("reassessed_summary")
    return {"pass": not differences, "network_used": False, "model_calls": 0,
            "scenario_count": len(scenarios), "differences": differences,
            "scope": "reproduction of saved synthetic engineering inputs and source messages"}
