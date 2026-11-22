"""Zero-dependency exact small-sample error bounds, clustering disclosure, and conservative cost check."""
from __future__ import annotations

import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
DECISION_COST = ROOT / "decision_cost"
if str(DECISION_COST) not in sys.path:
    sys.path.insert(0, str(DECISION_COST))


def binomial_cdf(k: int, n: int, p: float) -> float:
    """Exact P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    return sum(math.comb(n, j) * (p**j) * ((1.0 - p) ** (n - j)) for j in range(k + 1))


def clopper_pearson_upper(k: int, n: int, confidence: float = 0.95, *, one_sided: bool = False) -> float:
    """Exact Clopper-Pearson upper confidence bound for k successes in n trials."""
    if n <= 0 or not (0 <= k <= n):
        raise ValueError(f"Invalid binomial counts: k={k}, n={n}")
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"Invalid confidence: {confidence}")
    if k == n:
        return 1.0
    tail = (1.0 - confidence) if one_sided else (1.0 - confidence) / 2.0
    if k == 0:
        return 1.0 - (tail ** (1.0 / n))
    lo, hi = k / n, 1.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if binomial_cdf(k, n, mid) > tail:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def clopper_pearson_lower(k: int, n: int, confidence: float = 0.95, *, one_sided: bool = False) -> float:
    """Exact Clopper-Pearson lower confidence bound for k successes in n trials."""
    if n <= 0 or not (0 <= k <= n):
        raise ValueError(f"Invalid binomial counts: k={k}, n={n}")
    if k == 0:
        return 0.0
    return 1.0 - clopper_pearson_upper(n - k, n, confidence=confidence, one_sided=one_sided)


def _betacf(a: float, b: float, x: float) -> float:
    """Lentz's continued fraction for the regularized incomplete beta function I_x(a, b)."""
    max_iter = 200
    eps = 3.0e-14
    fpmin = 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Exact I_x(a, b) for 0 <= x <= 1, a > 0, b > 0 using pure stdlib math."""
    if a <= 0.0 or b <= 0.0:
        raise ValueError("Beta parameters must be positive")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_quantile(q: float, a: float, b: float) -> float:
    """Inverse CDF of Beta(a, b) via bisection to ~1e-14 accuracy."""
    if not (0.0 <= q <= 1.0):
        raise ValueError(f"Quantile out of range: {q}")
    if q == 0.0:
        return 0.0
    if q == 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if regularized_incomplete_beta(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def rate_bounds(k: int, n: int, confidence: float = 0.95) -> dict:
    """Compute exact Clopper-Pearson + Uniform Beta(1,1) + Jeffreys Beta(0.5,0.5) bounds."""
    if n <= 0:
        return {
            "errors": k,
            "opportunities": n,
            "observed_rate": None,
            "note": "No automatic decisions in this denominator.",
        }
    tail = (1.0 - confidence) / 2.0
    out = {
        "errors": k,
        "opportunities": n,
        "observed_rate": k / n,
        "nominal_under_iid_assumption": True,
        "clopper_pearson_95_two_sided": {
            "lower": clopper_pearson_lower(k, n, confidence, one_sided=False),
            "upper": clopper_pearson_upper(k, n, confidence, one_sided=False),
        },
        "clopper_pearson_95_one_sided_upper": clopper_pearson_upper(k, n, confidence, one_sided=True),
        "bayesian_priors": {},
    }
    for name, a0, b0 in (("uniform_beta_1_1", 1.0, 1.0), ("jeffreys_beta_0.5_0.5", 0.5, 0.5)):
        a_post, b_post = a0 + k, b0 + (n - k)
        out["bayesian_priors"][name] = {
            "prior": {"alpha": a0, "beta": b0},
            "posterior": {"alpha": a_post, "beta": b_post},
            "posterior_mean": a_post / (a_post + b_post),
            "credible_95_two_sided": {
                "lower": beta_quantile(tail, a_post, b_post),
                "upper": beta_quantile(1.0 - tail, a_post, b_post),
            },
            "credible_95_one_sided_upper": beta_quantile(confidence, a_post, b_post),
        }
    return out


def compute_statistical_bounds(decision_comparison: dict) -> dict:
    """Summarize small-sample error bounds, clustering structure, and conservative-prior cost check."""
    import evaluation as dc_eval

    base_rows = decision_comparison["base_rows"]
    recovered_rows = decision_comparison["recovered_rows"]
    traces = decision_comparison["traces_by_policy"]
    cfg = decision_comparison["scenario_config"]
    scores = decision_comparison["scores"]
    timing = decision_comparison["timing"]

    by_cid = {r["case_id"]: r for r in base_rows}
    events = sorted({r["event_id"] for r in base_rows})
    task_slots = sorted({(r["event_id"], r["task_id"], r["slot_id"]) for r in base_rows})
    high_risk_rule = cfg["high_risk_rule"]

    policies_report = {}
    for policy, tr in traces.items():
        auto_all = [t for t in tr if not t["to_human"]]
        auto_high = [t for t in auto_all if by_cid[t["case_id"]]["rule"] == high_risk_rule]
        by_event = {}
        for ev in events:
            ev_all = [t for t in auto_all if by_cid[t["case_id"]]["event_id"] == ev]
            ev_high = [t for t in auto_high if by_cid[t["case_id"]]["event_id"] == ev]
            by_event[ev] = {
                "all_automatic_errors": sum(t["auto_error"] for t in ev_all),
                "all_automatic_opportunities": len(ev_all),
                "high_risk_automatic_errors": sum(t["auto_error"] for t in ev_high),
                "high_risk_automatic_opportunities": len(ev_high),
            }
        policies_report[policy] = {
            "all_automatic": rate_bounds(sum(t["auto_error"] for t in auto_all), len(auto_all)),
            "high_risk_concentration_alert_automatic": rate_bounds(
                sum(t["auto_error"] for t in auto_high), len(auto_high)
            ),
            "by_event": by_event,
        }

    # Conservative high-risk prior stress test: replace rule_error_probability['concentration_alert']
    # with point/posterior/upper-bound rates derived from raw's 0/11 high-risk automatic observation.
    raw_hr = policies_report["raw"]["high_risk_concentration_alert_automatic"]
    stress_levels = [
        ("scenario_default_0.05", cfg["rule_error_probability"][high_risk_rule]),
        ("beta_1_1_posterior_mean", raw_hr["bayesian_priors"]["uniform_beta_1_1"]["posterior_mean"]),
        ("jeffreys_95_one_sided_upper", raw_hr["bayesian_priors"]["jeffreys_beta_0.5_0.5"]["credible_95_one_sided_upper"]),
        ("clopper_pearson_95_one_sided_upper", raw_hr["clopper_pearson_95_one_sided_upper"]),
    ]

    conservative_cost_check = []
    for label, p_hr in stress_levels:
        alt_cfg = {
            **cfg,
            "rule_error_probability": {**cfg["rule_error_probability"], high_risk_rule: p_hr},
        }
        row_out = {"risk_assumption": label, "assumed_high_risk_error_prob": p_hr, "policies": {}}
        for pol, rows_used, real_pol in (
            ("raw", base_rows, "raw"),
            ("verified", base_rows, "verified"),
            ("rule", base_rows, "rule"),
            ("verified_recovered", recovered_rows, "verified"),
            ("rule_recovered", recovered_rows, "rule"),
        ):
            _, sm = dc_eval.evaluate(rows_used, real_pol, scores, timing, alt_cfg, {}, alt_cfg["price_per_minute"], 1)
            row_out["policies"][pol] = {
                "to_human": sm["to_human"],
                "auto_error": sm["auto_error"],
                "average_cost": sm["average_cost"],
                "total_cost": sm["total_cost"],
            }
        best_pol = min(row_out["policies"], key=lambda k: row_out["policies"][k]["average_cost"])
        row_out["lowest_average_cost_policy"] = best_pol
        conservative_cost_check.append(row_out)

    return {
        "clustering_and_independence_disclosure": {
            "scheduled_decisions": len(base_rows),
            "distinct_historical_events": len(events),
            "events": events,
            "distinct_event_task_slot_units": len(task_slots),
            "repeated_runs_per_task_group": 3,
            "warning": (
                "Repeated model runs share the same two frozen historical events (usdc_2023 and ftx_2022) "
                "and six task-slot pairs. Clopper-Pearson and Beta bounds treat automatic decisions as "
                "independent Bernoulli trials and are therefore nominal upper bounds under an i.i.d. assumption; "
                "true out-of-window generalization requires the held-out 2023-06-15 confirmation window."
            ),
        },
        "policies": policies_report,
        "conservative_high_risk_prior_cost_check": conservative_cost_check,
    }
