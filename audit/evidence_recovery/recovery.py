"""Deterministic attribution recovery audit over frozen formal runs, faults, and decisions.

Read-only with respect to baseline/ and decision_cost/. Never mutates input reports
or archived baselines; records every recovered evidence ID explicitly.
"""
from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from decimal import Decimal, localcontext
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "baseline"
DECISION_COST = ROOT / "decision_cost"
for path in (str(BASELINE), str(DECISION_COST), str(BASELINE / "audit/decision_replay_20261112")):
    if path not in sys.path:
        sys.path.insert(0, path)

from curve_audit import verification as verification_mod
from curve_audit.common import read_json, sha256_file
from curve_audit.current_study import (
    VERSION,
    WINDOWS,
    digest,
    publish,
    reference_candidates,
    replay_claim,
    report_claims,
    score_report,
    shift,
    template_report,
)
from curve_audit.current_verify import (
    bound_recorded_sources,
    publish_verified,
    read_study_binding,
    verify_v2,
)
from curve_audit.publication_arithmetic import check_claim
from replay import RESEARCH_RULES, action, outcome


def recover_attribution(primary: dict, task: dict, report: dict, *, allow_partial: bool = False):
    """Deterministically recover missing evidence_ids only under 6 strict safety invariants.

    Invariants (all required):
      1. replay_claim(primary, task, claim) returns UNKNOWN with reason 'incomplete_attribution'
         (which implies query, unit, value, and timestamps already passed exact recomputation).
      2. claim['dataset_sha256'] == digest(primary).
      3. claim['formula_version'] == VERSION.
      4. expected['evidence_ids'] is nonempty.
      5. Original evidence_ids is nonempty AND a strict subset of expected['evidence_ids'].
      6. Input report is never mutated; every recovery or rejection is audited explicitly.
    """
    recovered = deepcopy(report)
    primary_sha = digest(primary)
    claims = report_claims(task, recovered, allow_empty=True, allow_partial=allow_partial)
    records: dict[str, dict] = {}
    for claim in claims:
        cid = claim["claim_id"]
        v1 = replay_claim(primary, task, claim)
        if v1["verdict"] != "UNKNOWN" or v1["reason"] != "incomplete_attribution":
            continue
        expected_ids = sorted(set((v1.get("expected") or {}).get("evidence_ids") or []))
        orig_ids = list(claim.get("evidence_ids") or [])
        orig_set = set(orig_ids)
        exp_set = set(expected_ids)

        if claim.get("dataset_sha256") != primary_sha or claim.get("formula_version") != VERSION:
            records[cid] = {
                "status": "rejected",
                "reason": "missing_or_mismatched_metadata",
                "original_evidence_ids": orig_ids,
                "expected_evidence_ids": expected_ids,
            }
            continue
        if not expected_ids:
            records[cid] = {
                "status": "rejected",
                "reason": "empty_expected_evidence",
                "original_evidence_ids": orig_ids,
                "expected_evidence_ids": expected_ids,
            }
            continue
        if not orig_set:
            records[cid] = {
                "status": "rejected",
                "reason": "empty_original_evidence_ids",
                "original_evidence_ids": orig_ids,
                "expected_evidence_ids": expected_ids,
            }
            continue
        if not (orig_set < exp_set):
            records[cid] = {
                "status": "rejected",
                "reason": "original_evidence_not_strict_subset",
                "original_evidence_ids": orig_ids,
                "unexpected_evidence_ids": sorted(orig_set - exp_set),
                "expected_evidence_ids": expected_ids,
            }
            continue

        claim["evidence_ids"] = expected_ids
        records[cid] = {
            "status": "recovered",
            "reason": "strict_subset_evidence_attribution_recovery",
            "original_evidence_ids": orig_ids,
            "added_evidence_ids": sorted(exp_set - orig_set),
            "recovered_evidence_ids": expected_ids,
        }
    return recovered, records


