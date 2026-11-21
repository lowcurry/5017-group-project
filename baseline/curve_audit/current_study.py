"""Executable two-event study. Deterministic demos are never model observations."""
from __future__ import annotations
import argparse, copy, csv, hashlib, json, random, re, time
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from .common import canonical, read_json, write_json, ROOT
from .metrics import calculate
from .verification import recompute, normalize_query, validate_dataset
from .independent_source import as_recompute_dataset, replay_record

VERSION = '3pool-erc20-balanceOf-20261102'
TOLERANCES = {m: {'atol': '0.00000001', 'rtol': '0'} for m in
              ('share_pct','quote_rate','size_sensitivity_bps','imbalance_l1')}
WINDOWS = [('usdc_2023','2023-03-10T00:00:00Z','2023-03-11T00:00:00Z'),
           ('ftx_2022','2022-11-09T00:00:00Z','2022-11-10T00:00:00Z')]

def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()
def shift(value, hours): return (datetime.fromisoformat(value.replace('Z','+00:00'))+timedelta(hours=hours)).isoformat().replace('+00:00','Z')
def equal(a,b,metric):
    try:
        a,b=Decimal(str(a)),Decimal(str(b));t=TOLERANCES[metric]
        return a.is_finite() and b.is_finite() and abs(a-b)<=Decimal(t['atol'])+Decimal(t['rtol'])*abs(b)
    except (ArithmeticError,ValueError,TypeError):return False

def build_tasks():
    tasks=[]
    for event,start,end in WINDOWS:
        def q(metric='share_pct',operation='last',a=None,b=None,**extra):
            return dict(metric=metric,operation=operation,start=a or start,end=b or a or start,**extra)
        specs=[
          ('单点与单位',[
             ('DAI 名义占比',q(a=shift(start,2),coin='DAI')),
             ('1000 USDC→USDT 报价率',q('quote_rate',a=shift(start,2),direction='USDC_USDT',size=1000)),
             ('100000 相对 1000 输入的规模敏感度',q('size_sensitivity_bps',a=shift(start,2),direction='USDC_USDT',size=100000))]),
          ('窗口与追问',[
             ('USDC 占比末减初',q(operation='change',a=start,b=end,coin='USDC')),
             ('允许小时网格的采样失衡最大值及目标时点',q('imbalance_l1','maximum',start,end)),
             ('S2 返回时点后一小时的 100000 USDC→USDT 报价率',{'metric':'quote_rate','operation':'last','direction':'USDC_USDT','size':100000,'time_from_slot':'S2','offset_hours':1})]),
          ('混合证据边界',[
             ('允许首点 USDC 占比',q(coin='USDC')),
             ('允许证据内最大单小时 USDC 占比增幅及起止时点；不得跨越被禁时点',q(operation='max_hourly_change',a=shift(start,18),b=end,coin='USDC')),
             ('连续时间真实失衡峰值是否可由小时采样确定',q('continuous_imbalance_peak','maximum',start,end))])]
        for family,components in specs:
            task={'task_id':f'R{len(tasks)+1:02d}','event_id':event,'window':{'start':start,'end':end},
                  'question':family,'required_components':[{'slot_id':f'S{i+1}','label':label,'request':query} for i,(label,query) in enumerate(components)],
                  'allowed_evidence':{'excluded_targets':[shift(start,22)] if family=='混合证据边界' else []}}
            tasks.append(task)
    return tasks

