"""Identical G/E tool loops with pre-dispatch token reservation and frozen inputs."""
import argparse,copy,csv,json,os,time
from pathlib import Path
from .common import ROOT,read_json,write_json,canonical,utc
from .current_study import (VERSION,TOLERANCES,digest,allowed_data,compute,build_tasks,score_report,report_claims)
from .deepseek_adapter import DeepSeekAdapter
from .budget_guard import BudgetExceeded, BudgetLimits

BASE='''You are a Curve 3pool investigation agent. Use only this task's permitted data and supplied tools. Return exactly one JSON action each turn, with no markdown. Actions:
{"action":"read_window","start":"UTC ISO","end":"UTC ISO"}
{"action":"calculate","query":{"metric":"share_pct|quote_rate|size_sensitivity_bps|imbalance_l1","operation":"first|last|change|maximum|max_hourly_change","start":"UTC ISO","end":"UTC ISO","coin":"DAI|USDC|USDT","direction":"USDC_USDT|USDT_USDC","size":1000}}
A read_window request returns raw integer balances and quotes, target times and block identity. The evidence label for a snapshot is E_ plus its id. Prefer calculate for a derived metric. Omit irrelevant query keys. A calculate request returns raw-evidence labels and calculation metadata. max_hourly_change compares only adjacent permitted hourly points, never across a gap. Read/calculation permissions are identical in G and E.
{"action":"submit","report_json":"JSON STRING containing the final report"}
Report object: task_id, answer (150–250 Chinese characters of investigation text), claims array. Each claim: unique claim_id (ASCII letters, numbers, underscores or hyphens), slot_id, query, value (decimal string, or null if refused), unit, status (supported/refused/unsupported), explanation, depends_on (slot IDs), optional evidence_ids, formula_version, dataset_sha256, target_timestamp_utc (for a focused observation), interval_start/interval_end (for an hourly change). Evidence fields are available to all groups. Provide exactly one claim per required slot. Refuse only when the allowed evidence cannot support the requested component. For a continuous_imbalance_peak refusal, set the claim's explanation to the exact code sampling_resolution_insufficient and describe the hourly-sampling limitation in the report's answer. Preserve units, directions and exact target windows. Raw numerical values and reference answers are not given before tool use. You have no independent verifier during generation.'''
EVIDENCE='''For every numerical claim, attach exact evidence_ids, the complete resolved query, formula_version and dataset_sha256 from the public context. Check its units, target times and permitted evidence. Do not fill unsupported gaps. Separate observation from interpretation, and qualify interpretation that is not established by the data.'''

FINAL_TURN = {
    'role': 'user',
    'content': 'Final turn: submit report_json now, including partial answers and explicit refusals. No more tools.',
}
WINDOW_FIELDS = (
    'id', 'target_timestamp_utc', 'timestamp_utc', 'block_number', 'block_hash',
    'raw_balances', 'raw_quotes',
)


def read_window(data, start, end):
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError('Window bounds must be UTC timestamps')
    first, last = utc(start), utc(end)
    if first > last:
        raise ValueError('Window start must be before or equal to end')
    rows = [
        {field: snapshot[field] for field in WINDOW_FIELDS}
        for snapshot in data['snapshots']
        if first <= utc(snapshot['target_timestamp_utc']) <= last
    ]
    return {'snapshots': rows}


def parse_report(text,task):
    if not isinstance(text,str):raise ValueError('submit requires report_json STRING')
    report=json.loads(text)
    report_claims(task, report)
    return report

