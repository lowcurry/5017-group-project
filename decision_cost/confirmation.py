"""Check a held-out window offline or run the 18 scheduled calls after reference review."""
import argparse
from collections import Counter
import csv
from datetime import date
from hashlib import sha256
import os
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
from evaluation import BASE, HERE, POLICIES, evaluate, load, save, table
from models import FEATURES, MODELS, features, predictor
from replay import action, outcome
from curve_audit.budget_guard import BudgetLimits
from curve_audit.current_runner import agent_run
from curve_audit.current_study import digest, equal, reference_candidates, score_report, template_report
from curve_audit.current_verify import bound_recorded_sources, publish_verified, verify_v2
from curve_audit.deepseek_adapter import DeepSeekAdapter

LEDGER_DIR = str(BASE / 'audit/cost_trace_20261111')
if LEDGER_DIR not in sys.path:
    sys.path.insert(0, LEDGER_DIR)
from ledger import RunLedger

RULES = {('H02','S1'):'share_direction', ('H02','S2'):'peak_follow_up_hour',
         ('H03','S2'):'concentration_alert'}
LEDGER_PHASES = ('model_call', 'verify_v2', 'publish_verified', 'policy_score')


def _normalize_model_usage(raw_usage):
    if not isinstance(raw_usage, dict):
        return None
    inp, out = raw_usage.get('input_tokens'), raw_usage.get('output_tokens')
    if type(inp) is not int or type(out) is not int or inp < 0 or out < 0:
        return None
    usage = {'input_tokens': inp, 'output_tokens': out}
    cached = raw_usage.get('cached_input_tokens')
    if type(cached) is int and 0 <= cached <= inp:
        usage['cached_input_tokens'] = cached
    return usage


def _record_model_attempts(run_ledger, provider, spec, record):
    ledger_data = record.get('ledger') or {}
    calls = ledger_data.get('calls')
    status = record.get('status', 'completed')
    if isinstance(calls, list) and calls:
        for index, call in enumerate(calls, 1):
            outcome_label = call.get('state') or status
            run_ledger.attempt(
                'model', provider, f"{spec['run_id']}_call_{index}", 1, outcome_label,
                usage=_normalize_model_usage(call.get('usage')),
            )
        return
    actual = _normalize_model_usage(ledger_data.get('actual_usage'))
    if actual is not None or status != 'completed':
        run_ledger.attempt('model', provider, f"{spec['run_id']}_call_1", 1, status, usage=actual)


def digest_file(path):
    return sha256(path.read_bytes()).hexdigest()


def study_data(window):
    private=window/'coordinator'
    binding=load(private/'binding.json')
    tasks=load(window/'reviewers/tasks.json')
    data,secondary=bound_recorded_sources(binding, HERE/'holdout/raw')
    refs=reference_candidates(data,tasks)
    runs=load(private/'runs.json')
    if refs!=reference_candidates(secondary,tasks) or refs!=load(private/'candidate-references.json'):
        raise ValueError('Two-source reference mismatch')
    for obj,key in ((tasks,'tasks_sha256'),(refs,'references_sha256'),(runs,'runs_sha256')):
        if digest(obj)!=binding[key]:
            raise ValueError('Preparation mismatch: '+key)
    for obj,name in ((data,'primary'),(secondary,'secondary')):
        if obj!=load(window/f'reviewers/{name}.json'):
            raise ValueError('Reviewer data differ from raw captures')
    return tasks,data,secondary,refs,runs


