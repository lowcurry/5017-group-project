"""Independently check the balance arithmetic used in new publications."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from .current_study import TOLERANCES


TOKEN_DECIMALS = {'DAI': 18, 'USDC': 6, 'USDT': 6}
BALANCE_OPERATIONS = {
    'share_pct': {'first', 'last', 'change', 'maximum', 'max_hourly_change'},
    'imbalance_l1': {'maximum'},
}


def _time(value):
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.utcoffset() != timedelta(0):
        raise ValueError('expected an explicit UTC timestamp')
    return moment.astimezone(timezone.utc)


def _utc(moment):
    return moment.isoformat().replace('+00:00', 'Z')


def _points(dataset, task, start, end):
    """Index allowed targets and prefer the hourly observation over aliases."""
    earliest = _time(task['window']['start'])
    latest = _time(task['window']['end'])
    excluded = {_time(value) for value in task['allowed_evidence']['excluded_targets']}
    if not earliest <= start <= end <= latest:
        raise ValueError('window outside allowed evidence')
    points = {}
    for row in dataset['snapshots']:
        target = _time(row['target_timestamp_utc'])
        if not earliest <= target <= latest or target in excluded:
            continue
        block = row['block_hash'].lower()
        other = points.get(target)
        if other and block != other['block_hash'].lower():
            raise ValueError('conflicting blocks for one target')
        priority = (row.get('series') != 'hourly', row['id'])
        if other is None or priority < (other.get('series') != 'hourly', other['id']):
            points[target] = row
    return dict(sorted(points.items()))


def _shares(row):
    balances = row['raw_balances']
    if balances.keys() != TOKEN_DECIMALS.keys():
        raise ValueError('expected raw balances for all three pool coins')
    amounts = {}
    for coin, decimals in TOKEN_DECIMALS.items():
        raw = balances[coin]
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or len(raw) > 78:
            raise ValueError('invalid raw balance')
        amount = int(raw)
        if str(amount) != raw or amount >= 2**256:
            raise ValueError('invalid raw balance')
        amounts[coin] = Fraction(amount, 10**decimals)
    total = sum(amounts.values())
    if total <= 0:
        raise ValueError('zero total pool balance')
    return {coin: amount / total for coin, amount in amounts.items()}


def _measure(row, metric, coin):
    shares = _shares(row)
    if metric == 'share_pct':
        return 100 * shares[coin]
    # Half the sum of absolute deviations equals the sum of positive deviations.
    third = Fraction(1, 3)
    return sum((share - third for share in shares.values() if share > third), Fraction(0))


def _require_coverage(points, allowed, dataset, start, end):
    if start not in points or end not in points:
        raise ValueError('missing window endpoint')
    minutes = dataset.get('sampling', {}).get('interval_minutes', 60)
    if type(minutes) is not int or minutes <= 0:
        raise ValueError('invalid sampling interval')
    hourly = [target for target, row in allowed.items() if row.get('series') == 'hourly']
    if not hourly:
        if start != end:
            raise ValueError('no hourly observations in window')
        return
    step = timedelta(minutes=minutes)
    anchor = hourly[0]
    steps = max(0, -((anchor - start) // step))
    cursor = anchor + steps * step
    while cursor <= min(end, hourly[-1]):
        if cursor not in points or points[cursor].get('series') != 'hourly':
            raise ValueError('missing hourly observation')
        cursor += step


def _expected(dataset, task, request):
    decimals = dataset['decimals']
    if decimals != TOKEN_DECIMALS or any(type(value) is not int for value in decimals.values()):
        raise ValueError('unexpected token decimals')
    metric, operation = request['metric'], request['operation']
    start, end = _time(request['start']), _time(request['end'])
    allowed = _points(dataset, task, start, end)
    points = {target: row for target, row in allowed.items() if start <= target <= end}
    coin = request.get('coin', 'USDC')
    unit = 'percent' if metric == 'share_pct' else 'dimensionless'
    timestamps = {}
    if operation == 'max_hourly_change':
        hour = timedelta(hours=1)
        targets = list(points)
        pairs = [(left, right) for left, right in zip(targets, targets[1:]) if right - left == hour]
        if not pairs:
            raise ValueError('no allowed adjacent hourly observations')
        candidates = {target for pair in pairs for target in pair}
        levels = {target: _measure(points[target], metric, coin)
                  for target in candidates}
        left, right = max(pairs, key=lambda pair: levels[pair[1]] - levels[pair[0]])
        value = levels[right] - levels[left]
        timestamps = {'interval_start': _utc(left), 'interval_end': _utc(right)}
        evidence = levels
        unit = 'percentage_points'
    elif operation == 'change':
        value = _measure(points[end], metric, coin) - _measure(points[start], metric, coin)
        unit = 'percentage_points'
        evidence = (start, end)
    else:
        _require_coverage(points, allowed, dataset, start, end)
        # A block sampled more than once contributes once at its earliest target.
        unique = {}
        for target, row in points.items():
            unique.setdefault(row['block_hash'].lower(), target)
        targets = list(unique.values())
        if operation == 'maximum':
            levels = {target: _measure(points[target], metric, coin) for target in targets}
            # In insertion order, the earliest target wins an exact tie.
            selected = max(levels, key=levels.__getitem__)
            value, evidence = levels[selected], targets
        else:
            selected = targets[0] if operation == 'first' else targets[-1]
            value, evidence = _measure(points[selected], metric, coin), (selected,)
        timestamps = {'target_timestamp_utc': _utc(selected)}
    return value, unit, timestamps, {'E_' + points[target]['id'] for target in evidence}


def _matches(value, expected, metric):
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite() or (decimal and abs(decimal.adjusted()) > 1000):
            return False
        tolerance = TOLERANCES[metric]
        limit = Fraction(tolerance['atol']) + Fraction(tolerance['rtol']) * abs(expected)
        return abs(Fraction(decimal) - expected) <= limit
    except (InvalidOperation, ValueError):
        return False


def check_claim(dataset, task, request, claim):
    """Return a verdict for a covered task request, otherwise None."""
    metric = request['metric']
    if request['operation'] not in BALANCE_OPERATIONS.get(metric, ()):
        return None
    try:
        query = claim['query']
        same_query = (
            all(query[key] == request[key] for key in ('metric', 'operation'))
            and all(_time(query[key]) == _time(request[key]) for key in ('start', 'end'))
            and (metric != 'share_pct' or query.get('coin', 'USDC') == request.get('coin', 'USDC'))
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        same_query = False
    if not same_query:
        return {'verdict': 'FAIL', 'reason': 'independent_arithmetic_query_mismatch'}
    try:
        value, unit, times, evidence = _expected(dataset, task, request)
    except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError) as exc:
        return {'verdict': 'UNKNOWN', 'reason': 'independent_arithmetic_unavailable',
                'detail': str(exc)}
    expected = {'exact_value': str(value), 'unit': unit, **times, 'evidence_ids': sorted(evidence)}
    if claim.get('unit') != unit or not _matches(claim.get('value'), value, metric):
        reason = 'independent_arithmetic_value_or_unit_mismatch'
    elif any(claim.get(field) != target for field, target in times.items()):
        reason = 'independent_arithmetic_time_mismatch'
    elif set(claim.get('evidence_ids', ())) != evidence:
        return {'verdict': 'UNKNOWN', 'reason': 'independent_arithmetic_evidence_mismatch',
                'expected': expected}
    else:
        return {'verdict': 'PASS', 'reason': 'independent_arithmetic_matches', 'expected': expected}
    return {'verdict': 'FAIL', 'reason': reason, 'expected': expected}
