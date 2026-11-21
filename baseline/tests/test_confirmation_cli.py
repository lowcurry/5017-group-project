"""Preparation preserves human records and never silently dispatches inference."""
import contextlib, copy, io, json, tempfile, unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch
from curve_audit import confirmation_cli as cli
from curve_audit.confirmation import ABSOLUTE_TOLERANCE, number_equal, has_refusal_reason
from curve_audit.verification import recompute, verify_claims, TOLERANCE

ROOT=Path(__file__).resolve().parents[1]

class ConfirmationCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.prep=Path(self.tmp.name)/'prepare'
        self.result=cli.prepare(self.prep)

    def test_preparation_is_draft_not_a_completed_experiment(self):
        self.assertEqual(8,self.result['tasks']);self.assertEqual(26,self.result['slots'])
        self.assertEqual(0,self.result['formal_outputs'])
        manifest=cli.read(self.prep/'task_manifest.json')
        self.assertEqual('draft',manifest['status'])
        runs=cli.read(self.prep/'planned_runs.json')
        self.assertEqual((24,72,8),tuple(len(runs[k]) for k in ('initials','finals','fixed_references')))
        public=cli.read(self.prep/'human_reference/tasks.public.json')
        self.assertNotIn('candidate_references',json.dumps(public))
        self.assertNotIn('resolved_query',json.dumps(public))

    def test_prepare_never_overwrites_filled_human_records(self):
        form=self.prep/'human_reference/reviewer_a_blank.csv'
        form.write_text('user-supplied human record')
        with self.assertRaises(FileExistsError):cli.prepare(self.prep)
        self.assertEqual('user-supplied human record',form.read_text())

    def test_readiness_cannot_dispatch_with_current_provider(self):
        with patch('curve_audit.provider.complete',side_effect=AssertionError('unexpected inference')):
            result=cli.readiness(self.prep)
        self.assertFalse(result['pass']);self.assertFalse(result['model_inference_performed'])
        self.assertIn('equal_hard_token_caps_not_enforced',result['blockers'])
        self.assertIn('validated_human_reference_records_missing',result['blockers'])

    def test_gate_exit_code_is_nonzero_and_no_outputs_created(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code=cli.main(['formal-readiness','--preparation',str(self.prep)])
        self.assertEqual(2,code)
        self.assertEqual(0,json.loads(output.getvalue())['formal_outputs'])

    def test_incomplete_source_cannot_become_ground_truth_candidates(self):
        record={'acquisition_mode':'public_https','status':'unknown','complete_allowed_grid':False}
        path=Path(self.tmp.name)/'unknown.json';path.write_text(json.dumps(record))
        with patch.object(cli,'as_recompute_dataset',return_value={}):
            with self.assertRaisesRegex(ValueError,'Incomplete source'):
                cli._combined_records([path])

    def test_partial_missing_tasks_retain_the_computable_point(self):
        # Fixed raw-input fixture tests candidate construction, not human approval.
        data=json.loads((ROOT/'data/raw/expanded_archive.json').read_text())
        destination=Path(self.tmp.name)/'candidates'
        with patch.object(cli,'_combined_records',return_value=(data,[{'content_sha256':'a'*64}])):
            result=cli.reference_candidates(self.prep,[],destination)
        self.assertEqual('pending_external_records',result['human_review'])
        refs=cli.read(destination/'reference_review_package.private.json')['candidate_references']
        self.assertEqual(22,sum(r['answerable'] for r in refs))
        for task in ('C07','C08'):
            by_slot={r['slot_id']:r for r in refs if r['task_id']==task}
            self.assertTrue(by_slot['S1']['answerable'])
            self.assertFalse(by_slot['S2']['answerable'])
            self.assertFalse(by_slot['S3']['answerable'])

class PolicyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((ROOT/'data/raw/pilot_archive.json').read_text())
        self.query={'metric':'quote_rate','operation':'first','start':'2023-03-10T18:00:00Z',
                    'end':'2023-03-10T19:00:00Z','direction':'USDC_USDT','size':1000}
        expected=recompute(self.data,self.query)
        claim={k:copy.deepcopy(expected[k]) for k in ('query','value','unit','evidence_ids')}
        claim.update(claim_id='p1',calculation_id='policy_fixture',status='supported')
        self.report={'task_id':'policy','claims':[claim],'answer':'','refusal_reason':None,'faults':[]}

    def test_verifier_uses_shared_exact_decimal_boundary(self):
        self.assertEqual(ABSOLUTE_TOLERANCE,TOLERANCE)
        base=Decimal(self.report['claims'][0]['value'])
        for delta,accepted in [('0.00000001',True),('0.0000000100000000000000000000000000000000000001',False)]:
            report=copy.deepcopy(self.report)
            with localcontext() as ctx:
                ctx.prec=100;report['claims'][0]['value']=str(base+Decimal(delta))
            self.assertEqual(accepted,number_equal(str(base),report['claims'][0]['value']))
            self.assertEqual(accepted,verify_claims(self.data,report)['pass'])

    def test_refusal_reason_locations_and_alias_agree(self):
        for location in ('answer','claim_reason','claim_refusal'):
            report=copy.deepcopy(self.report);claim=report['claims'][0]
            claim.update(status='refused',value=None,evidence_ids=[])
            claim['query']=dict(self.query,start='2020-01-01T00:00:00Z',end='2020-01-01T01:00:00Z')
            if location=='answer':report['answer']='Outside available evidence.'
            elif location=='claim_reason':claim['reason']='Outside available evidence.'
            else:claim['refusal_reason']='Outside available evidence.'
            self.assertTrue(has_refusal_reason(claim,report))
            self.assertTrue(verify_claims(self.data,report)['pass'])

if __name__=='__main__':unittest.main()
