import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from curve_audit.current_study import *
from curve_audit.budget_guard import BudgetLimits,BudgetExceeded


def fixture():
    base=read_json(ROOT/'data/raw/pilot_archive.json')
    original=base['snapshots'][0];rows=[]
    for event,start,end in WINDOWS:
        for i in range(26):
            row=copy.deepcopy(original);n=len(rows)+100
            row.update(id='FIX_'+str(n),target_timestamp_utc=shift(start,i),timestamp_utc=shift(start,i),block_number=n,
                       block_hash='0x'+f'{n:064x}',parent_hash='0x'+f'{n-1:064x}',series='hourly')
            row['next_block']={'block_number':n+1,'block_hash':'0x'+f'{n+1:064x}','parent_hash':row['block_hash'],
                               'timestamp_utc':shift(start,i).replace(':00Z',':01Z')}
            for c,value in [('DAI',100),('USDC',100+i*i),('USDT',100)]:
                row['raw_balances'][c]=str(value*10**base['decimals'][c]);row['balances'][c]=str(value)
            rows.append(row)
    base['snapshots']=rows
    return base

class StudyTests(unittest.TestCase):
    def setUp(self):self.data=fixture();self.tasks=build_tasks();self.refs=reference_candidates(self.data,self.tasks)
    def test_scope_and_dual_implementation(self):
        self.assertEqual(validate_dataset(self.data),[])
        self.assertEqual(len(self.refs),18);self.assertEqual(sum(r['answerable'] for r in self.refs),16)
        for task in self.tasks:
            report,_=template_report(self.data,task)
            score=score_report(self.data,task,report,self.refs)
            self.assertEqual(score['counts']['correct'],score['counts']['answerable'])
            self.assertEqual(score['counts']['reproducible_correct'],score['counts']['answerable'])
    def test_window_read_preserves_raw_values_and_does_not_expose_excluded_points(self):
        from curve_audit.current_runner import read_window
        task = self.tasks[2]
        permitted = allowed_data(self.data, task)
        rows = read_window(permitted, task['window']['start'], task['window']['end'])['snapshots']
        excluded = set(task['allowed_evidence']['excluded_targets'])
        expected = [row for row in permitted['snapshots']
                    if task['window']['start'] <= row['target_timestamp_utc'] <= task['window']['end']]
        self.assertEqual(len(rows), len(expected))
        for row, source in zip(rows, expected):
            self.assertNotIn(row['target_timestamp_utc'], excluded)
            for field in ('id','block_number','block_hash','raw_balances','raw_quotes'):
                self.assertEqual(row[field], source[field])
            self.assertNotIn('calldata', row)
            self.assertNotIn('balances', row)

    def test_oversized_window_leaves_budget_for_a_submission(self):
        from curve_audit.current_runner import agent_run
        from curve_audit.deepseek_adapter import DeepSeekAdapter

        task = self.tasks[0]
        report, _ = template_report(self.data, task)
        messages = []
        def transport(request):
            messages.append(copy.deepcopy(request['messages']))
            if len(messages) == 1:
                action = {'action':'read_window', **task['window']}
            else:
                response = json.loads(request['messages'][-2]['content'])
                self.assertIn('tool_error', response)
                self.assertNotIn('tool_result', response)
                action = {'action':'submit','report_json':json.dumps(report)}
            return {'model':'deepseek-flash','usage':{'prompt_tokens':500,'completion_tokens':100},
                    'choices':[{'message':{'content':json.dumps(action)},'finish_reason':'stop'}]}

        row = agent_run(self.data, task, 'G', DeepSeekAdapter(transport=transport),
                        BudgetLimits(11000, 1000, 12000, 2), per_call_output=500)
        self.assertEqual(row['status'], 'completed', row['error'])
        self.assertEqual(row['tool_budget_rejections'], 1)
        self.assertEqual(row['ledger']['dispatched_model_calls'], 2)

    def test_endpoint_change_does_not_require_missing_interior(self):
        task=self.tasks[2];data=allowed_data(self.data,task)
        q=dict(metric='share_pct',operation='change',start=task['window']['start'],end=task['window']['end'],coin='USDC')
        a,b=compute(data,q),compute(data,q,independent=True)
        self.assertTrue(equal(a['value'],b['value'],'share_pct'))
    def test_no_cross_mask_hourly_interval(self):
        task=self.tasks[2];report,_=template_report(self.data,task);claim=report['claims'][1]
        self.assertNotIn(task['allowed_evidence']['excluded_targets'][0],[claim['interval_start'],claim['interval_end']])
        self.assertEqual(shift(claim['interval_start'],1),claim['interval_end'])
    def test_missing_citation_is_not_numerical_error(self):
        task=self.tasks[0];report,_=template_report(self.data,task);report['claims'][0]['evidence_ids']=[]
        score=score_report(self.data,task,report,self.refs)
        self.assertEqual(score['counts']['correct'],3);self.assertEqual(score['counts']['reproducible_correct'],2)
        self.assertEqual(replay_claim(self.data,task,report['claims'][0])['verdict'],'UNKNOWN')
    def test_wrong_unit_wrong_window_and_duplicate_cannot_pass(self):
        task=self.tasks[0];report,_=template_report(self.data,task)
        for field,value in [('unit','USD'),('value','NaN')]:
            wrong=copy.deepcopy(report);wrong['claims'][0][field]=value
            self.assertEqual(score_report(self.data,task,wrong,self.refs)['counts']['correct'],2)
        bad=copy.deepcopy(report['claims'][0]);bad['query']['start']=task['window']['start']
        self.assertEqual(replay_claim(self.data,task,bad)['reason'],'task_query_mismatch')
        report['claims'].append(copy.deepcopy(report['claims'][0]))
        self.assertEqual(score_report(self.data,task,report,self.refs)['counts']['correct'],2)
    def test_deleting_answers_cannot_raise_primary(self):
        task=self.tasks[0];report,_=template_report(self.data,task);report['claims']=[]
        result=score_report(self.data,task,report,self.refs)
        self.assertEqual(result['primary_rate'],0);self.assertEqual(result['counts']['omission'],3)
    def test_publisher_resolves_dependencies_using_slot_ids(self):
        task = self.tasks[1]
        report, _ = template_report(self.data, task)
        for index, claim in enumerate(report['claims'], start=1):
            claim['claim_id'] = f'C{index}'
        published, checks = publish(self.data, task, report)
        self.assertEqual(checks['C3']['verdict'], 'PASS')
        self.assertIn('C3', [claim['claim_id'] for claim in published['claims']])

    def test_wrong_dependency_is_withdrawn_and_prose_rerendered(self):
        task=self.tasks[1];report,_=template_report(self.data,task)
        report['claims'][1]['value']='999';report['answer']='unverified prose'
        published,checks=publish(self.data,task,report)
        self.assertEqual(len(published['claims']),1);self.assertNotIn('unverified prose',published['answer'])
        self.assertEqual(checks['R02_S3']['reason'],'withdrawn_dependency')

    def test_publisher_removes_unverified_metadata_and_withdraws_cycles(self):
        task = self.tasks[1]
        report, _ = template_report(self.data, task)
        report['appendix'] = 'unverified appendix'
        first, second, third = report['claims']
        first.update(explanation='unverified explanation', target_timestamp_utc='unverified timestamp',
                     interpretation='unverified interpretation')
        second['depends_on'] = ['S3']
        third['depends_on'] = ['S2']
        published, checks = publish(self.data, task, report)
        self.assertEqual([claim['slot_id'] for claim in published['claims']], ['S1'])
        self.assertNotIn('unverified', json.dumps(published))
        self.assertEqual(checks[second['claim_id']]['reason'], 'withdrawn_dependency')
        self.assertEqual(checks[third['claim_id']]['reason'], 'withdrawn_dependency')

    def test_only_a_follow_up_task_can_access_the_extra_hour(self):
        end = self.tasks[0]['window']['end']
        self.assertEqual(allowed_data(self.data, self.tasks[0])['snapshots'][-1]['target_timestamp_utc'], end)
        self.assertEqual(allowed_data(self.data, self.tasks[1])['snapshots'][-1]['target_timestamp_utc'], shift(end, 1))
        with self.assertRaisesRegex(ValueError, 'requires a share_pct'):
            compute(allowed_data(self.data, self.tasks[1]), {'metric': 'quote_rate', 'operation': 'max_hourly_change'})

    def test_refusal_reason_cannot_credit_an_unrelated_query(self):
        task = self.tasks[2]
        report, _ = template_report(self.data, task)
        self.assertEqual(score_report(self.data, task, report, self.refs)['counts']['justified_refusal'], 1)
        report['claims'][2]['query'] = {}
        self.assertEqual(score_report(self.data, task, report, self.refs)['counts']['justified_refusal'], 0)

    def test_publisher_enforces_frozen_dependency_when_report_omits_it(self):
        from curve_audit.current_verify import verify_v2

        task = self.tasks[1]
        report, _ = template_report(self.data, task)
        report['claims'][1]['value'] = '999'
        report['claims'][2].pop('depends_on')
        verifier = verify_v2(self.data, self.data, task, report)
        self.assertEqual(verifier['R02_S3']['verdict'], 'PASS')
        published, checks = publish(self.data, task, report, checks_override=verifier)
        self.assertEqual([claim['slot_id'] for claim in published['claims']], ['S1'])
        self.assertEqual(checks['R02_S3']['reason'], 'withdrawn_dependency')

    def test_publication_and_v2_reject_ambiguous_reports(self):
        from curve_audit.current_verify import verify_v2

        task = self.tasks[0]
        report, _ = template_report(self.data, task)
        bad = {**report['claims'][0], 'value': '-9999'}
        variants = [
            {'claims': [bad, *report['claims']]},
            {'claims': [bad, *report['claims'][1:2]]},
            {'claims': [{**bad, 'claim_id': ''}, *report['claims'][1:]]},
            {'claims': [{**report['claims'][0], 'claim_id': 'R01_S1\nUnverified assertion'}, *report['claims'][1:]]},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                invalid = {**report, **variant}
                with self.assertRaises(ValueError):
                    verify_v2(self.data, self.data, task, invalid)
                with self.assertRaises(ValueError):
                    publish(self.data, task, invalid)
        stopped = {**report, 'answer': '', 'claims': []}
        self.assertEqual(verify_v2(self.data, self.data, task, stopped), {})
        self.assertEqual(publish(self.data, task, stopped)[0]['claims'], [])

    def test_verifier_does_not_call_production_calculator(self):
        task=self.tasks[0];report,_=template_report(self.data,task)
        with patch('curve_audit.current_study.calculate',side_effect=AssertionError('production reused')):
            self.assertEqual(replay_claim(self.data,task,report['claims'][0])['verdict'],'PASS')

if __name__=='__main__':unittest.main()

class RunnerTests(unittest.TestCase):
    def test_usage_accounting_handles_null_response_and_nullable_cache_details(self):
        from curve_audit.deepseek_adapter import DeepSeekAdapter

        limits = BudgetLimits(10000, 100, 10100, 2)
        adapter = DeepSeekAdapter(transport=lambda body: None)
        ledger = adapter.new_ledger('G', limits)
        with self.assertRaisesRegex(RuntimeError, 'Malformed DeepSeek response'):
            adapter.call([], ledger, 'invalid', 'G', max_output=20)
        self.assertEqual(ledger.snapshot()['unknown_usage_calls'], 1)
        self.assertTrue(ledger.blocked)
        response = {'model': 'deepseek-flash', 'usage': {
            'prompt_tokens': 2, 'completion_tokens': 1, 'prompt_cache_hit_tokens': 1,
            'prompt_tokens_details': None}, 'choices': [{'message': {'content': '{}'}}]}
        adapter = DeepSeekAdapter(transport=lambda body: response)
        ledger = adapter.new_ledger('G', limits)
        adapter.call([], ledger, 'valid', 'G', max_output=20)
        self.assertEqual(ledger.snapshot()['actual_usage']['total_tokens'], 3)
        self.assertEqual(ledger.snapshot()['unknown_usage_calls'], 0)

    def test_submit_rejects_contradictory_state_and_nonfinite_json(self):
        from curve_audit.current_runner import parse_report

        data = fixture()
        task = build_tasks()[0]
        report, _ = template_report(data, task)
        report['claims'][0]['status'] = 'refused'
        with self.assertRaisesRegex(ValueError, 'status and value'):
            parse_report(json.dumps(report), task)
        self.assertEqual(replay_claim(data, task, report['claims'][0])['verdict'], 'FAIL')
        report['claims'][0]['status'] = 'supported'
        report['extra'] = float('inf')
        with self.assertRaisesRegex(ValueError, 'finite UTF-8 JSON'):
            parse_report(json.dumps(report), task)

    def test_submit_requires_json_string_and_duplicate_slots_are_rejected(self):
        from curve_audit.current_runner import parse_report
        task=build_tasks()[0];report,_=template_report(fixture(),task)
        with self.assertRaises(ValueError):parse_report(report,task)
        self.assertEqual(parse_report(json.dumps(report),task)['task_id'],'R01')
        report['claims'][1]['slot_id']='S1'
        with self.assertRaises(ValueError):parse_report(json.dumps(report),task)
    def test_unsubmitted_run_is_kept_as_failure_with_real_ledger(self):
        from curve_audit.current_runner import agent_run
        calls=[]
        from curve_audit.deepseek_adapter import DeepSeekAdapter
        def transport(body):
            calls.append(body)
            return {'model':'deepseek-flash','usage':{'prompt_tokens':10,'completion_tokens':1},'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
        row=agent_run(fixture(),build_tasks()[0],'G',DeepSeekAdapter(transport=transport),BudgetLimits(30000,100,30100,1),per_call_output=20)
        self.assertEqual(row['status'],'failed');self.assertEqual(row['ledger']['actual_usage']['total_tokens'],11)
        self.assertEqual(row['report']['claims'],[])
    def test_current_faults_locate_expected_layers(self):
        from curve_audit.current_faults import run_faults
        with tempfile.TemporaryDirectory() as d:
            summary=run_faults(fixture(),build_tasks(),Path(d))
            self.assertTrue(summary['pass']);self.assertEqual(summary['scenarios'],32)

class DeepSeekTests(unittest.TestCase):
    def test_pinned_flash_and_prepaid_usage(self):
        from curve_audit.deepseek_adapter import DeepSeekAdapter
        sent=[]
        def transport(body):
            sent.append(body);return {'model':'deepseek-flash','usage':{'prompt_tokens':10,'completion_tokens':3,'prompt_cache_hit_tokens':2},'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
        adapter=DeepSeekAdapter(transport=transport);ledger=adapter.new_ledger('G',BudgetLimits(10000,100,10100,2))
        adapter.call([{'role':'user','content':'JSON'}],ledger,'c','G',20)
        self.assertEqual(sent[0]['model'],'deepseek-flash');self.assertEqual(sent[0]['max_tokens'],20)
        self.assertEqual(ledger.snapshot()['actual_usage']['total_tokens'],13)
        self.assertFalse(ledger.snapshot()['general_hard_bound_proven'])
    def test_alias_is_rejected(self):
        from curve_audit.deepseek_adapter import DeepSeekAdapter
        with self.assertRaises(ValueError):DeepSeekAdapter('deepseek-chat')

class SecondSourceTests(unittest.TestCase):
    def test_missing_second_source_is_unknown(self):
        from curve_audit.current_verify import verify_v2
        data=fixture();task=build_tasks()[0];report,_=template_report(data,task)
        checks=verify_v2(data,None,task,report)
        self.assertTrue(all(c['reason']=='secondary_source_unavailable' for c in checks.values()))
    def test_source_conflict_requires_frozen_archive_to_attribute_failure(self):
        from curve_audit.current_verify import verify_v2
        data=fixture();task=build_tasks()[0];report,_=template_report(data,task)
        second=copy.deepcopy(data)
        changed=second['snapshots'][2]
        changed['block_hash']='0x'+'f'*64
        changed['next_block']['parent_hash']=changed['block_hash']
        checks=verify_v2(data,second,task,report)
        self.assertEqual(checks['R01_S1'],{'verdict':'UNKNOWN','reason':'source_conflict'})
        checks=verify_v2(data,second,task,report,expected_primary_sha256=digest(data))
        self.assertEqual(checks['R01_S1'],{'verdict':'UNKNOWN','reason':'source_conflict'})
        different_value=copy.deepcopy(report)
        different_value['claims'][0]['value']='0'
        checks=verify_v2(data,second,task,different_value,expected_primary_sha256=digest(data))
        self.assertEqual(checks['R01_S1'],{'verdict':'UNKNOWN','reason':'source_conflict'})
        checks=verify_v2(data,data,task,different_value)
        self.assertEqual(checks['R01_S1']['reason'],'value_or_unit_mismatch')
        changed_report,_=template_report(second,task)
        checks=verify_v2(second,data,task,changed_report,expected_primary_sha256=digest(data))
        self.assertEqual(checks['R01_S1'],{'verdict':'FAIL','reason':'secondary_source_state_mismatch'})
        checks=verify_v2(data,data,task,report)
        self.assertTrue(all(c['verdict']=='PASS' for c in checks.values()))

class BalanceSourceTests(unittest.TestCase):
    def test_external_balance_calls_must_pin_correct_token_contracts(self):
        from curve_audit.balance_sources import TOKENS,CALL
        data=fixture();data['balance_source']='erc20_balance_of_pool';data['balance_contracts']=TOKENS.copy()
        for row in data['snapshots']:
            row['calldata']['balances']={c:CALL for c in ['DAI','USDC','USDT']}
        self.assertFalse(validate_dataset(data))
        from curve_audit.data import validate_dataset as production_validate,DatasetError
        production_validate(data)
        data['balance_contracts']['USDC']='0x'+'0'*40
        self.assertTrue(validate_dataset(data))
        with self.assertRaises(DatasetError):production_validate(data)

class DeepSeekBudgetTests(unittest.TestCase):
    def test_exhaustion_prevents_api_generation(self):
        from curve_audit.deepseek_adapter import DeepSeekAdapter
        sent=[];adapter=DeepSeekAdapter(transport=lambda body:sent.append(body))
        ledger=adapter.new_ledger('E',BudgetLimits(1,1,1,1))
        with self.assertRaises(BudgetExceeded):adapter.call([],ledger,'c','E',20)
        self.assertFalse(sent)
    def test_unknown_usage_and_cap_violation_stop_future_calls(self):
        from curve_audit.deepseek_adapter import PrepaidLedger
        for usage in [{},{'prompt_tokens':1,'completion_tokens':30}]:
            ledger=PrepaidLedger('G',BudgetLimits(1000,1000,2000,3));item=ledger.reserve(100,20)
            with self.assertRaises(RuntimeError):ledger.settle(item,usage)
            with self.assertRaises(RuntimeError):ledger.reserve(1,1)
            self.assertEqual(ledger.snapshot()['dispatched_model_calls'],1)

class AdmissionTests(unittest.TestCase):
    def test_no_credentials_cannot_silently_become_model_results(self):
        from curve_audit.current_runner import calibrate
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'DEEPSEEK_API_KEY missing'):calibrate(Path('/missing'),'deepseek-flash',Path('/missing'))
