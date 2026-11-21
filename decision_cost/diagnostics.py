"""Policy diagnostics in the same units as the cost analysis."""
import math
from collections import Counter


def diagnostics(rows, traces, summaries, cfg, timing):
    indexed={r['case_id']:r for r in rows}
    base={r['policy']:r for r in summaries}
    observed_gain=base['raw']['auto_error']-base['verified']['auto_error']
    incremental_reviews=base['verified']['to_human']-base['raw']['to_human']
    added_cost=(base['verified']['human_cost']+base['verified']['compute_cost']
                -base['raw']['human_cost']-base['raw']['compute_cost'])
    raw_residual=sum(t['error_cost'] for t in traces if t['policy']=='raw' and t['to_human'])
    verified_residual=sum(t['error_cost'] for t in traces if t['policy']=='verified' and t['to_human'])
    result={'raw_to_verified':{
        'additional_reviews':incremental_reviews,'automatic_errors_intercepted':observed_gain,
        'break_even_avoided_loss_with_perfect_review':added_cost/observed_gain if observed_gain else None,
        'break_even_avoided_loss_with_configured_review_outcomes':
        (added_cost+verified_residual-raw_residual)/observed_gain if observed_gain else None,
        'interpretation':'One avoided automatic error. Other residual review costs/losses held at configured values.'},
        'models':{},'risk_exposure':{}}
    for policy,summary in base.items():
        lines=[t for t in traces if t['policy']==policy]
        high=[t for t in lines if indexed[t['case_id']]['rule']==cfg['high_risk_rule']]
        result['risk_exposure'][policy]={'scheduled':len(high),'automatic':sum(not t['to_human'] for t in high),
            'automatic_errors':sum(t['auto_error'] for t in high),
            'routes':dict(Counter(t['route'] for t in lines))}
        stages=[v for key,v in timing.items() if key.endswith('/'+policy)]
        if not stages:
            continue
        automatic=[t for t in lines if indexed[t['case_id']]['raw_action'] is not None]
        brier=sum((t['model_risk']-(indexed[t['case_id']]['raw_outcome']=='wrong'))**2 for t in automatic)/len(automatic)
        one_fit=sum(t['training_seconds'] for t in stages)/len(stages)*cfg['cpu_cost_per_second']
        variable=(summary['total_cost']-sum(t['amortized_training_cost'] for t in lines))/len(rows)
        gain=base['rule']['average_cost']-variable
        result['models'][policy]={'brier_on_submitted_raw_decisions':brier,'sample_size':len(automatic),
            'average_one_fit_cost':one_fit,'per_decision_saving_vs_rule_before_training':gain,
            'decisions_to_recoup_fit_vs_rule':math.ceil(one_fit/gain) if gain>0 else None,
            'timing_scope':'CPU elapsed time for fit and warm in-memory risk score; excludes I/O and feature construction'}
    return result
