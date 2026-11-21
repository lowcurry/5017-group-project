from collections import defaultdict, deque
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from curve_audit import current_runner, verification
from curve_audit.common import ROOT, read_json, write_json
from curve_audit.current_study import digest, template_report
from curve_audit.publication_arithmetic import check_claim


def point(hour):
    return f'2026-01-01T{hour:02d}:00:00Z'


def row(hour, amounts, *, block=None):
    return {'id': str(hour), 'target_timestamp_utc': point(hour), 'series': 'hourly',
            'block_hash': str(hour if block is None else block),
            'raw_balances': {coin: str(value * 10**scale)
                             for coin, scale, value in zip(('DAI', 'USDC', 'USDT'), (18, 6, 6), amounts)}}


class ArithmeticChecks(unittest.TestCase):
    def setUp(self):
        self.dataset = {'decimals': {'DAI': 18, 'USDC': 6, 'USDT': 6},
                        'snapshots': [row(0, (1, 1, 2)), row(1, (1, 3, 0)), row(2, (1, 0, 3))]}
        self.task = {'window': {'start': point(0), 'end': point(3)},
                     'allowed_evidence': {'excluded_targets': []}}

    def check(self, operation, value, unit, evidence, *, metric='share_pct', end=2, **times):
        query = {'metric': metric, 'operation': operation, 'start': point(0), 'end': point(end)}
        claim = {'query': query, 'value': value, 'unit': unit,
                 'evidence_ids': [f'E_{hour}' for hour in evidence], **times}
        return check_claim(self.dataset, self.task, query, claim)

    def test_share_tolerance_and_invalid_inputs(self):
        def verdict(value, unit='percent'):
            return self.check('last', value, unit, [0], end=0,
                              target_timestamp_utc=point(0))['verdict']
        self.assertEqual(verdict('25.00000001'), 'PASS')
        self.assertEqual(verdict('25.00000001000001'), 'FAIL')
        self.assertEqual(verdict('25', 'percentage_points'), 'FAIL')
        self.dataset['snapshots'][0] = row(0, (0, 0, 0))
        self.assertEqual(verdict('25'), 'UNKNOWN')
        self.dataset['snapshots'][0] = row(0, (1, 1, 2))
        self.dataset['decimals']['DAI'] = 6
        self.assertEqual(verdict('25'), 'UNKNOWN')

    def test_peak_prefers_first_tie_and_deduplicates_blocks(self):
        self.dataset['snapshots'].append(row(3, (1, 0, 3), block=2))
        self.dataset['snapshots'].reverse()
        for entry in self.dataset['snapshots']:
            entry['raw_balances'] = {coin: str(int(amount) * 11)
                                     for coin, amount in entry['raw_balances'].items()}

        def result(at):
            return self.check('maximum', '0.416666666667', 'dimensionless', [0, 1, 2],
                              metric='imbalance_l1', end=3, target_timestamp_utc=point(at))

        self.assertEqual(result(1)['verdict'], 'PASS')
        self.assertEqual(result(2)['reason'], 'independent_arithmetic_time_mismatch')
        self.task['allowed_evidence']['excluded_targets'] = [point(1)]
        self.assertEqual(result(1)['verdict'], 'UNKNOWN')

    def test_change_keeps_endpoints_and_hourly_change_cannot_cross_a_gap(self):
        self.task['allowed_evidence']['excluded_targets'] = [point(1)]
        self.dataset['snapshots'].append(row(3, (1, 2, 1)))
        self.assertEqual(self.check('change', '25', 'percentage_points', [0, 3], end=3)['verdict'], 'PASS')
        result = self.check('max_hourly_change', '50', 'percentage_points', [2, 3], end=3,
                            interval_start=point(2), interval_end=point(3))
        self.assertEqual(result['verdict'], 'PASS')
        result = self.check('max_hourly_change', '50', 'percentage_points', [0, 2], end=3,
                            interval_start=point(0), interval_end=point(2))
        self.assertEqual(result['reason'], 'independent_arithmetic_time_mismatch')
        self.task['allowed_evidence']['excluded_targets'].append(point(3))
        result = self.check('max_hourly_change', '50', 'percentage_points', [0, 2], end=3,
                            interval_start=point(0), interval_end=point(2))
        self.assertEqual(result['verdict'], 'UNKNOWN')

    def test_scope_comparison_uses_the_task_request(self):
        request = {'metric': 'share_pct', 'coin': 'DAI', 'operation': 'last',
                   'start': point(0), 'end': point(0)}
        wrong_query = {**request, 'coin': 'USDC'}
        result = check_claim(self.dataset, self.task, request, {'query': wrong_query})
        self.assertEqual(result['reason'], 'independent_arithmetic_query_mismatch')


