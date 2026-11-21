"""Time the local path from recorded inputs to verified publication files."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
from statistics import median
import sys
from tempfile import TemporaryDirectory

from ledger import RunLedger


def benchmark(root, index):
    from curve_audit.common import read_json, sha256_file, write_json
    from curve_audit.current_study import digest, publish, published_payload, replay_claim
    from curve_audit.current_verify import bound_recorded_sources, read_study_binding, verify_v2

    preparation = root / 'results/current_preparation'
    formal = root / 'experiments/formal_v1'
    sources = root / 'data/current_study'
    with TemporaryDirectory(prefix='5017-cost-') as tmp:
        output = Path(tmp) / 'publication.json'
        ledger = RunLedger(f'offline-publication-{index}', 'offline_replay')
        with ledger.phase('load_archived_inputs'):
            binding = read_study_binding(preparation)
            tasks = read_json(preparation / 'tasks.json')
            if digest(tasks) != binding['tasks_sha256']:
                raise ValueError('Tasks differ from the frozen binding')
            tasks = {task['task_id']: task for task in tasks}
            schedule = read_json(formal / 'schedule.json')
            saved = read_json(formal / 'publication_replay.json')
            freeze = read_json(formal / 'freeze.json')
            expected = {row['run_id']: row for row in saved['reports']}
            run_ids = [row['run_id'] for row in schedule]
            if len(run_ids) != 36 or len(expected) != 36 or set(run_ids) != set(expected):
                raise ValueError('Archived publication does not match the 36-run schedule')
            if saved['freeze_sha256'] != freeze['freeze_sha256'] or freeze['binding'] != binding:
                raise ValueError('Archived publication and source binding do not match the freeze')
            runs = []
            for item in schedule:
                run_id = item['run_id']
                path = formal / (run_id + '.json')
                run = read_json(path)
                if (any(run[key] != value for key, value in item.items())
                        or sha256_file(path) != expected[run_id]['original_run_sha256']):
                    raise ValueError(f'{run_id} differs from the archived schedule or hash')
                runs.append(run)
        with ledger.phase('load_and_bind_rpc_captures'):
            primary, secondary = bound_recorded_sources(binding, sources)
        with ledger.phase('verify_v1'):
            v1 = [{claim['claim_id']: replay_claim(primary, tasks[run['task_id']], claim)
                   for claim in run['report']['claims']} for run in runs]
        with ledger.phase('verify_v2'):
            v2 = [verify_v2(primary, secondary, tasks[run['task_id']], run['report'],
                            expected_primary_sha256=binding['dataset_sha256']) for run in runs]
        with ledger.phase('publish_and_write_json'):
            publications = []
            for run, decisions in zip(runs, v2):
                published, verification = publish(primary, tasks[run['task_id']], run['report'],
                                                  checks_override=decisions)
                publications.append({'run_id': run['run_id'], 'published': published,
                                     'verification': verification})
            write_json(output, publications)
        with ledger.phase('validate_against_archive'):
            for run, primary_checks, secondary_checks in zip(runs, v1, v2):
                if primary_checks != run['V1'] or secondary_checks != run['V2']:
                    raise ValueError(f"Verification differs for {run['run_id']}")
            saved_projection = [
                {'run_id': run_id, 'published': published_payload(expected[run_id]['published']),
                 'verification': expected[run_id]['verification']}
                for run_id in run_ids
            ]
            if read_json(output) != saved_projection:
                raise ValueError('Written publication differs from the archived projection')
        snapshot = ledger.snapshot()
    return freeze['freeze_sha256'], sum(len(run['report']['claims']) for run in runs), snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2],
                        help='path to the extracted submission root')
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--output', type=Path, help='optional new JSON file for timing results')
    args = parser.parse_args()
    if args.samples < 1:
        parser.error('--samples must be positive')
    if args.output and (args.output.exists() or args.output.is_symlink()):
        parser.error('--output must be a new file')

    root = args.root.resolve()
    sys.path.insert(0, str(root))
    observed = [benchmark(root, i) for i in range(1, args.samples + 1)]
    freeze_hash, claims, _ = observed[0]
    if any((current_hash, count) != (freeze_hash, claims) for current_hash, count, _ in observed):
        raise ValueError('Replay inputs changed between samples')
    samples = [row for _, _, row in observed]
    phase_names = samples[0]['phase_seconds']
    here = Path(__file__).resolve().parent
    code = {'curve_audit/' + name: root / 'curve_audit' / name for name in
            ('current_study.py', 'current_verify.py')}
    code.update({'audit/cost_trace_20261111/' + name: here / name for name in
                 ('ledger.py', 'replay_cost.py')})
    output = {
        'scope': 'Serial local replay of archived G/E reports from input loading through checked publication',
        'environment': {'system': platform.system(), 'machine': platform.machine(),
                        'python': platform.python_version()},
        'freeze_sha256': freeze_hash, 'archived_runs': 36, 'emitted_claims': claims,
        'code_sha256': {name: sha256(path.read_bytes()).hexdigest() for name, path in code.items()},
        'new_rpc_attempts': 0, 'new_model_attempts': 0,
        'median_elapsed_seconds': median(row['elapsed_seconds'] for row in samples),
        'median_phase_seconds': {phase: median(row['phase_seconds'][phase] for row in samples)
                                 for phase in phase_names},
        'samples': samples,
    }
    serialized = json.dumps(output, indent=2, allow_nan=False) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(serialized)
    else:
        print(serialized, end='')


if __name__ == '__main__':
    main()