def agent_run(dataset,task,group,adapter,limits,*,per_call_output=2048):
    if group not in ['G','E']:raise ValueError('Unknown group')
    ledger=adapter.new_ledger(group,limits)
    context={'task':task,'dataset_sha256':digest(dataset),'formula_version':VERSION,'balance_source':dataset.get('balance_source'),'token_contracts':dataset.get('balance_contracts'),'decimals':dataset['decimals'],'units':{'share_pct':'percent','share_change':'percentage_points','quote_rate':'destination/source','size_sensitivity_bps':'basis_points','imbalance_l1':'dimensionless'}}
    messages=[{'role':'system','content':BASE+('\n'+EVIDENCE if group=='E' else '')},{'role':'user','content':canonical(context)}]
    data=allowed_data(dataset,task);trace=[];report=None;error=None;parse_repairs=0;started=time.monotonic()
    tool_budget_rejections = 0
    for index in range(limits.model_calls):
        if index==limits.model_calls-1:messages.append(FINAL_TURN)
        try:
            raw,meta=adapter.call(messages,ledger,'call_'+str(index),group,max_output=per_call_output)
            trace.append({'request_messages':copy.deepcopy(messages),'raw_response':raw,'metadata':meta})
            messages.append({'role':'assistant','content':raw})
            action=None
            try:
                action=json.loads(raw)
                if not isinstance(action,dict):raise ValueError('Action must be an object')
                name=action.get('action')
                if name=='submit':
                    report=parse_report(action.get('report_json'),task);break
                if index==limits.model_calls-1:raise ValueError('Submission required at call boundary')
                if name=='calculate':result=compute(data,action['query'])
                elif name=='read_window':
                    result = read_window(data, action['start'], action['end'])
                else:raise ValueError('Unknown tool action')
                response = {'role': 'user', 'content': canonical({'tool_result': result})}
                next_request = [*messages, response]
                if index == limits.model_calls - 2:
                    next_request.append(FINAL_TURN)
                try:
                    ledger.check_capacity(adapter.input_upper_bound(next_request), per_call_output)
                except BudgetExceeded:
                    tool_budget_rejections += 1
                    response['content'] = canonical({
                        'tool_error': 'This result would exceed the remaining budget. '
                                      'Use a smaller window, calculate one metric, or submit with the evidence already received.'
                    })
                messages.append(response)
            except (ValueError,KeyError,TypeError) as exc:
                if isinstance(action,dict) and action.get('action')=='submit':
                    parse_repairs+=1
                    if parse_repairs>1:raise ValueError('Submission repair exhausted')
                messages.append({'role':'user','content':canonical({'tool_error':str(exc),'submission_repairs_left':max(0,1-parse_repairs)})})
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc);break
    if report is None:
        report={'task_id':task['task_id'],'claims':[],'answer':''};error=error or 'step_limit_without_submission'
    return {'task_id':task['task_id'],'group':group,'status':'completed' if error is None else 'failed','error':error,
            'report':report,'trace':trace,'tool_budget_rejections':tool_budget_rejections,'ledger':ledger.snapshot(),'wall_seconds':time.monotonic()-started,'model_inference_attempted':ledger.snapshot()['dispatched_model_calls']>0,'model_inference_performed':True if ledger.snapshot()['actual_usage']['total_tokens'] else (None if ledger.snapshot()['dispatched_model_calls'] else False)}

def check_human(preparation,a,b):
    prep=Path(preparation);refs=read_json(prep/'references.automatic.private.json')
    keys={(r['task_id'],r['slot_id']) for r in refs};people=[];errors=[]
    for role,path in [('A',a),('B',b)]:
        with Path(path).open(newline='') as f:rows=list(csv.DictReader(f))
        if len(rows)!=18 or {(r.get('task_id'),r.get('slot_id')) for r in rows}!=keys:errors.append(role+': 18 unique components required');continue
        names={r.get('reviewer','').strip() for r in rows};people.append(names)
        if len(names)!=1 or not next(iter(names),''):errors.append(role+': named reviewer required')
        for row in rows:
            ref=next(r for r in refs if (r['task_id'],r['slot_id'])==(row['task_id'],row['slot_id']))
            if not all(row.get(k,'').strip() for k in ['date_utc','calculation_and_source','decision','answerable']):errors.append(role+': incomplete evidence '+row['task_id']+'/'+row['slot_id']);continue
            if row['decision'].strip().lower()!='approve':errors.append(role+': unresolved disagreement');continue
            if row['answerable'].lower() not in (['yes','true','1'] if ref['answerable'] else ['no','false','0']):errors.append(role+': answerability differs');continue
            if ref['answerable']:
                from .current_study import equal
                expected=ref['result']
                if row.get('unit')!=expected['unit'] or not equal(row.get('value'),expected['value'],ref['resolved_query']['metric']):errors.append(role+': reference disagreement requires investigation')
                required='|'.join(expected[k] for k in ['target_timestamp_utc','interval_start','interval_end'] if k in expected)
                if required and row.get('target_or_interval')!=required:errors.append(role+': reference time/interval differs')
    if len(people)==2 and people[0]==people[1]:errors.append('A and B must be different people')
    return {'pass':not errors,'errors':errors,'record_identity_scope':'Names and completeness checked; independence cannot be proved by software','reviewer_files_sha256':[digest(Path(a).read_text()),digest(Path(b).read_text())]}

