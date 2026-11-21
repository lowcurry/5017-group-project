"""Rebuild the offline study from frozen observations and reports."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
import csv
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'baseline'
sys.path.insert(0, str(BASELINE))

from curve_audit.common import read_json
from curve_audit.current_study import equal, publish, replay_claim, score_report
from curve_audit.current_verify import publish_verified, verify_v2
from curve_audit.formal_replay import main as replay_formal
from curve_audit.verification import normalize_query

FORMAL = BASELINE / 'experiments/formal_v1'
PREP = BASELINE / 'results/current_preparation'
AUDIT = BASELINE / 'audit'
STAGES = ('v1', 'v2', 'guarded')


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fault_cohort(case):
    if case['case_id'].endswith('_fault'):
        return 'fault'
    return case['kind']


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def same_query(claim, reference):
    query = claim['query']
    expected = reference['resolved_query']
    try:
        if expected.get('operation') == 'max_hourly_change':
            return query == expected
        return normalize_query(query) == normalize_query(expected)
    except (KeyError, ValueError, TypeError):
        return False


def compare_number(claim, ref):
    if not ref['answerable'] or not claim or claim.get('value') is None:
        return None
    result = ref['result']
    if not same_query(claim, ref) or claim.get('unit') != result['unit']:
        return None
    for field in ('target_timestamp_utc', 'interval_start', 'interval_end'):
        if field in result and claim.get(field) != result[field]:
            return None
    return equal(claim['value'], result['value'], result['query']['metric'])


def stage_record(claim_id, released_ids, checks):
    check = checks.get(claim_id, {})
    return {
        'verdict': check.get('verdict'),
        'reason': check.get('reason'),
        'published': claim_id in released_ids,
    }


def evaluate(run, task, refs, primary, secondary, dataset_hash):
    report = run['report']
    score = score_report(primary, task, report, refs)
    raw_claims = {claim['slot_id']: claim for claim in report['claims']}
    v1 = {claim['claim_id']: replay_claim(primary, task, claim) for claim in report['claims']}
    v2 = verify_v2(primary, secondary, task, report, expected_primary_sha256=dataset_hash)
    pub1, checks1 = publish(primary, task, report, checks_override=v1)
    pub2, checks2 = publish(primary, task, report, checks_override=v2)
    pub3, checks3, arithmetic = publish_verified(primary, task, report, v2)
    stages = {
        name: ({claim['claim_id'] for claim in packet['claims']}, checks)
        for name, packet, checks in zip(STAGES, (pub1, pub2, pub3), (checks1, checks2, checks3))
    }
    states = {slot['slot_id']: slot for slot in score['slots']}
    rows = []
    for ref in refs:
        slot_id = ref['slot_id']
        claim = raw_claims.get(slot_id)
        claim_id = claim['claim_id'] if claim else None
        state = states[slot_id]
        numeric = bool(claim and claim.get('value') is not None)
        rows.append({
            **{key: run[key] for key in ('run_id', 'group', 'repetition', 'status')},
            'task_id': task['task_id'],
            'event_id': task['event_id'],
            'slot_id': slot_id,
            'answerable': ref['answerable'],
            'claim_id': claim_id,
            'emitted_number': numeric,
            'structured_refusal': bool(claim and claim.get('value') is None),
            'benchmark_state': state['state'],
            'correct_and_reproducible': state['state'] == 'correct' and state['replay']['verdict'] == 'PASS',
            'same_context_number_correct': compare_number(claim, ref),
            'raw_replay': state['replay'],
            'verifier_v1': v1.get(claim_id),
            'verifier_v2': v2.get(claim_id),
            'arithmetic': arithmetic.get(claim_id),
            'stages': {name: stage_record(claim_id, *stage) for name, stage in stages.items()},
            'report_claim': claim,
            'reference': ref,
        })
    return rows


def coverage(rows):
    answerable = [row for row in rows if row['answerable']]
    numeric = [row for row in rows if row['emitted_number']]
    comparable = [row for row in numeric if row['same_context_number_correct'] is not None]
    states = Counter(row['benchmark_state'] for row in answerable)
    result = {
        'runs': len({row['run_id'] for row in rows}),
        'answerable': len(answerable),
        'unanswerable': len(rows) - len(answerable),
        'raw_correct': states['correct'],
        'reproducible_correct': sum(row['correct_and_reproducible'] for row in answerable),
        'requested_answerable_omissions_in_completed': sum(
            row['benchmark_state'] == 'omission' and row['status'] == 'completed'
            for row in answerable
        ),
        'requested_answerable_unfilled_in_failed_runs': sum(row['status'] != 'completed' for row in answerable),
        'over_refusal': states['over_refusal'],
        'refusals_on_unanswerable': sum(not row['answerable'] and row['structured_refusal'] for row in rows),
        'justified_refusal': sum(row['benchmark_state'] == 'justified_refusal' for row in rows),
        'emitted_numbers': len(numeric),
        'wrong_emitted_numeric_slots': sum(row['benchmark_state'] != 'correct' for row in numeric),
        'comparable_numbers': len(comparable),
        'numeric_value_errors_with_matching_context': sum(not row['same_context_number_correct'] for row in comparable),
    }
    for stage in STAGES:
        for name, correctly_answered in (('correct', True), ('wrong', False)):
            group = [row for row in numeric if (row['benchmark_state'] == 'correct') == correctly_answered]
            retained = sum(row['stages'][stage]['published'] for row in group)
            result[f'{stage}_{name}_retained'] = retained
            result[f'{stage}_{name}_withheld'] = len(group) - retained
    return result


def flattened(row):
    return {
        **{key: row[key] for key in ('run_id', 'group', 'repetition', 'status', 'event_id', 'task_id',
                                      'slot_id', 'answerable', 'benchmark_state', 'correct_and_reproducible',
                                      'emitted_number', 'structured_refusal', 'same_context_number_correct')},
        'answer_value': (row['report_claim'] or {}).get('value'),
        'answer_unit': (row['report_claim'] or {}).get('unit'),
        'reference_value': row['reference'].get('result', {}).get('value'),
        'reference_unit': row['reference'].get('result', {}).get('unit'),
        'reference_refusal_reason': row['reference'].get('reason'),
        'raw_replay_reason': row['raw_replay']['reason'],
        'v1_check': (row['verifier_v1'] or {}).get('verdict'),
        'v2_check': (row['verifier_v2'] or {}).get('verdict'),
        **{f'{name}_{key}': value for name, stage in row['stages'].items() for key, value in stage.items()},
        'independent_arithmetic_reason': (row['arithmetic'] or {}).get('reason'),
    }


def decimal_text(value):
    with localcontext() as context:
        context.prec = 50
        return format(Decimal(value.numerator) / Decimal(value.denominator), 'f')


def denominator_case(primary, secondary):
    suite = read_json(AUDIT / 'arithmetic_faults/results.json')
    guard = read_json(AUDIT / 'arithmetic_faults/publication_guard_results.json')
    task_id, slot_id = 'R01', 'S1'
    description = 'omit_usdt_from_denominator'
    case = next(row for row in suite['observations'] if (row['task'], row['slot'], row['fault'])
                == (task_id, slot_id, description))
    outcome = next(row for row in guard['observations'] if (row['task'], row['slot'], row['fault'])
                   == (task_id, slot_id, description))
    target = case['correct_target']
    first, second = [next(row for row in source['snapshots'] if row['target_timestamp_utc'] == target)
                     for source in (primary, secondary)]
    for field in ('block_number', 'block_hash', 'raw_balances'):
        if first[field] != second[field]:
            raise ValueError(f'Worked example has conflicting {field}')
    values = {coin: Fraction(int(raw), 10 ** primary['decimals'][coin])
              for coin, raw in first['raw_balances'].items()}
    correct = 100 * values['DAI'] / sum(values.values())
    wrong = 100 * values['DAI'] / (values['DAI'] + values['USDC'])
    if not (equal(decimal_text(correct), case['correct_value'], 'share_pct')
            and equal(decimal_text(wrong), case['fault_value'], 'share_pct')):
        raise ValueError('Worked example did not reproduce archived case')
    return {
        'task_id': task_id, 'slot_id': slot_id, 'fault': description,
        'target_utc': target, 'block_number': first['block_number'], 'block_hash': first['block_hash'],
        'balances': {coin: {'raw_integer': first['raw_balances'][coin], 'decimals': primary['decimals'][coin],
                            'normalized': decimal_text(values[coin])} for coin in values},
        'correct_percent': decimal_text(correct), 'injected_percent': decimal_text(wrong),
        'difference_pp': decimal_text(wrong - correct), 'replayed_outcomes': outcome,
    }


def aggregate(rows, tasks, run_rows):
    output = []
    complete_pairs = {
        (run['task_id'], run['repetition']) for run in run_rows if run['group'] == 'G' and
        run['status'] == 'completed' and any(
            other['task_id'] == run['task_id'] and other['repetition'] == run['repetition']
            and other['group'] == 'E' and other['status'] == 'completed' for other in run_rows)
    }
    events = sorted({task['event_id'] for task in tasks.values()})
    for cohort, members in (
        ('all_scheduled', rows),
        ('complete_pairs', [row for row in rows if (row['task_id'], row['repetition']) in complete_pairs]),
    ):
        for event in ('all', *events):
            for group in ('T', 'G', 'E'):
                block = [row for row in members if row['group'] == group
                         and (event == 'all' or row['event_id'] == event)]
                if block:
                    output.append({'cohort': cohort, 'event': event, 'group': group, **coverage(block)})
    return output


def build(output):
    # The archived replay checks the frozen inputs, all saved reports, and the fault suite.
    replay_stdout = StringIO()
    with redirect_stdout(replay_stdout):
        replay_formal(BASELINE)
    replay_data = json.loads(replay_stdout.getvalue())
    tasks = {task['task_id']: task for task in read_json(PREP / 'tasks.json')}
    primary, secondary = [read_json(PREP / filename) for filename in ('dataset.json', 'secondary_dataset.json')]
    refs = read_json(PREP / 'references.automatic.private.json')
    binding = read_json(PREP / 'binding.json')
    runs = [read_json(FORMAL / (row['run_id'] + '.json')) for row in read_json(FORMAL / 'schedule.json')]
    for task_id in tasks:
        saved = read_json(FORMAL / (task_id + '_T.json'))
        runs.append({'run_id': task_id + '_T', 'group': 'T', 'repetition': None, 'status': 'completed',
                     'task_id': task_id, 'report': saved['report']})
    rows = []
    for run in runs:
        task = tasks[run['task_id']]
        rows.extend(evaluate(run, task, [ref for ref in refs if ref['task_id'] == run['task_id']],
                             primary, secondary, binding['dataset_sha256']))
    for group, counts in replay_data['group_counts'].items():
        computed = coverage([row for row in rows if row['group'] == group])
        for current, historical in (('raw_correct', 'correct'), ('v2_correct_retained', 'published_correct'),
                                     ('answerable', 'answerable'), ('reproducible_correct', 'reproducible_correct')):
            if computed[current] != counts[historical]:
                raise ValueError(f'{group}/{current} did not match formal replay')
    write_csv(output / 'components.csv', [flattened(row) for row in rows])
    write_json(output / 'components.json', rows)
    write_csv(output / 'coverage.csv', aggregate(rows, tasks, runs))
    write_json(output / 'reference-evidence.json', {
        'dataset_sha256': binding['dataset_sha256'],
        'source_bindings': {key: binding[key] for key in ('source_hashes', 'secondary_source_hashes')},
        'tolerances': binding['tolerances'], 'tasks': list(tasks.values()),
        'snapshots': {row['id']: {key: row[key] for key in ('block_number', 'block_hash', 'target_timestamp_utc',
                                                            'raw_balances', 'raw_quotes', 'calldata')}
                      for row in primary['snapshots']}, 'references': refs,
        'provenance': 'Frozen references and historical human review. See baseline audit and attestation.',
    })
    faults = read_json(FORMAL / 'verifier_replay_20261105.json')
    fixtures = {row['case_id']: row for row in read_json(FORMAL / 'fault_batch/frozen_cases.json')['cases']}
    write_csv(output / 'faults.csv', [{'case_id': row['case_id'], 'kind': row['kind'],
                                      'cohort': fault_cohort(row),
                                      'task_id': fixtures[row['case_id']]['task'],
                                      'slot_id': fixtures[row['case_id']]['slot'],
                                      'injection_mode': fixtures[row['case_id']]['mode'],
                                      'v1': row['V1']['verdict'], 'v1_reason': row['V1']['reason'],
                                      'v2': row['V2']['verdict'], 'v2_reason': row['V2']['reason']}
                                     for row in faults['observations']])
    write_json(output / 'denominator-case.json', denominator_case(primary, secondary))
    write_json(output / 'run-register.json', [{
        'run_id': run['run_id'], 'group': run['group'], 'task_id': run['task_id'], 'repetition': run['repetition'],
        'status': run['status'], 'error': run.get('error'), 'requested_model': run.get('model'),
        'returned_models': sorted({call['metadata']['model'] for call in run.get('trace', [])
                                   if call['metadata'].get('model')}),
        'tokens': run.get('ledger', {}).get('actual_usage'),
        'file_sha256': sha256((FORMAL / (run['run_id'] + '.json')).read_bytes()).hexdigest(),
    } for run in runs])
    resource = read_json(AUDIT / 'resource_use_20261109.json')
    timing = read_json(AUDIT / 'cost_trace_20261111/offline_timing.json')
    write_json(output / 'resource-use.json', {
        'historical_model_generation': resource['generation'],
        'historical_rpc_collection': resource['acquisition'],
        'archived_local_verifier_and_publisher_medians_seconds': {
            key: value['median'] for key, value in resource['offline_timing']['seconds'].items()
        },
        'archived_serial_offline_median_seconds': timing['median_elapsed_seconds'],
        'historical_prose_review_and_adjudication_person_minutes': 420,
        'historical_actual_charges': None,
        'historical_live_end_to_end_time': None,
    })
    write_json(output / 'formal-replay.json', replay_data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metrics-only', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error('Choose a new output directory')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as temp:
        stage = Path(temp) / 'study'
        stage.mkdir()
        build(stage)
        if not args.metrics_only:
            from plots import render
            render(stage)
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError(args.output)
        stage.rename(args.output)


if __name__ == '__main__':
    main()
