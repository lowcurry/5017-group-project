"""Freeze and score a separate, scripted verifier fault batch."""
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path('/Users/lowcurry/Documents/5017work/work/completed_project_v2')
sys.path.insert(0, str(ROOT))
from curve_audit.common import now_utc, read_json, sha256_file, write_json
from curve_audit.current_study import digest, replay_claim, template_report
from curve_audit.current_verify import verify_v2

PREP = ROOT / 'results/current_preparation'
primary = read_json(PREP / 'dataset.json')
secondary = read_json(PREP / 'secondary_dataset.json')
tasks = {task['task_id']: task for task in read_json(PREP / 'tasks.json')}
out = Path(__file__).with_name('fault_batch')
out.mkdir(exist_ok=True)

def change_field(path, value):
    return {'path': path, 'value': value}

cases = []
expected = {}

def add(case_id, kind, task, slot, mode='clean', point=None, change=None,
        v1=('PASS', None), v2=('PASS', None)):
    cases.append(dict(case_id=case_id, kind=kind, task=task, slot=slot, mode=mode,
                      point=point, change=change))
    expected[case_id] = {'V1': v1, 'V2': v2}

def pair(number, category, task, slot, **options):
    pair_id = f'P{number:02d}'
    add(pair_id + '_clean', 'clean', task, slot)
    add(pair_id + '_fault', category, task, slot, **options)

unit = ('FAIL', 'value_or_unit_mismatch')
query = ('FAIL', 'task_query_mismatch')
state = ('FAIL', 'secondary_source_state_mismatch')
missing = ('UNKNOWN', 'secondary_source_unavailable')
refusal = ('UNKNOWN', 'refusal_or_omission')
pair(1, 'unit', 'R02', 'S1', mode='report', change=change_field(['unit'], 'percent'), v1=unit, v2=unit)
pair(2, 'unit', 'R05', 'S2', mode='report', change=change_field(['unit'], 'basis_points'), v1=unit, v2=unit)
pair(3, 'unit', 'R04', 'S2', mode='report', change=change_field(['unit'], 'USDC/USDT'), v1=unit, v2=unit)
pair(4, 'block', 'R04', 'S1', mode='primary_number', point='2022-11-09T02:00:00Z', v2=state)
pair(5, 'block', 'R02', 'S3', mode='primary_hash', point='2023-03-11T01:00:00Z', v2=state)
pair(6, 'block', 'R05', 'S2', mode='primary_number', point='2022-11-09T15:00:00Z', v2=state)
pair(7, 'window', 'R05', 'S1', mode='report', change=change_field(['query','start'], '2022-11-09T01:00:00Z'), v1=query, v2=query)
pair(8, 'window', 'R02', 'S2', mode='report', change=change_field(['query','end'], '2023-03-10T23:00:00Z'), v1=query, v2=query)
pair(9, 'missing_data', 'R06', 'S1', mode='drop_secondary', point='2022-11-09T00:00:00Z', v2=missing)
pair(10, 'missing_data', 'R05', 'S3', mode='drop_secondary', point='2022-11-09T16:00:00Z', v2=missing)
pair(11, 'wrong_value', 'R04', 'S3', mode='report', change=change_field(['value'], '0'), v1=unit, v2=unit)
pair(12, 'direction', 'R05', 'S3', mode='report', change=change_field(['query','direction'], 'USDT_USDC'), v1=query, v2=query)
add('R1', 'legal_refusal', 'R03', 'S3', v1=refusal, v2=refusal)
add('R2', 'legal_refusal', 'R06', 'S3', v1=refusal, v2=refusal)
add('R3', 'legal_refusal', 'R01', 'S1', mode='exclude_point', point='2023-03-10T02:00:00Z', v1=refusal, v2=refusal)
add('R4', 'legal_refusal', 'R04', 'S2', mode='exclude_point', point='2022-11-09T02:00:00Z', v1=refusal, v2=refusal)
add('U1', 'source_unavailable', 'R02', 'S1', mode='no_secondary', v2=missing)
add('U2', 'source_unavailable', 'R05', 'S2', mode='no_secondary', v2=missing)
add('U3', 'source_conflict', 'R02', 'S2', mode='secondary_hash', point='2023-03-11T00:00:00Z',
    v2=('UNKNOWN', None))
