import csv
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from confirmation import study_data, run as run_confirmation
from evaluation import HERE, MODELS, choose, evaluate, load, observations, probabilities, read_reviews, table
from curve_audit.current_study import template_report


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