def review_rows(path, refs, *, arbitrator=False):
    with path.open(encoding='utf-8-sig',newline='') as f:
        entries=list(csv.DictReader(f))
    results={}; people=set()
    for row in entries:
        key=row['task_id'],row['slot_id']
        if key in results or key not in refs:
            raise ValueError(f'Unexpected/duplicate review item {key}')
        if arbitrator and all(not row.get(k,'').strip() for k in ('reviewer','answerable','value','adjudication_reason')):
            continue
        if not row['reviewer'].strip() or not row['calculation_and_source'].strip():
            raise ValueError(f'{key} is missing the reviewer or calculation')
        date.fromisoformat(row['date_utc'])
        if arbitrator and not row['adjudication_reason'].strip():
            raise ValueError(f'Missing adjudication reason for {key}')
        people.add(row['reviewer'].strip().casefold())
        if row['answerable'].strip().lower() not in ('yes','no'):
            raise ValueError(f'{key} requires yes or no')
        results[key]=row
    if not arbitrator and (set(results)!=set(refs) or len(people)!=1):
        raise ValueError('Expected one reviewer and all nine items')
    return results,people


def agrees(row, ref):
    if (row['answerable'].strip().lower()=='yes')!=ref['answerable']:
        return False
    if not ref['answerable']:
        return not row['value'].strip() and not row['unit'].strip() and not row['target_or_interval'].strip()
    expected=ref['result']
    times='|'.join(expected[k] for k in ('target_timestamp_utc','interval_start','interval_end') if k in expected)
    return row['unit'].strip()==expected['unit'] and row['target_or_interval'].strip()==times and equal(
        row['value'].strip(),expected['value'],ref['resolved_query']['metric'])


def validate_reviews(a,b,arbitration,refs):
    indexed={(r['task_id'],r['slot_id']):r for r in refs}
    first,one=review_rows(a,indexed); second,two=review_rows(b,indexed)
    if one==two:
        raise ValueError('Two distinct reviewers are required')
    conflicts={k for k,ref in indexed.items() if not (agrees(first[k],ref) and agrees(second[k],ref))}
    resolved,three=review_rows(arbitration,indexed,arbitrator=True) if arbitration else ({},set())
    if (set(resolved)!=conflicts or three & (one|two) or len(three)>1 or
            any(not agrees(row,indexed[k]) for k,row in resolved.items())):
        raise ValueError('Resolve the listed reference differences or revise and re-freeze the benchmark: '+str(sorted(conflicts)))
    return {role:digest_file(path) for role,path in (('A',a),('B',b),('adjudication',arbitration)) if path}