def check_consolidated_review(preparation, review_path):
    from .current_study import equal

    prep = Path(preparation).resolve()
    path = Path(review_path).resolve()
    relative_path = path.relative_to(prep)
    record = read_json(path)
    scope = 'user_attested_consolidated_human_review'
    if (record.get('record_identity_scope') != scope
            or record.get('independent_dual_review_verified') is not False
            or not record.get('attestation') or not record.get('attestation_date_local')):
        raise ValueError('Consolidated review requires an explicit human provenance statement')
    source = path.with_name(record['source_file'])
    if hashlib_sha(source) != record.get('source_sha256'):
        raise ValueError('Consolidated review workbook hash differs')
    for filename, key in (
        ('dataset.json', 'dataset_sha256'),
        ('tasks.json', 'tasks_sha256'),
        ('references.automatic.private.json', 'references_sha256'),
    ):
        if digest(read_json(prep / filename)) != record.get(key):
            raise ValueError('Consolidated review input differs: ' + filename)
    references = read_json(prep / 'references.automatic.private.json')
    components = record['components']
    reviewed = {(row['task_id'], row['slot_id']): row for row in components}
    expected = {(row['task_id'], row['slot_id']): row for row in references}
    if len(components) != 18 or reviewed.keys() != expected.keys():
        raise ValueError('Consolidated review requires 18 unique components')
    for key, ref in expected.items():
        row = reviewed[key]
        if row['answerable'] is not ref['answerable'] or not row['calculation_or_refusal_basis'].strip():
            raise ValueError('Consolidated review answerability or basis differs: ' + '/'.join(key))
        if not ref['answerable']:
            if any(row.get(field) is not None for field in ('value','unit','target_utc','end_utc')):
                raise ValueError('Refused component contains an answer: ' + '/'.join(key))
            continue
        result, query = ref['result'], ref['resolved_query']
        start = result.get('target_timestamp_utc', result.get('interval_start', query['start']))
        end = None if 'target_timestamp_utc' in result else result.get('interval_end', query['end'])
        if (row['unit'] != result['unit'] or row['target_utc'] != start or row['end_utc'] != end
                or not equal(row['value'], result['value'], query['metric'])):
            raise ValueError('Consolidated review value, unit or time differs: ' + '/'.join(key))
    return {
        'pass': True,
        'record_identity_scope': scope,
        'independent_dual_review_verified': False,
        'review_record': str(relative_path),
        'review_record_sha256': hashlib_sha(path),
        'source_file': str(source.relative_to(prep)),
        'source_sha256': record['source_sha256'],
        'components_checked': len(reviewed),
    }


def validate_source_binding(preparation, binding, source_dir=None):
    from .current_verify import bound_recorded_sources

    folder = Path(source_dir) if source_dir is not None else preparation.resolve().parents[1] / 'data/current_study'
    primary, secondary = bound_recorded_sources(binding, folder)
    if (primary != read_json(preparation / 'dataset.json')
            or secondary != read_json(preparation / 'secondary_dataset.json')):
        raise ValueError('Bound raw recordings do not match the prepared datasets')
    return primary, secondary


def freeze(preparation,config_path,a=None,b=None,*,consolidated_review=None,source_dir=None):
    prep=Path(preparation);config=read_json(config_path)
    if consolidated_review:
        if a or b:
            raise ValueError('Choose two reviewer files or one consolidated review')
        review=check_consolidated_review(prep,consolidated_review)
    else:
        if not a or not b:
            raise ValueError('Both independent reviewer files are required')
        review=check_human(prep,a,b)
    if not review['pass']:raise ValueError('; '.join(review['errors']))
    if not config.get('model'):raise ValueError('Exact main model required')
    if config.get('provider','deepseek')=='deepseek' and config.get('budget_claim')!='empirically_checked_client_upper_bound':raise ValueError('DeepSeek budget must disclose client upper-bound assumption; no server cumulative cap')
    for key in ['input_tokens','output_tokens','total_tokens','model_calls','per_call_output']:
        if not isinstance(config.get(key),int) or config[key]<=0:raise ValueError('Freeze positive budget: '+key)
    if not config.get('calibration_record'):raise ValueError('Development calibration record required')
    calibration_path=prep/config['calibration_record'];calibration=read_json(calibration_path)
    if calibration.get('status')!='completed' or calibration.get('frozen_before_formal') is not True:raise ValueError('Complete development calibration before formal freeze')
    binding=read_json(prep/'binding.json')
    for name,key in [('dataset.json','dataset_sha256'),('tasks.json','tasks_sha256'),('references.automatic.private.json','references_sha256'),('runs.json','runs_sha256')]:
        if digest(read_json(prep/name))!=binding[key]:raise ValueError('Preparation changed: '+name)
    validate_source_binding(prep, binding, source_dir)
    code={p.name:hashlib_sha(p) for p in (ROOT/'curve_audit').glob('*.py')}
    body={'binding':binding,'config':config,'human_review':review,'calibration_sha256':hashlib_sha(calibration_path),'code':code}
    record={**body,'freeze_sha256':digest(body)}
    path=prep/'formal_freeze.json'
    with path.open('x') as f:json.dump(record,f,ensure_ascii=False,indent=2)
    return {'status':'frozen','sha256':record['freeze_sha256']}

