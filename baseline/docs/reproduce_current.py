"""One-command offline verification of the actual current delivery."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from curve_audit.common import read_json
from curve_audit.current_study import digest,engineering_demo,published_payload
from curve_audit.current_faults import run_faults
from curve_audit.current_verify import bound_recorded_sources,read_study_binding,verify_v2
from curve_audit.independent_source import replay_record
import tempfile

def main():
    folder=ROOT/'data/current_study';records=[]
    for p in sorted(folder.glob('*.json')):
        value=read_json(p)
        if isinstance(value,dict) and value.get('schema_version')=='independent-source-1':records.append({'file':p.name,**replay_record(value)})
    preparation=ROOT/'results/current_preparation';tasks=read_json(preparation/'tasks.json')
    binding=read_study_binding(preparation)
    primary,secondary=bound_recorded_sources(binding,folder)
    if digest(tasks)!=binding['tasks_sha256'] or read_json(preparation/'dataset.json')!=primary:
        raise ValueError('Prepared dataset or tasks do not match the frozen binding')
    with tempfile.TemporaryDirectory(prefix='5017-replay-') as d:
        demo=Path(d)/'demo';summary=engineering_demo(preparation,demo)
        generated=read_json(demo/'outputs.json')
        recorded=read_json(ROOT/'results/current_demo/outputs.json')
        for row in recorded:
            row['published'] = published_payload(row['published'])
        outputs_match=generated==recorded
        faults=run_faults(primary,tasks,Path(d)/'faults')
        v2=[]
        for row,task in zip(generated,tasks):v2.extend(verify_v2(primary,secondary,task,row['report']).values())
    result={'pass':all(r['pass'] for r in records) and summary['pass'] and outputs_match and faults['pass'] and sum(r['verdict']=='PASS' for r in v2)==16,
            'network_used':False,'model_calls':0,'source_record_replays':len(records),'source_points_per_successful_endpoint':len(primary['snapshots']),
            'demo_matches_saved':outputs_match,'reproducible_correct_components':summary['totals']['reproducible_correct'],'V2_pass_components':sum(r['verdict']=='PASS' for r in v2),'new_fault_scenarios':faults['scenarios'],
            'human_references_validated':False,'formal_model_outputs':0}
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['pass'] else 1
if __name__=='__main__':raise SystemExit(main())
