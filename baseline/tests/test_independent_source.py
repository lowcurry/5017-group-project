"""Synthetic JSON-RPC fixtures exercise the independent path without networking."""
import ast
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from curve_audit.independent_source import (
    DEFAULT_CONFIG, RecordingRpc, SourceError, SourceUnavailable, acquire_snapshot,
    as_recompute_dataset, collect_independent, compare_source_records, decode_uint256, encode_call,
    locate_block, plan_task, replay_record, save_record, validate_config,
)

BASE = 1_600_000_000
HEAD = 1000
PROVIDER = {"name": "fixture_public_a", "url": "https://example.com/ethereum", "documentation": "synthetic offline fixture"}
PROVIDER_B = {"name": "fixture_public_b", "url": "https://example.org/ethereum", "documentation": "synthetic offline fixture"}


def timestamp(block, offset=0):
    return datetime.fromtimestamp(BASE + block * 12 + offset, timezone.utc).isoformat().replace("+00:00", "Z")


def block_hash(block):
    return "0x" + hashlib.sha256(f"synthetic block {block}".encode()).hexdigest()


def task(first=100, last=None, *, offset=0, **extra):
    return {"id": "offline_fixture", "start": timestamp(first, offset), "end": timestamp(first if last is None else last, offset), "interval_minutes": 1, **extra}


class FakeChain:
    """Regular chain with synthetic raw state, never labeled real Curve values."""
    def __init__(self, *, change_raw=0, chain=1, broken_parent=None, fail_state=False, invalid_word=False):
        self.messages = []
        self.change_raw, self.chain, self.broken_parent = change_raw, chain, broken_parent
        self.fail_state, self.invalid_word = fail_state, invalid_word

    def __call__(self, request):
        self.messages.append(copy.deepcopy(request))
        method, params = request["method"], request["params"]
        if method == "eth_chainId":
            result = hex(self.chain)
        elif method == "eth_blockNumber":
            result = hex(HEAD)
        elif method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            result = {"number": hex(number), "hash": block_hash(number),
                      "parentHash": block_hash(number - 1), "timestamp": hex(BASE + number * 12)}
            if number == self.broken_parent:
                result["parentHash"] = "0x" + "f" * 64
        elif method == "eth_call":
            if self.fail_state:
                return {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32000, "message": "fixture archive unavailable"}}
            self.assert_call(params)
            data = params[0]["data"]
            words = [int(data[index:index + 64], 16) for index in range(10, len(data), 64)]
            if data[2:10] == "4903b0d1":
                # Normalized balances: 1,000, 2,000 and 3,000, plus a block-dependent component.
                index = words[0]
                value = ((index + 1) * 1000 + int(params[1], 16)) * 10 ** (18 if index == 0 else 6)
            else:
                i, j, raw_input = words
                value = raw_input - raw_input // (1000 + i * 10 + j)
            value += self.change_raw
            result = "0x12" if self.invalid_word else "0x" + value.to_bytes(32, "big").hex()
        else:
            raise AssertionError("unexpected RPC method")
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}

    @staticmethod
    def assert_call(params):
        assert params[0]["to"] == DEFAULT_CONFIG["pool"]
        assert params[1].startswith("0x") and params[1] != "latest"


