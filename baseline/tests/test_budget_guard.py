"""Offline guards against unfair budgets, duplicate costs and hidden spend."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from curve_audit.budget_guard import (
    BudgetCapability, BudgetCapabilityError, BudgetEnforcementViolation,
    BudgetExceeded, BudgetLimits, BudgetStateError, ResourceUsage,
    TokenBudgetLedger, TokenUsage, codex_cli_capability, require_hard_token_budget,
)


def capable():
    # Test-only capability of an imaginary enforcing adapter, never an API claim.
    return BudgetCapability("offline-enforcing-fixture", True, True, True, True,
                            True, ("test fixture has no inference",))


def ledger(limits=None, physical=None):
    return TokenBudgetLedger(("A", "B", "C"), limits or BudgetLimits(100, 100, 150, 3),
                             capable(), physical)


def settle(book, call_id, arms=("A",), input_tokens=10, output_tokens=5, cap=20):
    book.reserve(call_id, arms, input_tokens, cap)
    book.acknowledge_dispatch(call_id, exact_input_tokens=input_tokens,
                              applied_max_output_tokens=cap)
    book.settle(call_id, TokenUsage(input_tokens, output_tokens))


class BudgetGuardTests(unittest.TestCase):
    def test_cli_is_fail_closed_without_request_or_reservation(self):
        capability = codex_cli_capability()
        book = TokenBudgetLedger(("A",), BudgetLimits(100, 100, 200, 1), capability)
        with self.assertRaisesRegex(BudgetCapabilityError, "cannot run a hard equal-token"):
            book.reserve("call-1", ("A",), 10, 10)
        self.assertEqual(book.snapshot()["calls"], [])
        self.assertFalse(capability.to_dict()["hard_token_budget_supported"])

    def test_capability_requires_all_controls_and_evidence(self):
        with self.assertRaises(BudgetCapabilityError):
            require_hard_token_budget(BudgetCapability("unsupported", True, True, False, True, True, ("fixture",)))
        with self.assertRaises(BudgetCapabilityError):
            require_hard_token_budget(BudgetCapability("unsubstantiated", True, True, True, True, True))

    def test_false_flags_cannot_upgrade_known_unsupported_codex_route(self):
        for identity in ("codex-cli-chatgpt-plan", "codex-cli", " Codex_CLI ",
                         "codex", "chatgpt-plan", "chatgpt-oauth", "codex-chatgpt-plan"):
            with self.subTest(identity=identity):
                false_claim = BudgetCapability(identity, True, True, True, True, True,
                                               ("arbitrary unsupported assertion",))
                with self.assertRaisesRegex(BudgetCapabilityError, "known_unsupported_provider_route"):
                    require_hard_token_budget(false_claim)
                self.assertFalse(false_claim.to_dict()["hard_token_budget_supported"])
                book = TokenBudgetLedger(("A",), BudgetLimits(100, 100, 200, 1), false_claim)
                with self.assertRaises(BudgetCapabilityError):
                    book.reserve("must-not-dispatch", ("A",), 10, 10)
                self.assertEqual(book.snapshot()["calls"], [])

    def test_configuration_dict_is_not_a_trusted_adapter_capability(self):
        with self.assertRaisesRegex(BudgetCapabilityError, "trusted adapter factory"):
            require_hard_token_budget({"provider": "codex-cli", "hard_output_token_cap": True})

    def test_limits_and_usage_reject_negative_boolean_and_malformed_values(self):
        for value in (-1, True, 1.5, "20"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BudgetLimits(value, 20, 100, 1)
            with self.subTest(usage=value), self.assertRaises(ValueError):
                TokenUsage(10, value)
        with self.assertRaises(ValueError):
            TokenUsage.from_mapping({})
        with self.assertRaises(ValueError):
            TokenUsage.from_mapping({"input_tokens": 2, "output_tokens": 3, "total_tokens": 9})

    def test_shared_initial_draft_has_one_physical_cost_and_full_arm_attribution(self):
        book = ledger()
        settle(book, "draft", ("A", "B", "C"), input_tokens=20, output_tokens=10)
        for arm in ("A", "B", "C"):
            settle(book, f"final-{arm}", (arm,), input_tokens=10, output_tokens=5)
        result = book.snapshot()
        self.assertEqual(result["actual_usage"], dict(input_tokens=50, output_tokens=25, total_tokens=75, model_calls=4))
        for arm in ("A", "B", "C"):
            self.assertEqual(result["arm_attribution"][arm]["actual_usage"],
                             dict(input_tokens=30, output_tokens=15, total_tokens=45, model_calls=2))
        self.assertTrue(result["complete_usage"])
        json.dumps(result, allow_nan=False)

    def test_reservations_block_oversubscription_then_release_unused_output(self):
        book = ledger(BudgetLimits(100, 25, 100, 3))
        book.reserve("first", ("A",), 10, 20)
        with self.assertRaisesRegex(BudgetExceeded, "output_tokens"):
            book.reserve("second", ("A",), 1, 6)
        book.acknowledge_dispatch("first", exact_input_tokens=10, applied_max_output_tokens=20)
        book.settle("first", TokenUsage(10, 5))
        book.reserve("second", ("A",), 1, 20)
        self.assertEqual(book.snapshot()["charged_or_reserved_usage"]["output_tokens"], 25)

    def test_total_input_and_model_call_caps_are_separately_enforced(self):
        for limits, regex, request in (
            (BudgetLimits(9, 100, 100, 3), "input_tokens", (10, 1)),
            (BudgetLimits(100, 100, 10, 3), "total_tokens", (6, 5)),
            (BudgetLimits(100, 100, 100, 0), "model_calls", (1, 1)),
        ):
            with self.subTest(regex=regex):
                book = ledger(limits)
                with self.assertRaisesRegex(BudgetExceeded, regex):
                    book.reserve("request", ("A",), *request)
                self.assertEqual(book.snapshot()["calls"], [])

    def test_physical_budget_counts_shared_request_once(self):
        book = ledger(physical=BudgetLimits(10, 20, 30, 1))
        book.reserve("shared", ("A", "B", "C"), 10, 20)
        with self.assertRaisesRegex(BudgetExceeded, "physical"):
            book.reserve("extra", ("B",), 0, 1)

    def test_shared_draft_rejected_if_any_one_arm_is_exhausted(self):
        book = ledger(BudgetLimits(100, 100, 100, 1))
        settle(book, "already-A")
        with self.assertRaisesRegex(BudgetExceeded, "arm A"):
            book.reserve("draft", ("B", "C", "A"), 10, 10)
        self.assertEqual(book.snapshot()["arm_attribution"]["B"]["charged_or_reserved_usage"]["model_calls"], 0)

    def test_dispatch_must_match_exact_count_and_applied_upstream_cap(self):
        book = ledger()
        book.reserve("draft", ("A",), 10, 20)
        for count, cap in ((11, 20), (10, 21), (True, 20)):
            with self.subTest(count=count, cap=cap), self.assertRaises((BudgetStateError, ValueError)):
                book.acknowledge_dispatch("draft", exact_input_tokens=count, applied_max_output_tokens=cap)
        with self.assertRaises(BudgetStateError):
            book.settle("draft", TokenUsage(10, 5))
        self.assertEqual(book.snapshot()["calls"][0]["state"], "reserved")

    def test_duplicate_call_or_settlement_cannot_duplicate_actual_usage(self):
        book = ledger()
        settle(book, "call")
        with self.assertRaises(BudgetStateError):
            book.reserve("call", ("B",), 10, 10)
        with self.assertRaises(BudgetStateError):
            book.settle("call", TokenUsage(10, 5))
        self.assertEqual(book.snapshot()["actual_usage"]["total_tokens"], 15)

    def test_unknown_usage_timeout_keeps_reservation_and_blocks_existing_pending_calls(self):
        book = ledger()
        book.reserve("timeout", ("A",), 10, 20)
        book.reserve("pending", ("B",), 10, 20)
        book.acknowledge_dispatch("timeout", exact_input_tokens=10, applied_max_output_tokens=20)
        book.mark_usage_unknown("timeout", "timeout; upstream usage unavailable")
        with self.assertRaisesRegex(BudgetStateError, "blocked"):
            book.reserve("retry", ("A",), 1, 1)
        with self.assertRaisesRegex(BudgetStateError, "blocked"):
            book.acknowledge_dispatch("pending", exact_input_tokens=10, applied_max_output_tokens=20)
        result = book.snapshot()
        self.assertFalse(result["complete_usage"])
        self.assertEqual(result["unknown_usage_calls"], 1)
        self.assertEqual(result["dispatched_model_calls"], 1)
        self.assertEqual(result["charged_or_reserved_usage"]["total_tokens"], 60)
        self.assertIsNone(result["calls"][0]["usage"])

    def test_upstream_output_violation_records_actual_spend_and_blocks_ledger(self):
        book = ledger()
        book.reserve("violation", ("A",), 10, 20)
        book.acknowledge_dispatch("violation", exact_input_tokens=10, applied_max_output_tokens=20)
        with self.assertRaises(BudgetEnforcementViolation):
            book.settle("violation", TokenUsage(10, 25))
        result = book.snapshot()
        self.assertEqual(result["actual_usage"]["total_tokens"], 35)
        self.assertEqual(result["enforcement_violations"], 1)
        with self.assertRaises(BudgetStateError):
            book.reserve("next", ("B",), 1, 1)

    def test_missing_or_malformed_usage_is_not_zero_cost_and_automatically_blocks(self):
        for usage in ({}, {"input_tokens": 10}, {"input_tokens": 10, "output_tokens": -1}):
            with self.subTest(usage=usage):
                book = ledger()
                book.reserve("incomplete", ("A",), 10, 20)
                book.acknowledge_dispatch("incomplete", exact_input_tokens=10, applied_max_output_tokens=20)
                with self.assertRaisesRegex(BudgetStateError, "Incomplete usage"):
                    book.settle("incomplete", usage)
                with self.assertRaisesRegex(BudgetStateError, "blocked"):
                    book.reserve("next", ("B",), 1, 1)
                self.assertFalse(book.snapshot()["complete_usage"])
                self.assertEqual(book.snapshot()["charged_or_reserved_usage"]["total_tokens"], 30)

    def test_inexact_input_count_also_invalidates_hard_budget_capability_at_run_time(self):
        book = ledger()
        book.reserve("call", ("A",), 10, 20)
        book.acknowledge_dispatch("call", exact_input_tokens=10, applied_max_output_tokens=20)
        with self.assertRaises(BudgetEnforcementViolation):
            book.settle("call", TokenUsage(9, 5))
        self.assertEqual(book.snapshot()["actual_usage"]["input_tokens"], 9)

    def test_cancellation_only_before_dispatch_and_cannot_reuse_call_id(self):
        book = ledger()
        book.reserve("cancel", ("A",), 10, 20)
        book.cancel_before_dispatch("cancel")
        self.assertEqual(book.snapshot()["charged_or_reserved_usage"]["model_calls"], 0)
        with self.assertRaises(BudgetStateError):
            book.reserve("cancel", ("A",), 10, 20)
        book.reserve("sent", ("A",), 10, 20)
        book.acknowledge_dispatch("sent", exact_input_tokens=10, applied_max_output_tokens=20)
        with self.assertRaises(BudgetStateError):
            book.cancel_before_dispatch("sent")

    def test_cached_and_reasoning_tokens_are_subsets_not_double_counted(self):
        usage = TokenUsage.from_mapping({"input_tokens": 30, "output_tokens": 20,
                                        "input_tokens_details": {"cached_tokens": 25},
                                        "output_tokens_details": {"reasoning_tokens": 12}, "total_tokens": 50})
        self.assertEqual(usage.total_tokens, 50)
        for value in (TokenUsage(30, 20, 25, 12).to_dict(), {"input_tokens": 30, "output_tokens": 20, "cached_input_tokens": 25}):
            book = ledger()
            book.reserve("call", ("A",), 30, 20)
            book.acknowledge_dispatch("call", exact_input_tokens=30, applied_max_output_tokens=20)
            book.settle("call", value)
            self.assertEqual(book.snapshot()["actual_usage"]["total_tokens"], 50)
        with self.assertRaises(ValueError):
            TokenUsage(10, 5, cached_input_tokens=11)
        with self.assertRaises(ValueError):
            TokenUsage(10, 5, reasoning_tokens=6)

    def test_non_model_costs_are_separate_shared_once_and_missing_price_is_not_zero(self):
        book = ledger()
        settle(book, "draft", ("A", "B", "C"))
        book.record_resources("shared-rpc", ("A", "B", "C"), ResourceUsage(rpc_requests=4, tool_calls=1, wall_seconds=2))
        book.record_resources("cpu-C", ("C",), ResourceUsage(cpu_seconds=1.5, wall_seconds=1.75, billed_usd="0.01"))
        result = book.snapshot()
        self.assertEqual(result["actual_usage"]["total_tokens"], 15)
        self.assertEqual(result["non_model_resources"]["rpc_requests"], 4)
        self.assertEqual(result["non_model_resources"]["billing_status"], "partial")
        self.assertEqual(result["non_model_resources"]["known_billed_usd"], "0.01")
        self.assertIsNone(result["arm_attribution"]["A"]["non_model_resources"]["known_billed_usd"])
        self.assertEqual(result["arm_attribution"]["C"]["non_model_resources"]["summed_event_wall_seconds"], 3.75)
        with self.assertRaises(BudgetStateError):
            book.record_resources("shared-rpc", ("A",), ResourceUsage())
        for value in (float("nan"), float("inf"), -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ResourceUsage(wall_seconds=value)
        for value in ("not-a-number", "NaN", "Infinity", "-0.1"):
            with self.subTest(billing=value), self.assertRaises(ValueError):
                ResourceUsage(billed_usd=value)

    def test_parallel_reservations_cannot_overallocate_remaining_budget(self):
        book = ledger(BudgetLimits(100, 100, 100, 1))
        def try_reserve(number):
            try:
                book.reserve(str(number), ("A",), 10, 10)
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = list(pool.map(try_reserve, range(20)))
        self.assertEqual(sum(accepted), 1)
        self.assertEqual(len(book.snapshot()["calls"]), 1)

    def test_invalid_arm_assignment_cannot_hide_shared_cost(self):
        book = ledger()
        for arms in ((), ("A", "A"), ("D",)):
            with self.subTest(arms=arms), self.assertRaises(ValueError):
                book.reserve("invalid", arms, 10, 20)
        self.assertEqual(book.snapshot()["calls"], [])


if __name__ == "__main__":
    unittest.main()
