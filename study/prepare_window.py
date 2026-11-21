"""Prepare the proposed held-out 24-hour window from two existing RPC recordings."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from build import write_csv, write_json

from curve_audit.balance_sources import POOL
from curve_audit.current_study import TOLERANCES, VERSION, build_tasks, combine_sources, digest, reference_candidates, shift
from curve_audit.current_verify import bound_source_pair

CANDIDATE_START = '2023-06-15T00:00:00Z'
SEED = 20261121


def window_tasks(start, event_id):
    prototypes = build_tasks()[:3]
    from_time = datetime.fromisoformat(prototypes[0]['window']['start'])
    to_time = datetime.fromisoformat(start)
    hours = (to_time - from_time).total_seconds() / 3600
    tasks = deepcopy(prototypes)
    for index, task in enumerate(tasks, 1):
        task.update(event_id=event_id, task_id=f'H{index:02d}')
        task['window'] = {key: shift(value, hours) for key, value in task['window'].items()}
        task['allowed_evidence']['excluded_targets'] = [
            shift(value, hours) for value in task['allowed_evidence']['excluded_targets']
        ]
        for component in task['required_components']:
            for key in ('start', 'end'):
                if key in component['request']:
                    component['request'][key] = shift(component['request'][key], hours)
    return tasks


def prepare(primary_path, secondary_path, output, *, start=CANDIDATE_START, event_id='holdout_20230615'):
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    primary, primary_records = combine_sources([primary_path])
    secondary, secondary_records = combine_sources([secondary_path])
    expected_grid = [shift(start, hour) for hour in range(26)]
    for data in (primary, secondary):
        if (data['chain_id'] != 1 or data['pool'].lower() != POOL
                or data.get('balance_source') != 'erc20_balance_of_pool'):
            raise ValueError('Expected Ethereum Curve 3pool ERC-20 balanceOf captures')
        if [row['target_timestamp_utc'] for row in data['snapshots']] != expected_grid:
            raise ValueError('Both captures must cover exactly the proposed 26 hourly targets')
    tasks = window_tasks(start, event_id)
    references = reference_candidates(primary, tasks)
    if sum(row['answerable'] for row in references) != 8 or len(references) != 9:
        raise ValueError('Window does not meet the proposed task coverage')
    if references != reference_candidates(secondary, tasks):
        raise ValueError('Recorded sources disagree on candidate answers')
    runs = [{'task_id': task['task_id'], 'group': group, 'repetition': rep,
             'run_id': f"{task['task_id']}_{group}_{rep}"}
            for task in tasks for rep in range(1, 4) for group in ('G', 'E')]
    random.Random(SEED).shuffle(runs)
    binding = {
        'version': VERSION, 'seed': SEED, 'primary_metric': 'reproducible_correct_fixed_component_coverage',
        'tolerances': TOLERANCES, 'tasks_sha256': digest(tasks), 'references_sha256': digest(references),
        'runs_sha256': digest(runs), 'dataset_sha256': digest(primary), 'secondary_dataset_sha256': digest(secondary),
        'source_hashes': [row['content_sha256'] for row in primary_records],
        'secondary_source_hashes': [row['content_sha256'] for row in secondary_records],
        'human_references_validated': False, 'formal_outputs': 0,
    }
    bound_source_pair(binding, [primary_path], [secondary_path])
    for first, second in zip(primary['snapshots'], secondary['snapshots']):
        if any(first[key] != second[key] for key in ('block_number', 'block_hash', 'raw_balances', 'raw_quotes', 'calldata')):
            raise ValueError(f"Source conflict at {first['target_timestamp_utc']}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        stage = Path(temp) / 'window'
        private = stage / 'coordinator'
        reviewers = stage / 'reviewers'
        private.mkdir(parents=True)
        reviewers.mkdir()
        for directory, filename, data in (
            (private, 'candidate-references.json', references), (private, 'binding.json', binding),
            (private, 'runs.json', runs), (reviewers, 'tasks.json', tasks),
            (reviewers, 'primary.json', primary), (reviewers, 'secondary.json', secondary),
        ):
            write_json(directory / filename, data)
        fields = ('reviewer', 'date_utc', 'answerable', 'value', 'unit', 'target_or_interval', 'calculation_and_source')
        rows = [{'task_id': row['task_id'], 'slot_id': row['slot_id'], **dict.fromkeys(fields, '')} for row in references]
        for role in ('A', 'B'):
            write_csv(reviewers / f'review-{role}.csv', rows)
        write_csv(private / 'adjudication.csv', [{**row, 'adjudication_reason': ''} for row in rows])
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--primary', required=True, type=Path)
    parser.add_argument('--secondary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    prepare(args.primary, args.secondary, args.output)


if __name__ == '__main__':
    main()
