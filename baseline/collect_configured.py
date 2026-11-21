from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time,os
import curve_audit.independent_source as src
from collect_current import PersistentTransport,out

def collect(job):
 event,start,end=job;path=out/f'{event}_alchemy.json'
 if path.exists():return {'existing':str(path)}
 url=os.environ.get('ETH_RPC_URL') or ('https://eth-mainnet.g.alchemy.com/v2/'+os.environ['ALCHEMY_API_KEY'])
 provider={'name':'alchemy_configured','url':'https://configured-alchemy.invalid/credentials-redacted'}
 t=time.monotonic();record=src.collect_independent({'id':event,'start':start,'end':end,'interval_minutes':60},src.DEFAULT_CONFIG,[provider],policy={'attempts':1,'timeout_seconds':8,'max_requests_per_provider':2000},transports={'alchemy_configured':PersistentTransport(url)})
 record['acquisition_mode']='public_https';record['dataset']['fixture_status']='independent_historical_rpc_record'
 record['transport_implementation']='PersistentTransport in collect_current.py; actual configured HTTPS endpoint; URL redacted'
 record.pop('content_sha256');record['content_sha256']=src._digest(record);src.save_record(path,record)
 return {'path':str(path),'status':record['status'],'snapshots':len(record['dataset']['snapshots']),'replay':src.replay_record(record)['pass'],'seconds':round(time.monotonic()-t,2)}
if __name__=='__main__':
 jobs=[('usdc_2023','2023-03-10T00:00:00Z','2023-03-11T01:00:00Z'),('ftx_2022','2022-11-09T00:00:00Z','2022-11-10T00:00:00Z')]
 with ThreadPoolExecutor(max_workers=2) as pool:
  results=list(pool.map(collect,jobs))
 (out/'configured_collection_summary.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results),flush=True)