class BatchPublicationTests(unittest.TestCase):
    def test_batch_run_publishes_six_controls_and_blocks_six_shared_errors(self):
        from audit.arithmetic_faults.replay_publication_guard import altered_report, injected_fault

        source = ROOT / 'results/current_preparation'
        primary = read_json(source / 'dataset.json')
        tasks = {task['task_id']: task for task in read_json(source / 'tasks.json')}
        baseline = read_json(ROOT / 'audit/arithmetic_faults/results.json')
        fixtures = defaultdict(deque)
        runs = []
        targets = {}
        for index, case in enumerate(baseline['observations'], 1):
            task_id = case['task']
            task = tasks[task_id]
            clean, _ = template_report(primary, task)
            wrong = altered_report(primary, task, clean, case)
            targets[task_id] = next(c['claim_id'] for c in clean['claims'] if c['slot_id'] == case['slot'])
            fixtures[(task_id, 'G')].append((wrong, case['fault']))
            fixtures[(task_id, 'E')].append((clean, None))
            for group in ('G', 'E'):
                runs.append({'run_id': f'{task_id}_{group}_{index}', 'task_id': task_id,
                             'group': group, 'repetition': index})

        with TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            root = Path(directory)
            preparation = root / 'prep'
            copytree(source, preparation)
            (preparation / 'formal_freeze.json').unlink()
            write_json(preparation / 'runs.json', runs)
            binding = read_json(preparation / 'binding.json')
            write_json(preparation / 'binding.json', {**binding, 'runs_sha256': digest(runs)})
            source_dir = ROOT / 'data/current_study'
            self.assertEqual(current_runner.main([
                'freeze', '--preparation', str(preparation), '--source-dir', str(source_dir),
                '--config', str(ROOT / 'config/current_model.flash.json'),
                '--consolidated-review', str(preparation / 'reference_review/review_attestation.json'),
            ]), 0)

            with ExitStack() as mutation:
                def fixture(_dataset, task, group, *_args, **_kwargs):
                    mutation.close()
                    report, fault = fixtures[(task['task_id'], group)].popleft()
                    if fault:
                        mutation.enter_context(injected_fault(verification, fault))
                    return {'report': report, 'status': 'completed',
                            'ledger': {'actual_usage': {'total_tokens': 0},
                                       'unknown_usage_calls': 0, 'enforcement_violations': 0}}

                output = root / 'run'
                with (patch.dict(current_runner.os.environ, {'DEEPSEEK_API_KEY': 'offline-test'}),
                      patch.object(current_runner, 'DeepSeekAdapter'),
                      patch.object(current_runner, 'agent_run', side_effect=fixture)):
                    self.assertEqual(current_runner.main([
                        'run', '--preparation', str(preparation), '--source-dir', str(source_dir),
                        '--output', str(output),
                    ]), 0)

            self.assertEqual(len(runs), 12)
            for run in runs:
                with self.subTest(run=run['run_id']):
                    result = read_json(output / ('deepseek-flash_' + run['run_id'] + '.json'))
                    cid = targets[run['task_id']]
                    keep = run['group'] == 'E'
                    self.assertEqual([result[layer][cid]['verdict'] for layer in ('V1', 'V2')],
                                     ['PASS', 'PASS'])
                    self.assertEqual(result['independent_arithmetic'][cid]['verdict'],
                                     'PASS' if keep else 'FAIL')
                    self.assertEqual(cid in {c['claim_id'] for c in result['published']['claims']}, keep)
                    if not keep and run['task_id'] in {'R02', 'R05'}:
                        self.assertEqual(result['verification'][run['task_id'] + '_S3']['reason'],
                                         'withdrawn_dependency')