def _publish_verified_partial(primary: dict, task: dict, report: dict, v2: dict):
    """Apply publication_arithmetic.check_claim to partial single-claim fault fixtures."""
    checks = deepcopy(v2)
    arithmetic = {}
    requests = {c["slot_id"]: c["request"] for c in task["required_components"]}
    claims = report_claims(task, report, allow_empty=True, allow_partial=True)
    kept = []
    withdrawals = []
    for claim in claims:
        cid = claim["claim_id"]
        if v2[cid]["verdict"] == "PASS":
            res = check_claim(primary, task, requests[claim["slot_id"]], claim)
            if res is not None:
                arithmetic[cid] = res
                if res["verdict"] != "PASS":
                    checks[cid] = res
        if checks[cid]["verdict"] == "PASS":
            kept.append(claim)
        else:
            withdrawals.append({"claim_id": cid, **checks[cid]})
    published = {"task_id": task["task_id"], "claims": kept, "withdrawals": withdrawals}
    return published, checks, arithmetic


def publish_with_recovery(
    primary: dict,
    secondary: dict | None,
    task: dict,
    report: dict,
    *,
    expected_primary_sha256: str | None = None,
    allow_partial: bool = False,
) -> dict:
    """Run deterministic attribution recovery followed by full V2 + independent arithmetic guard."""
    started = perf_counter()
    recovered, recovery_records = recover_attribution(primary, task, report, allow_partial=allow_partial)
    v2 = verify_v2(
        primary,
        secondary,
        task,
        recovered,
        expected_primary_sha256=expected_primary_sha256,
        allow_partial=allow_partial,
    )
    if allow_partial:
        published, verification, arithmetic = _publish_verified_partial(primary, task, recovered, v2)
    else:
        published, verification, arithmetic = publish_verified(primary, task, recovered, v2)
    elapsed = perf_counter() - started
    return {
        "recovered_report": recovered,
        "recovery_records": recovery_records,
        "v2": v2,
        "verification": verification,
        "independent_arithmetic": arithmetic,
        "published": published,
        "elapsed_seconds": elapsed,
    }


def _load_formal_context(root: Path):
    base = root / "baseline"
    prep = base / "results/current_preparation"
    formal = base / "experiments/formal_v1"
    freeze = read_json(formal / "freeze.json")
    binding = freeze["binding"]
    primary, secondary = bound_recorded_sources(binding, base / "data/current_study")
    tasks = {t["task_id"]: t for t in read_json(prep / "tasks.json")}
    references = read_json(prep / "references.automatic.private.json")
    schedule = read_json(formal / "schedule.json")
    return base, formal, freeze, binding, primary, secondary, tasks, references, schedule


