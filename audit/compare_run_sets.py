"""Summarize the frozen G/E comparison on scheduled and completed pairs."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path


METRICS = ('answerable', 'correct', 'reproducible_correct', 'published_correct')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def compare(root, *, include_pairs=False):
    formal = root / 'experiments/formal_v1'
    events = {task['task_id']: task['event_id']
              for task in load(root / 'results/current_preparation/tasks.json')}
    schedule = load(formal / 'schedule.json')
    summary = load(formal / 'result_summary.json')
    publication = load(formal / 'publication_replay.json')
    if summary['freeze_sha256'] != publication['freeze_sha256']:
        raise ValueError('Formal and publication freeze digests differ.')
    published_runs = {row['run_id']: row for row in publication['reports']}
    run_ids = {row['run_id'] for row in schedule}
    if len(run_ids) != len(schedule) or run_ids != published_runs.keys() or len(run_ids) != len(publication['reports']):
        raise ValueError('Publication replay must match the scheduled runs one to one.')

    pairs = defaultdict(dict)
    for scheduled in schedule:
        run_id = scheduled['run_id']
        data = (formal / f'{run_id}.json').read_bytes()
        published = published_runs[run_id]
        run = json.loads(data)
        if any(run[field] != value for field, value in scheduled.items()):
            raise ValueError(f'Run metadata differs from schedule: {run_id}')
        if sha256(data).hexdigest() != published['original_run_sha256']:
            raise ValueError(f'Run hash differs from publication replay: {run_id}')
        key = (run['task_id'], run['repetition'])
        group = run['group']
        if group not in ('G', 'E') or group in pairs[key]:
            raise ValueError(f'Expected one G and one E run per pair: {key}')
        counts = {metric: run['score']['counts'][metric] for metric in METRICS[:-1]}
        counts['published_correct'] = published['published_score']['counts']['correct']
        pairs[key][group] = (run, counts)

    results = {name: defaultdict(lambda: {'G': Counter(), 'E': Counter()})
               for name in ('scheduled', 'complete_pairs')}
    excluded = []
    details = []
    for (task_id, repetition), pair in sorted(pairs.items()):
        if set(pair) != {'G', 'E'}:
            raise ValueError(f'Incomplete scheduled pair: {task_id}, repetition {repetition}')
        stopped = [run for run, _ in pair.values() if run['status'] != 'completed']
        if stopped:
            excluded.append({'task_id': task_id, 'repetition': repetition,
                             'excluded_runs': [r['run_id'] for r, _ in pair.values()],
                             'stop_reasons': {r['run_id']: r['error'] for r in stopped}})
        if include_pairs:
            details.append({'task_id': task_id, 'repetition': repetition, 'event_id': events[task_id],
                            'completed': not stopped, 'groups': {group: counts for group, (_, counts) in sorted(pair.items())}})
        for group, (_, counts) in pair.items():
            for scope in ('overall', events[task_id]):
                results['scheduled'][scope][group].update(counts)
                if not stopped:
                    results['complete_pairs'][scope][group].update(counts)

    for group in ('G', 'E'):
        for scope, counts in results['scheduled'].items():
            expected = summary['by_group'][group] if scope == 'overall' else summary['by_event'][group][scope]
            if any(counts[group][metric] != expected[metric] for metric in METRICS):
                raise ValueError(f'Computed totals disagree with the frozen summary: {group}, {scope}')
    result = {'scheduled_pairs': len(pairs), 'completed_pairs': len(pairs) - len(excluded),
              'excluded_pairs': excluded, 'counts': results}
    if include_pairs:
        result['pairs'] = details
    return result


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Root directory of the extracted submission')
    args = parser.parse_args()
    print(json.dumps(compare(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