def combine_sources(paths):
    records=[read_json(p) for p in paths];data=None;rows={}
    for source_path,record in zip(paths,records):
        if record.get('schema_version')=='erc20-supplement-1':
            from .balance_sources import load_supplement
            part,record=load_supplement(source_path)
        else:
            if record.get('acquisition_mode')!='public_https' or not replay_record(record)['pass']:
                raise ValueError('Source must be an intact public RPC recording')
            part=as_recompute_dataset(record)
        if record.get('status')!='available':raise ValueError('Incomplete source cannot be silently promoted')
        if data is None:data=copy.deepcopy(part)
        elif any(data.get(k)!=part.get(k) for k in ['chain_id','pool','coin_order','decimals','quote_sizes','balance_source','balance_contracts']):raise ValueError('Cannot mix balance definitions or public configurations')
        for row in part['snapshots']:
            point=row['target_timestamp_utc']
            normalized=copy.deepcopy(row);normalized['id']='UTC_'+point.replace('-','').replace(':','')
            if point in rows and rows[point]!=normalized:raise ValueError('Conflicting snapshots')
            rows[point]=normalized
    if not data:raise ValueError('No sources')
    data['snapshots']=sorted(rows.values(),key=lambda s:s['target_timestamp_utc'])
    data['sampling'].update(start_utc=data['snapshots'][0]['target_timestamp_utc'],end_utc=data['snapshots'][-1]['target_timestamp_utc'])
    problems=validate_dataset(data)
    if problems:raise ValueError('; '.join(problems))
    return data,records

def allowed_data(dataset,task):
    data=copy.deepcopy(dataset);excluded=set(task['allowed_evidence']['excluded_targets'])
    offsets = [component['request']['offset_hours'] for component in task['required_components']
               if 'time_from_slot' in component['request']]
    lo,hi=task['window']['start'],shift(task['window']['end'],max([0, *offsets]))
    data['snapshots']=[r for r in data['snapshots'] if lo<=r['target_timestamp_utc']<=hi and r['target_timestamp_utc'] not in excluded]
    return data

def resolve(request,resolved):
    query=copy.deepcopy(request)
    if 'time_from_slot' in query:
        dependency=query.pop('time_from_slot')
        point=shift(resolved[dependency]['target_timestamp_utc'],query.pop('offset_hours'))
        query.update(start=point,end=point)
    return query

def compute(data,query,*,independent=False):
    """Production uses Decimal calculator; verifier uses separate raw-integer code."""
    if query.get('metric')=='continuous_imbalance_peak':raise ValueError('sampling_resolution_insufficient')
    if query.get('operation')=='max_hourly_change':
        if query.get('metric') != 'share_pct' or query.get('coin', 'USDC') not in data['coin_order']:
            raise ValueError('max_hourly_change requires a share_pct query with a supported coin')
        rows=sorted([r for r in data['snapshots'] if query['start']<=r['target_timestamp_utc']<=query['end']],key=lambda r:r['target_timestamp_utc'])
        pairs=[(a,b) for a,b in zip(rows,rows[1:]) if shift(a['target_timestamp_utc'],1)==b['target_timestamp_utc']]
        if not pairs:raise ValueError('no_allowed_adjacent_pairs')
        if independent:
            from fractions import Fraction
            def share(r):
                amounts={c:Fraction(int(r['raw_balances'][c]),10**data['decimals'][c]) for c in data['coin_order']}
                return 100*amounts[query.get('coin','USDC')]/sum(amounts.values())
            pair=max(pairs,key=lambda ab:share(ab[1])-share(ab[0]))
            f=share(pair[1])-share(pair[0]);value=Decimal(f.numerator)/Decimal(f.denominator)
        else:
            def share(r):
                q=dict(metric='share_pct',operation='last',start=r['target_timestamp_utc'],end=r['target_timestamp_utc'],coin=query.get('coin','USDC'))
                return Decimal(calculate(data,q)['value'])
            pair=max(pairs,key=lambda ab:share(ab[1])-share(ab[0]));value=share(pair[1])-share(pair[0])
        evidence=[r for ab in pairs for r in ab];unique={r['id']:r for r in evidence}
        return {'query':query,'value':str(value),'unit':'percentage_points','evidence_ids':['E_'+r['id'] for r in unique.values()],
                'interval_start':pair[0]['target_timestamp_utc'],'interval_end':pair[1]['target_timestamp_utc']}
    # An endpoint difference remains answerable when an interior sample is masked.
    if query.get('operation')=='change':
        values=[];used=[]
        for point in [query['start'],query['end']]:
            point_query={**query,'operation':'last','start':point,'end':point}
            item=(recompute if independent else calculate)(data,point_query);values.append(Decimal(item['value']));used.extend(item['evidence_ids'])
        return {'query':normalize_query(query),'value':str(values[1]-values[0]),'unit':'percentage_points' if query['metric']=='share_pct' else item['unit'],'evidence_ids':list(dict.fromkeys(used))}
    return (recompute if independent else calculate)(data,query)