def replay_formal_36(root: Path = ROOT) -> dict:
    """Compare guarded baseline publication vs. guarded publication with deterministic recovery."""
    _, formal, _, binding, primary, secondary, tasks, references, schedule = _load_formal_context(root)
    primary_sha = binding["dataset_sha256"]

    trace_rows = []
    group_totals = {
        group: {
            "answerable": 0,
            "raw_correct": 0,
            "baseline_published_correct": 0,
            "baseline_published_incorrect": 0,
            "recovered_published_correct": 0,
            "recovered_published_incorrect": 0,
            "direct_attribution_recoveries": 0,
            "cascade_dependency_recoveries": 0,
            "total_recovered_correct": 0,
        }
        for group in ("G", "E")
    }
    by_run_published = {}
    timings_ms = {"baseline": [], "with_recovery": []}

    for item in sorted(schedule, key=lambda r: r["run_id"]):
        run_id = item["run_id"]
        group = item["group"]
        run = read_json(formal / f"{run_id}.json")
        task = tasks[run["task_id"]]
        report = run["report"]
        report_snapshot_before = digest(report)

        t0 = perf_counter()
        base_v2 = verify_v2(primary, secondary, task, report, expected_primary_sha256=primary_sha)
        base_pub, base_ver, base_arith = publish_verified(primary, task, report, base_v2)
        timings_ms["baseline"].append((perf_counter() - t0) * 1000.0)

        rec_out = publish_with_recovery(primary, secondary, task, report, expected_primary_sha256=primary_sha)
        timings_ms["with_recovery"].append(rec_out["elapsed_seconds"] * 1000.0)

        if digest(report) != report_snapshot_before:
            raise AssertionError(f"Input report mutated during recovery on {run_id}")

        raw_score = score_report(primary, task, report, references)
        base_score = score_report(primary, task, base_pub, references)
        rec_score = score_report(primary, task, rec_out["published"], references)

        raw_slot_state = {s["slot_id"]: s["state"] for s in raw_score["slots"]}
        base_kept = {c["claim_id"] for c in base_pub["claims"]}
        rec_kept = {c["claim_id"] for c in rec_out["published"]["claims"]}
        by_run_published[run_id] = {
            "baseline_published": base_pub,
            "recovered_published": rec_out["published"],
            "recovery_records": rec_out["recovery_records"],
        }

        gt = group_totals[group]
        gt["answerable"] += raw_score["counts"]["answerable"]
        gt["raw_correct"] += raw_score["counts"]["correct"]
        gt["baseline_published_correct"] += base_score["counts"]["correct"]
        gt["baseline_published_incorrect"] += base_score["counts"]["incorrect"]
        gt["recovered_published_correct"] += rec_score["counts"]["correct"]
        gt["recovered_published_incorrect"] += rec_score["counts"]["incorrect"]

        for claim in report["claims"]:
            cid = claim["claim_id"]
            slot = claim["slot_id"]
            state = raw_slot_state[slot]
            b_pub_flag = cid in base_kept
            r_pub_flag = cid in rec_kept
            r_rec = rec_out["recovery_records"].get(cid)
            newly_published = (not b_pub_flag) and r_pub_flag
            recovery_mode = "none"
            if newly_published:
                if r_rec and r_rec["status"] == "recovered":
                    recovery_mode = "direct_attribution"
                    if state == "correct":
                        gt["direct_attribution_recoveries"] += 1
                        gt["total_recovered_correct"] += 1
                elif base_ver[cid]["reason"] == "withdrawn_dependency":
                    recovery_mode = "cascade_dependency"
                    if state == "correct":
                        gt["cascade_dependency_recoveries"] += 1
                        gt["total_recovered_correct"] += 1
                else:
                    recovery_mode = "unexpected"
            trace_rows.append({
                "run_id": run_id,
                "task_id": run["task_id"],
                "event_id": task["event_id"],
                "group": group,
                "claim_id": cid,
                "slot_id": slot,
                "raw_slot_state": state,
                "baseline_verdict": base_ver[cid]["verdict"],
                "baseline_reason": base_ver[cid]["reason"],
                "baseline_published": b_pub_flag,
                "recovery_status": r_rec["status"] if r_rec else "not_applicable",
                "orig_evidence_count": len(claim.get("evidence_ids") or []),
                "added_evidence_count": len(r_rec.get("added_evidence_ids") or []) if r_rec else 0,
                "recovered_verdict": rec_out["verification"][cid]["verdict"],
                "recovered_reason": rec_out["verification"][cid]["reason"],
                "recovered_published": r_pub_flag,
                "recovery_mode": recovery_mode,
            })

    total_emitted = len(trace_rows)
    false_accepts = [
        r for r in trace_rows if (not r["baseline_published"]) and r["recovered_published"] and r["raw_slot_state"] != "correct"
    ]
    lost_correct = [
        r for r in trace_rows if r["baseline_published"] and (not r["recovered_published"])
    ]
    summary = {
        "scheduled_runs": len(schedule),
        "emitted_structured_claims": total_emitted,
        "by_group": group_totals,
        "totals": {
            "answerable": sum(v["answerable"] for v in group_totals.values()),
            "raw_correct": sum(v["raw_correct"] for v in group_totals.values()),
            "baseline_published_correct": sum(v["baseline_published_correct"] for v in group_totals.values()),
            "recovered_published_correct": sum(v["recovered_published_correct"] for v in group_totals.values()),
            "direct_attribution_recoveries": sum(v["direct_attribution_recoveries"] for v in group_totals.values()),
            "cascade_dependency_recoveries": sum(v["cascade_dependency_recoveries"] for v in group_totals.values()),
            "total_recovered_correct": sum(v["total_recovered_correct"] for v in group_totals.values()),
            "false_accepts_on_non_correct_claims": len(false_accepts),
            "previously_published_lost": len(lost_correct),
        },
        "trace_rows": trace_rows,
        "by_run_published": by_run_published,
        "timings_ms": timings_ms,
    }
    return summary


