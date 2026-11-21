"""ERC20 balanceOf(pool) supplement bound to independently located RPC blocks."""
import copy,json
from pathlib import Path
from .common import read_json
from .independent_source import as_recompute_dataset,decode_uint256,_decimal_text
from .current_study import digest
TOKENS={'DAI':'0x6b175474e89094c44da98b954eedeac495271d0f','USDC':'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48','USDT':'0xdac17f958d2ee523a2206206994597c13d831ec7'}
POOL='0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7'
CALL='0x70a08231'+POOL[2:].rjust(64,'0')

def load_supplement(path):
    path=Path(path);record=read_json(path)
    if digest({k:v for k,v in record.items() if k!='content_sha256'})!=record.get('content_sha256'):raise ValueError('ERC20 supplement hash mismatch')
    base_name = record['base_source_file']
    if not isinstance(base_name, str) or Path(base_name).name != base_name or '\\' in base_name:
        raise ValueError('ERC20 base source must be a sibling recording')
    base=read_json(path.with_name(base_name))
    if base['content_sha256']!=record['base_source_sha256']:raise ValueError('ERC20 supplement/base binding mismatch')
    if base.get('acquisition_mode') != 'public_https':raise ValueError('ERC20 base must be a public RPC recording')
    data=as_recompute_dataset(base)
    if base['status']!='available':raise ValueError('Incomplete independent UTC acquisition')
    if record.get('token_contracts')!=TOKENS or record.get('pool')!=POOL:raise ValueError('Wrong ERC20 public configuration')
    by_target={r['target_utc']:r for r in record['observations']}
    if len(by_target)!=len(data['snapshots']) or len(by_target)!=len(record['observations']):raise ValueError('Missing/duplicate ERC20 observations')
    for row in data['snapshots']:
        observation=by_target[row['target_timestamp_utc']]
        responses={r['id']:r for r in observation['responses']}
        if len(responses)!=3 or len(observation['requests'])!=3:raise ValueError('Incomplete balance response batch')
        for request,coin in zip(observation['requests'],data['coin_order']):
            expected={'jsonrpc':'2.0','id':data['coin_order'].index(coin)+1,'method':'eth_call','params':[{'to':TOKENS[coin],'data':CALL},hex(row['block_number'])]}
            if request!=expected:raise ValueError('ERC20 calldata, target, token, or pinned block mismatch')
            response=responses[request['id']]
            if response.get('jsonrpc')!='2.0' or 'error' in response:raise ValueError('ERC20 source error')
            raw=decode_uint256(response['result'])
            row['raw_balances'][coin]=raw;row['balances'][coin]=_decimal_text(raw,data['decimals'][coin]);row['calldata']['balances'][coin]=CALL
    data['balance_source']='erc20_balance_of_pool';data['balance_contracts']=TOKENS
    return data,record