def reference_candidates(dataset,tasks):
    refs=[]
    for task in tasks:
        resolved={};data=allowed_data(dataset,task)
        for component in task['required_components']:
            try:
                query=resolve(component['request'],resolved)
                result=compute(data,query,independent=True);resolved[component['slot_id']]=result
                ref={'answerable':True,'result':result,'resolved_query':result['query']}
            except (ValueError,KeyError) as exc:
                ref={'answerable':False,'reason':str(exc),'resolved_query':copy.deepcopy(component['request'])}
            refs.append({'task_id':task['task_id'],'slot_id':component['slot_id'],'label_source':'automatic_not_human',**ref})
    return refs

def template_report(dataset,task):
    resolved={};claims=[];trace=[];data=allowed_data(dataset,task)
    for component in task['required_components']:
        slot=component['slot_id'];depends=[component['request']['time_from_slot']] if 'time_from_slot' in component['request'] else []
        query=resolve(component['request'],resolved)
        try:
            result=compute(data,query);resolved[slot]=result
            claim={**result,'status':'supported','formula_version':VERSION,'dataset_sha256':digest(dataset)}
            trace.append({'slot_id':slot,'query':query,'result':result})
        except ValueError as exc:
            claim={'query':query,'value':None,'unit':None,'status':'refused','explanation':str(exc),'evidence_ids':[]}
            trace.append({'slot_id':slot,'query':query,'error':str(exc)})
        claim.update(claim_id=task['task_id']+'_'+slot,slot_id=slot,depends_on=depends);claims.append(claim)
    return {'task_id':task['task_id'],'claims':claims,'answer':'确定性模板输出；不属于模型实验。'},trace

def valid_claim_state(claim):
    value = claim.get('value')
    status = claim.get('status')
    if value is None:
        return 'value' in claim and status in ('refused', 'unsupported')
    return status == 'supported' and type(value) in (str, int, float)


def replay_claim(dataset,task,claim):
    if validate_dataset(dataset):return {'verdict':'UNKNOWN','reason':'invalid_source_dataset'}
    if not valid_claim_state(claim):return {'verdict':'FAIL','reason':'invalid_claim_state'}
    data=allowed_data(dataset,task)
    if claim.get('value') is None:return {'verdict':'UNKNOWN','reason':'refusal_or_omission'}
    try:
        query=claim['query'];resolved={}
        for component in task['required_components']:
            request=resolve(component['request'],resolved)
            if component['slot_id']==claim.get('slot_id'):
                if query.get('operation')=='max_hourly_change':same=query==request
                else:same=normalize_query(query)==normalize_query(request)
                if not same:return {'verdict':'FAIL','reason':'task_query_mismatch'}
                break
            try:resolved[component['slot_id']]=compute(data,request,independent=True)
            except ValueError:pass
        else:return {'verdict':'UNKNOWN','reason':'unregistered_claim_requires_review'}
        expected=compute(data,query,independent=True)
    except (ValueError,KeyError,TypeError) as exc:return {'verdict':'UNKNOWN','reason':str(exc)}
    if claim.get('unit')!=expected['unit'] or not equal(claim['value'],expected['value'],query['metric']):
        return {'verdict':'FAIL','reason':'value_or_unit_mismatch','expected':expected}
    for key in ['target_timestamp_utc','interval_start','interval_end']:
        if key in expected and claim.get(key)!=expected[key]:return {'verdict':'FAIL','reason':key+'_mismatch','expected':expected}
    if not (claim.get('dataset_sha256')==digest(dataset) and claim.get('formula_version')==VERSION
            and set(claim.get('evidence_ids',[]))==set(expected['evidence_ids'])):
        return {'verdict':'UNKNOWN','reason':'incomplete_attribution','expected':expected}
    return {'verdict':'PASS','reason':'raw_recomputation_matches','expected':expected}

