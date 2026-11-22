"""Build deterministic offline evidence-recovery and statistical-bounds artifacts."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import median

from bounds import compute_statistical_bounds
from recovery import (
    ROOT,
    replay_decision_cost_comparison,
    replay_extension_40,
    replay_faults_32,
    replay_formal_36,
    replay_shared_arithmetic_6,
)

HERE = Path(__file__).resolve().parent


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write empty CSV: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def build_all(output_dir: Path, *, root: Path = ROOT, timing_output: Path | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    formal = replay_formal_36(root)
    faults_32 = replay_faults_32(root)
    shared_6 = replay_shared_arithmetic_6(root)
    ext_40 = replay_extension_40(root)
    decision_cmp = replay_decision_cost_comparison(root, formal)
    stat_bounds = compute_statistical_bounds(decision_cmp)

    # Deterministic core summary (strictly free of wall-clock jitter).
    summary = {
        "scope": "Offline deterministic attribution recovery audit over frozen formal runs, fault suites, and 36 historical decisions. No live model or RPC calls.",
        "safety_invariants": [
            "replay_claim returns UNKNOWN with reason incomplete_attribution",
            "dataset_sha256 matches bound primary dataset digest",
            "formula_version matches VERSION",
            "original evidence_ids is nonempty and a strict subset of expected evidence_ids",
            "full re-verification through verify_v2 + publish_verified independent arithmetic + task dependencies",
            "input report left untouched; recovered evidence IDs recorded explicitly",
        ],
        "formal_36_runs": {
            "scheduled_runs": formal["scheduled_runs"],
            "emitted_structured_claims": formal["emitted_structured_claims"],
            "by_group": formal["by_group"],
            "totals": formal["totals"],
        },
        "fault_safety_gates": {
            "frozen_32_faults": {k: v for k, v in faults_32.items() if k != "observations"},
            "shared_arithmetic_6_pairs": {k: v for k, v in shared_6.items() if k != "observations"},
            "extension_40_cases": {k: v for k, v in ext_40.items() if k != "observations"},
            "total_fault_false_accepts": (
                faults_32["false_accepts"] + shared_6["false_accepts"] + ext_40["false_accepts"]
            ),
        },
        "decision_replay_36": {
            "baseline_decision_counts": decision_cmp["baseline_decision_counts"],
            "recovered_decision_counts": decision_cmp["recovered_decision_counts"],
            "decision_changes_from_recovery": decision_cmp["decision_changes_from_recovery"],
            "break_even_raw_to_verified": decision_cmp["break_even_raw_to_verified"],
            "break_even_raw_to_verified_recovered": decision_cmp["break_even_raw_to_verified_recovered"],
        },
    }

    write_json(output_dir / "recovery_summary.json", summary)
    write_csv(output_dir / "recovery_trace.csv", formal["trace_rows"])
    write_csv(output_dir / "decision_cost_comparison.csv", decision_cmp["policy_summaries"])
    write_json(output_dir / "statistical_bounds.json", stat_bounds)

    if timing_output is not None:
        timing_output.parent.mkdir(parents=True, exist_ok=True)
        write_json(timing_output, {
            "baseline_median_ms_per_run": median(formal["timings_ms"]["baseline"]),
            "with_recovery_median_ms_per_run": median(formal["timings_ms"]["with_recovery"]),
            "baseline_total_ms": sum(formal["timings_ms"]["baseline"]),
            "with_recovery_total_ms": sum(formal["timings_ms"]["with_recovery"]),
        })
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "results")
    parser.add_argument("--timing-output", type=Path, default=None)
    args = parser.parse_args()
    summary = build_all(args.output, timing_output=args.timing_output)
    print(json.dumps({
        "output": str(args.output),
        "formal_totals": summary["formal_36_runs"]["totals"],
        "fault_false_accepts": summary["fault_safety_gates"]["total_fault_false_accepts"],
        "decision_counts_recovered": summary["decision_replay_36"]["recovered_decision_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
