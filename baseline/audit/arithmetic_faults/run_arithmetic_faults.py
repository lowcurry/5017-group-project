"""Measure reported arithmetic errors and errors shared by both verifiers."""
import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from pathlib import Path
import sys
from unittest.mock import patch


def number(value):
    with localcontext() as context:
        context.prec = 80
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def share(row, coin, decimals):
    balances = {name: Fraction(int(raw), 10 ** decimals[name])
                for name, raw in row['raw_balances'].items()}
    return balances[coin] / sum(balances.values())


def run(root):
    sys.path.insert(0, str(root.resolve()))
    from curve_audit import verification
    from curve_audit.common import read_json
    from curve_audit.current_study import allowed_data, digest, equal, publish, replay_claim, template_report
    from curve_audit.current_verify import bound_recorded_sources, read_study_binding, verify_v2

    prep = root / 'results/current_preparation'
    binding = read_study_binding(prep)
    tasks = read_json(prep / 'tasks.json')
    if digest(tasks) != binding['tasks_sha256']:
        raise ValueError('Frozen tasks digest mismatch')
    primary, secondary = bound_recorded_sources(binding, root / 'data/current_study')
    decimals = primary['decimals']
    wrong_decimals = {**decimals, 'DAI': 6}
    original_metric = verification._metric
    observations = []

    def share_mutation(snapshot, query):
        if query['metric'] == 'share_pct' and query['coin'] == 'DAI':
            raw = snapshot['raw_balances']
            with localcontext() as context:
                context.prec = 80
                dai = Decimal(raw['DAI']).scaleb(-decimals['DAI'])
                usdc = Decimal(raw['USDC']).scaleb(-decimals['USDC'])
                return dai / (dai + usdc) * 100
        return original_metric(snapshot, query)

    def scale_mutation(snapshot, query):
        if query['metric'] == 'share_pct' and query['coin'] == 'DAI':
            raw = snapshot['raw_balances']
            with localcontext() as context:
                context.prec = 80
                dai = Decimal(raw['DAI']).scaleb(-6)
                other = sum(Decimal(raw[name]).scaleb(-decimals[name]) for name in ('USDC', 'USDT'))
                return dai / (dai + other) * 100
        return original_metric(snapshot, query)

    def invert_key_max(items, *args, **kwargs):
        # The window-bound checks use max without a key. Change only the metric selection.
        return (min if kwargs.get('key') is not None else max)(items, *args, **kwargs)

    def peak(snapshot):
        return sum(abs(share(snapshot, name, decimals) - Fraction(1, 3)) for name in decimals) / 2

    for task in tasks:
        required = task['task_id']
        if required not in {'R01', 'R02', 'R04', 'R05'}:
            continue
        report, _ = template_report(primary, task)
        available = allowed_data(primary, task)['snapshots']
        if required in {'R01', 'R04'}:
            claim = report['claims'][0]
            row = next(s for s in available if s['target_timestamp_utc'] == claim['query']['start'])
            oracle = (share(row, 'DAI', decimals) * 100, row['target_timestamp_utc'])
            variants = [
                ('omit_usdt_from_denominator', share(row, 'DAI', decimals) * 100
                 / (1 - share(row, 'USDT', decimals)),
                 row['target_timestamp_utc'], '_metric', share_mutation),
                ('dai_6_instead_of_18_decimals', share(row, 'DAI', wrong_decimals) * 100,
                 row['target_timestamp_utc'], '_metric', scale_mutation),
            ]
        else:
            claim = report['claims'][1]
            window = [s for s in available if claim['query']['start'] <= s['target_timestamp_utc'] <= claim['query']['end']]
            best = max(window, key=peak)
            worst = min(window, key=peak)
            oracle = (peak(best), best['target_timestamp_utc'])
            variants = [('minimum_instead_of_maximum', peak(worst), worst['target_timestamp_utc'], 'max', invert_key_max)]

        metric = claim['query']['metric']

        def matches(item):
            return equal(item['value'], number(oracle[0]), metric) and item['target_timestamp_utc'] == oracle[1]

        def observe(item):
            cid = item['claim_id']
            partial = {'task_id': required, 'answer': '', 'claims': [item]}
            v1 = replay_claim(primary, task, item)
            v2 = verify_v2(primary, secondary, task, partial,
                           expected_primary_sha256=binding['dataset_sha256'], allow_partial=True)[cid]
            full = deepcopy(report)
            full['claims'] = [item if entry['claim_id'] == cid else entry for entry in full['claims']]
            full_checks = verify_v2(primary, secondary, task, full, expected_primary_sha256=binding['dataset_sha256'])
            published, _ = publish(primary, task, full, checks_override=full_checks)
            return {'V1': {k: v1[k] for k in ('verdict', 'reason')},
                    'V2': {k: v2[k] for k in ('verdict', 'reason')},
                    'published': any(entry['claim_id'] == cid for entry in published['claims']),
                    'oracle_agrees': matches(item)}

        if not matches(claim):
            raise ValueError(f'Independent rational reference disagrees with control for {required}')
        for kind, bad, target, name, replacement in variants:
            bad_claim = {**claim, 'value': number(bad), 'target_timestamp_utc': target}
            clean = observe(claim)
            fault = observe(bad_claim)
            with patch.object(verification, name, replacement, create=name == 'max'):
                shared = observe(bad_claim)
            observations.append({'task': required, 'event': task['event_id'], 'slot': claim['slot_id'],
                                 'fault': kind, 'correct_value': number(oracle[0]), 'fault_value': number(bad),
                                 'correct_target': oracle[1], 'fault_target': target,
                                 'clean': clean, 'faulty_report': fault, 'shared_verifier_bug': shared})
    totals = {}
    for condition in ('clean', 'faulty_report', 'shared_verifier_bug'):
        entries = [row[condition] for row in observations]
        totals[condition] = {layer: dict(sorted(Counter(entry[layer]['verdict'] for entry in entries).items()))
                             for layer in ('V1', 'V2')}
        totals[condition].update(published=sum(entry['published'] for entry in entries),
                                 oracle_disagreements=sum(not entry['oracle_agrees'] for entry in entries))
    return {'pairs': len(observations), 'totals': totals, 'observations': observations}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Extracted package directory')
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))
