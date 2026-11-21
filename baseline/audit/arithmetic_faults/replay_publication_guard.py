"""Replay the six P2 pairs through the report verification command."""
import argparse
from contextlib import nullcontext, redirect_stdout
from copy import deepcopy
from decimal import Decimal, localcontext
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


def injected_fault(verification, fault):
    if fault == 'minimum_instead_of_maximum':
        def choose(items, *args, **kwargs):
            selector = min if kwargs.get('key') is not None else max
            return selector(items, *args, **kwargs)
        return patch.object(verification, 'max', choose, create=True)

    original = verification._metric

    def altered_metric(snapshot, query):
        if (query['metric'], query.get('coin')) != ('share_pct', 'DAI'):
            return original(snapshot, query)
        decimals = {'DAI': 18, 'USDC': 6, 'USDT': 6}
        if fault == 'omit_usdt_from_denominator':
            del decimals['USDT']
        elif fault == 'dai_6_instead_of_18_decimals':
            decimals['DAI'] = 6
        else:
            raise ValueError(f'Unexpected fault: {fault}')
        with localcontext() as context:
            context.prec = 80
            amounts = {coin: Decimal(snapshot['raw_balances'][coin]).scaleb(-scale)
                       for coin, scale in decimals.items()}
            return amounts['DAI'] / sum(amounts.values()) * 100

    return patch.object(verification, '_metric', altered_metric)


def altered_report(primary, task, report, case):
    from curve_audit.current_study import allowed_data, compute, resolve

    report = deepcopy(report)
    claim = next(claim for claim in report['claims'] if claim['slot_id'] == case['slot'])
    claim.update(value=case['fault_value'], target_timestamp_utc=case['fault_target'])
    for component in task['required_components']:
        request = component['request']
        if request.get('time_from_slot') == case['slot']:
            dependent = next(item for item in report['claims'] if item['slot_id'] == component['slot_id'])
            query = resolve(request, {case['slot']: claim})
            dependent.update(compute(allowed_data(primary, task), query, independent=True))
    return report


def run(root):
    sys.path.insert(0, str(root.resolve()))
    from curve_audit import verification
    from curve_audit.__main__ import main
    from curve_audit.common import read_json, write_json
    from curve_audit.current_study import template_report
    from curve_audit.current_verify import _source_paths

    baseline = read_json(root / 'audit/arithmetic_faults/results.json')
    prep = root / 'results/current_preparation'
    binding = read_json(prep / 'binding.json')
    tasks = {task['task_id']: task for task in read_json(prep / 'tasks.json')}
    primary = read_json(prep / 'dataset.json')
    source_dir = root / 'data/current_study'
    arguments = ['verify', '--preparation', str(prep)]
    for flag, key in (('--primary-sources', 'source_hashes'), ('--secondary-sources', 'secondary_source_hashes')):
        arguments.extend([flag, *(str(path) for path in _source_paths(source_dir, binding[key]))])
    observations = []
    for case in baseline['observations']:
        task = tasks[case['task']]
        clean, _ = template_report(primary, task)
        wrong = altered_report(primary, task, clean, case)
        cid = next(claim['claim_id'] for claim in clean['claims'] if claim['slot_id'] == case['slot'])
        entry = {field: case[field] for field in ('task', 'slot', 'fault')}
        conditions = ('clean', 'faulty_report', 'shared_verifier_bug')
        for condition in conditions:
            report = clean if condition == 'clean' else wrong
            fault = (injected_fault(verification, case['fault'])
                     if condition == 'shared_verifier_bug' else nullcontext())
            with TemporaryDirectory() as directory, redirect_stdout(StringIO()), fault:
                path = Path(directory)
                write_json(path / 'input.json', report)
                main([*arguments, '--task', case['task'], '--report', str(path / 'input.json'),
                      '--output', str(path / 'verified.json')])
                result = read_json(path / 'verified.json')
            guarded = result['independent_arithmetic'].get(cid)
            published = {claim['claim_id'] for claim in result['published']['claims']}
            dependents = [claim['claim_id'] for claim in report['claims'] if case['slot'] in claim.get('depends_on', [])]
            for verifier in ('V1', 'V2'):
                if result[verifier][cid]['verdict'] != case[condition][verifier]['verdict']:
                    raise ValueError(f'{case["task"]} {condition} did not reproduce {verifier}')
            expected = {'clean': 'PASS', 'faulty_report': None, 'shared_verifier_bug': 'FAIL'}[condition]
            if ((guarded['verdict'] if guarded else None) != expected
                    or (cid in published) != (condition == 'clean')):
                raise ValueError(f'{case["task"]} {condition} produced an unexpected publication decision')
            dependent_checks = {}
            for dependent in dependents:
                reason = result['verification'][dependent]['reason']
                if condition == 'shared_verifier_bug' and (
                        result['V2'][dependent]['verdict'] != 'PASS'
                        or reason != 'withdrawn_dependency' or dependent in published):
                    raise ValueError(f'{case["task"]} did not exercise withdrawal of a passing dependency')
                dependent_checks[dependent] = {
                    'V2': result['V2'][dependent]['verdict'], 'final_reason': reason,
                    'published': dependent in published,
                }
            entry[condition] = {
                'V1': result['V1'][cid]['verdict'], 'V2': result['V2'][cid]['verdict'],
                'independent_arithmetic': guarded,
                'final_reason': result['verification'][cid]['reason'],
                'published_before': case[condition]['published'], 'published_after': cid in published,
                'dependent_claims': dependent_checks,
            }
        observations.append(entry)
    totals = {
        condition: {key: sum(row[condition][key] for row in observations)
                    for key in ('published_before', 'published_after')}
        for condition in conditions
    }
    return {'pairs': len(observations), 'totals': totals, 'observations': observations}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Extracted package directory')
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))