add('U4', 'source_conflict', 'R06', 'S2', mode='secondary_hash', point='2022-11-09T20:00:00Z',
    v2=('UNKNOWN', None))
assert len(cases) == len(expected) == 32

def materialize(case):
    task = deepcopy(tasks[case['task']])
    p, s = primary, secondary
    mode, point = case['mode'], case['point']
    if mode == 'exclude_point':
        task['allowed_evidence']['excluded_targets'].append(point)
    elif mode == 'no_secondary':
        s = None
    elif mode == 'drop_secondary':
        s = {**secondary, 'snapshots': [r for r in secondary['snapshots'] if r['target_timestamp_utc'] != point]}
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
    report, _ = template_report(p, task)
    claim = next(c for c in report['claims'] if c['slot_id'] == case['slot'])
    if mode == 'report':
        update = case['change']
        parent = claim
        for part in update['path'][:-1]:
            parent = parent[part]
        parent[update['path'][-1]] = update['value']
    return p, s, task, claim

if sys.argv[1] == 'freeze':
    assert not (out / 'frozen_cases.json').exists()
    for case in cases:
        p, s, task, claim = materialize(case)
        case['input_sha256'] = digest([p, s, task, claim])
    write_json(out / 'answer_key.json', expected)
    metadata = {
        'frozen_utc': now_utc(), 'dataset_sha256': digest(primary),
        'secondary_dataset_sha256': digest(secondary), 'script_sha256': sha256_file(Path(__file__)),
        'answer_key_sha256': sha256_file(out / 'answer_key.json'), 'cases': cases,
        'scope': 'Scripted follow-up with disjoint scenario instances. Frozen separately before verifier execution.',
    }
    write_json(out / 'frozen_cases.json', metadata)
else:
    from collections import Counter
    frozen = read_json(out / 'frozen_cases.json')
    assert frozen['script_sha256'] == sha256_file(Path(__file__))
    observations = []
    for case in frozen['cases']:
        p, s, task, claim = materialize(case)
        assert case['input_sha256'] == digest([p, s, task, claim])
        v1 = replay_claim(p, task, claim)
        v2 = verify_v2(p, s, task, {'claims': [claim]})[claim['claim_id']]
        observation = {k: case[k] for k in ('case_id','kind')}
        for label, value in [('V1', v1), ('V2', v2)]:
            observation[label] = {key: value[key] for key in ('verdict','reason')}
        observations.append(observation)
    write_json(out / 'observed.json', observations)
    assert frozen['answer_key_sha256'] == sha256_file(out / 'answer_key.json')
    answers = read_json(out / 'answer_key.json')
    mismatches = []
    by_layer = {}
    for layer in ('V1', 'V2'):
        groups = {'fault': Counter(), 'clean': Counter(), 'legal_refusal': Counter(),
                  'source_unavailable': Counter(), 'source_conflict': Counter()}
        for row in observations:
            expected_state, expected_reason = answers[row['case_id']][layer]
            actual = row[layer]
            if actual['verdict'] != expected_state or (expected_reason and actual['reason'] != expected_reason):
                mismatches.append({'case_id': row['case_id'], 'layer': layer, 'expected': answers[row['case_id']][layer], 'actual': actual})
            group = row['kind'] if row['kind'] in groups else 'fault'
            groups[group][actual['verdict']] += 1
        by_layer[layer] = groups
    result = {'cases': len(observations), 'logical_checks': 2 * len(observations),
              'observed_by_layer': by_layer, 'expectation_mismatches': mismatches,
              'cases_sha256': sha256_file(out / 'frozen_cases.json'),
              'scope': 'Local replay against fixed snapshots and simulated faults. No live provider outage asserted.'}
    write_json(out / 'summary.json', result)
    print(result)