def score_report(dataset,task,report,references):
    refs={r['slot_id']:r for r in references if r['task_id']==task['task_id']}
    claims=report.get('claims',[]);by_slot={};duplicates=set()
    for claim in claims:
        sid=claim.get('slot_id')
        if sid in by_slot:duplicates.add(sid)
        by_slot[sid]=claim
    counts=Counter(answerable=0,correct=0,reproducible_correct=0,over_refusal=0,omission=0,unanswerable=0,justified_refusal=0,incorrect=0)
    rows=[]
    for slot,ref in refs.items():
        claim=by_slot.get(slot);check={'verdict':'UNKNOWN','reason':'missing_or_duplicate_slot'}
        state='omission';correct=False
        if claim and slot not in duplicates:
            refused=claim.get('value') is None and claim.get('status') in ['refused','unsupported']
            if not valid_claim_state(claim):
                state = 'incorrect'
                check = {'verdict': 'FAIL', 'reason': 'invalid_claim_state'}
            elif ref['answerable']:
                if refused:state='over_refusal'
                elif claim.get('value') is not None:
                    expected=ref['result'];query=claim.get('query',{})
                    try:
                        qsame = isinstance(query, dict) and (
                            query == ref['resolved_query'] if query.get('operation') == 'max_hourly_change'
                            else normalize_query(query) == normalize_query(ref['resolved_query']))
                    except (TypeError,ValueError):qsame=False
                    correct=qsame and claim.get('unit')==expected['unit'] and equal(claim['value'],expected['value'],query.get('metric'))
                    for key in ['target_timestamp_utc','interval_start','interval_end']:
                        if key in expected:correct=correct and claim.get(key)==expected[key]
                    state='correct' if correct else 'incorrect'
                    check=replay_claim(dataset,task,claim)
            else:
                state = 'justified_refusal' if (
                    refused and claim.get('explanation') == ref['reason']
                    and claim.get('query') == ref['resolved_query']) else 'incorrect'
        counts['answerable' if ref['answerable'] else 'unanswerable']+=1
        counts[state]+=1
        if correct and check['verdict']=='PASS':counts['reproducible_correct']+=1
        rows.append({'slot_id':slot,'state':state,'replay':check})
    return {'counts':dict(counts),'primary_rate':counts['reproducible_correct']/counts['answerable'] if counts['answerable'] else None,'slots':rows,
            'human_validation':False,'extra_prose_status':'requires_independent_human_review'}


