"""Run additional verification cases over subwindows of the frozen observations."""

import argparse
from copy import deepcopy
from decimal import Decimal, localcontext
import json
from pathlib import Path
import re
import sys


def run_extension(root):
    sys.path.insert(0, str(root))
    from curve_audit.budget_guard import BudgetLimits
    from curve_audit.common import read_json, sha256_file
    from curve_audit.current_runner import agent_run
    from curve_audit.current_study import (
        WINDOWS, digest, publish, reference_candidates, replay_claim, score_report,
        shift, template_report,
    )
    from curve_audit.current_verify import bound_recorded_sources, read_study_binding, verify_v2
    from curve_audit.deepseek_adapter import DeepSeekAdapter

    binding = read_study_binding(root / 'results/current_preparation')
    primary, secondary = bound_recorded_sources(binding, root / 'data/current_study')
    primary_hash = binding['dataset_sha256']
    observations = []
    windows = []

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def task_for(case_id, event, start, end, requests, excluded=()):
        return {
            'task_id': case_id, 'event_id': event, 'window': {'start': start, 'end': end},
            'question': 'Deterministic subwindow verification fixture',
            'required_components': [
                {'slot_id': f'S{index}', 'label': request['metric'], 'request': request}
                for index, request in enumerate(requests, 1)
            ],
            'allowed_evidence': {'excluded_targets': list(excluded)},
        }

    def verdict(case, task, report, slot, expected, *, first=primary, second=secondary):
        claim = next(claim for claim in report['claims'] if claim['slot_id'] == slot)
        cid = claim['claim_id']
        v1 = replay_claim(first, task, claim)
        v2 = verify_v2(first, second, task, report, expected_primary_sha256=primary_hash)
        publication, publish_checks = publish(first, task, report, checks_override=v2)
        observation = {
            'task_id': task['task_id'], 'case': case, 'slot': slot,
            'v1': {key: v1[key] for key in ('verdict', 'reason')},
            'v2': {key: v2[cid][key] for key in ('verdict', 'reason')},
            'published': cid in {claim['claim_id'] for claim in publication['claims']},
            'publication_reason': publish_checks[cid]['reason'],
        }
        actual = (observation['v1']['verdict'], observation['v2']['verdict'],
                  observation['v2']['reason'], observation['published'],
                  observation['publication_reason'])
        require(actual == expected, f"{task['task_id']}/{case}: {actual!r} differs from {expected!r}")
        observations.append(observation)

    def shifted_balance(dataset, at):
        changed = deepcopy(dataset)
        row = next(snapshot for snapshot in changed['snapshots'] if snapshot['target_timestamp_utc'] == at)
        decimals = changed['decimals']['USDC']
        raw = int(row['raw_balances']['USDC']) + 1_000_000 * 10 ** decimals
        row['raw_balances']['USDC'] = str(raw)
        with localcontext() as context:
            context.prec = 90
            row['balances']['USDC'] = format(Decimal(raw).scaleb(-decimals), 'f')
        return changed

    accept = ('PASS', 'PASS', 'raw_recomputation_matches', True, 'raw_recomputation_matches')
    for event, first_hour, _ in WINDOWS:
        for offset in (3, 15):
            start, excluded, end = (shift(first_hour, offset + hour) for hour in (0, 3, 6))
            ident = f'X_{event}_{offset:02d}'
            endpoint = {'metric': 'share_pct', 'operation': 'change', 'start': start, 'end': end, 'coin': 'USDC'}
            maximum = {**endpoint, 'operation': 'max_hourly_change'}
            task = task_for(ident, event, start, end, [endpoint, maximum], [excluded])
            clean, _ = template_report(primary, task)
            best = clean['claims'][1]
            require(shift(best['interval_start'], 1) == best['interval_end'] and
                    excluded not in (best['interval_start'], best['interval_end']),
                    f'{ident}: clean hourly interval crosses an excluded sample')
            windows.append({'task_id': ident, 'event_id': event, 'start_utc': start,
                            'end_utc': end, 'excluded_interior_utc': excluded})
            verdict('endpoint_change_with_mask', task, clean, 'S1', accept)
            verdict('adjacent_maximum_with_mask', task, clean, 'S2', accept)
            bridged = deepcopy(clean)
            bridged['claims'][1].update(interval_start=shift(excluded, -1), interval_end=shift(excluded, 1))
            verdict('false_interval_across_mask', task, bridged, 'S2',
                    ('FAIL', 'FAIL', 'interval_start_mismatch', False, 'interval_start_mismatch'))

            # A source can lose one endpoint or disagree on a balance while preserving its block ID.
            missing = {**secondary, 'snapshots': [row for row in secondary['snapshots']
                       if row['target_timestamp_utc'] != end]}
            verdict('missing_secondary_endpoint', task, clean, 'S1',
                    ('PASS', 'UNKNOWN', 'secondary_source_unavailable', False, 'secondary_source_unavailable'),
                    second=missing)
            verdict('secondary_balance_conflict', task, clean, 'S1',
                    ('PASS', 'UNKNOWN', 'source_conflict', False, 'source_conflict'),
                    second=shifted_balance(secondary, end))
            verdict('secondary_change_at_excluded_sample', task, clean, 'S2', accept,
                    second=shifted_balance(secondary, excluded))
            changed = shifted_balance(primary, end)
            altered_report, _ = template_report(changed, task)
            verdict('primary_balance_drift', task, altered_report, 'S1',
                    ('PASS', 'FAIL', 'secondary_source_state_mismatch', False, 'secondary_source_state_mismatch'),
                    first=changed)

            peak = {'metric': 'imbalance_l1', 'operation': 'maximum', 'start': start, 'end': end}
            follow = {'metric': 'quote_rate', 'operation': 'last', 'direction': 'USDC_USDT',
                      'size': 100000, 'time_from_slot': 'S1', 'offset_hours': 1}
            dependency = task_for(ident + '_follow', event, start, end, [peak, follow])
            dependent_report, _ = template_report(primary, dependency)
            verdict('clean_follow_up', dependency, dependent_report, 'S2', accept)
            dependent_report['claims'][0]['value'] = '-9999'
            dependent_report['claims'][1].pop('depends_on')
            verdict('mandatory_dependency_withdrawal', dependency, dependent_report, 'S2',
                    ('PASS', 'PASS', 'raw_recomputation_matches', False, 'withdrawn_dependency'))

    legacy_path = root / 'data/raw/expanded_archive.json'
    legacy_hash = sha256_file(legacy_path)
    require(legacy_hash == 'b2081abc6a21b575ffcadaae0a86925b32a2d932a0e1addcf424b83e4b7ddab4',
            'Legacy archive differs from the recorded sanity-check input')
    legacy = read_json(legacy_path)
    legacy_windows = []
    for day in ('2023-03-09', '2023-03-12'):
        start, end = f'{day}T00:00:00Z', f'{day}T06:00:00Z'
        share = {'metric': 'share_pct', 'operation': 'change', 'start': start, 'end': end, 'coin': 'USDC'}
        quote = {'metric': 'quote_rate', 'operation': 'last', 'start': end, 'end': end,
                 'direction': 'USDC_USDT', 'size': 1000}
        task = task_for(f'X_legacy_{day}', 'usdc_2023_legacy', start, end, [share, quote])
        report, _ = template_report(legacy, task)
        legacy_windows.append({'task_id': task['task_id'], 'start_utc': start, 'end_utc': end})
        single_source = ('PASS', 'UNKNOWN', 'secondary_source_unavailable', False, 'secondary_source_unavailable')
        verdict('single_source_endpoint_change', task, report, 'S1', single_source, first=legacy, second=None)
        verdict('single_source_spot_quote', task, report, 'S2', single_source, first=legacy, second=None)

    refusal_checks = []
    for event, start, end in WINDOWS:
        query = {'metric': 'continuous_imbalance_peak', 'operation': 'maximum', 'start': start, 'end': end}
        task = task_for(f'X_{event}_refusal', event, start, end, [query])
        refs = reference_candidates(primary, [task])
        require(len(refs) == 1 and not refs[0]['answerable'] and
                refs[0]['reason'] == 'sampling_resolution_insufficient', 'Refusal reference changed')
        reason = refs[0]['reason']
        baseline, _ = template_report(primary, task)
        for group in ('G', 'E'):
            for case, explanation, expected in (
                ('literal_reason', reason, 1),
                ('prose_only', '小时采样无法确定连续时间峰值。', 0),
            ):
                report = deepcopy(baseline)
                report['answer'] = '小时采样无法确定连续时间峰值。'
                report['claims'][0]['explanation'] = explanation
                requested = []

                def transport(request):
                    prompt = request['messages'][0]['content']
                    codes = re.findall(r'explanation to the exact code ([a-z_]+)', prompt)
                    requested.extend(codes)
                    return {'model': 'deepseek-flash', 'usage': {'prompt_tokens': 100, 'completion_tokens': 25},
                            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                                'action': 'submit', 'report_json': json.dumps(report, ensure_ascii=False)})}}]}

                run = agent_run(primary, task, group, DeepSeekAdapter(transport=transport),
                                BudgetLimits(30000, 500, 30500, 1), per_call_output=500)
                require(run['status'] == 'completed', f"{event}/{group}/{case}: {run['error']}")
                count = score_report(primary, task, run['report'], refs)['counts']['justified_refusal']
                require(requested == [reason] and count == expected, f'{event}/{group}/{case}: refusal contract drift')
                refusal_checks.append({'event_id': event, 'group': group, 'case': case,
                                       'prompt_reason': requested[0], 'expected_justified_refusal': expected,
                                       'actual_justified_refusal': count})

    require(digest(primary) == primary_hash and digest(secondary) == binding['secondary_dataset_sha256'],
            'Extension modified a bound source')
    return {'pass': True, 'scope': 'post-study deterministic windows, synthetic faults and mocked refusals',
            'network_used': False, 'new_model_calls': 0, 'primary_dataset_sha256': primary_hash,
            'two_source_subwindows': windows,
            'single_source_archive': {'path': legacy_path.relative_to(root).as_posix(),
                                      'sha256': legacy_hash, 'balance_method': 'pool.balances(i)',
                                      'windows': legacy_windows},
            'verification_cases': observations, 'mocked_refusal_checks': refusal_checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2],
                        help='Extracted submission root, or the authoring package directory')
    args = parser.parse_args()
    print(json.dumps(run_extension(args.root.resolve()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
