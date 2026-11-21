"""Derive comparable measures from the frozen study and its follow-up replays."""
from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare_run_sets import compare


FORMAL = 'experiments/formal_v1'
SUMMARY = f'{FORMAL}/result_summary.json'
DECISIONS = 'audit/decision_replay_20261112/results.json'
GUARD = 'audit/arithmetic_faults/publication_guard_results.json'
VERIFIER = f'{FORMAL}/verifier_replay_20261105.json'
RESOURCES = 'audit/resource_use_20261109.json'
OUTCOMES = ('correct', 'wrong', 'deferred')
COVERAGE = ('correct', 'reproducible_correct', 'published_correct')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def quantile(values, proportion):
    """Linearly interpolate at zero-based index (n - 1) * proportion."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    index = int(position)
    return ordered[index] + (position - index) * (ordered[min(index + 1, len(ordered) - 1)] - ordered[index])


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
    paired = compare(root, include_pairs=True)
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

    task_ids = sorted({pair['task_id'] for pair in paired['pairs']})
    for cohort in ('scheduled', 'complete_pairs'):
        cohort_pairs = [pair for pair in paired['pairs'] if cohort == 'scheduled' or pair['completed']]
        require(all(pair['groups']['G']['answerable'] == pair['groups']['E']['answerable']
                    for pair in cohort_pairs), 'Pair members must have the same answerable denominator.')
        by_scope = {'overall': cohort_pairs}
        for field in ('event_id', 'task_id'):
            for scope in sorted({pair[field] for pair in cohort_pairs}):
                by_scope[scope] = [pair for pair in cohort_pairs if pair[field] == scope]
        for scope, pairs in by_scope.items():
            for name in COVERAGE:
                deltas = [pair['groups']['E'][name] - pair['groups']['G'][name] for pair in pairs]
                outcomes = Counter('tie' if delta == 0 else 'e_win' if delta > 0 else 'g_win' for delta in deltas)
                for outcome in ('e_win', 'tie', 'g_win'):
                    metric('paired_comparison', cohort, scope, 'E vs G', name + '_' + outcome,
                           numerator=outcomes[outcome], denominator=len(pairs))
                metric('paired_comparison', cohort, scope, 'E minus G', name + '_mean_gap',
                       numerator=sum(deltas), denominator=len(pairs), unit='components/pair')
            if scope in task_ids:
                for group in ('G', 'E'):
                    for name in COVERAGE:
                        total = sum(pair['groups'][group]['answerable'] for pair in pairs)
                        metric('formal', cohort, scope, group, name,
                               numerator=sum(pair['groups'][group][name] for pair in pairs), denominator=total)
        for ignored in task_ids:
            remaining = [pair for pair in cohort_pairs if pair['task_id'] != ignored]
            denominator = sum(pair['groups']['G']['answerable'] for pair in remaining)
            if denominator:
                delta = sum(pair['groups']['E']['reproducible_correct']
                            - pair['groups']['G']['reproducible_correct'] for pair in remaining)
                metric('paired_comparison', cohort, 'drop_' + ignored, 'E minus G',
                       'reproducible_correct_delta', value=round(100 * delta / denominator, 6), unit='pp')

    resources = read(RESOURCES)['generation']
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
        usage = resources[group]
        require(row['tokens'] == usage['total_tokens'] == usage['input_tokens'] + usage['output_tokens']
                and row['model_calls'] == usage['model_calls'], f'Usage totals differ for {group}.')
        for key in ('input_tokens', 'output_tokens'):
            formal('model_' + key, value=usage[key], unit='tokens')
        formal('cached_input_token_share', numerator=usage['cached_input_tokens'], denominator=usage['input_tokens'])
        formal('tokens_per_scheduled_run', numerator=row['tokens'],
               denominator=row['scheduled_runs'], unit='tokens/run')
        formal('cumulative_recorded_run_time', value=round(usage['wall_seconds'], 3), unit='s')
        formal('budget_stop_rate', numerator=row['pre_dispatch_budget_stops'], denominator=row['scheduled_runs'])
        formal('correct_withdrawn', numerator=row['correct_claims_withdrawn'], denominator=row['correct'])

    g, e = summary['by_group']['G'], summary['by_group']['E']
    for name in ('reproducible_correct', 'published_correct'):
        delta = e[name] - g[name]
        extra_tokens = e['tokens'] - g['tokens']
        metric('formal', 'scheduled', 'overall', 'E minus G', f'incremental_tokens_per_extra_{name}',
               numerator=extra_tokens, denominator=delta if delta > 0 else 0, unit='tokens/component')

    verifier = read(VERIFIER)
    observations = verifier['observations']
    require(len(observations) == verifier['cases'] == len({case['case_id'] for case in observations}),
            'Missing or duplicated frozen verifier cases.')
    for layer in ('V1', 'V2'):
        verdicts = defaultdict(Counter)
        families = defaultdict(Counter)
        expected = verifier['observed_by_layer'][layer]
        for case in observations:
            kind, verdict = case['kind'], case[layer]['verdict']
            group = kind if kind in expected else 'fault'
            verdicts[group][verdict] += 1
            if group == 'fault':
                families[kind][verdict] += 1
        require(verdicts == expected, f'{layer} fault totals differ from the archive.')
        for collection, scope in ((verdicts, 'overall'), (families, 'injected_fault_family')):
            for kind, counts in sorted(collection.items()):
                for verdict in ('PASS', 'FAIL', 'UNKNOWN'):
                    metric('verifier_faults', kind, scope, layer, verdict.lower(),
                           numerator=counts[verdict], denominator=sum(counts.values()))
        faulty, clean = verdicts['fault'], verdicts['clean']
        true_fail = faulty['FAIL']
        false_fail = clean['FAIL']
        for name, count, total in (
            ('strict_fault_recall', true_fail, sum(faulty.values())),
            ('strict_detection_precision', true_fail, true_fail + false_fail),
            ('strict_detection_f1', 2 * true_fail, 2 * true_fail + false_fail + faulty['PASS'] + faulty['UNKNOWN']),
            ('fault_nonacceptance', true_fail + faulty['UNKNOWN'], sum(faulty.values())),
            ('clean_acceptance', clean['PASS'], sum(clean.values())),
        ):
            metric('verifier_faults', 'fault_vs_clean', 'overall', layer, name, numerator=count, denominator=total)

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
            by_fault = defaultdict(list)
            for case in guard['observations']:
                by_fault[case['fault']].append(case[condition][key])
            for fault, outcomes in sorted(by_fault.items()):
                metric('arithmetic_faults', condition, fault, stage, 'published',
                       numerator=sum(outcomes), denominator=len(outcomes))
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
        cohorts[row['rule']].append(row)
    archived = {'overall': research['overall'], **research['by_group'], **research['by_rule']}
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
            metric('research_decisions', 'scheduled', scope, stage, 'automation_coverage',
                   numerator=counts['correct'] + counts['wrong'], denominator=len(decisions))
        for outcome in ('correct', 'wrong'):
            pool = [row for row in decisions if row['raw_outcome'] == outcome]
            count = sum(row['verified_outcome'] == 'deferred' for row in pool)
            require(count == expected[f'{outcome}_sent_to_review'], 'Research review transitions differ.')
            metric('research_decisions', 'scheduled', scope, 'verified', outcome + '_sent_to_review',
                   numerator=count, denominator=len(pool))
        referred = [row for row in decisions if row['raw_outcome'] in ('correct', 'wrong')
                    and row['verified_outcome'] == 'deferred']
        require(len(referred) == expected['additional_reviews'], 'Additional review total differs.')
        metric('research_decisions', 'scheduled', scope, 'verified', 'additional_review_yield',
               numerator=sum(row['raw_outcome'] == 'wrong' for row in referred), denominator=len(referred))

    screen = decision['quote_screen']
    quotes = sorted(screen['quotes'], key=lambda row: (row['event_id'], row['target_timestamp_utc']))
    samples = defaultdict(list)
    relative = defaultdict(list)
    seen.clear()
    for row in quotes:
        key = row['event_id'], row['target_timestamp_utc']
        matched = Decimal(row['size_matched_output_usdt'])
        gap = Decimal(row['extrapolated_output_usdt']) - matched
        require(key not in seen and matched > 0 and gap.is_finite() and gap == Decimal(row['extrapolation_error_usdt']),
                'Quote replay gap or timestamp differs.')
        seen.add(key)
        samples['overall'].append(gap)
        samples[row['event_id']].append(gap)
        for scope in ('overall', row['event_id']):
            relative[scope].append(abs(gap) * 10000 / matched)
    for scope, gaps in sorted(samples.items()):
        metric('quote_screen', 'historical', scope, 'proxy', 'overestimated',
               numerator=sum(gap > 0 for gap in gaps), denominator=len(gaps))
        absolute = list(map(abs, gaps))
        for name, value in (('mean_signed_error', mean(gaps)), ('mean_absolute_error', mean(absolute)),
                            ('median_absolute_error', median(absolute)),
                            ('p90_absolute_error', quantile(absolute, Decimal('.9'))),
                            ('max_absolute_error', max(absolute))):
            metric('quote_screen', 'historical', scope, 'proxy', name, value=float(value), unit='USDT')
        metric('quote_screen', 'historical', scope, 'proxy', 'p90_relative_absolute_error',
               value=round(float(quantile(relative[scope], Decimal('.9'))), 6), unit='bps')
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
        false = sum(proxy and not full for proxy, full in eligible)
        missed = sum(full and not proxy for proxy, full in eligible)
        require(len(eligible) == threshold['snapshots']
                and false == threshold['false_eligibility_count'] and missed == threshold['missed_eligibility_count']
                and sum(proxy for proxy, _ in eligible) == threshold['proxy_eligible']
                and sum(full for _, full in eligible) == threshold['size_matched_eligible'],
                'Quote threshold replay total differs.')
        for name, count in (('decision_disagreement', false + missed), ('false_eligibility', false),
                            ('missed_eligibility', missed)):
            metric('quote_screen', 'historical', f"{threshold['haircut_bps']} bps", 'proxy vs size-matched',
                   name, numerator=count, denominator=len(quotes))

    return {'schema_version': 2, 'formal_freeze_sha256': summary['freeze_sha256'],
            'input_sha256': sources, 'pair_counts': {key: paired[key] for key in ('scheduled_pairs', 'completed_pairs')},
            'metrics': rows, 'pairs': paired['pairs'], 'quote_points': quotes}
