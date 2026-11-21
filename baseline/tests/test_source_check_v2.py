"""Offline historical-source selection and field comparison regressions."""
import copy
import tempfile
import unittest
from pathlib import Path

from curve_audit.data import COINS, DIRECTIONS, QUOTE_SIZES, RpcError, load_dataset, parse_utc
from curve_audit.source_check_v2 import check_provider, freeze_protocol, selection

ROOT = Path(__file__).resolve().parents[1]


class KnownStateClient:
    def __init__(self, dataset, *, fail_chain=False, fail_archive=False, disagree=False):
        self.rows = {r["block_number"]: r for r in dataset["snapshots"]}
        self.headers = {}
        for row in self.rows.values():
            for ref in (row, row["next_block"]):
                self.headers[ref["block_number"]] = {"number": hex(ref["block_number"]), "hash": ref["block_hash"], "parentHash": ref["parent_hash"], "timestamp": hex(int(parse_utc(ref["timestamp_utc"]).timestamp()))}
        self.request_count = 0
        self.fail_chain, self.fail_archive, self.disagree = fail_chain, fail_archive, disagree

    def block(self, number):
        self.request_count += 1
        return self.headers[number]

    def call(self, method, params):
        self.request_count += 1
        if method == "eth_chainId":
            if self.fail_chain:
                raise RpcError("RPC transport failed after bounded retries")
            return "0x1"
        if self.fail_archive:
            raise RpcError("RPC response rejected or malformed")
        row = self.rows[int(params[1], 16)]
        calldata = params[0]["data"]
        for coin in COINS:
            if calldata == row["calldata"]["balances"][coin]:
                value = int(row["raw_balances"][coin]) + int(self.disagree)
                return "0x" + format(value, "064x")
        for direction in DIRECTIONS:
            for size in QUOTE_SIZES:
                if calldata == row["calldata"]["quotes"][direction][str(size)]:
                    return "0x" + format(int(row["raw_quotes"][direction][str(size)]), "064x")
        raise AssertionError("Unexpected calldata")


class SourceCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "data/raw/expanded_archive.json"
        cls.dataset = load_dataset(cls.path)

    def protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            return freeze_protocol(self.path, Path(tmp))

    def test_selection_is_frozen_and_contains_risk_anchors(self):
        rows, design = selection(self.dataset)
        self.assertEqual(design["random_rank_sample_count"], 15)
        self.assertEqual(design["selected_count_after_deduplication"], 19)
        self.assertTrue(set(design["anchor_selectors"].values()) <= {r["id"] for r in rows})
        with tempfile.TemporaryDirectory() as tmp:
            first = freeze_protocol(self.path, Path(tmp))
            second = freeze_protocol(self.path, Path(tmp))
            self.assertEqual(first, second)

    def test_exact_19_snapshot_raw_state_and_headers_agree(self):
        result = check_provider(self.dataset, self.protocol(), "drpc", client=KnownStateClient(self.dataset))
        self.assertEqual(result["status"], "selected_fields_agree")
        self.assertEqual(result["counts"]["header_objects_agree"], 38)
        self.assertEqual(result["counts"]["state_fields_agree"], 171)

    def test_state_disagreement_is_not_reported_as_agreement(self):
        result = check_provider(self.dataset, self.protocol(), "drpc", client=KnownStateClient(self.dataset, disagree=True))
        self.assertEqual(result["status"], "disagreement")
        self.assertEqual(result["counts"]["state_fields_disagree"], 57)

    def test_header_only_agreement_does_not_validate_raw_state(self):
        result = check_provider(self.dataset, self.protocol(), "drpc", client=KnownStateClient(self.dataset, fail_archive=True))
        self.assertEqual(result["status"], "partial_agreement")
        self.assertEqual(result["counts"]["state_fields_agree"], 0)
        self.assertEqual(result["counts"]["state_fields_not_attempted"], 170)

    def test_chain_failure_skips_and_does_not_claim_source_validation(self):
        result = check_provider(self.dataset, self.protocol(), "drpc", client=KnownStateClient(self.dataset, fail_chain=True))
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["counts"]["state_fields_not_attempted"], 171)

    def test_no_arbitrary_endpoint_or_private_credential_discovery(self):
        with self.assertRaisesRegex(ValueError, "allowlist"):
            check_provider(self.dataset, self.protocol(), "not_a_frozen_public_provider")


if __name__ == "__main__":
    unittest.main()
