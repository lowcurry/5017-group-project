"""Compare next-request admission for the observed budget stops without inference."""
from copy import deepcopy
import json
from pathlib import Path
import sys

from curve_audit.budget_guard import BudgetExceeded, BudgetLimits
from curve_audit.common import canonical, read_json, write_json
from curve_audit.current_runner import BASE, EVIDENCE, FINAL_TURN, WINDOW_FIELDS, read_window
from curve_audit.current_study import allowed_data, build_tasks, compute, shift
from curve_audit.deepseek_adapter import DeepSeekAdapter

project = Path(sys.argv[1])
out = Path(sys.argv[2])
prep = project / 'results/current_preparation'
dataset = read_json(prep / 'dataset.json')
tasks = {task['task_id']: task for task in read_json(prep / 'tasks.json')}

# Recover the exact task object seen by the model, including development adjustments.
paths = [*sorted((project / 'experiments/formal_v1').glob('deepseek-flash_*.json')),
         *sorted((prep / 'development_flash').glob('D*.json'))]
adapter = DeepSeekAdapter()
cases = []
for path in paths:
    row = read_json(path)
    if not (row.get('error') or '').startswith('BudgetExceeded:'):
        continue
    last = row['trace'][-1]
    baseline = deepcopy(last['request_messages'])
    task = json.loads(baseline[1]['content'])['task']
    allowed = allowed_data(dataset, task)
    action = json.loads(last['raw_response'])
    baseline.append({'role': 'assistant', 'content': last['raw_response']})
    if action['action'] == 'read_window':
        result = {'snapshots': [
            point for point in allowed['snapshots']
            if action['start'] <= point['target_timestamp_utc'] <= action['end']
        ]}
    elif action['action'] == 'calculate':
        result = compute(allowed, action['query'])
    else:
        raise ValueError('Unrecognized final action: ' + action['action'])
    baseline.append({'role': 'user', 'content': canonical({'tool_result': result})})
    limits = BudgetLimits(**row['ledger']['per_arm_limits'])
    if len(row['trace']) == limits.model_calls - 1:
        baseline.append(FINAL_TURN)
    revised = deepcopy(baseline)
    revised[0]['content'] = BASE + ('\n' + EVIDENCE if row['group'] == 'E' else '')
    windows = []
    for message in revised[2:]:
        if message['role'] != 'user':
            continue
        try:
            content = json.loads(message['content'])
        except ValueError:
            continue
        snapshots = content.get('tool_result', {}).get('snapshots')
        if snapshots is None:
            continue
        before = len(message['content'].encode())
        content['tool_result']['snapshots'] = [
            {field: point[field] for field in WINDOW_FIELDS} for point in snapshots
        ]
        message['content'] = canonical(content)
        windows.append({'snapshots': len(snapshots), 'before_bytes': before,
                        'after_bytes': len(message['content'].encode())})

    ledger = adapter.new_ledger(row['group'], limits)
    ledger.calls = deepcopy(row['ledger']['calls'])
    output_cap = ledger.calls[-1]['output_cap']
    before = adapter.input_upper_bound(baseline)
    after = adapter.input_upper_bound(revised)
    try:
        ledger.check_capacity(before, output_cap)
    except BudgetExceeded:
        pass
    else:
        raise AssertionError('Original request should be blocked: ' + path.name)
    ledger.check_capacity(after, output_cap)
    cases.append({
        'run': path.stem, 'source': str(path.relative_to(project)), 'windows': windows,
        'recorded_input_tokens': ledger.totals()['input_tokens'],
        'input_limit': limits.input_tokens, 'next_request_bound_before': before,
        'next_request_bound_after': after, 'next_request_admitted_after': True,
    })

assert len(cases) == 3
write_json(out, {'cases': cases,
                 'scope': 'The same recorded prior usage and tool choices. Admission only, with no model inference.'})
for case in cases:
    print(case['run'], case['next_request_bound_before'], '->', case['next_request_bound_after'])