def run(window, output, a, b, arbitration):
    tasks,primary,secondary,refs,runs=study_data(window)
    reviews=validate_reviews(a,b,arbitration,refs)
    pinned=load(HERE/'holdout/freeze.json')
    if any(digest_file(HERE/path)!=value for path,value in pinned['files'].items()):
        raise ValueError('Frozen confirmation files changed')
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise ValueError('Missing model credential')
    model_config=load(BASE/'config/current_model.flash.json')
    limits=BudgetLimits(**{key:model_config[key] for key in ('input_tokens','output_tokens','total_tokens','model_calls')})
    cfg=load(HERE/'scenario.json')
    raw_files=list((HERE/'holdout/raw').glob('*.json'))
    cfg['shared_rpc_items']=sum(len(o['requests']) for p in raw_files if p.name.endswith('_erc20.json')
                                for o in load(p)['observations'])
    for p in raw_files:
        if not p.name.endswith('_erc20.json'):
            for attempt in load(p)['provider_attempts']:
                cfg['shared_rpc_items']+=len(attempt['transcript'])
    output.mkdir(parents=True,exist_ok=False)
    save(output/'freeze-used.json',pinned)
    save(output/'review-digests.json',reviews); save(output/'schedule.json',runs)
    candidate=load(HERE/'results/confirmation-models.json')
    if candidate['features']!=list(FEATURES):
        raise ValueError('Model feature order changed')
    models={mode:predictor(candidate['models'][mode]) for mode in MODELS}
    by_id={t['task_id']:t for t in tasks}
    rows=[]; scores={}; timing={}; run_ledgers=[]
    for spec in runs:
        task=by_id[spec['task_id']]
        run_ledger=RunLedger(spec['run_id'], f"confirmation_{spec['group']}")
        with run_ledger.phase('model_call'):
            record=agent_run(primary,task,spec['group'],DeepSeekAdapter(model_config['model']),limits,
                             per_call_output=model_config['per_call_output'])
            _record_model_attempts(run_ledger, model_config['model'], spec, record)
        record.update(spec,requested_model=model_config['model'])
        record['score']=score_report(primary,task,record['report'],refs)
        report=record['report']
        with run_ledger.phase('verify_v2'):
            checks=verify_v2(primary,secondary,task,report,expected_primary_sha256=digest(primary))
        with run_ledger.phase('publish_verified'):
            published,verification,arithmetic=publish_verified(primary,task,report,checks)
        record['verification']=verification
        record['independent_arithmetic']=arithmetic
        record['published']=published
        with run_ledger.phase('policy_score'):
            used=[(slot,rule) for (task_id,slot),rule in RULES.items() if task_id==spec['task_id']]
            usage=record['ledger']['actual_usage']
            for slot,rule in used:
                claim=next((c for c in report['claims'] if c['slot_id']==slot),None)
                kept=next((c for c in published['claims'] if c['slot_id']==slot),None)
                ref=next(r for r in refs if (r['task_id'],r['slot_id'])==(spec['task_id'],slot))
                expected=action(rule,{**ref['result'],'status':'supported'})
                raw=action(rule,claim); verified=action(rule,kept)
                event=task['event_id']; cid=spec['run_id']+'_'+slot
                x=np.array(features(rule,claim,raw))
                for mode,predict in models.items():
                    begin=perf_counter(); scores[cid,mode]=predict(x)
                    timing.setdefault(f'{event}/{mode}',[]).append(perf_counter()-begin)
                rows.append({**spec,'case_id':cid,'event_id':event,'slot_id':slot,'rule':rule,
                             'raw_action':raw,'raw_outcome':outcome(raw,expected),
                             'verified_action':verified,'expected_action':expected,
                             'allocated_input':usage['input_tokens']/len(used),
                             'allocated_output':usage['output_tokens']/len(used)})
        ledger_snapshot=run_ledger.snapshot()
        record['run_ledger']=ledger_snapshot
        run_ledgers.append(ledger_snapshot)
        save(output/(spec['run_id']+'.json'),record)
    for t in tasks:
        report,_=template_report(primary,t)
        save(output/(t['task_id']+'_T.json'),{'report':report,'score':score_report(primary,t,report,refs)})
    save(output/'run-ledgers.json', {
        'cost_basis': 'scenario_estimate_only_for_policy_table',
        'recorded_charges_note': 'No billing receipts attached; recorded_charges_by_currency remains empty.',
        'runs': run_ledgers,
        'phase_seconds_totals': {
            phase: sum(item['phase_seconds'].get(phase, 0.0) for item in run_ledgers)
            for phase in LEDGER_PHASES
        },
        'known_model_usage_totals': {
            key: sum(item['known_model_usage'].get(key, 0) for item in run_ledgers)
            for key in ('input_tokens', 'output_tokens', 'cached_input_tokens')
        },
        'recorded_charges_by_currency': {},
    })
    stored=load(HERE/'results/timing.json')
    timing={key:{'cpu_inference_seconds_per_case':float(np.median(v)),
                 'training_seconds':float(np.mean([r['training_seconds'] for k,r in stored.items()
                                                   if k.endswith('/'+key.split('/')[-1])]))}
            for key,v in timing.items()}
    summaries=[]; traces=[]
    for policy in POLICIES:
        details,summary=evaluate(rows,policy,scores,timing,cfg,{},cfg['price_per_minute'],1)
        summaries.append(summary); traces.extend(details)
    table(output/'decisions.csv',rows); table(output/'policies.csv',summaries); table(output/'trace.csv',traces)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--window',type=Path,default=HERE/'holdout/preparation')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--review-a',type=Path,required=True); p.add_argument('--review-b',type=Path,required=True)
    p.add_argument('--adjudication',type=Path)
    args=p.parse_args()
    run(args.window,args.output,args.review_a,args.review_b,args.adjudication)
