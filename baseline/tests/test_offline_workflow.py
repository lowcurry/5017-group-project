"""Offline integration preserves computable slots and actual dependency order."""
import json,unittest
from pathlib import Path
from unittest.mock import patch
from curve_audit.confirmation import build_draft_tasks,public_task
from curve_audit.offline_workflow import run_task

ROOT=Path(__file__).resolve().parents[1]
class OfflineWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/'data/raw/expanded_archive.json').read_text());cls.tasks=build_draft_tasks()

    def test_partial_task_does_not_refuse_computable_point(self):
        row=run_task(self.data,public_task(self.tasks[6]))
        self.assertEqual(['supported','unsupported','unsupported'],[c['status'] for c in row['report']['claims']])
        self.assertTrue(row['independent_arithmetic']['pass'])

    def test_peak_queries_depend_on_returned_target(self):
        row=run_task(self.data,public_task(self.tasks[4]))
        self.assertTrue(row['independent_arithmetic']['pass'])
        self.assertEqual([[],['S1'],['S1'],['S1']],[s['depends_on'] for s in row['steps']])
        self.assertTrue(all(s['arguments']['start']==s['arguments']['end'] for s in row['steps'][1:]))

    def test_no_model_or_private_reference_needed(self):
        with patch('curve_audit.provider.complete',side_effect=AssertionError('model called')):
            row=run_task(self.data,public_task(self.tasks[0]))
        self.assertFalse(row['inference_performed']);self.assertTrue(row['independent_arithmetic']['pass'])

    def test_independent_verifier_detects_bad_production_output(self):
        from curve_audit.metrics import calculate
        def wrong(data,query):
            result=calculate(data,query);result['value']='999';return result
        with patch('curve_audit.offline_workflow.calculate',side_effect=wrong):
            row=run_task(self.data,public_task(self.tasks[0]))
        self.assertFalse(row['independent_arithmetic']['pass'])

if __name__=='__main__':unittest.main()
