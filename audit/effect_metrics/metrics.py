"""Derive comparable measures from the frozen study and its follow-up replays."""
from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare_run_sets import compare


FORMAL = 'experiments/formal_v1'
SUMMARY = f'{FORMAL}/result_summary.json'
DECISIONS = 'audit/decision_replay_20261112/results.json'
GUARD = 'audit/arithmetic_faults/publication_guard_results.json'
OUTCOMES = ('correct', 'wrong', 'deferred')
COVERAGE = ('correct', 'reproducible_correct', 'published_correct')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def measure(root):
    sources = {}
    rows = []

    def read(name):
        data = (root / name).read_bytes()
        sources[name] = sha256(data).hexdigest()
        return json.loads(data)

    def metric(experiment, cohort, scope, group, name, *, numerator=None, denominator=None,
               value=None, unit='%'):
        if numerator is not None and denominator:
            factor = 100 if unit == '%' else 1
            value = round(factor * numerator / denominator, 6)
        rows.append(dict(experiment=experiment, cohort=cohort, scope=scope, group=group,
                         metric=name, numerator=numerator, denominator=denominator,
                         value=value, unit=unit))

    summary = read(SUMMARY)
    publication = read(f'{FORMAL}/publication_replay.json')
    read(f'{FORMAL}/schedule.json')
    read('results/current_preparation/tasks.json')
    paired = compare(root)
    require(paired['scheduled_pairs'] * 2 == summary['model_outputs'], 'Formal schedule total differs.')
    for cohort, scopes in paired['counts'].items():
        for scope, groups in sorted(scopes.items()):
            for group, row in sorted(groups.items()):
                total = row['answerable']
                require(0 <= row['published_correct'] <= row['reproducible_correct']
                        <= row['correct'] <= total, f'Invalid formal coverage: {cohort}/{scope}/{group}')
                for name in COVERAGE:
                    metric('formal', cohort, scope, group, name,
                           numerator=row[name], denominator=total)
                metric('formal', cohort, scope, group, 'correct_retained',
                       numerator=row['published_correct'], denominator=row['correct'])
            g, e = groups['G'], groups['E']
            for name in COVERAGE:
                if g['answerable'] and e['answerable']:
                    difference = 100 * (e[name] / e['answerable'] - g[name] / g['answerable'])
                    metric('formal', cohort, scope, 'E minus G', name + '_delta',
                           value=round(difference, 6), unit='pp')

    for group, row in sorted(summary['by_group'].items()):
        def formal(name, **fields):
            metric('formal', 'scheduled', 'overall', group, name, **fields)
        formal('completed_runs', numerator=row['successful_submissions'], denominator=row['scheduled_runs'])
        formal('wrong_numeric_filtered', numerator=row['incorrect_numeric_claims_withdrawn'],
               denominator=row['incorrect_numeric_submissions'])
        formal('model_tokens', value=row['tokens'], unit='tokens')
        formal('model_calls', value=row['model_calls'], unit='calls')
        formal('tokens_per_reproducible_correct', numerator=row['tokens'],
               denominator=row['reproducible_correct'], unit='tokens/component')
        formal('tokens_per_published_correct', numerator=row['tokens'],
               denominator=row['published_correct'], unit='tokens/component')

    guard = read(GUARD)
    conditions = ('clean', 'faulty_report', 'shared_verifier_bug')
    require(guard['pairs'] == len(guard['observations']) > 0, 'Unexpected arithmetic replay count.')
    dependent = 0
    dependent_withdrawn = 0
    for condition in conditions:
        for stage in ('before', 'after'):
            key = f'published_{stage}'
            count = sum(case[condition][key] for case in guard['observations'])
            require(count == guard['totals'][condition][key], 'Arithmetic replay total differs.')
            metric('arithmetic_faults', condition, 'overall', stage, 'published',
                   numerator=count, denominator=guard['pairs'])
    for case in guard['observations']:
        for claim in case['shared_verifier_bug']['dependent_claims'].values():
            if claim['V2'] == 'PASS':
                dependent += 1
                dependent_withdrawn += (not claim['published'] and claim['final_reason'] == 'withdrawn_dependency')
    metric('arithmetic_faults', 'shared_verifier_bug', 'overall', 'after', 'dependent_withdrawals',
           numerator=dependent_withdrawn, denominator=dependent)

    decision = read(DECISIONS)
    require(decision['formal_freeze_sha256'] == summary['freeze_sha256'] == publication['freeze_sha256']
            and decision['publication_sha256'] == sources[f'{FORMAL}/publication_replay.json'],
            'Decision replay uses a different formal publication.')
    research = decision['research']
    cohorts = defaultdict(list)
    seen = set()
    for row in research['decisions']:
        key = row['run_id'], row['slot_id']
        require(key not in seen, 'Duplicate research decision.')
        seen.add(key)
        cohorts['overall'].append(row)
        cohorts[row['group']].append(row)
    archived = {'overall': research['overall'], **research['by_group']}
    require(set(cohorts) == set(archived), 'Research replay groups differ.')
    for scope, decisions in sorted(cohorts.items()):
        expected = archived[scope]
        require(len(decisions) == expected['decisions'], 'Research decision total differs.')
        for stage in ('raw', 'verified'):
            counts = Counter(row[f'{stage}_outcome'] for row in decisions)
            require({name: counts[name] for name in OUTCOMES} == expected[stage]
                    and counts.keys() <= set(OUTCOMES),
                    f'Research replay total differs: {scope}/{stage}')
            for outcome in OUTCOMES:
                metric('research_decisions', 'scheduled', scope, stage, outcome,
                       numerator=counts[outcome], denominator=len(decisions))
            metric('research_decisions', 'scheduled', scope, stage, 'automated_accuracy',
                   numerator=counts['correct'], denominator=counts['correct'] + counts['wrong'])
        for outcome in ('correct', 'wrong'):
            pool = [row for row in decisions if row['raw_outcome'] == outcome]
            count = sum(row['verified_outcome'] == 'deferred' for row in pool)
            require(count == expected[f'{outcome}_sent_to_review'], 'Research review transitions differ.')
            metric('research_decisions', 'scheduled', scope, 'verified', outcome + '_sent_to_review',
                   numerator=count, denominator=len(pool))

    screen = decision['quote_screen']
    quotes = sorted(screen['quotes'], key=lambda row: (row['event_id'], row['target_timestamp_utc']))
    samples = defaultdict(list)
    seen.clear()
    for row in quotes:
        key = row['event_id'], row['target_timestamp_utc']
        gap = Decimal(row['extrapolated_output_usdt']) - Decimal(row['size_matched_output_usdt'])
        require(key not in seen and gap.is_finite() and gap == Decimal(row['extrapolation_error_usdt']),
                'Quote replay gap or timestamp differs.')
        seen.add(key)
        samples['overall'].append(gap)
        samples[row['event_id']].append(gap)
    for scope, gaps in sorted(samples.items()):
        metric('quote_screen', 'historical', scope, 'proxy', 'overestimated',
               numerator=sum(gap > 0 for gap in gaps), denominator=len(gaps))
        for name, value in (('median_absolute_error', median(map(abs, gaps))),
                            ('max_absolute_error', max(map(abs, gaps)))):
            metric('quote_screen', 'historical', scope, 'proxy', name, value=float(value), unit='USDT')
    gap_summary = screen['quote_error']
    all_gaps = samples['overall']
    require(sum(gap > 0 for gap in all_gaps) == gap_summary['proxy_overestimates']
            and median(map(abs, all_gaps)) == Decimal(gap_summary['median_absolute_error_usdt'])
            and max(map(abs, all_gaps)) == abs(Decimal(
                gap_summary['largest_absolute_error_case']['extrapolation_error_usdt'])),
            'Quote error summary differs from the recorded observations.')
    for threshold in sorted(screen['overall'], key=lambda row: row['haircut_bps']):
        floor = Decimal(threshold['floor_usdt'])
        eligible = [(Decimal(row['extrapolated_output_usdt']) >= floor,
                     Decimal(row['size_matched_output_usdt']) >= floor) for row in quotes]
        differences = sum(proxy != full for proxy, full in eligible)
        require(len(eligible) == threshold['snapshots']
                and differences == threshold['false_eligibility_count'] + threshold['missed_eligibility_count']
                and sum(proxy for proxy, _ in eligible) == threshold['proxy_eligible']
                and sum(full for _, full in eligible) == threshold['size_matched_eligible'],
                'Quote threshold replay total differs.')
        metric('quote_screen', 'historical', f"{threshold['haircut_bps']} bps", 'proxy vs size-matched',
               'decision_disagreement', numerator=differences, denominator=len(quotes))

    return {'schema_version': 1, 'formal_freeze_sha256': summary['freeze_sha256'],
            'input_sha256': sources, 'pair_counts': {key: paired[key] for key in ('scheduled_pairs', 'completed_pairs')},
            'metrics': rows, 'quote_points': quotes}
