"""Summarize archived resource use and time the packaged verification stages."""
from collections import Counter
import json
from pathlib import Path
import platform
from statistics import median
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from curve_audit.common import read_json, sha256_file
from curve_audit.current_study import publish, published_payload, replay_claim
from curve_audit.current_verify import bound_recorded_sources, verify_v2


def generation_resources(runs):
    totals = {group: Counter() for group in ('G', 'E')}
    for run in runs:
        usage = run['ledger']['actual_usage']
        receipts = [step['metadata']['usage'] for step in run['trace']]
        if (sum(receipt['prompt_tokens'] for receipt in receipts) != usage['input_tokens']
                or sum(receipt['completion_tokens'] for receipt in receipts) != usage['output_tokens']
                or len(receipts) != run['ledger']['dispatched_model_calls']):
            raise ValueError(f"Generation ledger differs from receipts for {run['run_id']}")
        totals[run['group']].update({
            **usage,
            'model_calls': len(receipts),
            'cached_input_tokens': sum(r['prompt_cache_hit_tokens'] for r in receipts),
            'wall_seconds': run['wall_seconds'],
        })
    return totals


def acquisition_resources(source_dir):
    totals = {}
    for provider in ('alchemy', 'drpc', 'publicnode'):
        totals[provider] = Counter()
        for event in ('usdc_2023', 'ftx_2022'):
            stem = f'{event}_{provider}'
            record = read_json(source_dir / f'{stem}.json')
            for attempt in record['provider_attempts']:
                transcript = attempt['transcript']
                if len(transcript) != attempt['rpc_attempt_count']:
                    raise ValueError(f'RPC transcript count differs for {stem}')
                totals[provider].update({
                    'base_rpc_attempts': len(transcript),
                    'base_retries': sum(row['attempt'] > 1 for row in transcript),
                    'base_attempt_seconds': sum(row['elapsed_seconds'] for row in transcript),
                })
            if provider != 'publicnode':
                supplement = read_json(source_dir / f'{stem}_erc20.json')
                totals[provider]['supplement_rpc_items'] += sum(
                    len(row['requests']) for row in supplement['observations'])
    return totals


def time_verification(runs, tasks, primary, secondary, primary_hash, saved_publication):
    def primary_checks():
        return [{claim['claim_id']: replay_claim(primary, tasks[run['task_id']], claim)
                 for claim in run['report']['claims']} for run in runs]

    def secondary_checks():
        return [verify_v2(primary, secondary, tasks[run['task_id']], run['report'],
                          expected_primary_sha256=primary_hash) for run in runs]

    def publish_checked():
        return [publish(primary, tasks[run['task_id']], run['report'], checks_override=run['V2'])
                for run in runs]

    methods = {'V1': primary_checks, 'V2': secondary_checks, 'publication': publish_checked}
    expected = {
        'V1': [run['V1'] for run in runs],
        'V2': [run['V2'] for run in runs],
        'publication': [(published_payload(saved_publication[run['run_id']]['published']),
                         saved_publication[run['run_id']]['verification']) for run in runs],
    }
    samples = {name: [] for name in methods}
    for _ in range(5):
        for name, method in methods.items():
            start = perf_counter()
            observed = method()
            samples[name].append(perf_counter() - start)
            if observed != expected[name]:
                raise ValueError(f'{name} output differs from the saved replay')
    return {name: {'samples': values, 'median': median(values)} for name, values in samples.items()}


def main():
    formal = ROOT / 'experiments/formal_v1'
    prep = ROOT / 'results/current_preparation'
    sources = ROOT / 'data/current_study'
    binding = read_json(prep / 'binding.json')
    primary, secondary = bound_recorded_sources(binding, sources)
    tasks = {task['task_id']: task for task in read_json(prep / 'tasks.json')}
    runs = [read_json(formal / f"{row['run_id']}.json") for row in read_json(formal / 'schedule.json')]
    saved = {row['run_id']: row for row in read_json(formal / 'publication_replay.json')['reports']}
    print(json.dumps({
        'generation': generation_resources(runs),
        'acquisition': acquisition_resources(sources),
        'offline_timing': {
            'scope': 'Separate wall-clock batches on preloaded frozen model records and recorded source data',
            'platform': platform.system(),
            'machine': platform.machine(),
            'python': platform.python_version(),
            'archived_model_records': len(runs),
            'emitted_claims': sum(len(run['report']['claims']) for run in runs),
            'new_rpc_calls': 0,
            'new_model_calls': 0,
            'verifier_source_sha256': {name: sha256_file(ROOT / 'curve_audit' / name)
                                       for name in ('current_study.py', 'current_verify.py')},
            'seconds': time_verification(runs, tasks, primary, secondary, binding['dataset_sha256'], saved),
        },
    }, indent=2))


if __name__ == '__main__':
    main()
