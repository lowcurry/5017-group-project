"""Fail-closed, provider-neutral token reservations and nonduplicated accounting.

This module never invokes a model. An adapter must establish its capabilities,
count the complete request exactly, and apply the returned output cap upstream.
A prompt, subprocess timeout, context size or post-hoc truncation is not a cap.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import math
from threading import RLock
from typing import Mapping, Sequence


class BudgetCapabilityError(RuntimeError):
    """The provider cannot enforce the promised experiment budget."""


class BudgetExceeded(RuntimeError):
    """A request would exceed its physical or attributed arm budget."""


class BudgetStateError(RuntimeError):
    """A call ID, lifecycle transition or dispatch acknowledgement is invalid."""


class BudgetEnforcementViolation(RuntimeError):
    """Actual usage exceeded a reserved upstream bound; it is still recorded."""


def _integer(value: int, name: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < int(positive):
        raise ValueError(f"{name} must be a {'positive' if positive else 'nonnegative'} integer")


def _known_unsupported_provider(provider: str) -> bool:
    """Never let caller-supplied flags upgrade an examined unsupported route."""
    if not isinstance(provider, str):
        return False
    identity = provider.strip().lower().replace("_", "-")
    return (identity in {"codex", "chatgpt-plan", "chatgpt-oauth"}
            or identity.startswith("codex-cli") or identity.startswith("codex-chatgpt"))


@dataclass(frozen=True)
class BudgetCapability:
    provider: str
    exact_input_token_count: bool
    hard_output_token_cap: bool
    output_cap_includes_reasoning: bool
    single_request_control: bool
    complete_usage_reporting: bool
    evidence: tuple[str, ...] = ()

    def missing(self) -> list[str]:
        fields = ("exact_input_token_count", "hard_output_token_cap",
                  "output_cap_includes_reasoning", "single_request_control",
                  "complete_usage_reporting")
        missing = [name for name in fields if getattr(self, name) is not True]
        if not isinstance(self.provider, str) or not self.provider.strip():
            missing.append("valid_provider_identity")
        if _known_unsupported_provider(self.provider):
            missing.append("known_unsupported_provider_route")
        return missing

    def to_dict(self) -> dict:
        result = asdict(self)
        result["hard_token_budget_supported"] = not self.missing()
        result["known_unsupported_provider"] = _known_unsupported_provider(self.provider)
        result["missing_capabilities"] = self.missing()
        return result


def codex_cli_capability() -> BudgetCapability:
    """Conservative, offline declaration for the examined logged-in CLI route.

    Version/help were inspected, without loading auth or performing inference.
    Update this declaration only after new documented capability is verified.
    """
    return BudgetCapability(
        provider="codex-cli-chatgpt-plan",
        exact_input_token_count=False,
        hard_output_token_cap=False,
        output_cap_includes_reasoning=False,
        single_request_control=False,
        complete_usage_reporting=False,
        evidence=(
            "Local codex-cli 0.162.0-alpha.17.2: codex exec --help; no hard token budget option",
            "https://learn.chatgpt.com/docs/developer-commands?surface=cli",
            "https://learn.chatgpt.com/docs/config-file/config-reference",
            "https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations",
        ),
    )


def require_hard_token_budget(capability: BudgetCapability) -> None:
    if not isinstance(capability, BudgetCapability):
        raise BudgetCapabilityError("A trusted adapter factory must supply BudgetCapability")
    missing = capability.missing()
    if missing:
        raise BudgetCapabilityError(
            f"{capability.provider} cannot run a hard equal-token experiment; "
            f"missing: {', '.join(missing)}. Do not substitute a prompt, timeout, "
            "context-window setting or post-hoc usage filter."
        )
    if not capability.evidence:
        raise BudgetCapabilityError("A supported adapter must provide capability evidence")


@dataclass(frozen=True)
class BudgetLimits:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model_calls: int

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            _integer(value, key)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
            _integer(getattr(self, key), key)
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens is a subset of input_tokens")
        if self.reasoning_tokens is not None:
            _integer(self.reasoning_tokens, "reasoning_tokens")
            if self.reasoning_tokens > self.output_tokens:
                raise ValueError("reasoning_tokens is a subset of output_tokens")

    @classmethod
    def from_mapping(cls, value: Mapping) -> "TokenUsage":
        # Missing usage is unknown, never a zero-cost model request.
        if not isinstance(value, Mapping) or not {"input_tokens", "output_tokens"} <= value.keys():
            raise ValueError("Complete input_tokens and output_tokens usage is required")
        input_details = value.get("input_tokens_details")
        output_details = value.get("output_tokens_details")
        input_details = {} if input_details is None else input_details
        output_details = {} if output_details is None else output_details
        if not isinstance(input_details, Mapping) or not isinstance(output_details, Mapping):
            raise ValueError("Usage detail fields must be mappings")
        cached = value.get("cached_input_tokens", input_details.get("cached_tokens", 0))
        if "cached_input_tokens" in value and "cached_tokens" in input_details and cached != input_details["cached_tokens"]:
            raise ValueError("Conflicting cached input token counts")
        result = cls(value["input_tokens"], value["output_tokens"], cached,
                     output_details.get("reasoning_tokens"))
        if "total_tokens" in value:
            _integer(value["total_tokens"], "total_tokens")
            if value["total_tokens"] != result.total_tokens:
                raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return result

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {**asdict(self), "total_tokens": self.total_tokens}


@dataclass(frozen=True)
class Reservation:
    call_id: str
    arms: tuple[str, ...]
    exact_input_tokens: int
    max_output_tokens: int
    phase: str

    def charges(self) -> dict:
        return {"input_tokens": self.exact_input_tokens,
                "output_tokens": self.max_output_tokens,
                "total_tokens": self.exact_input_tokens + self.max_output_tokens,
                "model_calls": 1}


@dataclass(frozen=True)
class ResourceUsage:
    """Resource event measured independently of model token usage.

    wall_seconds is a sum of event durations, not parallel experiment elapsed
    time. billed_usd is an observed charge only, never an inferred CLI price.
    """
    rpc_requests: int = 0
    tool_calls: int = 0
    cpu_seconds: float = 0.0
    wall_seconds: float = 0.0
    billed_usd: str | None = None

    def __post_init__(self) -> None:
        _integer(self.rpc_requests, "rpc_requests")
        _integer(self.tool_calls, "tool_calls")
        for key in ("cpu_seconds", "wall_seconds"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        if self.billed_usd is not None:
            try:
                amount = Decimal(str(self.billed_usd))
            except InvalidOperation as exc:
                raise ValueError("billed_usd must be a recorded decimal amount") from exc
            if not amount.is_finite() or amount < 0:
                raise ValueError("billed_usd must be finite and nonnegative")


def _zero() -> dict:
    return dict(input_tokens=0, output_tokens=0, total_tokens=0, model_calls=0)


def _add(target: dict, source: dict) -> None:
    for key in target:
        target[key] += source[key]


class TokenBudgetLedger:
    """One physical ledger plus full-cost attribution to each audit arm.

    A shared initial draft uses arms=("A", "B", "C") in one reservation. Its
    actual tokens appear once physically and in full in each arm. Do not add the
    arm totals to obtain the experiment's actual usage. Every retry needs a new
    call ID and a new reservation; an unknown-usage timeout blocks further calls.
    """

    def __init__(self, arms: Sequence[str], per_arm_limits: BudgetLimits,
                 capability: BudgetCapability, physical_limits: BudgetLimits | None = None):
        self.arms = tuple(arms)
        if not self.arms or len(set(self.arms)) != len(self.arms) or any(not isinstance(a, str) or not a for a in self.arms):
            raise ValueError("arms must contain unique nonempty names")
        self.per_arm_limits = per_arm_limits
        self.physical_limits = physical_limits
        self.capability = capability
        self._calls: dict[str, dict] = {}
        self._resources: dict[str, dict] = {}
        self._lock = RLock()

    def _arms(self, arms: Sequence[str]) -> tuple[str, ...]:
        result = tuple(arms)
        if not result or len(set(result)) != len(result) or not set(result) <= set(self.arms):
            raise ValueError("A call/event must target unique configured arms")
        return result

    def _totals(self, *, actual_only: bool, arm: str | None = None) -> dict:
        total = _zero()
        for call in self._calls.values():
            reservation = call["reservation"]
            if arm is not None and arm not in reservation.arms:
                continue
            if call["state"] == "cancelled_before_dispatch":
                continue
            if call.get("usage") is not None:
                usage = call["usage"]
                _add(total, {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                             "total_tokens": usage.total_tokens, "model_calls": 1})
            elif not actual_only:
                _add(total, reservation.charges())
        return total

    @staticmethod
    def _check(total: dict, new: dict, limits: BudgetLimits, scope: str) -> None:
        for key, maximum in asdict(limits).items():
            if total[key] + new[key] > maximum:
                raise BudgetExceeded(f"{scope} {key} budget exceeded")

    def reserve(self, call_id: str, arms: Sequence[str], exact_input_tokens: int,
                max_output_tokens: int, phase: str = "audit") -> Reservation:
        with self._lock:
            require_hard_token_budget(self.capability)
            if any(call["state"] in {"unknown_usage", "enforcement_violation"} for call in self._calls.values()):
                raise BudgetStateError("Ledger blocked by unknown usage or upstream enforcement violation")
            if not isinstance(call_id, str) or not call_id or call_id in self._calls:
                raise BudgetStateError("call_id must be new and nonempty, including for retries")
            _integer(exact_input_tokens, "exact_input_tokens")
            _integer(max_output_tokens, "max_output_tokens", positive=True)
            if not isinstance(phase, str) or not phase:
                raise ValueError("phase must be a nonempty name")
            reservation = Reservation(call_id, self._arms(arms), exact_input_tokens, max_output_tokens, phase)
            charges = reservation.charges()
            for arm in reservation.arms:
                self._check(self._totals(actual_only=False, arm=arm), charges, self.per_arm_limits, f"arm {arm}")
            if self.physical_limits is not None:
                self._check(self._totals(actual_only=False), charges, self.physical_limits, "physical")
            self._calls[call_id] = {"reservation": reservation, "state": "reserved", "usage": None}
            return reservation

    def acknowledge_dispatch(self, call_id: str, *, exact_input_tokens: int,
                             applied_max_output_tokens: int) -> None:
        """Called by the trusted adapter immediately before sending one request.

        The adapter must pass this cap to a supported upstream API, not merely
        echo it into the prompt. Recount if instructions/schema/context changed.
        """
        with self._lock:
            if any(call["state"] in {"unknown_usage", "enforcement_violation"} for call in self._calls.values()):
                raise BudgetStateError("Ledger blocked by unknown usage or upstream enforcement violation")
            call = self._call(call_id, "reserved")
            reservation = call["reservation"]
            _integer(exact_input_tokens, "exact_input_tokens")
            _integer(applied_max_output_tokens, "applied_max_output_tokens", positive=True)
            if exact_input_tokens != reservation.exact_input_tokens or applied_max_output_tokens != reservation.max_output_tokens:
                raise BudgetStateError("Dispatched request count/cap does not match the reservation")
            call["state"] = "dispatched"

    def _call(self, call_id: str, state: str) -> dict:
        if call_id not in self._calls or self._calls[call_id]["state"] != state:
            raise BudgetStateError(f"call_id must exist and be {state}")
        return self._calls[call_id]

    def settle(self, call_id: str, usage: TokenUsage | Mapping) -> None:
        with self._lock:
            call = self._call(call_id, "dispatched")
            try:
                actual = usage if isinstance(usage, TokenUsage) else TokenUsage.from_mapping(usage)
            except (TypeError, ValueError) as exc:
                call["state"] = "unknown_usage"
                call["reason"] = "Upstream usage absent or malformed; reservation retained"
                raise BudgetStateError("Incomplete usage blocks further dispatch") from exc
            reservation = call["reservation"]
            call["usage"] = actual
            if actual.input_tokens != reservation.exact_input_tokens or actual.output_tokens > reservation.max_output_tokens:
                # Never discard a real over-budget request from the cost ledger.
                call["state"] = "enforcement_violation"
                raise BudgetEnforcementViolation("Actual input differs from exact count or output exceeds upstream cap")
            call["state"] = "settled"

    def cancel_before_dispatch(self, call_id: str) -> None:
        with self._lock:
            self._call(call_id, "reserved")["state"] = "cancelled_before_dispatch"

    def mark_usage_unknown(self, call_id: str, reason: str) -> None:
        with self._lock:
            if not isinstance(reason, str) or not reason:
                raise ValueError("A nonempty, credential-free reason is required")
            call = self._call(call_id, "dispatched")
            call["state"] = "unknown_usage"
            call["reason"] = reason

    def record_resources(self, event_id: str, arms: Sequence[str], usage: ResourceUsage) -> None:
        with self._lock:
            if not isinstance(event_id, str) or not event_id or event_id in self._resources:
                raise BudgetStateError("resource event_id must be new and nonempty")
            if not isinstance(usage, ResourceUsage):
                raise TypeError("usage must be ResourceUsage")
            self._resources[event_id] = {"arms": self._arms(arms), "usage": usage}

    def _resource_totals(self, arm: str | None = None) -> dict:
        events = [x["usage"] for x in self._resources.values() if arm is None or arm in x["arms"]]
        known = [Decimal(str(x.billed_usd)) for x in events if x.billed_usd is not None]
        return {"rpc_requests": sum(x.rpc_requests for x in events),
                "tool_calls": sum(x.tool_calls for x in events),
                "cpu_seconds": sum(x.cpu_seconds for x in events),
                "summed_event_wall_seconds": sum(x.wall_seconds for x in events),
                "known_billed_usd": str(sum(known, Decimal(0))) if known else None,
                "billing_status": "complete" if events and len(known) == len(events) else "partial" if known else "unavailable"}

    def snapshot(self) -> dict:
        with self._lock:
            states = [x["state"] for x in self._calls.values()]
            calls = []
            for call in self._calls.values():
                item = {"reservation": asdict(call["reservation"]), "state": call["state"],
                        "usage": call["usage"].to_dict() if call["usage"] is not None else None}
                if "reason" in call:
                    item["reason"] = call["reason"]
                calls.append(item)
            return {"schema_version": 1, "capability": self.capability.to_dict(),
                    "per_arm_limits": asdict(self.per_arm_limits),
                    "physical_limits": asdict(self.physical_limits) if self.physical_limits else None,
                    "actual_usage": self._totals(actual_only=True),
                    "actual_usage_scope": "Only calls with known usage; unknown usage is excluded, never asserted to be zero",
                    "dispatched_model_calls": sum(x in {"dispatched", "settled", "unknown_usage", "enforcement_violation"} for x in states),
                    "charged_or_reserved_usage": self._totals(actual_only=False),
                    "complete_usage": not any(x in {"reserved", "dispatched", "unknown_usage"} for x in states),
                    "enforcement_violations": states.count("enforcement_violation"),
                    "unknown_usage_calls": states.count("unknown_usage"),
                    "arm_attribution": {arm: {"actual_usage": self._totals(actual_only=True, arm=arm),
                                               "charged_or_reserved_usage": self._totals(actual_only=False, arm=arm),
                                               "non_model_resources": self._resource_totals(arm)} for arm in self.arms},
                    "non_model_resources": self._resource_totals(),
                    "accounting_note": "Shared drafts count once in actual_usage and in full in every assigned arm; arm attribution is not additive. Cached/reasoning tokens are subsets, not additional tokens.",
                    "calls": calls,
                    "resource_events": [{"event_id": key, "arms": value["arms"], "usage": asdict(value["usage"])} for key, value in self._resources.items()]}
