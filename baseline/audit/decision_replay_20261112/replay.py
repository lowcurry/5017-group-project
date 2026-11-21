"""Replay downstream decisions against frozen reports and raw Curve quotes."""

from argparse import ArgumentParser
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys


RESEARCH_RULES = {
    'R02': {'S1': 'share_direction', 'S2': 'peak_follow_up_hour'},
    'R03': {'S2': 'concentration_alert'},
    'R05': {'S1': 'share_direction', 'S2': 'peak_follow_up_hour'},
    'R06': {'S2': 'concentration_alert'},
}
QUOTE_HAIRCUTS_BPS = (1, 5, 10, 25, 50)
ALERT_CHANGE_PP = Decimal('1')
PROXY_SIZE = 1_000
ORDER_SIZE = 100_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_bytes())


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


def action(rule, claim):
    if claim is None:
        return None
    expected_unit = 'dimensionless' if rule == 'peak_follow_up_hour' else 'percentage_points'
    if claim.get('status') != 'supported' or claim.get('unit') != expected_unit:
        return None
    try:
        value = Decimal(str(claim.get('value')))
        if not value.is_finite():
            return None
        if rule == 'peak_follow_up_hour':
            hour = claim.get('target_timestamp_utc')
            if isinstance(hour, str) and datetime.strptime(hour, '%Y-%m-%dT%H:00:00Z').strftime('%Y-%m-%dT%H:00:00Z') == hour:
                return hour
            return None
    except (ValueError, InvalidOperation):
        return None
    if rule == 'share_direction':
        return 'increasing' if value > 0 else 'decreasing' if value < 0 else 'unchanged'
    return 'escalate' if value >= ALERT_CHANGE_PP else 'no_alert'


def outcome(decision, expected):
    if decision is None:
        return 'deferred'
    return 'correct' if decision == expected else 'wrong'


def research_rows(formal, expected_schedule, tasks, references, freeze_hash):
    publication = read_json(formal / 'publication_replay.json')
    reports = {row['run_id']: row for row in publication['reports']}
    schedule = read_json(formal / 'schedule.json')
    require(publication['freeze_sha256'] == freeze_hash, 'Publication refers to another freeze')
    require(len(schedule) == len(reports) == len(publication['reports']) == 36
            and {row['run_id']: row for row in schedule} == expected_schedule
            and reports.keys() == expected_schedule.keys(), 'Schedule or publication is incomplete')
    ref_by_slot = {(ref['task_id'], ref['slot_id']): ref for ref in references}
    rows = []
    for entry in sorted(schedule, key=lambda item: item['run_id']):
        run_id, task_id = entry['run_id'], entry['task_id']
        path = formal / f'{run_id}.json'
        archive = reports[run_id]
        require(file_hash(path) == archive['original_run_sha256'], f'Raw report hash mismatch for {run_id}')
        run = read_json(path)
        require(all(run[key] == val for key, val in entry.items()), f'Run metadata mismatch for {run_id}')
        raw = {claim['slot_id']: claim for claim in run['report']['claims']}
        published = {claim['slot_id']: claim for claim in archive['published']['claims']}
        require(len(raw) == len(run['report']['claims']) and len(published) == len(archive['published']['claims'])
                and published.keys() <= raw.keys(), f'Invalid claim slots for {run_id}')
        withdrawals = {item['claim_id']: item['reason'] for item in archive['published']['withdrawals']}
        score = {item['slot_id']: item['state'] for item in run['score']['slots']}
        for slot, rule in RESEARCH_RULES.get(task_id, {}).items():
            ref = ref_by_slot[(task_id, slot)]
            require(ref['answerable'], f'Expected an answerable benchmark for {task_id}/{slot}')
            expected = action(rule, {**ref['result'], 'status': 'supported'})
            require(expected is not None, f'Invalid benchmark for {task_id}/{slot}')
            raw_claim = raw.get(slot)
            before = action(rule, raw_claim)
            after = action(rule, published.get(slot))
            require(after is None or after == before, f'Publisher changed the action for {run_id}/{slot}')
            rows.append({
                **entry, 'event_id': tasks[task_id]['event_id'], 'slot_id': slot, 'rule': rule,
                'expected_action': expected, 'raw_action': before, 'verified_action': after,
                'raw_outcome': outcome(before, expected), 'verified_outcome': outcome(after, expected),
                'raw_value': raw_claim['value'] if raw_claim else None,
                'reference_value': ref['result']['value'], 'formal_raw_slot_score': score[slot],
                'withdrawal_reason': withdrawals.get(raw_claim['claim_id']) if raw_claim else None,
            })
    require(len(rows) == 36, 'Expected all 36 scheduled research decisions')
    return rows


