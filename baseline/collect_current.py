"""Fetch both events through independent persistent public HTTPS connections."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
import http.client,json,time
import curve_audit.independent_source as src
out=Path('data/current_study');out.mkdir(parents=True,exist_ok=True)

class PersistentTransport:
 def __init__(self,url):
  u=urlsplit(url);self.host=u.hostname;self.path=u.path or '/';self.connection=None
 def __call__(self,payload):
  try:
   if self.connection is None:self.connection=http.client.HTTPSConnection(self.host,timeout=8)
   self.connection.request('POST',self.path,body=json.dumps(payload),headers={'Content-Type':'application/json','User-Agent':'curve-independent-source/1'})
   r=self.connection.getresponse();body=r.read(2000001)
   if r.status!=200:return {'http_status':r.status,'raw_body':body.decode(errors='replace')}
   return json.loads(body)
  except (OSError,ValueError,http.client.HTTPException):
   if self.connection:self.connection.close()
   self.connection=None
   raise src.SourceUnavailable('persistent_transport_failed') from None

original_locate=src.locate_block
original_acquire=src.acquire_snapshot

def fast_locate(rpc,target):
 if not hasattr(rpc,'last_selected'):result=original_locate(rpc,target)
 else:
  goal=int(src._utc(target).timestamp());lo=rpc.last_selected;hi=lo+600
  while src._quantity(rpc.header(hi)['timestamp'])<=goal:
   lo=hi;hi+=600
  while hi-lo>1:
   mid=(lo+hi)//2
   if src._quantity(rpc.header(mid)['timestamp'])<=goal:lo=mid
   else:hi=mid
  result=(lo,rpc.header(lo),rpc.header(lo+1))
 rpc.last_selected=result[0];return result

def logged_acquire(rpc,target,identifier,config):
 row=original_acquire(rpc,target,identifier,config)
 print(json.dumps({'target':target,'block':row['block_number']},ensure_ascii=False),flush=True)
 return row
src.locate_block=fast_locate;src.acquire_snapshot=logged_acquire

def collect(job):
 event,start,end,name,url=job;path=out/f'{event}_{name}.json'
 if path.exists():return {'existing':str(path)}
 t=time.monotonic();record=src.collect_independent({'id':event,'start':start,'end':end,'interval_minutes':60},src.DEFAULT_CONFIG,[{'name':name,'url':url}],policy={'attempts':1,'timeout_seconds':8,'max_requests_per_provider':2000},transports={name:PersistentTransport(url)})
 # This custom transport makes real HTTPS calls; preserve that fact and rehash.
 record['acquisition_mode']='public_https';record['dataset']['fixture_status']='independent_historical_rpc_record'
 record['transport_implementation']='PersistentTransport in collect_current.py; no injected source values'
 record.pop('content_sha256');record['content_sha256']=src._digest(record)
 src.save_record(path,record)
 return {'path':str(path),'status':record['status'],'snapshots':len(record['dataset']['snapshots']),'replay':src.replay_record(record)['pass'],'seconds':round(time.monotonic()-t,2)}
if __name__=='__main__':
 jobs=[(e,s,t,n,u) for e,s,t in [('usdc_2023','2023-03-10T00:00:00Z','2023-03-11T01:00:00Z'),('ftx_2022','2022-11-09T00:00:00Z','2022-11-10T00:00:00Z')] for n,u in [('drpc','https://eth.drpc.org/'),('publicnode','https://ethereum-rpc.publicnode.com')]]
 results=[]
 with ThreadPoolExecutor(max_workers=4) as pool:
  for result in pool.map(collect,jobs):print(json.dumps(result),flush=True);results.append(result)
 (out/'collection_summary.json').write_text(json.dumps(results,indent=2)+'\n')
