"""New semantic faults for the current replay path; generated before evaluation."""
import copy
from .current_study import template_report,replay_claim,publish
from .common import write_json

def run_faults(dataset,tasks,out):
    scenarios=[]
    # Twelve new task-specific mutations, each with a matched clean report.
    task=tasks[0];base,_=template_report(dataset,task)
    mutations=[('unit',lambda c:c.update(unit='USD')),
      ('decimal_scale',lambda c:c.update(value=str(float(c['value'])/1000000))),
      ('wrong_value',lambda c:c.update(value='999999')),
      ('window',lambda c:c['query'].update(start=task['window']['start'])),
      ('coin',lambda c:c['query'].update(coin='USDT')),
      ('block_time',lambda c:c.update(target_timestamp_utc=task['window']['start'])),
      ('direction',lambda c:c['query'].update(direction='USDT_USDC')),
      ('size',lambda c:c['query'].update(size=10000)),
      ('incomplete_evidence',lambda c:c.update(evidence_ids=[])),
      ('dataset_binding',lambda c:c.update(dataset_sha256='0'*64)),
      ('formula_version',lambda c:c.update(formula_version='unregistered')),
      ('claim_slot',lambda c:c.update(slot_id='S99'))]
    for i,(name,mutate) in enumerate(mutations):
        index=1 if name in ['direction','size'] else 0
        clean=copy.deepcopy(base['claims'][index]);bad=copy.deepcopy(clean);mutate(bad)
        expected='UNKNOWN' if name in ['incomplete_evidence','dataset_binding','formula_version','claim_slot'] else 'FAIL'
        for suffix,claim,verdict in [('bad',bad,expected),('clean',clean,'PASS')]:
            actual=replay_claim(dataset,task,claim)
            scenarios.append({'id':f'N{i+1:02d}_{suffix}','type':name,'expected':verdict,'actual':actual['verdict'],'reason':actual['reason'],'claim':claim})
    for i,task in enumerate([tasks[2],tasks[5],tasks[2],tasks[5]]):
        report,_=template_report(dataset,task);claim=report['claims'][2]
        verdict=replay_claim(dataset,task,claim)
        scenarios.append({'id':f'R{i+1}','type':'valid_refusal','expected':'UNKNOWN','actual':verdict['verdict'],'reason':verdict['reason'],'scope':'refusal is not a numerical PASS'})
    # Missing/corrupted source must not be called a model factual error.
    for i in range(4):
        broken=copy.deepcopy(dataset)
        if i==0:broken['snapshots']=[]
        elif i==1:broken['snapshots'][0]['raw_balances']['USDC']='not-an-integer'
        elif i==2:broken['chain_id']=2
        else:broken['snapshots'][0]['next_block']['parent_hash']='0x'+'0'*64
        actual=replay_claim(broken,tasks[0],base['claims'][0])
        scenarios.append({'id':f'U{i+1}','type':'invalid_source','expected':'UNKNOWN','actual':actual['verdict'],'reason':actual['reason']})
    summary={'pass':all(s['expected']==s['actual'] for s in scenarios),'scenarios':len(scenarios),'actual_faults':12,'clean_pairs':12,'refusals':4,'source_unknowns':4,
             'scope':'32 new current-path regression scenarios, separate from existing 32 engineering scenarios; not a formal held-out benchmark'}
    write_json(out/'scenarios.json',scenarios);write_json(out/'summary.json',summary)
    return summary