def replay_faults_32(root: Path = ROOT) -> dict:
    """Verify 0 false accepts when running recovery + V2 + independent arithmetic on 32 frozen faults."""
    _, formal, _, binding, primary, secondary, tasks, _, _ = _load_formal_context(root)
    folder = formal / "fault_batch"
    frozen = read_json(folder / "frozen_cases.json")
    answer_key = read_json(folder / "answer_key.json")
    original_sha = frozen["dataset_sha256"]
    if digest(primary) != original_sha or digest(secondary) != frozen["secondary_dataset_sha256"]:
        raise ValueError("Fault dataset binding mismatch")

    results = []
    false_accepts = 0
    clean_kept = 0
    clean_total = 0
    non_clean_total = 0

    for case in frozen["cases"]:
        task = deepcopy(tasks[case["task"]])
        p, s = primary, secondary
        mode, point = case["mode"], case["point"]
        if mode == "exclude_point":
            task["allowed_evidence"]["excluded_targets"].append(point)
        elif mode == "no_secondary":
            s = None
        elif mode == "drop_secondary":
            s = {**secondary, "snapshots": [r for r in secondary["snapshots"] if r["target_timestamp_utc"] != point]}
        elif mode in ("primary_number", "primary_hash", "secondary_hash"):
            altered = deepcopy(primary if mode.startswith("primary") else secondary)
            row = next(r for r in altered["snapshots"] if r["target_timestamp_utc"] == point)
            if mode.endswith("number"):
                row["block_number"] -= 1
                row["next_block"]["block_number"] -= 1
            else:
                row["block_hash"] = "0x" + digest({"controlled_fixture": case["case_id"]})
                row["next_block"]["parent_hash"] = row["block_hash"]
            if mode.startswith("primary"):
                p = altered
            else:
                s = altered
        report, _ = template_report(p, task)
        claim = next(c for c in report["claims"] if c["slot_id"] == case["slot"])
        if mode == "report":
            change = case["change"]
            parent = claim
            for field in change["path"][:-1]:
                parent = parent[field]
            parent[change["path"][-1]] = change["value"]
        if digest([p, s, task, claim]) != case["input_sha256"]:
            raise ValueError(f"Materialized fault input mismatch for {case['case_id']}")

        partial_report = {"task_id": task["task_id"], "answer": "", "claims": [claim]}
        out = publish_with_recovery(p, s, task, partial_report, expected_primary_sha256=original_sha, allow_partial=True)
        cid = claim["claim_id"]
        published = cid in {c["claim_id"] for c in out["published"]["claims"]}
        expected_v2 = answer_key[case["case_id"]]["V2"][0]
        if case["kind"] == "clean":
            clean_total += 1
            if published:
                clean_kept += 1
        else:
            non_clean_total += 1
            if published:
                false_accepts += 1
        results.append({
            "case_id": case["case_id"],
            "kind": case["kind"],
            "expected_v2_verdict": expected_v2,
            "recovered_verdict": out["verification"][cid]["verdict"],
            "recovered_reason": out["verification"][cid]["reason"],
            "published": published,
            "recovery_triggered": cid in out["recovery_records"],
        })

    return {
        "cases_tested": len(results),
        "clean_total": clean_total,
        "clean_kept": clean_kept,
        "non_clean_total": non_clean_total,
        "false_accepts": false_accepts,
        "observations": results,
    }


