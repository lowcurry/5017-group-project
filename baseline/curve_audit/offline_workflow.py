"""Deterministic integration demo, explicitly outside formal model evaluation."""
from __future__ import annotations
import copy, json
from pathlib import Path

from .confirmation import public_task, content_hash, score_report
from .confirmation_cli import read, write_new, shifted, _combined_records
from .metrics import calculate
from .verification import verify_claims

def run_task(dataset,task):
    data=copy.deepcopy(dataset)
    excluded=task['allowed_evidence'].get('excluded_target_timestamp_utc')
    if excluded:data['snapshots']=[r for r in data['snapshots'] if r['target_timestamp_utc']!=excluded]
    claims,steps,resolved=[],[],{}
    for component in task['required_components']:
        slot=component['slot_id'];query=copy.deepcopy(component['request']);dependencies=[]
        try:
            if 'time_from_slot' in query:
                dependency=query.pop('time_from_slot');dependencies=[dependency]
                peak=resolved[dependency]['target_timestamp_utc']
                point=shifted(peak,query.pop('offset_hours'));query.update(start=point,end=point)
            result=calculate(data,query);resolved[slot]=result
            claim={**copy.deepcopy(result),'slot_id':slot,'claim_id':task['task_id']+'_'+slot,
                   'status':'supported','explanation':'Deterministic production calculator; no model inference.'}
            observation={'ok':True,'result':copy.deepcopy(result)}
        except (ValueError,KeyError,TypeError) as exc:
            claim={'slot_id':slot,'claim_id':task['task_id']+'_'+slot,'query':query,
                'value':None,'unit':None,'status':'unsupported','evidence_ids':[],
                'calculation_id':None,'explanation':'Cannot compute within the allowed evidence: '+str(exc)}
            observation={'ok':False,'error':str(exc)}
        claims.append(claim)
        steps.append({'step_id':slot,'depends_on':dependencies,'arguments':query,'observation':observation})
    report={'task_id':task['task_id'],'claims':claims,'answer':'Deterministic engineering demonstration; not a model answer.',
            'refusal_reason':None,'faults':[]}
    # The verifier's raw arithmetic is independent of the production calculator.
    checked=verify_claims(data,report)
    return {'task_id':task['task_id'],'report':report,'steps':steps,'independent_arithmetic':checked,
            'inference_performed':False,'generation':'deterministic_public_components_not_private_answer_values'}

def _execute(tasks,dataset,references):
    rows=[]
    for task in tasks:
        row=run_task(dataset,public_task(task))
        row['automatic_candidate_agreement']=score_report(row['report'],[r for r in references if r['task_id']==task['task_id']])
        rows.append(row)
    totals={key:sum(r['automatic_candidate_agreement']['counts'][key] for r in rows)
        for key in ('answerable_slots','correct_answerable_slots','unanswerable_slots','justified_refusal_slots')}
    return rows,totals

def run_offline_demo(preparation,source_paths,reference_package,output_dir):
    out=Path(output_dir)
    if out.exists():raise FileExistsError('Existing offline demonstration will not be overwritten')
    tasks=read(Path(preparation)/'tasks.draft.json');package=read(reference_package)
    body={k:v for k,v in package.items() if k!='review_package_sha256'}
    if package.get('review_package_sha256')!=content_hash(body):raise ValueError('Reference package hash mismatch')
    dataset,records=_combined_records(source_paths)
    if package.get('dataset_fingerprint')!=content_hash(dataset):raise ValueError('Candidate/source dataset binding mismatch')
    if package.get('tasks_sha256')!=content_hash([public_task(t) for t in tasks]):raise ValueError('Candidate/task binding mismatch')
    rows,totals=_execute(tasks,dataset,package['candidate_references'])
    summary={'pass':all(r['independent_arithmetic']['pass'] for r in rows)
        and totals['correct_answerable_slots']==totals['answerable_slots']
        and totals['justified_refusal_slots']==totals['unanswerable_slots'],
        'tasks':len(rows),'totals':totals,'model_calls':0,'human_reference_validated':False,
        'formal_model_outputs':0,'scope':'deterministic integration and independent arithmetic agreement with automatic candidates only; not model performance or human factual validation',
        'distinct_public_providers':sorted({p['name'] for r in records for p in r['providers']}),
        'source_independence_claim':'single available public interface; independent backend comparison not completed'}
    write_new(out/'input_binding.json',{'tasks':tasks,'dataset':dataset,'candidate_references':package['candidate_references'],
        'source_record_hashes':[r['content_sha256'] for r in records],'review_package_hash':package['review_package_sha256'],
        'fixture_status':'fresh_rpc_input_deterministic_engineering_demo_not_formal_model_evaluation'})
    write_new(out/'outputs.json',rows);write_new(out/'summary.json',summary)
    return summary

def replay_offline_demo(output_dir):
    out=Path(output_dir);bound=read(out/'input_binding.json')
    rows,totals=_execute(bound['tasks'],bound['dataset'],bound['candidate_references'])
    expected=read(out/'outputs.json');summary=read(out/'summary.json')
    return {'pass':rows==expected and totals==summary['totals'],'network_used':False,'model_calls':0,
        'tasks':len(rows),'scope':'reexecution of deterministic saved engineering inputs, not human/reference authenticity or fresh chain verification'}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--preparation',type=Path,required=True)
    parser.add_argument('--sources',type=Path,nargs='+',required=True)
    parser.add_argument('--reference-package',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run_offline_demo(args.preparation,args.sources,args.reference_package,args.output),ensure_ascii=False,indent=2))