def hashlib_sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def formal_run(preparation,output,*,source_dir=None):
    prep,out=Path(preparation),Path(output);frozen=read_json(prep/'formal_freeze.json')
    if digest({k:v for k,v in frozen.items() if k!='freeze_sha256'})!=frozen['freeze_sha256']:raise ValueError('Freeze changed')
    if not frozen['human_review']['pass']:raise ValueError('Human references not validated')
    review = frozen['human_review']
    if review.get('record_identity_scope') == 'user_attested_consolidated_human_review':
        if check_consolidated_review(prep, prep / review['review_record']) != review:
            raise ValueError('Consolidated review changed after freeze')
    provider=frozen['config'].get('provider','deepseek')
    if provider!='deepseek':raise ValueError('This study pins DeepSeek; unsupported provider')
    credential='DEEPSEEK_API_KEY'
    if not os.environ.get(credential):raise RuntimeError(credential+' missing; no inference attempted')
    for name,h in frozen['code'].items():
        if hashlib_sha(ROOT/'curve_audit'/name)!=h:raise ValueError('Code changed after freeze')
    for name,key in [('dataset.json','dataset_sha256'),('tasks.json','tasks_sha256'),('references.automatic.private.json','references_sha256'),('runs.json','runs_sha256')]:
        if digest(read_json(prep/name))!=frozen['binding'][key]:raise ValueError('Frozen input changed: '+name)
    if hashlib_sha(prep / frozen['config']['calibration_record']) != frozen['calibration_sha256']:
        raise ValueError('Development calibration changed after freeze')
    data, secondary = validate_source_binding(prep, frozen['binding'], source_dir)
    if out.exists():raise FileExistsError('Formal output exists; preserve it')
    out.mkdir(parents=True);config=frozen['config'];tasks=read_json(prep/'tasks.json');refs=read_json(prep/'references.automatic.private.json')
    runs=read_json(prep/'runs.json')
    adapter=DeepSeekAdapter(config['model'])
    limits=BudgetLimits(**{k:config[k] for k in ['input_tokens','output_tokens','total_tokens','model_calls']})
    from .current_verify import publish_verified, verify_v2
    models=[config['model']]+([config['comparison_model']] if config.get('comparison_model') else [])
    if len(set(models))!=len(models):raise ValueError('Comparison model must differ')
    scheduled=[{**r,'model':m,'run_id':m+'_'+r['run_id']} for m in models for r in runs]
    import random
    random.Random(frozen['binding']['seed']).shuffle(scheduled)
    write_json(out/'freeze.json',frozen);write_json(out/'schedule.json',scheduled);rows=[]
    for run in scheduled:
        adapter=DeepSeekAdapter(run['model'])
        task=next(t for t in tasks if t['task_id']==run['task_id'])
        row=agent_run(data,task,run['group'],adapter,limits,per_call_output=config['per_call_output']);row.update(run)
        row['score']=score_report(data,task,row['report'],refs)
        row['score']['human_validation']=frozen['human_review']['pass']
        row['V1']={c.get('claim_id'):__import__('curve_audit.current_study',fromlist=['replay_claim']).replay_claim(data,task,c) for c in row['report']['claims']}
        row['V2']=verify_v2(data,secondary,task,row['report'],
                           expected_primary_sha256=frozen['binding']['dataset_sha256'])
        row['published'], row['verification'], row['independent_arithmetic'] = publish_verified(
            data, task, row['report'], row['V2'])
        row['published_score']=score_report(data,task,row['published'],refs)
        write_json(out/(run['run_id']+'.json'),row);rows.append(row)
        print(json.dumps({'run_id':run['run_id'],'status':row['status']},ensure_ascii=False),flush=True)
    from .current_study import template_report
    for task in tasks:
        report,trace=template_report(data,task);write_json(out/(task['task_id']+'_T.json'),{'report':report,'trace':trace,'model_inference_performed':False})
    aggregates=[]
    for model in models:
        for group in ['G','E']:
            selected=[r for r in rows if r['model']==model and r['group']==group]
            counts={k:sum(r['score']['counts'][k] for r in selected) for k in selected[0]['score']['counts']}
            denominator=counts['answerable']
            aggregates.append({'model':model,'group':group,'scheduled_runs':len(selected),'counts':counts,'reproducible_correct_coverage':counts['reproducible_correct']/denominator if denominator else None,'correct_coverage':counts['correct']/denominator if denominator else None,'over_refusal_rate':counts['over_refusal']/denominator if denominator else None,'published_correct':sum(r['published_score']['counts']['correct'] for r in selected),'known_actual_tokens':sum(r['ledger']['actual_usage']['total_tokens'] for r in selected),'unknown_usage_calls':sum(r['ledger']['unknown_usage_calls'] for r in selected),'enforcement_violations':sum(r['ledger']['enforcement_violations'] for r in selected),'prose_review':'pending'})
    write_json(out/'summary.json',{'by_model_and_group':aggregates,'agent_runs':len(rows),'template_runs':6,'successful_submissions':sum(r['status']=='completed' for r in rows),'note':'Raw outputs and V1/V2; full human prose review remains required before final study conclusions'})
    return {'outputs':len(rows)+6,'directory':str(out)}

