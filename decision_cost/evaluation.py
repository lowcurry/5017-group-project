"""Replay cost-sensitive intervention policies on frozen historical decisions."""
import argparse
from collections import Counter
import csv
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import random
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
from models import FEATURES, MODELS, features, fit, predictor

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = ROOT / 'baseline'
sys.path.insert(0,str(BASE))
sys.path.insert(0, str(BASE / 'audit/decision_replay_20261112'))
from replay import replay
POLICIES = ('raw', 'verified', 'rule', *MODELS)


def load(path):
    return json.loads(path.read_text())


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def table(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def observations(seed):
    replayed = replay(BASE)
    rows = replayed['research']['decisions']
    if rows != load(BASE/'audit/decision_replay_20261112/results.json')['research']['decisions']:
        raise ValueError('Decision archive mismatch')
    random.Random(seed).shuffle(rows)
    shares = Counter(r['run_id'] for r in rows)
    case_inputs = []
    for i, row in enumerate(rows, 1):
        run = load(BASE/'experiments/formal_v1'/f"{row['run_id']}.json")
        claim = next((c for c in run['report']['claims'] if c['slot_id'] == row['slot_id']), None)
        usage = run['ledger']['actual_usage']
        case_id = f'D{i:03}'
        row.update(case_id=case_id, x=features(row['rule'], claim, row['raw_action']),
                   run_model_calls=run['ledger']['dispatched_model_calls'], run_wall_seconds=run['wall_seconds'],
                   allocated_input=usage['input_tokens']/shares[row['run_id']],
                   allocated_output=usage['output_tokens']/shares[row['run_id']])
        case_inputs.append({'case_id': case_id, 'task': row['task_id'], 'slot': row['slot_id'],
                            'decision_rule': row['rule'], 'claim': claim, 'report_text': run['report']['answer']})
    return rows, case_inputs


def probabilities(rows, config):
    fits, timings, scores = {}, {}, {}
    for event in sorted({r['event_id'] for r in rows}):
        train = [r for r in rows if r['event_id'] != event and r['raw_action'] is not None]
        x = np.array([r['x'] for r in train])
        y = np.array([r['raw_outcome'] == 'wrong' for r in train], dtype=float)
        test = [r for r in rows if r['event_id'] == event]
        for mode in MODELS:
            model, duration = fit(x, y, mode, config['training_seed'])
            predict = predictor(model)
            inputs = [np.array(r['x']) for r in test]
            values = [predict(value) for value in inputs]
            times = []
            for _ in range(7):
                start = perf_counter()
                for value in inputs:
                    predict(value)
                times.append((perf_counter()-start)/len(inputs))
            key = f'{event}/{mode}'
            timings[key] = {'training_seconds': duration, 'cpu_inference_seconds_per_case': median(times),
                            'training_count': len(train), 'training_errors': int(y.sum())}
            fits[key] = model
            scores.update({(r['case_id'], mode): p for r,p in zip(test, values)})
    return fits, timings, scores


def loss_for(rule, action, config, *, prediction):
    if rule != 'concentration_alert':
        return config['loss'][rule]
    # Before a decision: error severity conditional on the proposed action.
    # After a decision: error severity conditional on the benchmark outcome.
    miss = action == ('no_alert' if prediction else 'escalate')
    return config['loss']['concentration_miss' if miss else 'concentration_false_alarm']


def choose(policy, row, risk, config, wage, loss_scale):
    if policy == 'verified':
        return 'verify'
    if row['raw_action'] is None:
        return 'human'
    if policy == 'raw':
        return 'release'
    severity = loss_scale * loss_for(row['rule'], row['raw_action'], config, prediction=True)
    direct = risk * severity
    # A conservative prospective ceiling for review errors in asymmetric alert decisions.
    review_severity = max(config['loss']['concentration_miss'], config['loss']['concentration_false_alarm']) * loss_scale if row['rule']=='concentration_alert' else severity
    human = config['review_minutes'] * wage + config['review_error_probability'] * review_severity
    verify = (config['verify_cost'] + config['verification_referral_probability'] * human
              + config['verification_fraction_errors_missed'] * direct)
    costs = {'release': direct, 'verify': verify, 'human': human}
    return min(costs, key=costs.get)


def read_reviews(path, rows):
    if path is None:
        return {}
    with path.open(encoding='utf-8-sig', newline='') as f:
        records = list(csv.DictReader(f))
    expected = {r['case_id']:r for r in rows}
    if len(records)!=len(expected) or {r['case_id'] for r in records} != expected.keys():
        raise ValueError('Expected one row per case, including unfilled rows')
    results = {}
    for r in records:
        fields = ['reviewer','start_iso','end_iso','decision','previously_seen']
        if not any(r[k].strip() for k in fields):
            continue
        if not all(r[k].strip() for k in fields) or r['previously_seen'] not in ('yes','no','unsure'):
            raise ValueError('Incomplete review '+r['case_id'])
        start,end = (datetime.fromisoformat(r[k]) for k in ('start_iso','end_iso'))
        if start.utcoffset() is None or end.utcoffset() is None or end<=start:
            raise ValueError('Expected chronological times with timezone')
        rule = expected[r['case_id']]['rule']
        act = r['decision'].strip()
        if rule == 'peak_follow_up_hour':
            point = datetime.strptime(act, '%Y-%m-%dT%H:00:00Z')
            if point.strftime('%Y-%m-%dT%H:00:00Z') != act:
                raise ValueError('Invalid UTC hour')
        elif act not in ({'increasing','decreasing','unchanged'} if rule=='share_direction' else {'escalate','no_alert'}):
            raise ValueError('Invalid action')
        results[r['case_id']] = ((end-start).total_seconds()/60, act)
    return results


def evaluate(rows, policy, scores, timing, config, reviews, wage, scale):
    records = []
    for row in rows:
        rule, cid = row['rule'], row['case_id']
        risk = scores.get((cid,policy),config['rule_error_probability'][rule])
        route = choose(policy, row, risk, config, wage, scale)
        action = row['verified_action'] if route=='verify' else row['raw_action']
        human = route=='human' or action is None
        measured = human and cid in reviews
        minutes = reviews[cid][0] if measured else (config['review_minutes'] if human else 0)
        loss = scale * loss_for(rule, row['expected_action'], config, prediction=False)
        wrong = not human and action!=row['expected_action']
        residual = (float(reviews[cid][1]!=row['expected_action']) if measured else config['review_error_probability']) if human else 0
        t = timing.get(f"{row['event_id']}/{policy}", {})
        generation = (row['allocated_input']*config['input_cost_per_1000_tokens']+
                      row['allocated_output']*config['output_cost_per_1000_tokens'])/1000
        # Shared formal captures: two providers, 2,250 base attempts + 306 supplemental RPC items.
        shared = config['shared_rpc_items'] * config['shared_rpc_cost_per_request_item']/len(rows)
        inference = t.get('cpu_inference_seconds_per_case',0)*config['cpu_cost_per_second']
        training = t.get('training_seconds',0)*config['cpu_cost_per_second']/config['amortize_training_over_decisions']
        compute = generation + shared + (config['verify_cost'] if route=='verify' else 0) + inference + training
        records.append(dict(case_id=cid, policy=policy, route=route, to_human=human, minutes=minutes,
                            human_cost_source=('observed' if measured else 'scenario') if human else 'unused',
                            auto_error=int(wrong), high_risk_auto_error=int(wrong and rule==config['high_risk_rule']),
                            model_risk=risk if policy not in ('raw','verified') else None,
                            generation_cost=generation, shared_cost=shared, verifier_cost=config['verify_cost'] if route=='verify' else 0,
                            inference_cost=inference, amortized_training_cost=training, compute_cost=compute,
                            human_cost=minutes*wage, error_cost=loss*(wrong+residual),
                            total_cost=compute+minutes*wage+loss*(wrong+residual)))
    total = lambda key: sum(r[key] for r in records)
    result = {k:total(k) for k in ('compute_cost','human_cost','error_cost','total_cost','minutes','to_human','auto_error','high_risk_auto_error')}
    result.update(policy=policy, wage=wage, loss_scale=scale, average_cost=result['total_cost']/len(rows),
                  auto_completed=len(rows)-result['to_human'], cases=len(rows), measured_reviews=sum(r['human_cost_source']=='observed' for r in records),
                  within_observed_limit=result['high_risk_auto_error']<=config['automatic_high_risk_errors_allowed'])
    return records,result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, default=HERE/'scenario.json')
    parser.add_argument('--reviews', type=Path)
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error('Choose a new output directory')
    cfg=load(args.scenario)
    fixed=load(ROOT/'results/resource-use.json')['historical_rpc_collection']
    cfg['shared_rpc_items']=sum(fixed[p]['base_rpc_attempts']+fixed[p]['supplement_rpc_items'] for p in ('alchemy','drpc'))
    for field in ('review_minutes','price_per_minute','verify_cost','input_cost_per_1000_tokens',
                  'output_cost_per_1000_tokens','shared_rpc_cost_per_request_item','cpu_cost_per_second'):
        if not math.isfinite(cfg[field]) or cfg[field]<0:
            parser.error('Invalid nonnegative cost or time: '+field)
    probabilities_to_check = [cfg['review_error_probability'], cfg['verification_referral_probability'],
                              cfg['verification_fraction_errors_missed'], *cfg['rule_error_probability'].values()]
    if any(not 0 <= x <= 1 for x in probabilities_to_check) or cfg['amortize_training_over_decisions'] <= 0:
        parser.error('Invalid scenario probability or amortization volume')
    if any(not math.isfinite(x) or x<0 for x in cfg['loss'].values()):
        parser.error('Invalid loss')
    rows, cases = observations(cfg['training_seed'])
    reviews=read_reviews(args.reviews,rows)
    models,timing,scores=probabilities(rows,cfg)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as tmp:
        output=Path(tmp)/'results'
        output.mkdir()
        save(output/'models.json', {'features':FEATURES,'by_held_out_event':models})
        save(output/'timing.json', timing)
        save(output/'settings.json',cfg)
        save(output/'inputs.json',{str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in (
            BASE/'audit/decision_replay_20261112/results.json',ROOT/'results/resource-use.json')})
        table(output/'decision-ledger.csv',[{**{k:v for k,v in r.items() if k!='x'}, **dict(zip(FEATURES,r['x']))} for r in rows])
        traces, summaries, grid=[],[],[]
        for policy in POLICIES:
            trace,summary=evaluate(rows,policy,scores,timing,cfg,reviews,cfg['price_per_minute'],1)
            traces.extend(trace); summaries.append(summary)
            for wage in (0.25,0.5,1,2,4):
                for scale in (0.1,0.25,0.5,1,2,4,10):
                    grid.append(evaluate(rows,policy,scores,timing,cfg,reviews,wage,scale)[1])
        for name,data in [('policy-trace',traces),('policy-summary',summaries),('sensitivity',grid)]:
            table(output/(name+'.csv'),data)
        from diagnostics import diagnostics
        save(output/'diagnostics.json',diagnostics(rows,traces,summaries,cfg,timing))
        save(output/'human-observations.json', {
            'returned':len(reviews), 'by_rule':{
                rule:[{'case_id':r['case_id'], 'minutes':reviews[r['case_id']][0],
                       'correct':reviews[r['case_id']][1]==r['expected_action']}
                      for r in rows if r['rule']==rule and r['case_id'] in reviews]
                for rule in sorted({r['rule'] for r in rows})}})
        train=[r for r in rows if r['raw_action'] is not None]
        train_x=np.array([r['x'] for r in train])
        train_y=np.array([r['raw_outcome']=='wrong' for r in train],dtype=float)
        deploy={mode:fit(train_x,train_y,mode,cfg['training_seed'])[0] for mode in MODELS}
        save(output/'confirmation-models.json',{'features':FEATURES,'training_decisions':len(train),
             'training_event_ids':sorted({r['event_id'] for r in rows}), 'models':deploy})
        from figures import render
        render(output,summaries,grid)
        from packets import make_packet
        make_packet(output, cases, BASE)
        output.rename(args.output)


if __name__=='__main__':
    main()