def replay_shared_arithmetic_6(root: Path = ROOT) -> dict:
    """Verify 6 shared arithmetic faults remain 100% blocked even if partial-evidence recovery triggers."""
    base = root / "baseline"
    arith_dir = str(base / "audit/arithmetic_faults")
    if arith_dir not in sys.path:
        sys.path.insert(0, arith_dir)
    from replay_publication_guard import altered_report, injected_fault

    _, _, _, binding, primary, secondary, tasks, _, _ = _load_formal_context(root)
    primary_sha = binding["dataset_sha256"]
    baseline_cases = read_json(base / "audit/arithmetic_faults/results.json")["observations"]

    observations = []
    false_accepts = 0
    clean_kept = 0

    for case in baseline_cases:
        task = tasks[case["task"]]
        clean, _ = template_report(primary, task)
        wrong = altered_report(primary, task, clean, case)
        cid = next(c["claim_id"] for c in clean["claims"] if c["slot_id"] == case["slot"])

        # Condition A: clean; Condition B: shared_verifier_bug;
        # Condition C: adversarial shared_verifier_bug + truncated evidence_ids (to test if recovery can bypass guard).
        wrong_partial_ev = deepcopy(wrong)
        target_claim = next(c for c in wrong_partial_ev["claims"] if c["claim_id"] == cid)
        if len(target_claim.get("evidence_ids") or []) > 1:
            target_claim["evidence_ids"] = target_claim["evidence_ids"][:1]

        entry = {"task": case["task"], "slot": case["slot"], "fault": case["fault"]}
        for cond, rep, use_fault in (
            ("clean", clean, False),
            ("shared_verifier_bug", wrong, True),
            ("shared_verifier_bug_partial_evidence", wrong_partial_ev, True),
        ):
            ctx = injected_fault(verification_mod, case["fault"]) if use_fault else nullcontext()
            with ctx:
                out = publish_with_recovery(primary, secondary, task, rep, expected_primary_sha256=primary_sha)
            published_ids = {c["claim_id"] for c in out["published"]["claims"]}
            pub_flag = cid in published_ids
            if cond == "clean":
                if pub_flag:
                    clean_kept += 1
            else:
                if pub_flag:
                    false_accepts += 1
            entry[cond] = {
                "recovery_status": (out["recovery_records"].get(cid) or {}).get("status", "not_applicable"),
                "v2_verdict": out["v2"][cid]["verdict"],
                "independent_arithmetic_verdict": (out["independent_arithmetic"].get(cid) or {}).get("verdict"),
                "final_reason": out["verification"][cid]["reason"],
                "published": pub_flag,
            }
        observations.append(entry)

    return {
        "pairs": len(observations),
        "clean_kept": clean_kept,
        "shared_bug_cases_tested": len(observations) * 2,
        "false_accepts": false_accepts,
        "observations": observations,
    }