def report_claims(task, report, *, allow_empty=False, allow_partial=False):
    if (not isinstance(report, dict) or report.get('task_id') != task['task_id']
            or not isinstance(report.get('answer'), str)):
        raise ValueError('Invalid report envelope')
    try:
        canonical(report).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError('Report must be finite UTF-8 JSON') from exc
    claims = report.get('claims')
    if not isinstance(claims, list) or any(not isinstance(claim, dict) for claim in claims):
        raise ValueError('Claims must be a list of objects')
    if not claims and allow_empty:
        return claims

    slots = [claim.get('slot_id') for claim in claims]
    identifiers = [claim.get('claim_id') for claim in claims]
    for values in (slots, identifiers):
        if any(not isinstance(value, str) or not value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError('Claims require unique, nonempty slot IDs and claim IDs')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', identifier) for identifier in identifiers):
        raise ValueError('Claim IDs may use only ASCII letters, numbers, underscores and hyphens')
    required = {component['slot_id'] for component in task['required_components']}
    provided = set(slots)
    if not provided or not provided <= required or (not allow_partial and provided != required):
        raise ValueError('Report must contain exactly the required slots')
    for claim in claims:
        if not valid_claim_state(claim):
            raise ValueError('Claim status and value are inconsistent')
        if not isinstance(claim.get('query'), dict):
            raise ValueError('Claim query must be an object')
        for field in ('evidence_ids', 'depends_on'):
            entries = claim.get(field, [])
            if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
                raise ValueError(field + ' must be a list of strings')
    return claims


def published_payload(report):
    """Project current or archived releases onto the verified public fields."""
    fields = ('claim_id', 'slot_id', 'value', 'unit', 'status', 'evidence_ids',
              'dataset_sha256', 'formula_version')
    claims = []
    for claim in report['claims']:
        query = claim['query']
        operation = query['operation']
        times = () if operation == 'change' else (
            ('interval_start', 'interval_end') if operation == 'max_hourly_change'
            else ('target_timestamp_utc',))
        checked = {key: copy.deepcopy(claim[key]) for key in (*fields, *times) if key in claim}
        checked['query'] = (copy.deepcopy(query) if query['operation'] == 'max_hourly_change'
                            else normalize_query(query))
        claims.append(checked)
    return {
        'task_id': report['task_id'],
        'claims': claims,
        'answer': '\n'.join(f"{c['claim_id']}: {str(c['value']).strip()} {c['unit']}" for c in claims),
        'withdrawals': copy.deepcopy(report['withdrawals']),
    }


def publish(dataset,task,report,*,checks_override=None):
    checks = {}
    dependencies_by_claim = {}
    claims = report_claims(task, report, allow_empty=True)
    claim_id_by_slot = {claim['slot_id']: claim['claim_id'] for claim in claims}
    required_dependency = {component['slot_id']: component['request']['time_from_slot']
                           for component in task['required_components'] if 'time_from_slot' in component['request']}
    claim_ids = set(claim_id_by_slot.values())
    for claim in claims:
        cid = claim['claim_id']
        checks[cid] = (copy.deepcopy(checks_override[cid]) if checks_override is not None
                       else replay_claim(dataset, task, claim))
        dependencies = list(claim.get('depends_on', []))
        if claim['slot_id'] in required_dependency:
            dependencies.append(required_dependency[claim['slot_id']])
        dependencies_by_claim[cid] = {
            claim_id_by_slot.get(dep, dep if dep in claim_ids else task['task_id'] + '_' + dep)
            for dep in dependencies
        }
    eligible = {cid for cid, check in checks.items() if check['verdict'] == 'PASS'}
    kept = set()
    while ready := {cid for cid in eligible - kept if dependencies_by_claim[cid] <= kept}:
        kept.update(ready)
    for cid in eligible - kept:
        checks[cid] = {'verdict': 'UNKNOWN', 'reason': 'withdrawn_dependency'}
    retained = [claim for claim in claims if claim['claim_id'] in kept]
    withdrawals = [{'claim_id': cid, **check} for cid, check in checks.items() if cid not in kept]
    return published_payload({'task_id': report['task_id'], 'claims': retained,
                              'withdrawals': withdrawals}), checks

def prepare(sources,out):
    out=Path(out)
    if out.exists():raise FileExistsError('Choose a new preparation directory')
    dataset,records=combine_sources(sources);tasks=build_tasks();refs=reference_candidates(dataset,tasks)
    if len(refs)!=18 or sum(r['answerable'] for r in refs)!=16:raise ValueError('Unexpected coverage; inspect sources, do not tune on outputs')
    out.mkdir(parents=True)
    write_json(out/'dataset.json',dataset);write_json(out/'tasks.json',tasks)
    write_json(out/'references.automatic.private.json',refs)
    seed=20261102;runs=[{'run_id':f"{t['task_id']}_{g}_{n}",'task_id':t['task_id'],'group':g,'repetition':n} for t in tasks for n in range(1,4) for g in ['G','E']]
    random.Random(seed).shuffle(runs)
    write_json(out/'runs.json',runs)
    binding={'version':VERSION,'tasks_sha256':digest(tasks),'dataset_sha256':digest(dataset),'references_sha256':digest(refs),'runs_sha256':digest(runs),
             'source_hashes':[r['content_sha256'] for r in records],'tolerances':TOLERANCES,'seed':seed,
             'primary_metric':'reproducible_correct_fixed_component_coverage','human_references_validated':False,'formal_outputs':0}
    write_json(out/'binding.json',binding)
    # Blank independent worksheets deliberately do not reveal automatic candidate values.
    for role in ['A','B','arbitration']:
        with (out/f'reviewer_{role}.csv').open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['task_id','slot_id','reviewer','date_utc','answerable','value','unit','target_or_interval','calculation_and_source','decision'])
            writer.writerows([[t['task_id'],c['slot_id']]+['']*8 for t in tasks for c in t['required_components']])
    return binding

