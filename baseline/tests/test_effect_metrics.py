from importlib.util import find_spec
import json
import os
from pathlib import Path
from subprocess import run
import sys
from tempfile import TemporaryDirectory
import unittest
from xml.etree.ElementTree import parse

from curve_audit.common import ROOT


EFFECT = ROOT / 'audit/effect_metrics'
COMMAND = [sys.executable, '-B', str(EFFECT / 'build.py')]


class EffectMetricsTests(unittest.TestCase):
    def test_standard_library_export_preserves_pairs_and_verdict_semantics(self):
        with TemporaryDirectory() as folder:
            output = Path(folder) / 'metrics'
            command = [*COMMAND, '--metrics-only', '--output', str(output)]
            run(command, check=True, cwd=folder)
            self.assertEqual({p.name for p in output.iterdir()}, {'metrics.csv', 'metrics.json'})
            for path in output.iterdir():
                self.assertEqual(path.read_bytes(), (EFFECT / 'figures' / path.name).read_bytes())
            metrics = json.loads((output / 'metrics.json').read_bytes())
            self.assertEqual(metrics['pair_counts'], {'scheduled_pairs': 18, 'completed_pairs': 16})
            self.assertEqual(len(metrics['quote_points']), 50)
            keys = ('experiment', 'cohort', 'scope', 'group', 'metric')
            rows = {tuple(row[k] for k in keys): row for row in metrics['metrics']}
            self.assertEqual(len(rows), len(metrics['metrics']))

            def fraction(*key):
                row = rows[key]
                return row['numerator'], row['denominator']

            self.assertEqual(fraction('paired_comparison', 'complete_pairs', 'overall', 'E vs G',
                                      'reproducible_correct_e_win'), (5, 16))
            self.assertEqual(fraction('verifier_faults', 'fault_vs_clean', 'overall', 'V2',
                                      'strict_fault_recall'), (10, 12))
            self.assertEqual(fraction('verifier_faults', 'fault_vs_clean', 'overall', 'V2',
                                      'fault_nonacceptance'), (12, 12))
            self.assertEqual(fraction('research_decisions', 'scheduled', 'overall', 'verified',
                                      'additional_review_yield'), (1, 9))
            self.assertIsNone(rows['research_decisions', 'scheduled', 'E', 'verified',
                                   'wrong_sent_to_review']['value'])
            self.assertAlmostEqual(rows['quote_screen', 'historical', 'overall', 'proxy',
                                        'p90_absolute_error']['value'], 0.1373766)
            rejected = run(command, cwd=folder, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual((output / 'metrics.json').read_bytes(),
                             (EFFECT / 'figures' / 'metrics.json').read_bytes())

    @unittest.skipUnless(find_spec('matplotlib'), 'Optional chart dependency is not installed')
    def test_matplotlib_regenerates_all_charts_from_a_fresh_output_directory(self):
        with TemporaryDirectory() as folder:
            output = Path(folder) / 'charts'
            env = {**os.environ, 'MPLCONFIGDIR': str(Path(folder) / 'matplotlib')}
            run([*COMMAND, '--output', str(output)], check=True, cwd=folder, env=env)
            figures = list(output.glob('*.svg'))
            self.assertEqual(len(figures), 7)
            self.assertEqual({p.name for p in output.iterdir()},
                             {p.name for p in (EFFECT / 'figures').iterdir()})
            for path in figures:
                self.assertTrue(parse(path).getroot().tag.endswith('}svg'))
                self.assertIn('Matplotlib v', path.read_text(encoding='utf-8'))
            coverage = (output / 'coverage.svg').read_text(encoding='utf-8')
            self.assertIn('<!-- 34/48 -->', coverage)
            self.assertIn('<!-- 43/48 -->', coverage)
