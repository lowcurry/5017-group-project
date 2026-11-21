"""Recompute structured scores and the publication replay from saved runs."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
from zipfile import ZipFile

from .common import read_json, sha256_bytes, sha256_file
from .current_study import digest, publish, published_payload, replay_claim, score_report, template_report
from .current_verify import bound_recorded_sources, verify_v2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def replay_formal_faults(formal, tasks, primary, secondary):
    folder = formal / 'fault_batch'
    frozen = read_json(folder / 'frozen_cases.json')
    replay = read_json(formal / 'verifier_replay_20261105.json')
    answer_key = read_json(folder / 'answer_key.json')
    original_sha = frozen['dataset_sha256']
    require(digest(primary) == original_sha
            and digest(secondary) == frozen['secondary_dataset_sha256'],
            'Fault dataset binding mismatch')
    require(sha256_file(formal / 'fault_batch.py') == frozen['script_sha256'],
            'Fault generator binding mismatch')
    require(sha256_file(folder / 'answer_key.json') == frozen['answer_key_sha256'],
            'Fault answer key binding mismatch')
    require(sha256_file(folder / 'frozen_cases.json') == replay['cases_sha256'],
            'Replayed fault batch binding mismatch')
    require(len(frozen['cases']) == replay['cases'] == 32, 'Fault count mismatch')
    observations = []
    for case in frozen['cases']:
        task = deepcopy(tasks[case['task']])
        p, s = primary, secondary
        mode, point = case['mode'], case['point']
        if mode == 'exclude_point':
            task['allowed_evidence']['excluded_targets'].append(point)
        elif mode == 'no_secondary':
            s = None
        elif mode == 'drop_secondary':
            s = {**secondary, 'snapshots': [r for r in secondary['snapshots']
                                           if r['target_timestamp_utc'] != point]}
        elif mode in ('primary_number', 'primary_hash', 'secondary_hash'):
            altered = deepcopy(primary if mode.startswith('primary') else secondary)
            row = next(r for r in altered['snapshots'] if r['target_timestamp_utc'] == point)
            if mode.endswith('number'):
                row['block_number'] -= 1
                row['next_block']['block_number'] -= 1
            else:
                row['block_hash'] = '0x' + digest({'controlled_fixture': case['case_id']})
                row['next_block']['parent_hash'] = row['block_hash']
            if mode.startswith('primary'):
                p = altered
            else:
                s = altered
        else:
            require(mode in ('clean', 'report'), f'Unknown fault mode: {mode}')
        report, _ = template_report(p, task)
        claim = next(c for c in report['claims'] if c['slot_id'] == case['slot'])
        if mode == 'report':
            change = case['change']
            parent = claim
            for field in change['path'][:-1]:
                parent = parent[field]
            parent[change['path'][-1]] = change['value']
        case_id = case['case_id']
        require(digest([p, s, task, claim]) == case['input_sha256'],
                f'Materialized fault input mismatch for {case_id}')
        v1 = replay_claim(p, task, claim)
        report = {'task_id': task['task_id'], 'answer': '', 'claims': [claim]}
        v2 = verify_v2(p, s, task, report, expected_primary_sha256=original_sha,
                       allow_partial=True)[claim['claim_id']]
        observation = {'case_id': case_id, 'kind': case['kind']}
        for label, decision in [('V1', v1), ('V2', v2)]:
            verdict, reason = answer_key[case_id][label]
            require(decision['verdict'] == verdict and (reason is None or decision['reason'] == reason),
                    f'Fault expectation mismatch for {case_id}/{label}')
            observation[label] = {field: decision[field] for field in ('verdict', 'reason')}
        observations.append(observation)
    require(observations == replay['observations'], 'Recorded verifier repair replay differs')
    return len(observations)


def main(root: Path):
    preparation = root / 'results/current_preparation'
    formal = root / 'experiments/formal_v1'
    freeze = read_json(formal / 'freeze.json')
    binding = freeze['binding']
    require(digest({key: val for key, val in freeze.items() if key != 'freeze_sha256'})
            == freeze['freeze_sha256'], 'Freeze hash mismatch')
    require(read_json(preparation / 'formal_freeze.json') == freeze, 'Freeze copies differ')
    require(sha256_file(preparation / freeze['config']['calibration_record']) == freeze['calibration_sha256'],
            'Frozen calibration hash mismatch')
    with ZipFile(formal / 'frozen_sources.zip') as archive:
        require(set(archive.namelist()) == {'curve_audit/' + name for name in freeze['code']},
                'Frozen source paths mismatch')
        for name, expected in freeze['code'].items():
            require(sha256_bytes(archive.read('curve_audit/' + name)) == expected,
                    f'Frozen source hash mismatch for {name}')
    inputs = {}
    for name, bound in [
        ('tasks.json', 'tasks_sha256'),
        ('dataset.json', 'dataset_sha256'),
        ('references.automatic.private.json', 'references_sha256'),
        ('runs.json', 'runs_sha256'),
        ('secondary_dataset.json', 'secondary_dataset_sha256'),
    ]:
        inputs[name] = read_json(preparation / name)
        require(digest(inputs[name]) == binding[bound], f'Input hash mismatch for {name}')
    attestation = freeze['human_review']
    for path_key, sha_key in [('review_record', 'review_record_sha256'), ('source_file', 'source_sha256')]:
        require(sha256_file(preparation / attestation[path_key]) == attestation[sha_key],
                f'Reference review hash mismatch for {path_key}')

    dataset, secondary = inputs['dataset.json'], inputs['secondary_dataset.json']
    require(bound_recorded_sources(binding, root / 'data/current_study') == (dataset, secondary),
            'Bound raw recordings do not match the prepared datasets')
    references = inputs['references.automatic.private.json']
    tasks = {task['task_id']: task for task in inputs['tasks.json']}
    schedule = read_json(formal / 'schedule.json')
    model = freeze['config']['model']
    expected_schedule = [{**run, 'model': model, 'run_id': f"{model}_{run['run_id']}"}
                         for run in inputs['runs.json']]
    random.Random(binding['seed']).shuffle(expected_schedule)
    require(schedule == expected_schedule, 'Formal schedule does not match frozen runs and seed')
    replay = read_json(formal / 'publication_replay.json')
    expected = {row['run_id']: row for row in replay['reports']}
    require(replay['freeze_sha256'] == freeze['freeze_sha256'], 'Replay freeze hash mismatch')
    require(len(schedule) == len(expected) == 36
            and {row['run_id'] for row in schedule} == expected.keys(), 'Formal schedule mismatch')
    groups = {'G': Counter(), 'E': Counter()}
    for item in schedule:
        run_id = item['run_id']
        path = formal / (run_id + '.json')
        run = read_json(path)
        require(all(run[k] == v for k, v in item.items()), f'Run metadata mismatch for {run_id}')
        require(sha256_file(path) == expected[run_id]['original_run_sha256'],
                f'Original run hash mismatch for {run_id}')
        task, report = tasks[run['task_id']], run['report']
        score = score_report(dataset, task, report, references)
        require(score['counts'] == run['score']['counts'], f'Original score mismatch for {run_id}')
        v1 = {claim['claim_id']: replay_claim(dataset, task, claim) for claim in report['claims']}
        require(v1 == run['V1'], f'V1 replay mismatch for {run_id}')
        v2 = verify_v2(dataset, secondary, task, report, expected_primary_sha256=binding['dataset_sha256'])
        require(v2 == run['V2'], f'V2 replay mismatch for {run_id}')
        published, verification = publish(dataset, task, report, checks_override=v2)
        require(published == published_payload(expected[run_id]['published']), f'Publication mismatch for {run_id}')
        require(verification == expected[run_id]['verification'],
                f'Publication verification mismatch for {run_id}')
        published_score = score_report(dataset, task, published, references)
        require(published_score == expected[run_id]['published_score'],
                f'Published score mismatch for {run_id}')
        counts = groups[run['group']]
        counts.update(score['counts'])
        counts['published_correct'] += published_score['counts']['correct']
        counts['scheduled_runs'] += 1
        counts['successful_submissions'] += run['status'] == 'completed'
        counts['tokens'] += run['ledger']['actual_usage']['total_tokens']
    template = Counter()
    for task_id, task in tasks.items():
        row = read_json(formal / (task_id + '_T.json'))
        template.update(score_report(dataset, task, row['report'], references)['counts'])
    summary = read_json(formal / 'result_summary.json')
    require(template == summary['template'], 'Template total mismatch')
    for group, counts in groups.items():
        require(all(summary['by_group'][group][name] == count for name, count in counts.items()),
                f'Formal aggregate mismatch for {group}')
    fault_cases = replay_formal_faults(formal, tasks, dataset, secondary)
    print(json.dumps({'pass': True, 'network_used': False, 'new_model_calls': 0,
                      'frozen_model_runs': len(schedule), 'template_runs': len(tasks),
                      'frozen_fault_cases': fault_cases, 'fault_verdicts': 2 * fault_cases,
                      'group_counts': groups, 'template_counts': template}, indent=2))
