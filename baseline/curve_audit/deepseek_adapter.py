"""DeepSeek chat adapter with a client-side prepaid, conservative token bound.

The UTF-8-byte envelope is an explicit assumption to validate against actual
usage, NOT a server cumulative limit or a model-exact tokenizer guarantee.
"""
import json,os,urllib.request,urllib.error
from dataclasses import asdict
from .common import canonical
from .budget_guard import BudgetExceeded,TokenUsage

class PrepaidLedger:
    def __init__(self,group,limits):self.group=group;self.limits=limits;self.calls=[];self.blocked=False
    def check_capacity(self,input_bound,output_cap):
        if self.blocked:raise RuntimeError('Unknown usage or bound violation blocks further dispatch')
        totals=self.totals()
        for key,value,maximum in [('input_tokens',input_bound,self.limits.input_tokens),('output_tokens',output_cap,self.limits.output_tokens),('total_tokens',input_bound+output_cap,self.limits.total_tokens)]:
            if totals[key]+value>maximum:raise BudgetExceeded('Prepaid '+key+' limit')
        if len(self.calls)>=self.limits.model_calls:raise BudgetExceeded('Model-call limit')

    def reserve(self,input_bound,output_cap):
        self.check_capacity(input_bound,output_cap)
        item={'input_upper_bound':input_bound,'output_cap':output_cap,'state':'dispatched','usage':None};self.calls.append(item);return item
    def settle(self,item,raw_usage):
        try:
            cached = raw_usage.get('prompt_cache_hit_tokens')
            if cached is None:
                cached = (raw_usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)
            usage = TokenUsage(raw_usage['prompt_tokens'], raw_usage['completion_tokens'], cached)
        except (AttributeError, KeyError, TypeError, ValueError):
            item['state']='unknown_usage';self.blocked=True;raise RuntimeError('Missing/malformed usage; reservation retained') from None
        item['usage']=usage.to_dict()
        if usage.input_tokens>item['input_upper_bound'] or usage.output_tokens>item['output_cap']:
            item['state']='enforcement_violation';self.blocked=True;raise RuntimeError('Observed input bound or output cap violation')
        item['state']='settled'
    def totals(self):
        result={'input_tokens':0,'output_tokens':0,'total_tokens':0}
        for c in self.calls:
            usage=c['usage'] or {'input_tokens':c['input_upper_bound'],'output_tokens':c['output_cap']}
            for k in ['input_tokens','output_tokens']:result[k]+=usage[k]
        result['total_tokens']=result['input_tokens']+result['output_tokens'];return result
    def snapshot(self):
        known=[c['usage'] for c in self.calls if c['usage'] is not None]
        actual={k:sum(u[k] for u in known) for k in ['input_tokens','output_tokens']};actual['total_tokens']=sum(actual.values())
        return {'enforcement':'client_prepaid_upper_bound_assumption','input_bound':'UTF-8 bytes of serialized messages + 512 bytes per message + 1024 fixed overhead; requires empirical validation',
                'per_arm_limits':asdict(self.limits),'actual_usage':actual,'charged_or_reserved_usage':self.totals(),
                'dispatched_model_calls':len(self.calls),'complete_usage':all(c['state']=='settled' for c in self.calls),
                'unknown_usage_calls':sum(c['state']=='unknown_usage' for c in self.calls),'enforcement_violations':sum(c['state']=='enforcement_violation' for c in self.calls),
                'general_hard_bound_proven':False,'calls':self.calls}

class DeepSeekAdapter:
    def __init__(self,model='deepseek-flash',*,transport=None):
        if model not in ['deepseek-flash','deepseek-v4-pro']:raise ValueError('Pin actual DeepSeek model; aliases are not accepted')
        self.model=model;self.transport=transport or self._http
    def new_ledger(self,group,limits):return PrepaidLedger(group,limits)
    def _http(self,body):
        key=os.environ.get('DEEPSEEK_API_KEY')
        if not key:raise RuntimeError('DEEPSEEK_API_KEY missing; no model request sent')
        request=urllib.request.Request('https://api.deepseek.com/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':'curve-audit/4'})
        try:
            with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
        except urllib.error.HTTPError as exc:raise RuntimeError('DeepSeek HTTP '+str(exc.code)+'; no implicit retry') from None
        except (OSError,ValueError):raise RuntimeError('DeepSeek transport failure; usage unknown') from None
    @staticmethod
    def input_upper_bound(messages):
        return len(canonical(messages).encode()) + 512 * len(messages) + 1024

    def call(self,messages,ledger,call_id,arm,max_output=2048):
        if self.transport==self._http and not os.environ.get('DEEPSEEK_API_KEY'):raise RuntimeError('DEEPSEEK_API_KEY missing; no model request sent')
        bound=self.input_upper_bound(messages)
        item=ledger.reserve(bound,max_output)
        body={'model':self.model,'messages':messages,'max_tokens':max_output,'thinking':{'type':'disabled'},'stream':False,'response_format':{'type':'json_object'}}
        try:
            response = self.transport(body)
            if not isinstance(response, dict):
                raise RuntimeError('Malformed DeepSeek response; usage unknown')
        except Exception:
            item['state']='unknown_usage';ledger.blocked=True;raise
        ledger.settle(item,response.get('usage',{}))
        observed=response.get('model')
        if observed!=self.model:raise RuntimeError('Observed model differs from pinned model: '+str(observed))
        choices=response.get('choices',[])
        if not choices:raise RuntimeError('Missing choices')
        return choices[0]['message'].get('content') or '',{'model':observed,'status':choices[0].get('finish_reason'),'usage':response['usage'],'response_id':response.get('id'),'system_fingerprint':response.get('system_fingerprint'),'input_upper_bound':bound}
