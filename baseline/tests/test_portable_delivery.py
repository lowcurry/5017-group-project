from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from shutil import copyfile, copytree
from tempfile import TemporaryDirectory
import unittest

from curve_audit.common import ROOT, read_json
from curve_audit.current_verify import main as verify_cli
from docs.verify_delivery import CHECKSUMS, MANIFEST, verify


class PortableDeliveryTests(unittest.TestCase):
    def test_exact_inventory_rejects_extra_file_and_empty_manifest(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            report = folder / 'report.pdf'
            report.write_bytes(b'delivered report')
            checksum = sha256(report.read_bytes()).hexdigest()
            manifest = {
                'schema_version': 'course-submission-1',
                'files': {report.name: {'type': 'file', 'sha256': checksum, 'bytes': report.stat().st_size}},
            }
            (folder / MANIFEST).write_text(json.dumps(manifest))
            (folder / CHECKSUMS).write_text(f'{checksum}  {report.name}\n')
            self.assertTrue(verify(folder)['pass'])

            (folder / '.env.local').touch()
            result = verify(folder)
            self.assertFalse(result['pass'])
            self.assertEqual(result['unexpected_paths'], ['.env.local'])

            (folder / MANIFEST).write_text(json.dumps({**manifest, 'files': {}}))
            report.unlink()
            self.assertFalse(verify(folder)['pass'])

    def test_v2_cli_distinguishes_provider_provenance_for_identical_datasets(self):
        preparation = ROOT / 'results/current_preparation'
        sources = ROOT / 'data/current_study'
        report = ROOT / 'experiments/formal_v1/R02_T.json'
        binding = read_json(preparation / 'binding.json')
        self.assertEqual(binding['dataset_sha256'], binding['secondary_dataset_sha256'])
        with TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            args = ['--preparation', str(preparation), '--task', 'R02', '--report', str(report),
                    '--output']
            for provider, verdict in [('alchemy', 'UNKNOWN'), ('drpc', 'PASS')]:
                with self.subTest(provider=provider):
                    output = Path(directory) / f'{provider}.json'
                    paths = [str(sources / f'{event}_{provider}_erc20.json') for event in ('usdc_2023', 'ftx_2022')]
                    self.assertEqual(verify_cli(args + [str(output), '--secondary-sources', *paths]), 0)
                    result = read_json(output)
                    self.assertEqual({row['verdict'] for row in result['V2'].values()}, {verdict})
                    self.assertEqual(bool(result['secondary_error']), provider == 'alchemy')

    def test_verification_does_not_replace_input(self):
        preparation = ROOT / 'results/current_preparation'
        saved = ROOT / 'experiments/formal_v1/R02_T.json'
        with TemporaryDirectory() as directory, redirect_stderr(StringIO()) as stderr:
            report = Path(directory) / 'report.json'
            copyfile(saved, report)
            with self.assertRaises(SystemExit) as error:
                verify_cli(['--preparation', str(preparation), '--task', 'R02',
                            '--report', str(report), '--output', str(report)])
            self.assertEqual(error.exception.code, 2)
            self.assertIn('Output already exists', stderr.getvalue())
            self.assertEqual(report.read_bytes(), saved.read_bytes())

    def test_formal_freeze_rejects_same_source_even_when_datasets_match(self):
        from curve_audit.current_runner import freeze

        with TemporaryDirectory() as directory:
            preparation = Path(directory) / 'preparation'
            copytree(ROOT / 'results/current_preparation', preparation)
            frozen = preparation / 'formal_freeze.json'
            frozen.unlink()
            binding_path = preparation / 'binding.json'
            binding = read_json(binding_path)
            binding['secondary_source_hashes'] = binding['source_hashes']
            binding_path.write_text(json.dumps(binding))
            with self.assertRaisesRegex(ValueError, 'separate frozen raw source bindings'):
                freeze(preparation, ROOT / 'config/current_model.flash.json',
                       consolidated_review=preparation / 'reference_review/review_attestation.json',
                       source_dir=ROOT / 'data/current_study')
            self.assertFalse(frozen.exists())

    def test_v2_cli_rejects_duplicate_claim_before_writing_output(self):
        original = read_json(ROOT / 'experiments/formal_v1/R02_T.json')['report']
        duplicate = {**original['claims'][0], 'value': '-9999'}
        report = {**original, 'claims': [duplicate, *original['claims']]}
        with TemporaryDirectory() as directory, redirect_stderr(StringIO()):
            report_path = Path(directory) / 'report.json'
            output = Path(directory) / 'verified.json'
            report_path.write_text(json.dumps(report))
            args = ['--preparation', str(ROOT / 'results/current_preparation'), '--task', 'R02',
                    '--report', str(report_path), '--output', str(output)]
            with self.assertRaises(SystemExit) as error:
                verify_cli(args)
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(output.exists())

    def test_cli_rejects_unbound_tasks_without_a_secondary_source(self):
        preparation = ROOT / 'results/current_preparation'
        report = ROOT / 'experiments/formal_v1/R02_T.json'
        with TemporaryDirectory() as directory, redirect_stderr(StringIO()) as stderr:
            changed = Path(directory)
            for name in ('binding.json', 'dataset.json', 'formal_freeze.json'):
                copyfile(preparation / name, changed / name)
            tasks = read_json(preparation / 'tasks.json')
            tasks[1]['question'] = 'Modified task'
            (changed / 'tasks.json').write_text(json.dumps(tasks))
            output = changed / 'verified.json'
            args = ['--preparation', str(changed), '--task', 'R02', '--report', str(report), '--output', str(output)]
            with self.assertRaises(SystemExit) as error:
                verify_cli(args)
            self.assertEqual(error.exception.code, 2)
            self.assertIn('do not match the frozen binding', stderr.getvalue())
            self.assertFalse(output.exists())
