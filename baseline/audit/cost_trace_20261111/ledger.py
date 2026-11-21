"""Record wall time, provider attempts, and evidenced charges for a run."""

from collections import Counter, defaultdict
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
import re
from time import perf_counter


def _seconds(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError('elapsed seconds must be a finite nonnegative number')
    return value


class RunLedger:
    """One ledger per run. Use one writer and keep provider IDs free of credentials."""

    def __init__(self, run_id, mode):
        self.run_id = run_id
        self.mode = mode
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started = perf_counter()
        self.events = []

    @contextmanager
    def phase(self, name):
        start = perf_counter()
        status = 'ok'
        try:
            yield
        except BaseException:
            status = 'error'
            raise
        finally:
            self.events.append({'kind': 'phase', 'name': name, 'status': status,
                                'seconds': perf_counter() - start})

    def attempt(self, kind, provider, operation_id, number, outcome, *, seconds=None, usage=None):
        """Record a dispatched RPC or model attempt. A retry reuses the operation ID."""
        if kind not in ('rpc', 'model') or type(number) is not int or number < 1:
            raise ValueError('record an rpc or model attempt with a positive integer attempt number')
        if not provider or not operation_id or not outcome:
            raise ValueError('provider, operation ID and outcome are required')
        if usage is not None:
            if (kind != 'model' or not isinstance(usage, Mapping)
                    or not {'input_tokens', 'output_tokens'} <= usage.keys()
                    or set(usage) - {'input_tokens', 'output_tokens', 'cached_input_tokens'}):
                raise ValueError('model usage requires input and output tokens and allows cached input tokens')
            if any(type(tokens) is not int or tokens < 0 for tokens in usage.values()):
                raise ValueError('usage tokens must be nonnegative integers')
            if usage.get('cached_input_tokens', 0) > usage['input_tokens']:
                raise ValueError('cached input tokens exceed total input tokens')
        self.events.append({'kind': kind, 'provider': provider, 'operation_id': operation_id,
                            'attempt': number, 'outcome': outcome, 'seconds': _seconds(seconds),
                            'usage': None if usage is None else dict(usage)})

    def charge(self, provider, amount, currency, evidence_id):
        """Record an allocated, actual charge with its receipt reference."""
        if not isinstance(amount, str):
            raise ValueError('record a decimal amount as text')
        try:
            value = Decimal(amount)
        except InvalidOperation as exc:
            raise ValueError('charge amount is not decimal text') from exc
        if (not provider or not evidence_id or not isinstance(currency, str)
                or not re.fullmatch(r'[A-Z]{3}', currency) or not value.is_finite() or value < 0):
            raise ValueError('a nonnegative actual charge, currency and evidence ID are required')
        self.events.append({'kind': 'charge', 'provider': provider, 'amount': format(value, 'f'),
                            'currency': currency, 'evidence_id': evidence_id})

    def snapshot(self):
        wall = perf_counter() - self.started
        counts = Counter()
        known_usage = Counter()
        phase_seconds = defaultdict(float)
        charges = defaultdict(Decimal)
        for event in self.events:
            kind = event['kind']
            if kind == 'phase':
                phase_seconds[event['name']] += event['seconds']
            elif kind in ('rpc', 'model'):
                counts[f'{kind}_attempts'] += 1
                counts[f'{kind}_retries'] += event['attempt'] > 1
                if kind == 'model':
                    if event['usage'] is None:
                        counts['model_attempts_without_usage'] += 1
                    else:
                        known_usage.update(event['usage'])
                        counts['model_receipts_without_cache_count'] += 'cached_input_tokens' not in event['usage']
            else:
                charges[event['currency']] += Decimal(event['amount'])
        return {
            'run_id': self.run_id, 'mode': self.mode, 'started_at_utc': self.started_at,
            'elapsed_seconds': wall, 'phase_seconds': dict(phase_seconds),
            'attempt_counts': {name: counts[name] for name in (
                'rpc_attempts', 'rpc_retries', 'model_attempts', 'model_retries',
                'model_attempts_without_usage', 'model_receipts_without_cache_count',
            )},
            'known_model_usage': dict(known_usage),
            'recorded_charges_by_currency': {key: format(value, 'f') for key, value in charges.items()},
            'events': self.events.copy(),
        }