def replay_extension_40(root: Path = ROOT) -> dict:
    """Verify all 40 offline verification extension cases keep identical accept/withhold behavior under recovery."""
    base = root / "baseline"
    binding = read_study_binding(base / "results/current_preparation")
    primary, secondary = bound_recorded_sources(binding, base / "data/current_study")
    primary_hash = binding["dataset_sha256"]

    def task_for(case_id, event, start, end, requests, excluded=()):
        return {
            "task_id": case_id,
            "event_id": event,
            "window": {"start": start, "end": end},
            "question": "Deterministic subwindow verification fixture",
            "required_components": [
                {"slot_id": f"S{index}", "label": request["metric"], "request": request}
                for index, request in enumerate(requests, 1)
            ],
            "allowed_evidence": {"excluded_targets": list(excluded)},
        }

    def shifted_balance(dataset, at):
        changed = deepcopy(dataset)
        row = next(s for s in changed["snapshots"] if s["target_timestamp_utc"] == at)
        decimals = changed["decimals"]["USDC"]
        raw = int(row["raw_balances"]["USDC"]) + 1_000_000 * 10**decimals
        row["raw_balances"]["USDC"] = str(raw)
        with localcontext() as ctx:
            ctx.prec = 90
            row["balances"]["USDC"] = format(Decimal(raw).scaleb(-decimals), "f")
        return changed

    observations = []
    clean_total = clean_kept = non_clean_total = false_accepts = 0

    def check(case_name, task, report, slot, expect_published, *, first=primary, second=secondary):
        nonlocal clean_total, clean_kept, non_clean_total, false_accepts
        claim = next(c for c in report["claims"] if c["slot_id"] == slot)
        cid = claim["claim_id"]
        out = publish_with_recovery(first, second, task, report, expected_primary_sha256=primary_hash)
        pub_flag = cid in {c["claim_id"] for c in out["published"]["claims"]}
        if expect_published:
            clean_total += 1
            if pub_flag:
                clean_kept += 1
        else:
            non_clean_total += 1
            if pub_flag:
                false_accepts += 1
        observations.append({
            "task_id": task["task_id"],
            "case": case_name,
            "slot": slot,
            "expected_published": expect_published,
            "published": pub_flag,
            "final_reason": out["verification"][cid]["reason"],
        })

    for event, first_hour, _ in WINDOWS:
        for offset in (3, 15):
            start, excluded, end = (shift(first_hour, offset + h) for h in (0, 3, 6))
            ident = f"X_{event}_{offset:02d}"
            endpoint = {"metric": "share_pct", "operation": "change", "start": start, "end": end, "coin": "USDC"}
            maximum = {**endpoint, "operation": "max_hourly_change"}
            task = task_for(ident, event, start, end, [endpoint, maximum], [excluded])
            clean, _ = template_report(primary, task)
            check("endpoint_change_with_mask", task, clean, "S1", True)
            check("adjacent_maximum_with_mask", task, clean, "S2", True)

            bridged = deepcopy(clean)
            bridged["claims"][1].update(interval_start=shift(excluded, -1), interval_end=shift(excluded, 1))
            check("false_interval_across_mask", task, bridged, "S2", False)

            missing = {**secondary, "snapshots": [r for r in secondary["snapshots"] if r["target_timestamp_utc"] != end]}
            check("missing_secondary_endpoint", task, clean, "S1", False, second=missing)
            check("secondary_balance_conflict", task, clean, "S1", False, second=shifted_balance(secondary, end))
            check("secondary_change_at_excluded_sample", task, clean, "S2", True, second=shifted_balance(secondary, excluded))

            changed = shifted_balance(primary, end)
            altered_rep, _ = template_report(changed, task)
            check("primary_balance_drift", task, altered_rep, "S1", False, first=changed)

            peak = {"metric": "imbalance_l1", "operation": "maximum", "start": start, "end": end}
            follow = {
                "metric": "quote_rate", "operation": "last", "direction": "USDC_USDT",
                "size": 100000, "time_from_slot": "S1", "offset_hours": 1,
            }
            dep_task = task_for(ident + "_follow", event, start, end, [peak, follow])
            dep_rep, _ = template_report(primary, dep_task)
            check("clean_follow_up", dep_task, dep_rep, "S2", True)
            dep_rep_bad = deepcopy(dep_rep)
            dep_rep_bad["claims"][0]["value"] = "-9999"
            dep_rep_bad["claims"][1].pop("depends_on", None)
            check("mandatory_dependency_withdrawal", dep_task, dep_rep_bad, "S2", False)

    legacy = read_json(base / "data/raw/expanded_archive.json")
    for day in ("2023-03-09", "2023-03-12"):
        start, end = f"{day}T00:00:00Z", f"{day}T06:00:00Z"
        share = {"metric": "share_pct", "operation": "change", "start": start, "end": end, "coin": "USDC"}
        quote = {"metric": "quote_rate", "operation": "last", "start": end, "end": end, "direction": "USDC_USDT", "size": 1000}
        task = task_for(f"X_legacy_{day}", "usdc_2023_legacy", start, end, [share, quote])
        rep, _ = template_report(legacy, task)
        check("single_source_endpoint_change", task, rep, "S1", False, first=legacy, second=None)
        check("single_source_spot_quote", task, rep, "S2", False, first=legacy, second=None)

    return {
        "cases_tested": len(observations),
        "clean_total": clean_total,
        "clean_kept": clean_kept,
        "non_clean_total": non_clean_total,
        "false_accepts": false_accepts,
        "observations": observations,
    }