def research_counts(rows):
    before = Counter(row['raw_outcome'] for row in rows)
    after = Counter(row['verified_outcome'] for row in rows)
    return {
        'decisions': len(rows),
        'raw': {name: before[name] for name in ('correct', 'wrong', 'deferred')},
        'verified': {name: after[name] for name in ('correct', 'wrong', 'deferred')},
        'correct_sent_to_review': before['correct'] - after['correct'],
        'wrong_sent_to_review': before['wrong'] - after['wrong'],
        'additional_reviews': after['deferred'] - before['deferred'],
    }


def usdt(amount_in_microunits):
    return f'{Decimal(amount_in_microunits) / 1_000_000:.6f}'


def quote_cases(primary, secondary, event_windows, shift):
    require(primary['decimals']['USDT'] == secondary['decimals']['USDT'] == 6, 'Expected six USDT decimals')
    primary_rows = {row['target_timestamp_utc']: row for row in primary['snapshots']}
    secondary_rows = {row['target_timestamp_utc']: row for row in secondary['snapshots']}
    cases = []
    for event, (start, end) in sorted(event_windows.items()):
        require(shift(start, 24) == end, f'Expected a 24-hour window for {event}')
        for offset in range(25):
            target = shift(start, offset)
            row, other = primary_rows[target], secondary_rows[target]
            require(all(row[key] == other[key] for key in ('block_number', 'block_hash', 'raw_quotes')),
                    f'Second source quote does not agree at {target}')
            quote = row['raw_quotes']['USDC_USDT']
            small, large = int(quote[str(PROXY_SIZE)]), int(quote[str(ORDER_SIZE)])
            require(small > 0 and large > 0, f'Nonpositive quote at {target}')
            proxy = small * (ORDER_SIZE // PROXY_SIZE)
            cases.append({
                'event_id': event, 'target_timestamp_utc': target, 'block_number': row['block_number'],
                'snapshot_id': row['id'], 'small_quote_usdt': usdt(small),
                'extrapolated_output_usdt': usdt(proxy), 'size_matched_output_usdt': usdt(large),
                'extrapolation_error_usdt': usdt(proxy - large),
            })
    require(len(cases) == 50 and len({row['snapshot_id'] for row in cases}) == 50,
            'Expected 50 distinct historical targets')
    return cases


def quote_counts(cases, haircut_bps):
    floor = Decimal(ORDER_SIZE) * (Decimal(1) - Decimal(haircut_bps) / 10_000)
    false_eligibilities, missed_eligibilities = [], []
    proxy_eligible = matched_eligible = 0
    for case in cases:
        proxy = Decimal(case['extrapolated_output_usdt']) >= floor
        matched = Decimal(case['size_matched_output_usdt']) >= floor
        proxy_eligible += proxy
        matched_eligible += matched
        if proxy and not matched:
            shortfall = floor - Decimal(case['size_matched_output_usdt'])
            false_eligibilities.append({'snapshot_id': case['snapshot_id'], 'shortfall_usdt': f'{shortfall:.6f}'})
        if matched and not proxy:
            missed_eligibilities.append(case['snapshot_id'])
    maximum = max((Decimal(row['shortfall_usdt']) for row in false_eligibilities), default=None)
    return {
        'haircut_bps': haircut_bps, 'floor_usdt': f'{floor:.6f}', 'snapshots': len(cases),
        'proxy_eligible': proxy_eligible, 'size_matched_eligible': matched_eligible,
        'false_eligibility_count': len(false_eligibilities), 'missed_eligibility_count': len(missed_eligibilities),
        'largest_false_eligibility_shortfall_usdt': f'{maximum:.6f}' if maximum is not None else None,
        'false_eligibilities': false_eligibilities, 'missed_eligibilities': missed_eligibilities,
    }


def quote_error(cases):
    errors = [Decimal(case['extrapolation_error_usdt']) for case in cases]
    largest = max(cases, key=lambda case: abs(Decimal(case['extrapolation_error_usdt'])))
    return {
        'proxy_overestimates': sum(error > 0 for error in errors),
        'proxy_underestimates': sum(error < 0 for error in errors),
        'median_absolute_error_usdt': str(median(abs(error) for error in errors)),
        'largest_absolute_error_case': largest,
    }


def replay(root):
    from curve_audit.current_study import digest, reference_candidates, shift

    formal = root / 'experiments/formal_v1'
    preparation = root / 'results/current_preparation'
    freeze = read_json(formal / 'freeze.json')
    freeze_hash = freeze['freeze_sha256']
    require(digest({key: value for key, value in freeze.items() if key != 'freeze_sha256'}) == freeze_hash,
            'Invalid formal freeze digest')
    inputs = {}
    for file, binding in (
        ('dataset.json', 'dataset_sha256'), ('secondary_dataset.json', 'secondary_dataset_sha256'),
        ('tasks.json', 'tasks_sha256'), ('references.automatic.private.json', 'references_sha256'),
        ('runs.json', 'runs_sha256'),
    ):
        inputs[file] = read_json(preparation / file)
        require(digest(inputs[file]) == freeze['binding'][binding], f'Frozen input digest mismatch for {file}')

    primary, secondary = inputs['dataset.json'], inputs['secondary_dataset.json']
    tasks = {task['task_id']: task for task in inputs['tasks.json']}
    references = reference_candidates(primary, list(tasks.values()))
    require(references == reference_candidates(secondary, list(tasks.values())) == inputs['references.automatic.private.json'],
            'Recomputed references do not match both sources and the frozen reference')
    model = freeze['config']['model']
    schedule = {f"{model}_{run['run_id']}": {**run, 'run_id': f"{model}_{run['run_id']}", 'model': model}
                for run in inputs['runs.json']}
    decisions = research_rows(formal, schedule, tasks, references, freeze_hash)
    windows = {}
    for task in tasks.values():
        event = task['event_id']
        window = (task['window']['start'], task['window']['end'])
        require(event not in windows or windows[event] == window, f'Inconsistent window for {event}')
        windows[event] = window
    quotes = quote_cases(primary, secondary, windows, shift)
    return {
        'formal_freeze_sha256': freeze_hash,
        'source_digests': {name: digest(value) for name, value in inputs.items()},
        'publication_sha256': file_hash(formal / 'publication_replay.json'),
        'research': {
            'overall': research_counts(decisions),
            'by_group': {group: research_counts([row for row in decisions if row['group'] == group])
                         for group in ('G', 'E')},
            'by_rule': {rule: research_counts([row for row in decisions if row['rule'] == rule])
                        for rule in sorted({row['rule'] for row in decisions})},
            'decisions': decisions,
        },
        'quote_screen': {
            'input_usdc': ORDER_SIZE, 'proxy_input_usdc': PROXY_SIZE,
            'quote_error': quote_error(quotes),
            'overall': [quote_counts(quotes, haircut) for haircut in QUOTE_HAIRCUTS_BPS],
            'by_event': {event: [quote_counts([row for row in quotes if row['event_id'] == event], haircut)
                                 for haircut in QUOTE_HAIRCUTS_BPS] for event in windows},
            'quotes': quotes,
        },
    }


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Root of the extracted submission')
    args = parser.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    print(json.dumps(replay(args.root), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