class IndependentSourceTests(unittest.TestCase):
    def collect(self, requested=None, chain=None, **kwargs):
        return collect_independent(requested or task(), providers=[PROVIDER],
                                   transports={PROVIDER["name"]: chain or FakeChain()}, **kwargs)

    def test_independence_dependency_boundary_is_explicit(self):
        path = Path(__file__).resolve().parents[1] / "curve_audit/independent_source.py"
        tree = ast.parse(path.read_text())
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(any(name and any(part in name for part in ("metrics", "verification", ".data", "common")) for name in imported))

    def test_uint_encoding_and_decoding_boundary(self):
        encoded = encode_call("4903b0d1", [0, 2**256 - 1])
        self.assertEqual(encoded, "0x4903b0d1" + "0" * 64 + "f" * 64)
        self.assertEqual(decode_uint256("0x" + "f" * 64), str(2**256 - 1))
        for invalid in (True, -1, 2**256, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(SourceError):
                encode_call("4903b0d1", [invalid])
        for invalid in ("0x12", "0x" + "0" * 63, "0x" + "g" * 64, 1):
            with self.subTest(invalid=invalid), self.assertRaises(SourceUnavailable):
                decode_uint256(invalid)

    def test_config_pending_human_review_is_not_claimed_complete(self):
        record = self.collect()
        self.assertEqual(record["human_configuration_review_status"], "pending")
        self.assertFalse(record["operational_backend_independence_proven"])
        self.assertIn("no arbitrary prose", record["scope"])

    def test_config_rejects_wrong_index_name_and_overflow(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["directions"]["USDC_USDT"] = [2, 1]
        with self.assertRaisesRegex(SourceError, "disagree"):
            validate_config(config)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["quote_sizes"] = [2**256 - 1]
        with self.assertRaises(SourceError):
            validate_config(config)

    def test_timestamp_rejects_naive_non_utc_and_subseconds(self):
        for value in ("2023-03-10T00:00:00", "2023-03-10T08:00:00+08:00", "2023-03-10T00:00:00.1Z", "2023-03-10"):
            with self.subTest(value=value), self.assertRaises(SourceError):
                plan_task({"start": value, "end": value})

    def test_task_rejects_agent_archive_inputs(self):
        for field in ("block_number", "calldata", "raw_balances", "snapshots", "dataset"):
            with self.subTest(field=field), self.assertRaises(SourceError):
                plan_task({**task(), field: {}})

    def test_full_sampling_grid_cannot_silently_drop_peak_candidates(self):
        plan = plan_task(task(100, 115))
        self.assertEqual(plan["requested_targets"], [timestamp(100), timestamp(105), timestamp(110), timestamp(115)])
        with self.assertRaisesRegex(SourceError, "aligned"):
            plan_task(task(100, 114))
        with self.assertRaisesRegex(SourceError, "budget"):
            plan_task(task(100, 115), max_targets=3)

    def test_binary_search_exact_and_between_block_targets(self):
        for offset in (0, 1, 11):
            chain = FakeChain()
            rpc = RecordingRpc(chain)
            block, selected, following = locate_block(rpc, timestamp(123, offset))
            self.assertEqual(block, 123)
            self.assertEqual(int(selected["number"], 16) + 1, int(following["number"], 16))
            self.assertLess(len(chain.messages), 18)

    def test_target_outside_chain_is_unknown(self):
        for target in (timestamp(-1), timestamp(HEAD), timestamp(HEAD + 1)):
            result = self.collect({"start": target, "end": target})
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["dataset"]["snapshots"], [])
            self.assertTrue(replay_record(result)["pass"])

    def test_bad_ancestry_is_unknown_not_a_wrong_agent_verdict(self):
        result = self.collect(chain=FakeChain(broken_parent=101))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("ancestry", result["provider_attempts"][0]["errors"][0]["reason"])
        self.assertTrue(replay_record(result)["pass"])

    def test_independent_abi_direction_amount_and_decoding(self):
        chain = FakeChain()
        record = self.collect(chain=chain)
        row = record["dataset"]["snapshots"][0]
        self.assertEqual(row["block_number"], 100)
        self.assertEqual(row["balances"], {"DAI": "1100", "USDC": "2100", "USDT": "3100"})
        quote = row["calldata"]["quotes"]["USDC_USDT"]["100000"]
        self.assertEqual(quote, "0x5e0d443f" + format(1, "064x") + format(2, "064x") + format(100000 * 10**6, "064x"))
        self.assertEqual(len([message for message in chain.messages if message["method"] == "eth_call"]), 9)
        self.assertTrue(all("raw_response" in entry for entry in record["provider_attempts"][0]["transcript"]))

    def test_independent_dataset_is_compatible_with_recompute(self):
        # Imported only here: the acquisition module has no verifier dependency.
        from curve_audit.verification import recompute, validate_dataset
        record = self.collect(task(100, 115))
        dataset = as_recompute_dataset(record)
        self.assertEqual(validate_dataset(dataset), [])
        query = {"metric": "share_pct", "operation": "maximum", "start": timestamp(100), "end": timestamp(115), "coin": "USDC"}
        expected = recompute(dataset, query)
        self.assertEqual(expected["sample_count"], 4)
        self.assertEqual(len(expected["evidence_ids"]), 4)
        self.assertTrue(replay_record(record)["pass"])

    def test_scope_restriction_does_not_fetch_excluded_target(self):
        chain = FakeChain()
        record = self.collect(task(100, 110, allowed_targets=[timestamp(100), timestamp(110)]), chain=chain)
        self.assertEqual(record["status"], "available")
        self.assertTrue(record["complete_allowed_grid"])
        self.assertFalse(record["complete_requested_grid"])
        self.assertEqual(record["plan"]["excluded_targets"], [timestamp(105)])
        called_blocks = {int(message["params"][1], 16) for message in chain.messages if message["method"] == "eth_call"}
        self.assertEqual(called_blocks, {100, 110})
        from curve_audit.verification import recompute
        with self.assertRaisesRegex(ValueError, "missing"):
            recompute(as_recompute_dataset(record), {"metric": "imbalance_l1", "operation": "maximum", "start": timestamp(100), "end": timestamp(110)})

    def test_excluded_all_rows_is_available_only_within_empty_scope(self):
        chain = FakeChain()
        record = self.collect(task(allowed_targets=[]), chain=chain)
        self.assertFalse(record["complete_requested_grid"])
        self.assertEqual(record["dataset"]["snapshots"], [])
        self.assertFalse(any(message["method"] == "eth_call" for message in chain.messages))

    def test_unknown_archive_or_wrong_chain_never_becomes_disagreement(self):
        for chain in (FakeChain(fail_state=True), FakeChain(chain=5), FakeChain(invalid_word=True)):
            with self.subTest(chain=chain):
                record = self.collect(chain=chain)
                self.assertEqual(record["status"], "unknown")
                self.assertEqual(record["source_conflicts"], [])
                self.assertTrue(replay_record(record)["pass"])

    def test_two_source_raw_disagreement_is_unknown_and_excludes_row(self):
        record = collect_independent(task(), providers=[PROVIDER, PROVIDER_B],
                                     transports={PROVIDER["name"]: FakeChain(), PROVIDER_B["name"]: FakeChain(change_raw=1)})
        self.assertEqual(record["status"], "unknown")
        self.assertEqual(record["dataset"]["snapshots"], [])
        self.assertEqual(len(record["source_conflicts"]), 1)
        self.assertTrue(replay_record(record)["pass"])

    def test_one_unavailable_provider_failure_is_retained_with_second_source(self):
        record = collect_independent(task(), providers=[PROVIDER, PROVIDER_B],
                                     transports={PROVIDER["name"]: FakeChain(fail_state=True), PROVIDER_B["name"]: FakeChain()})
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["provider_attempts"][0]["status"], "unknown")
        self.assertEqual(len(record["provider_attempts"][0]["errors"]), 1)
        self.assertTrue(replay_record(record)["pass"])

    def test_transport_failure_retries_have_recorded_attempts(self):
        calls = []
        def unavailable(payload):
            calls.append(payload)
            raise TimeoutError()
        record = self.collect(chain=unavailable, policy={"attempts": 2})
        self.assertEqual(len(calls), 2)
        entries = record["provider_attempts"][0]["transcript"]
        self.assertEqual([entry["attempt"] for entry in entries], [1, 2])
        self.assertTrue(all(entry["failure"] == "RPC transport unavailable" for entry in entries))
        self.assertTrue(replay_record(record)["pass"])

    def test_rpc_message_id_mismatch_is_unknown(self):
        chain = FakeChain()
        def changed_id(payload):
            answer = chain(payload)
            answer["id"] += 1
            return answer
        record = self.collect(chain=changed_id)
        self.assertEqual(record["status"], "unknown")
        self.assertTrue(replay_record(record)["pass"])

    def test_request_budget_is_enforced_and_replayable(self):
        record = self.collect(policy={"max_requests_per_provider": 4})
        self.assertEqual(record["status"], "unknown")
        self.assertEqual(record["provider_attempts"][0]["rpc_attempt_count"], 4)
        self.assertIn("budget", record["provider_attempts"][0]["errors"][0]["reason"])
        self.assertTrue(replay_record(record)["pass"])

    def test_header_change_during_acquisition_is_unknown(self):
        chain = FakeChain()
        counts = {}
        def changing(payload):
            result = chain(payload)
            if payload["method"] == "eth_getBlockByNumber":
                number = int(payload["params"][0], 16)
                counts[number] = counts.get(number, 0) + 1
                if number == 100 and counts[number] > 1:
                    result["result"]["hash"] = "0x" + "e" * 64
            return result
        record = self.collect(chain=changing)
        self.assertEqual(record["status"], "unknown")
        self.assertIn("changed", record["provider_attempts"][0]["errors"][0]["reason"])
        self.assertTrue(replay_record(record)["pass"])

    def test_credential_endpoint_and_environment_discovery_not_supported(self):
        for url in ("https://user:secret@example.com", "https://example.com?key=secret", "http://example.com", "https://example.com#token"):
            with self.subTest(url=url), self.assertRaises(SourceError):
                collect_independent(task(), providers=[{**PROVIDER, "url": url}])
        with self.assertRaisesRegex(SourceError, "explicit"):
            collect_independent(task())

    def test_offline_replay_has_no_network_and_detects_changed_saved_data(self):
        record = self.collect()
        self.assertEqual(replay_record(record)["network_used"], False)
        tampered = copy.deepcopy(record)
        tampered["dataset"]["snapshots"][0]["raw_balances"]["USDC"] = "1"
        with self.assertRaisesRegex(SourceError, "hash mismatch"):
            replay_record(tampered)
        # Rebinding a hash does not make altered data reproduce from RPC messages.
        body = {key: value for key, value in tampered.items() if key != "content_sha256"}
        tampered["content_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertFalse(replay_record(tampered)["pass"])

    def test_offline_replay_rejects_extra_transcript_entries_even_if_rehashed(self):
        record = self.collect()
        record["provider_attempts"][0]["transcript"].append(copy.deepcopy(record["provider_attempts"][0]["transcript"][-1]))
        record["provider_attempts"][0]["rpc_attempt_count"] += 1
        body = {key: value for key, value in record.items() if key != "content_sha256"}
        record["content_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        result = replay_record(record)
        self.assertFalse(result["pass"])
        self.assertTrue(any("unused" in difference for difference in result["differences"]))

    def test_boolean_rpc_message_id_is_rejected(self):
        chain = FakeChain()
        def boolean_id(payload):
            response = chain(payload)
            response["id"] = True
            return response
        self.assertEqual(self.collect(chain=boolean_id)["status"], "unknown")

    def test_separate_source_records_conflict_without_majority_voting(self):
        first = self.collect()
        second = self.collect(chain=FakeChain(change_raw=1))
        self.assertEqual(compare_source_records([first, first])["status"], "available")
        compared = compare_source_records([first, first, second])
        self.assertEqual(compared["status"], "unknown")
        self.assertEqual(len(compared["source_conflicts"]), 1)
        with self.assertRaisesRegex(SourceError, "frozen task"):
            compare_source_records([first, self.collect(task(105))])

    def test_record_write_is_exclusive_and_does_not_mutate_data(self):
        record = self.collect()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "new-source.json"
            save_record(path, record)
            self.assertEqual(json.loads(path.read_text()), record)
            with self.assertRaises(FileExistsError):
                save_record(path, record)
        dataset = as_recompute_dataset(record)
        dataset["snapshots"].clear()
        self.assertEqual(len(record["dataset"]["snapshots"]), 1)

    def test_export_rejects_self_consistent_rehashed_derived_rows(self):
        record = self.collect()
        for row in (record["dataset"]["snapshots"][0], record["provider_attempts"][0]["snapshots"][0]):
            row["raw_balances"]["USDC"] = "999000000"
            row["balances"]["USDC"] = "999"
        body = {k: v for k, v in record.items() if k != "content_sha256"}
        record["content_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertFalse(replay_record(record)["pass"])
        with self.assertRaisesRegex(SourceError, "saved RPC transcript"):
            as_recompute_dataset(record)
        with self.assertRaisesRegex(SourceError, "saved RPC transcript"):
            compare_source_records([record])

    def test_phantom_provider_attempt_rejected_even_when_rehashed(self):
        record = self.collect()
        record["provider_attempts"].append(copy.deepcopy(record["provider_attempts"][0]))
        body = {k: v for k, v in record.items() if k != "content_sha256"}
        record["content_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self.assertRaisesRegex(SourceError, "exactly cover"):
            replay_record(record)

    def test_transport_failure_cannot_hide_mismatched_replay_request(self):
        def fail(payload):
            raise SourceUnavailable("replay request differs from saved RPC request")
        record = self.collect(chain=fail)
        record["provider_attempts"][0]["transcript"][0]["request"]["method"] = "never_executed_rpc"
        body = {k: v for k, v in record.items() if k != "content_sha256"}
        record["content_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertFalse(replay_record(record)["pass"])


if __name__ == "__main__":
    unittest.main()
