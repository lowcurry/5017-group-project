"""Fresh-study preparation and verified source/review gates; no implicit inference."""
from __future__ import annotations
import argparse, copy, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import ROOT
from .confirmation import (build_draft_tasks, public_protocol, public_tasks, planned_runs,
    content_hash, write_reference_review_package, validate_reference_review,
    require_formal_ready, FormalStudyBlocked)
from .budget_guard import codex_cli_capability, require_hard_token_budget, BudgetCapabilityError
from .independent_source import (DEFAULT_CONFIG, collect_independent, as_recompute_dataset,
    replay_record, save_record, SourceError)
from .verification import recompute

PROVIDERS = {
    'drpc': {'name':'drpc','url':'https://eth.drpc.org/','documentation':'https://drpc.org/docs/ethereum-api'},
    'publicnode': {'name':'publicnode','url':'https://ethereum-rpc.publicnode.com','documentation':'https://ethereum.publicnode.com/'},
    '1rpc': {'name':'1rpc','url':'https://public.1rpc.io/eth','documentation':'https://docs.1rpc.io/overview/supported-networks'},
}

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write_new(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def shifted(point,hours):
    return (datetime.fromisoformat(point.replace('Z','+00:00'))+timedelta(hours=hours)).isoformat().replace('+00:00','Z')

def prepare(destination):
    destination=Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError('Preparation exists; imported human records will not be overwritten')
    tasks=build_draft_tasks()
    destination.mkdir(parents=True,exist_ok=True)
    write_new(destination/'tasks.draft.json',tasks)
    write_new(destination/'protocol.json',public_protocol())
    write_new(destination/'planned_runs.json',planned_runs(tasks))
    write_new(destination/'source_config.json',DEFAULT_CONFIG)
    write_new(destination/'task_manifest.json',{'status':'draft','tasks_sha256':content_hash(public_tasks(tasks)),
        'event_scope':'single_event_draft_second_event_not_selected','reference_status':'pending_external_records'})
    # Collect full grids plus one-hour margins for result-dependent followups.
    windows=sorted({(t['window']['start'],t['window']['end']) for t in tasks})
    acquisition=[{'id':f'window_{i+1}','start':shifted(start,-1),'end':shifted(end,1),
                  'interval_minutes':60} for i,(start,end) in enumerate(windows)]
    write_new(destination/'acquisition_tasks.json',acquisition)
    write_reference_review_package(destination/'human_reference',tasks)
    return {'status':'prepared_not_formal','tasks':len(tasks),
        'slots':sum(len(t['required_components']) for t in tasks), 'formal_outputs':0,
        'human_review':'pending_external_records','budget':'pending_hard_cap_adapter',
        'preparation_directory':str(destination)}

def collect_sources(preparation,destination,providers,attempts=1):
    preparation,destination=Path(preparation),Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError('Source collection exists; choose a new run directory')
    tasks,config=read(preparation/'acquisition_tasks.json'),read(preparation/'source_config.json')
    chosen=[PROVIDERS[name] for name in providers]
    policy={'attempts':attempts,'timeout_seconds':12,'retry_delay_seconds':1,'max_requests_per_provider':2000}
    # Frozen before any request. No agent archive, block hint or saved calldata.
    write_new(destination/'collection_protocol.json',{'tasks':tasks,'config':config,'providers':chosen,
        'policy':policy,
        'status':'development_collection_not_formal_admission'})
    rows=[]
    for task in tasks:
        record=collect_independent(task,config,chosen,policy=policy)
        save_record(destination/(task['id']+'.json'),record)
        checked=replay_record(record)
        rows.append({'id':task['id'],'status':record['status'],'snapshots':len(record['dataset']['snapshots']),
                     'offline_replay_pass':checked['pass'],'source_record_sha256':record['content_sha256']})
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    result={'status':'collected_not_formally_admitted','records':rows,'formal_outputs':0,
        'human_config_review':config.get('manual_review',{}).get('status'),
        'distinct_public_provider_count':len(chosen),'backend_independence_proven':False}
    write_new(destination/'summary.json',result)
    return result

def _combined_records(paths):
    records=[read(path) for path in paths]
    if not records:raise ValueError('At least one source record is required')
    datasets=[as_recompute_dataset(record) for record in records]
    dataset=copy.deepcopy(datasets[0]);rows={}
    for record,data in zip(records,datasets):
        if record.get('acquisition_mode')!='public_https':
            raise ValueError('Synthetic/injected source cannot supply historical reference candidates')
        if record.get('status')!='available' or record.get('complete_allowed_grid') is not True:
            raise ValueError('Incomplete source collection cannot define human reference candidates')
        for field in ('chain_id','pool','coin_order','decimals','quote_sizes'):
            if data[field]!=dataset[field]:raise ValueError('Source public configuration mismatch')
        for row in data['snapshots']:
            target=row['target_timestamp_utc']
            if target in rows and rows[target]!=row:raise ValueError('Conflicting source rows')
            rows[target]=copy.deepcopy(row)
    dataset['snapshots']=sorted(rows.values(),key=lambda r:r['target_timestamp_utc'])
    # IDs are local evidence labels; ensure they do not collide across windows.
    for i,row in enumerate(dataset['snapshots']):row['id']=f'FRESH_{i:03d}'
    dataset['sampling']['start_utc']=dataset['snapshots'][0]['target_timestamp_utc'] if rows else None
    dataset['sampling']['end_utc']=dataset['snapshots'][-1]['target_timestamp_utc'] if rows else None
    return dataset,records

def reference_candidates(preparation,source_paths,destination):
    tasks=read(Path(preparation)/'tasks.draft.json')
    dataset,records=_combined_records(source_paths)
    candidates=[]
    for task in tasks:
        variant=copy.deepcopy(dataset)
        excluded=task['allowed_evidence'].get('excluded_target_timestamp_utc')
        if excluded:variant['snapshots']=[r for r in variant['snapshots'] if r['target_timestamp_utc']!=excluded]
        resolved={}
        for component in task['required_components']:
            original=component['request'];query=copy.deepcopy(original)
            reference={'task_id':task['task_id'],'slot_id':component['slot_id'],'query':original,
                       'label_source':'automatic_candidate_not_human_checked',
                       'raw_record_refs':[r['content_sha256'] for r in records]}
            try:
                if 'time_from_slot' in query:
                    peak=resolved[query.pop('time_from_slot')]['target_timestamp_utc']
                    point=shifted(peak,query.pop('offset_hours'))
                    query.update(start=point,end=point)
                result=recompute(variant,query)
                reference.update(answerable=True,result=result,resolved_query=query)
                resolved[component['slot_id']]=result
            except (ValueError,KeyError,TypeError) as exc:
                reference.update(answerable=False,value=None,unit=None,resolved_query=query,
                    refusal_basis=str(exc),requires_human_review=True)
            candidates.append(reference)
    package=write_reference_review_package(destination,tasks,candidates,
        dataset_fingerprint=content_hash(dataset))
    write_new(Path(destination)/'candidate_source_dataset.private.json',dataset)
    return {'status':'candidate_values_not_human_labels','slots':len(candidates),
            'review_package_sha256':package['review_package_sha256'],'human_review':'pending_external_records'}

def check_reviews(package_path,a=None,b=None,arbitration=None,output=None):
    result=validate_reference_review(read(package_path),read(a) if a else None,
        read(b) if b else None,read(arbitration) if arbitration else None)
    if output:write_new(output,result)
    return result

def readiness(preparation,review=None,source=None):
    capability=codex_cli_capability()
    budget={'pass':False,'enforcement':'unavailable','equal_condition_caps':False}
    try:
        require_hard_token_budget(capability)
        budget={'pass':True,'enforcement':'hard','equal_condition_caps':True}
    except BudgetCapabilityError:pass
    human=read(review) if review else {'pass':False}
    source_status=read(source) if source else {'pass':False}
    manifest=read(Path(preparation)/'task_manifest.json')
    try:
        result=require_formal_ready(human,task_manifest=manifest,source_status=source_status,budget_status=budget)
    except FormalStudyBlocked as exc:
        result={'pass':False,'status':'formal_not_started','blockers':str(exc).split('; ')}
    result.update(model_inference_performed=False,formal_outputs=0,budget_capability=capability.to_dict())
    return result

def main(argv=None):
    parser=argparse.ArgumentParser(description='Fresh MSBD5017 confirmation preparation and verification')
    sub=parser.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--output',type=Path,default=ROOT/'results/confirmation_preparation')
    collect=sub.add_parser('source-collect');collect.add_argument('--preparation',type=Path,default=ROOT/'results/confirmation_preparation')
    collect.add_argument('--output',type=Path,default=ROOT/'data/fresh_sources');collect.add_argument('--providers',nargs='+',choices=tuple(PROVIDERS),required=True)
    collect.add_argument('--attempts',type=int,choices=(1,2,3),default=1)
    replay=sub.add_parser('source-replay');replay.add_argument('record',type=Path)
    candidates=sub.add_parser('reference-candidates');candidates.add_argument('--preparation',type=Path,default=ROOT/'results/confirmation_preparation')
    candidates.add_argument('--sources',type=Path,nargs='+',required=True);candidates.add_argument('--output',type=Path,required=True)
    human=sub.add_parser('check-human-references');human.add_argument('package',type=Path)
    for name in ('reviewer-a','reviewer-b','arbitration','output'):human.add_argument('--'+name,type=Path)
    gate=sub.add_parser('formal-readiness');gate.add_argument('--preparation',type=Path,default=ROOT/'results/confirmation_preparation')
    gate.add_argument('--review',type=Path);gate.add_argument('--source',type=Path)
    faults=sub.add_parser('faults');faults.add_argument('--output',type=Path,default=ROOT/'results/confirmation_faults')
    args=parser.parse_args(argv)
    try:
        if args.command=='prepare':result=prepare(args.output)
        elif args.command=='source-collect':result=collect_sources(args.preparation,args.output,args.providers,args.attempts)
        elif args.command=='source-replay':result=replay_record(read(args.record))
        elif args.command=='reference-candidates':result=reference_candidates(args.preparation,args.sources,args.output)
        elif args.command=='check-human-references':result=check_reviews(args.package,args.reviewer_a,args.reviewer_b,args.arbitration,args.output)
        elif args.command=='formal-readiness':result=readiness(args.preparation,args.review,args.source)
        else:
            from .confirmation_faults import run_fault_matrix
            result=run_fault_matrix(args.output)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result.get('pass',True) else 2
    except (ValueError,OSError,SourceError) as exc:
        print(json.dumps({'pass':False,'error':str(exc),'model_inference_performed':False},ensure_ascii=False))
        return 2

if __name__=='__main__':raise SystemExit(main())