def replay_decision_cost_comparison(root: Path = ROOT, formal_result: dict | None = None) -> dict:
    """Evaluate downstream 36 research decisions & scenario costs with vs. without attribution recovery."""
    import evaluation as dc_eval

    if formal_result is None:
        formal_result = replay_formal_36(root)

    cfg = dc_eval.load(root / "decision_cost/results/settings.json")
    base_rows, _ = dc_eval.observations(cfg["training_seed"])
    _, _, scores = dc_eval.probabilities(base_rows, cfg)
    # Use the archived frozen timing.json so cost tables are byte-for-byte deterministic
    # and match decision_cost/results/policy-summary.csv exactly on baseline policies.
    timing = dc_eval.load(root / "decision_cost/results/timing.json")

    # Build recovered rows: identical pre-verification inputs/features, but verified_action uses recovered_published.
    by_run = formal_result["by_run_published"]
    recovered_rows = []
    recovered_decision_changes = []
    for row in base_rows:
        rec_pub_claims = {c["slot_id"]: c for c in by_run[row["run_id"]]["recovered_published"]["claims"]}
        rec_verified_action = action(row["rule"], rec_pub_claims.get(row["slot_id"]))
        rec_verified_outcome = outcome(rec_verified_action, row["expected_action"])
        new_row = {
            **row,
            "verified_action": rec_verified_action,
            "verified_outcome": rec_verified_outcome,
        }
        recovered_rows.append(new_row)
        if row["verified_outcome"] != rec_verified_outcome:
            recovered_decision_changes.append({
                "case_id": row["case_id"],
                "run_id": row["run_id"],
                "slot_id": row["slot_id"],
                "rule": row["rule"],
                "baseline_verified_outcome": row["verified_outcome"],
                "recovered_verified_outcome": rec_verified_outcome,
            })

    # Re-evaluate all 6 original policies on base_rows + recovered counterparts on recovered_rows.
    summaries = []
    traces_by_policy = {}
    for policy in dc_eval.POLICIES:
        tr, sm = dc_eval.evaluate(base_rows, policy, scores, timing, cfg, {}, cfg["price_per_minute"], 1)
        summaries.append(sm)
        traces_by_policy[policy] = tr

    for base_policy, label in (
        ("verified", "verified_recovered"),
        ("rule", "rule_recovered"),
        ("logistic", "logistic_recovered"),
    ):
        # For recovered policies, pass the same policy routing rule (scores keyed by base_policy).
        adjusted_scores = {
            (cid, label): val for (cid, mode), val in scores.items() if mode == base_policy
        }
        adjusted_timing = {
            f"{key.rsplit('/', 1)[0]}/{label}": val
            for key, val in timing.items()
            if key.endswith("/" + base_policy)
        }
        # Evaluate using base_policy's routing logic on recovered_rows
        tr, sm = dc_eval.evaluate(
            recovered_rows,
            base_policy,
            scores,
            timing,
            cfg,
            {},
            cfg["price_per_minute"],
            1,
        )
        sm = {**sm, "policy": label}
        tr = [{**item, "policy": label} for item in tr]
        summaries.append(sm)
        traces_by_policy[label] = tr

    # Break-even loss for raw -> verified vs raw -> verified_recovered
    by_policy = {s["policy"]: s for s in summaries}
    def break_even(target_policy: str) -> dict:
        raw_s, tgt_s = by_policy["raw"], by_policy[target_policy]
        gain = raw_s["auto_error"] - tgt_s["auto_error"]
        inc_reviews = tgt_s["to_human"] - raw_s["to_human"]
        added_cost = (tgt_s["human_cost"] + tgt_s["compute_cost"]) - (raw_s["human_cost"] + raw_s["compute_cost"])
        raw_res = sum(t["error_cost"] for t in traces_by_policy["raw"] if t["to_human"])
        tgt_res = sum(t["error_cost"] for t in traces_by_policy[target_policy] if t["to_human"])
        return {
            "additional_reviews": inc_reviews,
            "automatic_errors_intercepted": gain,
            "break_even_avoided_loss_perfect_review": added_cost / gain if gain else None,
            "break_even_avoided_loss_configured_review": (added_cost + tgt_res - raw_res) / gain if gain else None,
        }

    return {
        "decision_changes_from_recovery": recovered_decision_changes,
        "recovered_decision_counts": dict(Counter(r["verified_outcome"] for r in recovered_rows)),
        "baseline_decision_counts": dict(Counter(r["verified_outcome"] for r in base_rows)),
        "policy_summaries": summaries,
        "traces_by_policy": traces_by_policy,
        "break_even_raw_to_verified": break_even("verified"),
        "break_even_raw_to_verified_recovered": break_even("verified_recovered"),
        "base_rows": base_rows,
        "recovered_rows": recovered_rows,
        "scenario_config": cfg,
        "scores": scores,
        "timing": timing,
    }