def calibrate(preparation,model,output):
    """Development-only tasks, no E-G comparison used to select a model."""
    from .current_study import shift,reference_candidates
    prep,out=Path(preparation),Path(output)
    if not os.environ.get('DEEPSEEK_API_KEY'):raise RuntimeError('DEEPSEEK_API_KEY missing; no inference attempted')
    if out.exists():raise FileExistsError('Calibration exists; preserve all versions')
    data=read_json(prep/'dataset.json');tasks=copy.deepcopy(build_tasks()[:3])
    for i,task in enumerate(tasks):
        task['task_id']='D'+str(i+1).zfill(2);start=task['window']['start']
        task['question']='开发校准：'+task['question']
        if i==0:
            for component in task['required_components']:component['request'].update(start=shift(start,4),end=shift(start,4))
        elif i==1:
            task['required_components'][0]['request'].update(start=shift(start,1),end=shift(start,20))
            task['required_components'][1]['request'].update(start=shift(start,1),end=shift(start,20))
        else:
            task['allowed_evidence']['excluded_targets']=[shift(start,6)]
            task['required_components'][0]['request'].update(start=shift(start,4),end=shift(start,4))
            task['required_components'][1]['request'].update(start=shift(start,4),end=shift(start,10))
    refs=reference_candidates(data,tasks);adapter=DeepSeekAdapter(model);out.mkdir(parents=True)
    counts=[]
    for task in tasks:
        correct_runs=0
        for rep in range(1,4):
            row=agent_run(data,task,'G',adapter,BudgetLimits(48000,12000,60000,10))
            row['score']=score_report(data,task,row['report'],refs)
            correct_runs+=row['score']['counts']['correct']==row['score']['counts']['answerable']
            write_json(out/(task['task_id']+'_'+str(rep)+'.json'),row)
        counts.append({'task_id':task['task_id'],'correct_runs':correct_runs,'runs':3})
    passed=all(c['correct_runs']>=2 for c in counts)
    result={'status':'completed' if passed else 'difficulty_calibration_failed','frozen_before_formal':not (prep/'formal_freeze.json').exists(),'model':model,'family_counts':counts,'scope':'development only; automatic candidates, not human labels; no E-G reward used for model selection'}
    write_json(out/'summary.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('calibrate');a.add_argument('--preparation',type=Path,required=True);a.add_argument('--model',choices=['deepseek-flash','deepseek-v4-pro'],default='deepseek-flash');a.add_argument('--output',type=Path,required=True)
    a=sub.add_parser('check-human');a.add_argument('--preparation',type=Path,required=True);a.add_argument('--reviewer-a',type=Path,required=True);a.add_argument('--reviewer-b',type=Path,required=True)
    a=sub.add_parser('freeze');a.add_argument('--preparation',type=Path,required=True);a.add_argument('--config',type=Path,required=True);a.add_argument('--reviewer-a',type=Path);a.add_argument('--reviewer-b',type=Path);a.add_argument('--consolidated-review',type=Path);a.add_argument('--source-dir',type=Path)
    a=sub.add_parser('run');a.add_argument('--preparation',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--source-dir',type=Path)
    args=p.parse_args(argv)
    try:
        if args.command=='calibrate':result=calibrate(args.preparation,args.model,args.output)
        elif args.command=='check-human':result=check_human(args.preparation,args.reviewer_a,args.reviewer_b)
        elif args.command=='freeze':result=freeze(args.preparation,args.config,args.reviewer_a,args.reviewer_b,consolidated_review=args.consolidated_review,source_dir=args.source_dir)
        else:result=formal_run(args.preparation,args.output,source_dir=args.source_dir)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result.get('pass',True) else 2
    except (OSError,ValueError,RuntimeError) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc),'model_inference_performed':False},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
