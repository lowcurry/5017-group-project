"""Compute concentration alert outcomes by independent holdout window."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

ARMS = ("baseline", "recovery")
LABELS = {"escalate", "no_alert"}


def summarize(rows):
    counts = Counter((row["reference_action"], row["action"]) for row in rows)
    tn = counts["no_alert", "no_alert"]
    fp = counts["no_alert", "escalate"]
    deferred_negative = counts["no_alert", ""]
    tp = counts["escalate", "escalate"]
    fn = counts["escalate", "no_alert"]
    deferred_positive = counts["escalate", ""]
    n_negative = tn + fp + deferred_negative
    return {
        "reference_no_alert": n_negative,
        "reference_escalate": tp + fn + deferred_positive,
        "true_negatives": tn, "false_positives": fp, "negative_referrals": deferred_negative,
        "true_positives": tp, "false_negatives": fn, "positive_referrals": deferred_positive,
        "false_positive_rate_among_automatic_negatives": fp / (tn + fp) if tn + fp else None,
        "automatic_false_alert_fraction_among_all_negatives": fp / n_negative if n_negative else None,
        "automatic_negative_coverage": (tn + fp) / n_negative if n_negative else None,
    }


def metrics(sources):
    pooled = {arm: [] for arm in ARMS}
    by_event = {}
    for event, source in sources.items():
        with source.open(encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["rule"] == "concentration_alert"]
        ids = {r["case_id"] for r in rows}
        if not rows or len(rows) != len(ids):
            raise ValueError(f"{event} has missing or duplicate concentration decisions")
        by_event[event] = {}
        for arm in ARMS:
            selected = []
            for r in rows:
                action = r[f"{arm}_action"]
                if r["reference_action"] not in LABELS or action not in LABELS | {""}:
                    raise ValueError(f"Unrecognized alert outcome in {event}/{r['case_id']}")
                if r[f"{arm}_to_human"] != str(action == ""):
                    raise ValueError(f"Alert action and referral disagree in {event}/{r['case_id']}")
                selected.append({"reference_action": r["reference_action"], "action": action})
            by_event[event][arm] = summarize(selected)
            pooled[arm].extend(selected)
    return {
        "rule": "concentration_alert",
        "scope": "three completed holdout windows; runs within each window are repeats",
        "distinct_negative_windows": sum(v["baseline"]["reference_no_alert"] > 0 for v in by_event.values()),
        "distinct_positive_windows": sum(v["baseline"]["reference_escalate"] > 0 for v in by_event.values()),
        "by_window": by_event,
        "pooled_decision_counts": {arm: summarize(rows) for arm, rows in pooled.items()},
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("workspace", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    root = args.workspace
    sources = {day: root / day / "decisions.csv" for day in ("2023-06-15", "2023-10-23", "2024-01-11")}
    args.output.write_text(json.dumps(metrics(sources), indent=2) + "\n")
