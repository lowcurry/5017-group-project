from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,os
from collect_current import PersistentTransport
from curve_audit.balance_sources import TOKENS,POOL,CALL
from curve_audit.current_study import digest
from curve_audit.independent_source import replay_record,_digest
out=Path('data/current_study')

def collect(job):
 event,provider=job;base_path=out/f'{event}_{provider}.json';path=out/f'{event}_{provider}_erc20.json'
 if path.exists():return {'existing':str(path)}
 deadline=time.monotonic()+600
 while not base_path.exists():
  if time.monotonic()>deadline:raise RuntimeError('Source acquisition did not finish')
  time.sleep(2)
 base=json.loads(base_path.read_text())
 if base['policy'].get('location_strategy')!='sequential_bracket_600_v1':
  base['acquisition_record_hash_before_strategy_annotation']=base.pop('content_sha256');base['policy']['location_strategy']='sequential_bracket_600_v1';base['content_sha256']=_digest(base);base_path.write_text(json.dumps(base,indent=2)+'\n')
 if base['status']!='available' or not replay_record(base)['pass']:raise RuntimeError('Base source is not complete/replayable')
 if provider=='drpc':url='https://eth.drpc.org/'
 else:url=os.environ.get('ETH_RPC_URL') or ('https://eth-mainnet.g.alchemy.com/v2/'+os.environ['ALCHEMY_API_KEY'])
 transport=PersistentTransport(url);observations=[]
 for row in base['dataset']['snapshots']:
  requests=[{'jsonrpc':'2.0','id':i+1,'method':'eth_call','params':[{'to':TOKENS[c],'data':CALL},hex(row['block_number'])]} for i,c in enumerate(['DAI','USDC','USDT'])]
  response=transport(requests)
  if not isinstance(response,list) or len(response)!=3 or any('result' not in r for r in response):raise RuntimeError('ERC20 source batch unavailable')
  observations.append({'target_utc':row['target_timestamp_utc'],'requests':requests,'responses':response})
  print(json.dumps({'provider':provider,'event':event,'target':row['target_timestamp_utc']}),flush=True)
 record={'schema_version':'erc20-supplement-1','status':'available','pool':POOL,'token_contracts':TOKENS,'base_source_file':base_path.name,'base_source_sha256':base['content_sha256'],'observations':observations,'scope':'Token balanceOf(pool), not pool balances(index); real HTTPS requests; source credential redacted'}
 record['content_sha256']=digest(record);path.write_text(json.dumps(record,indent=2)+'\n')
 return {'file':str(path),'points':len(observations)}
if __name__=='__main__':
 jobs=[(e,p) for e in ['usdc_2023','ftx_2022'] for p in ['drpc','alchemy']]
 with ThreadPoolExecutor(max_workers=4) as pool:
  results=list(pool.map(collect,jobs))
 (out/'erc20_collection_summary.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results))
