"""Check a held-out window offline or run the 18 scheduled calls after reference review."""
import argparse
from collections import Counter
import csv
from datetime import date
from hashlib import sha256
import os
from pathlib import Path
from time import perf_counter

import numpy as np
from evaluation import BASE, HERE, POLICIES, evaluate, load, save, table
from models import FEATURES, MODELS, features, predictor
from replay import action, outcome
from curve_audit.budget_guard import BudgetLimits
from curve_audit.current_runner import agent_run
from curve_audit.current_study import digest, equal, reference_candidates, score_report, template_report
from curve_audit.current_verify import bound_recorded_sources, verify_v2
from curve_audit.current_study import publish
from curve_audit.deepseek_adapter import DeepSeekAdapter

RULES = {('H02','S1'):'share_direction', ('H02','S2'):'peak_follow_up_hour',
         ('H03','S2'):'concentration_alert'}


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
    rows=[]; scores={}; timing={}
    for spec in runs:
        task=by_id[spec['task_id']]
        record=agent_run(primary,task,spec['group'],DeepSeekAdapter(model_config['model']),limits,
                         per_call_output=model_config['per_call_output'])
        record.update(spec,requested_model=model_config['model'])
        record['score']=score_report(primary,task,record['report'],refs)
        save(output/(spec['run_id']+'.json'),record)
        report=record['report']
        checks=verify_v2(primary,secondary,task,report,expected_primary_sha256=digest(primary))
        published,_=publish(primary,task,report,checks_override=checks)
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
    for t in tasks:
        report,_=template_report(primary,t)
        save(output/(t['task_id']+'_T.json'),{'report':report,'score':score_report(primary,t,report,refs)})
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
