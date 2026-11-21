from copy import deepcopy
import csv
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import confirmation
from confirmation import study_data, run as run_confirmation
from evaluation import BASE, HERE, MODELS, choose, evaluate, load, observations, probabilities, read_reviews, table
from curve_audit.current_study import reference_candidates, template_report
from curve_audit.current_verify import verify_v2 as real_verify_v2


class CostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=load(HERE/'results/settings.json')
        cls.rows,_=observations(cls.cfg['training_seed'])
        cls.model,cls.times,cls.scores=probabilities(cls.rows,cls.cfg)

    def test_raw_verified_and_event_split(self):
        for policy, expected in [('raw',(1,1)),('verified',(10,0))]:
            _,summary=evaluate(self.rows,policy,self.scores,self.times,self.cfg,{},1,1)
            self.assertEqual((summary['to_human'],summary['auto_error']),expected)
        self.assertEqual({t['training_errors'] for t in self.times.values()},{0,1})
        for mode in MODELS:
            self.assertEqual(len([k for k in self.scores if k[1]==mode]),36)

    def test_future_fields_do_not_affect_routing(self):
        for r in self.rows:
            for mode in ('rule',*MODELS):
                p=self.scores.get((r['case_id'],mode),0.1)
                other=dict(r,expected_action='changed',verified_action='changed',raw_outcome='changed')
                self.assertEqual(choose(mode,r,p,self.cfg,1,1),choose(mode,other,p,self.cfg,1,1))

    def test_actual_review_replaces_both_time_and_residual_loss(self):
        row=next(r for r in self.rows if r['raw_action'] is None)
        fields=('reviewer','start_iso','end_iso','decision','previously_seen')
        packet=[{'case_id':r['case_id'],**dict.fromkeys(fields,'')} for r in self.rows]
        item=next(r for r in packet if r['case_id']==row['case_id'])
        item.update(reviewer='TEST',start_iso='2026-01-01T08:00:00+08:00',end_iso='2026-01-01T08:02:00+08:00',
                    decision=row['expected_action'],previously_seen='no')
        with TemporaryDirectory() as td:
            path=Path(td)/'test.csv'; table(path,packet)
            reviews=read_reviews(path,self.rows)
        trace,summary=evaluate(self.rows,'raw',self.scores,self.times,self.cfg,reviews,3,1)
        measured=next(t for t in trace if t['case_id']==row['case_id'])
        self.assertEqual((measured['minutes'],measured['human_cost'],measured['error_cost']),(2,6,0))
        self.assertEqual(summary['measured_reviews'],1)

    def test_window_runner_offline(self):
        window=HERE/'holdout/preparation'
        if not window.is_dir():
            self.skipTest('Two-source confirmation input pending')
        tasks,data,_,refs,_=study_data(window)
        def mock_run(primary,task,group,adapter,limits,per_call_output):
            return {'report':template_report(primary,task)[0],'status':'completed',
                    'ledger':{'actual_usage':{'input_tokens':17,'output_tokens':9}}}
        with TemporaryDirectory() as td:
            root=Path(td)
            # Synthetic approvals only within this test; never written to the study archive.
            a,b=root/'a.csv',root/'b.csv'
            for path in (a,b):
                records=[]
                for ref in refs:
                    result=ref.get('result',{})
                    records.append({'task_id':ref['task_id'],'slot_id':ref['slot_id'],'reviewer':path.stem,
                                    'date_utc':'2000-01-01','answerable':'yes' if ref['answerable'] else 'no',
                                    'value':result.get('value',''),'unit':result.get('unit',''),
                                    'target_or_interval':'|'.join(result[k] for k in ('target_timestamp_utc','interval_start','interval_end') if k in result),
                                    'calculation_and_source':'synthetic test input'})
                table(path,records)
            with self.assertRaises(ValueError):
                run_confirmation(window,root/'bad',window/'reviewers/review-A.csv',b,None)
            self.assertFalse((root/'bad').exists())
            with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test'}),patch('confirmation.agent_run',mock_run):
                out=root/'run'; run_confirmation(window,out,a,b,None)
            with (out/'decisions.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(len(list(csv.DictReader(f))),18)
            self.assertEqual(len(list(out.glob('*_T.json'))),3)

    @staticmethod
    def _synthetic_window():
        primary = load(BASE / 'results/current_preparation/dataset.json')
        secondary = deepcopy(primary)
        base_tasks = load(BASE / 'results/current_preparation/tasks.json')[:3]
        tasks = []
        for idx, task in enumerate(base_tasks, 1):
            copied = deepcopy(task)
            copied['task_id'] = f'H0{idx}'
            tasks.append(copied)
        refs = reference_candidates(primary, tasks)
        runs = [
            {'task_id': 'H02', 'group': 'G', 'repetition': 1, 'run_id': 'H02_G_1'},
            {'task_id': 'H03', 'group': 'E', 'repetition': 1, 'run_id': 'H03_E_1'},
        ]
        return tasks, primary, secondary, refs, runs

    @staticmethod
    def _existing_freeze_loader(path):
        data = load(path)
        if Path(path) == HERE / 'holdout/freeze.json':
            data = {
                **data,
                'files': {k: v for k, v in data['files'].items() if (HERE / k).is_file()},
            }
        return data

    def test_confirmation_uses_publish_verified_on_clean_run(self):
        tasks, primary, secondary, refs, runs = self._synthetic_window()

        def mock_run(data, task, group, adapter, limits, per_call_output):
            return {
                'report': template_report(data, task)[0],
                'status': 'completed',
                'ledger': {
                    'actual_usage': {'input_tokens': 120, 'output_tokens': 45, 'total_tokens': 165},
                    'calls': [{
                        'state': 'settled',
                        'usage': {
                            'input_tokens': 120, 'output_tokens': 45,
                            'cached_input_tokens': 20, 'reasoning_tokens': None, 'total_tokens': 165,
                        },
                    }],
                },
            }

        with TemporaryDirectory() as td:
            out = Path(td) / 'clean-run'
            with (patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}),
                  patch.object(confirmation, 'study_data', return_value=(tasks, primary, secondary, refs, runs)),
                  patch.object(confirmation, 'validate_reviews', return_value={'A': 'a', 'B': 'b'}),
                  patch.object(confirmation, 'load', side_effect=self._existing_freeze_loader),
                  patch.object(confirmation, 'agent_run', side_effect=mock_run)):
                run_confirmation(Path(td), out, Path('a.csv'), Path('b.csv'), None)

            h02 = json.loads((out / 'H02_G_1.json').read_text(encoding='utf-8'))
            self.assertIn('verification', h02)
            self.assertIn('independent_arithmetic', h02)
            self.assertIn('published', h02)
            self.assertTrue(h02['independent_arithmetic'])
            self.assertTrue(all(v['verdict'] == 'PASS' for v in h02['independent_arithmetic'].values()))
            with (out / 'decisions.csv').open(encoding='utf-8-sig') as f:
                rows = {r['case_id']: r for r in csv.DictReader(f)}
            self.assertEqual(rows['H02_G_1_S1']['verified_action'], rows['H02_G_1_S1']['expected_action'])
            self.assertEqual(rows['H02_G_1_S2']['verified_action'], rows['H02_G_1_S2']['expected_action'])
            self.assertEqual(rows['H03_E_1_S2']['verified_action'], rows['H03_E_1_S2']['expected_action'])

    def test_confirmation_blocks_shared_arithmetic_fault(self):
        tasks, primary, secondary, refs, runs = self._synthetic_window()
        faulty_runs = [runs[0]]

        def mock_faulty_run(data, task, group, adapter, limits, per_call_output):
            report = deepcopy(template_report(data, task)[0])
            for claim in report['claims']:
                if claim['slot_id'] == 'S1':
                    claim['value'] = str(-float(claim['value']))
            return {
                'report': report,
                'status': 'completed',
                'ledger': {'actual_usage': {'input_tokens': 50, 'output_tokens': 25}},
            }

        def buggy_shared_verify_v2(prim, sec, task, report, *, expected_primary_sha256=None, allow_partial=False):
            # Simulate a shared arithmetic bug where V1/V2 erroneously return PASS on tampered S1.
            checks = real_verify_v2(prim, sec, task, report, expected_primary_sha256=expected_primary_sha256, allow_partial=allow_partial)
            for claim in report['claims']:
                if claim['slot_id'] == 'S1':
                    checks[claim['claim_id']] = {'verdict': 'PASS', 'reason': 'raw_recomputation_matches'}
            return checks

        with TemporaryDirectory() as td:
            out = Path(td) / 'fault-run'
            with (patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}),
                  patch.object(confirmation, 'study_data', return_value=(tasks, primary, secondary, refs, faulty_runs)),
                  patch.object(confirmation, 'validate_reviews', return_value={'A': 'a', 'B': 'b'}),
                  patch.object(confirmation, 'load', side_effect=self._existing_freeze_loader),
                  patch.object(confirmation, 'agent_run', side_effect=mock_faulty_run),
                  patch.object(confirmation, 'verify_v2', side_effect=buggy_shared_verify_v2)):
                run_confirmation(Path(td), out, Path('a.csv'), Path('b.csv'), None)

            with (out / 'decisions.csv').open(encoding='utf-8-sig') as f:
                rows = {r['case_id']: r for r in csv.DictReader(f)}
            self.assertEqual(rows['H02_G_1_S1']['raw_action'], 'decreasing')
            self.assertEqual(rows['H02_G_1_S1']['expected_action'], 'increasing')
            self.assertEqual(rows['H02_G_1_S1']['verified_action'], '')
            h02 = json.loads((out / 'H02_G_1.json').read_text(encoding='utf-8'))
            s1_cid = next(c['claim_id'] for c in h02['report']['claims'] if c['slot_id'] == 'S1')
            self.assertEqual(h02['independent_arithmetic'][s1_cid]['verdict'], 'FAIL')
            self.assertEqual(
                h02['independent_arithmetic'][s1_cid]['reason'],
                'independent_arithmetic_value_or_unit_mismatch',
            )
            published_cids = {c['claim_id'] for c in h02['published']['claims']}
            self.assertNotIn(s1_cid, published_cids)

    def test_confirmation_records_run_ledger_and_summary(self):
        tasks, primary, secondary, refs, runs = self._synthetic_window()

        def mock_run(data, task, group, adapter, limits, per_call_output):
            if group == 'G':
                ledger = {
                    'actual_usage': {'input_tokens': 100, 'output_tokens': 30, 'total_tokens': 130},
                    'calls': [
                        {'state': 'settled', 'usage': {'input_tokens': 60, 'output_tokens': 10, 'cached_input_tokens': 15, 'total_tokens': 70}},
                        {'state': 'settled', 'usage': {'input_tokens': 40, 'output_tokens': 20, 'cached_input_tokens': 5, 'total_tokens': 60}},
                    ],
                }
            else:
                ledger = {'actual_usage': {'input_tokens': 80, 'output_tokens': 25}}
            return {'report': template_report(data, task)[0], 'status': 'completed', 'ledger': ledger}

        with TemporaryDirectory() as td:
            out = Path(td) / 'ledger-run'
            with (patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}),
                  patch.object(confirmation, 'study_data', return_value=(tasks, primary, secondary, refs, runs)),
                  patch.object(confirmation, 'validate_reviews', return_value={'A': 'a', 'B': 'b'}),
                  patch.object(confirmation, 'load', side_effect=self._existing_freeze_loader),
                  patch.object(confirmation, 'agent_run', side_effect=mock_run)):
                run_confirmation(Path(td), out, Path('a.csv'), Path('b.csv'), None)

            expected_phases = ('model_call', 'verify_v2', 'publish_verified', 'policy_score')
            for run_id, exp_in, exp_out in (('H02_G_1', 100, 30), ('H03_E_1', 80, 25)):
                rec = json.loads((out / f'{run_id}.json').read_text(encoding='utf-8'))
                rl = rec['run_ledger']
                for phase in expected_phases:
                    self.assertIn(phase, rl['phase_seconds'])
                    self.assertTrue(math.isfinite(rl['phase_seconds'][phase]) and rl['phase_seconds'][phase] >= 0)
                self.assertEqual(rl['known_model_usage']['input_tokens'], exp_in)
                self.assertEqual(rl['known_model_usage']['output_tokens'], exp_out)
                self.assertEqual(rl['recorded_charges_by_currency'], {})
                self.assertGreaterEqual(rl['attempt_counts']['model_attempts'], 1)

            summary_path = out / 'run-ledgers.json'
            self.assertTrue(summary_path.is_file())
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            self.assertEqual(summary['cost_basis'], 'scenario_estimate_only_for_policy_table')
            self.assertEqual(summary['recorded_charges_by_currency'], {})
            self.assertEqual(len(summary['runs']), 2)
            self.assertEqual(summary['known_model_usage_totals']['input_tokens'], 180)
            self.assertEqual(summary['known_model_usage_totals']['output_tokens'], 55)
            self.assertEqual(summary['known_model_usage_totals']['cached_input_tokens'], 20)