def engineering_demo(preparation,out):
    prep,out=Path(preparation),Path(out)
    if out.exists():raise FileExistsError('Output exists; preserve prior run')
    dataset,tasks,refs=[read_json(prep/name) for name in ['dataset.json','tasks.json','references.automatic.private.json']]
    binding=read_json(prep/'binding.json')
    if any(digest(v)!=binding[k] for v,k in [(dataset,'dataset_sha256'),(tasks,'tasks_sha256'),(refs,'references_sha256')]):raise ValueError('Preparation content changed')
    rows=[]
    for task in tasks:
        report,trace=template_report(dataset,task)
        scored=score_report(dataset,task,report,refs)
        published,checks=publish(dataset,task,report)
        rows.append({'task_id':task['task_id'],'report':report,'tool_trace':trace,'score':scored,'published':published,'verification':checks,'model_inference_performed':False})
    totals={k:sum(r['score']['counts'][k] for r in rows) for k in rows[0]['score']['counts']}
    summary={'pass':totals['correct']==16 and totals['reproducible_correct']==16 and totals['justified_refusal']==2,
             'tasks':6,'totals':totals,'model_calls':0,'formal_model_outputs':0,'human_references_validated':False,
             'scope':'deterministic template and independent arithmetic; not model performance'}
    write_json(out/'outputs.json',rows);write_json(out/'summary.json',summary)
    manifest={'binding':binding,'files':{'outputs.json':hashlib.sha256((out/'outputs.json').read_bytes()).hexdigest(),'summary.json':hashlib.sha256((out/'summary.json').read_bytes()).hexdigest()}}
    (out/'manifest.json').write_text(canonical(manifest))
    return summary

def main(argv=None):
    p=argparse.ArgumentParser(description='Two-event executable study, based on original Nov 2 plan and trials')
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--sources',type=Path,nargs='+',required=True);a.add_argument('--output',type=Path,required=True)
    a=sub.add_parser('replay');a.add_argument('directory',type=Path)
    a=sub.add_parser('demo');a.add_argument('--preparation',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    if args.command=='prepare':result=prepare(args.sources,args.output)
    elif args.command=='demo':result=engineering_demo(args.preparation,args.output)
    else:
        import tempfile
        bound=read_json(args.directory/'manifest.json')
        result={'pass':all(hashlib.sha256((args.directory/name).read_bytes()).hexdigest()==h for name,h in bound['files'].items()),'network_used':False,'model_calls':0}
        if result['pass']:
            prep=args.directory.parent/'current_preparation'
            with tempfile.TemporaryDirectory() as temp:
                fresh=Path(temp)/'replayed'
                engineering_demo(prep,fresh)
                result['pass']=read_json(fresh/'outputs.json')==read_json(args.directory/'outputs.json')
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result.get('pass',True) else 2
if __name__=='__main__':raise SystemExit(main())
