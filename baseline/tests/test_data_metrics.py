"""Admission, arithmetic, coverage and RPC-safety regression tests."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from curve_audit.data import (COINS, DECIMALS, DIRECTIONS, POOL, QUOTE_SIZES, DatasetError,
                              RpcClient, RpcError, calldata, collect, export_processed, load_dataset,
                              read_rpc_url, replicate, select_snapshots, validate_dataset)
from curve_audit.metrics import calculate, snapshot_metrics

ROOT = Path(__file__).resolve().parents[1]


def sample_dataset() -> dict:
    source = json.loads((ROOT / "data/raw/pilot_archive.json").read_text())
    snapshot = copy.deepcopy(next(row for row in source["snapshots"] if row["id"] == "H_00"))
    snapshots = []
    for index, amounts in enumerate(((1, 1, 2), (1, 2, 1), (1, 3, 0))):
        row = copy.deepcopy(snapshot)
        row.update({"id": f"H_{index:02d}", "target_timestamp_utc": f"2023-03-10T{18 + index:02d}:00:00Z",
                    "timestamp_utc": f"2023-03-10T{17 + index:02d}:59:59Z", "block_number": 100 + index,
                    "block_hash": "0x" + format(100 + index, "064x"),
                    "parent_hash": "0x" + format(99 + index, "064x")})
        row["raw_balances"] = {coin: str(amount * 10**DECIMALS[coin]) for coin, amount in zip(COINS, amounts)}
        row["balances"] = {coin: str(amount) for coin, amount in zip(COINS, amounts)}
        row["next_block"] = {"block_number": 101 + index, "block_hash": "0x" + format(101 + index, "064x"),
                             "parent_hash": row["block_hash"], "timestamp_utc": f"2023-03-10T{18 + index:02d}:00:11Z"}
        for direction in DIRECTIONS:
            outputs = (1000, 9990, 99000) if direction == "USDC_USDT" else (1000, 10010, 101000)
            row["raw_quotes"][direction] = {str(size): str(output * 10**6) for size, output in zip(QUOTE_SIZES, outputs)}
            row["quotes"][direction] = {str(size): str(output) for size, output in zip(QUOTE_SIZES, outputs)}
        snapshots.append(row)
    source["snapshots"] = snapshots
    source.pop("sampling", None)
    return source


def query(metric="share_pct", operation="change", **extra):
    return {"metric": metric, "operation": operation, "start": "2023-03-10T18:00:00Z",
            "end": "2023-03-10T20:00:00Z", **extra}


class DataAdmissionTests(unittest.TestCase):
    def test_pilot_is_valid_but_nearest_prior_proof_is_missing(self):
        path = ROOT / "data/raw/pilot_archive.json"
        before = path.read_bytes()
        dataset = load_dataset(path)
        self.assertEqual(dataset["_coverage"]["distinct_block_count"], 27)
        self.assertEqual(dataset["_coverage"]["target_count"], 27)
        self.assertFalse(dataset["_coverage"]["nearest_prior_verified"])
        self.assertEqual(path.read_bytes(), before)

    def test_reject_wrong_chain_contract_decimals_raw_normalization_and_calldata(self):
        mutations = [
            lambda d: d.update(chain_id=137),
            lambda d: d.update(pool="0x" + "1" * 40),
            lambda d: d["decimals"].update(USDC=18),
            lambda d: d["snapshots"][0]["raw_balances"].update(USDC="-1"),
            lambda d: d["snapshots"][0]["balances"].update(USDC="1.0000001"),
            lambda d: d["snapshots"][0]["quotes"]["USDC_USDT"].update({"1000": "NaN"}),
            lambda d: d["snapshots"][0]["calldata"]["balances"].update(USDC=calldata("4903b0d1", [2])),
            lambda d: d["snapshots"][0]["calldata"]["quotes"]["USDC_USDT"].update({"1000": calldata("5e0d443f", [2, 1, 1000 * 10**6])}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                dataset = sample_dataset()
                mutation(dataset)
                with self.assertRaises(DatasetError):
                    validate_dataset(dataset)

    def test_next_block_proves_nearest_prior_selection(self):
        dataset = sample_dataset()
        self.assertTrue(validate_dataset(dataset)["nearest_prior_verified"])
        dataset["snapshots"][0]["next_block"]["timestamp_utc"] = "2023-03-10T18:00:00Z"
        with self.assertRaisesRegex(DatasetError, "nearest prior"):
            validate_dataset(dataset)

    def test_alias_deduplication_prefers_hourly_and_rejects_conflict(self):
        dataset = sample_dataset()
        alias = copy.deepcopy(dataset["snapshots"][1])
        alias.update(id="S_STRESS", series="anchor")
        dataset["snapshots"].insert(0, alias)
        self.assertEqual([row["id"] for row in select_snapshots(dataset, query()["start"], query()["end"])],
                         ["H_00", "H_01", "H_02"])
        alias["raw_balances"]["USDC"] = "9000000"
        alias["balances"]["USDC"] = "9"
        with self.assertRaisesRegex(DatasetError, "Conflicting snapshots"):
            validate_dataset(dataset)

    def test_out_of_range_missing_endpoint_and_missing_internal_hour(self):
        dataset = sample_dataset()
        for start, end in (("2023-03-10T17:00:00Z", query()["end"]),
                           ("2023-03-10T18:30:00Z", query()["end"]),
                           (query()["end"], query()["start"])):
            with self.subTest(start=start, end=end), self.assertRaises(DatasetError):
                select_snapshots(dataset, start, end)
        dataset["snapshots"].pop(1)
        with self.assertRaisesRegex(DatasetError, "missing hourly"):
            select_snapshots(dataset, query()["start"], query()["end"])

    def test_explicit_credentials_only_and_no_sensitive_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            filename = Path(tmp) / "private.env"
            filename.write_text("ETH_RPC_URL=https://example.test/SECRET_KEY\n")
            self.assertEqual(read_rpc_url(filename), "https://example.test/SECRET_KEY")
            with self.assertRaises(RpcError) as caught:
                read_rpc_url()
            self.assertNotIn("SECRET_KEY", str(caught.exception))
            with patch("urllib.request.urlopen", side_effect=OSError("https://host/SECRET_KEY")):
                with self.assertRaises(RpcError) as caught:
                    RpcClient("https://example.test/SECRET_KEY", minimum_interval=0).call("eth_chainId", [])
            self.assertNotIn("SECRET_KEY", str(caught.exception))

    def test_exports_are_distinct_exact_decimal_rows_with_dictionary(self):
        dataset = sample_dataset()
        alias = copy.deepcopy(dataset["snapshots"][1])
        alias.update(id="S_STRESS", series="anchor")
        dataset["snapshots"].append(alias)
        with tempfile.TemporaryDirectory() as tmp:
            result = export_processed(dataset, Path(tmp) / "metrics.csv", Path(tmp) / "dictionary.json")
            self.assertEqual(result["rows"], 3)
            lines = (Path(tmp) / "metrics.csv").read_text().splitlines()
            self.assertEqual(len(lines), 4)
            self.assertIn("USDT_USDC_size_sensitivity_bps", lines[0])
            self.assertIn("nearest_prior_verified", json.loads((Path(tmp) / "dictionary.json").read_text())["coverage"])


class MetricsTests(unittest.TestCase):
    def test_exact_nominal_shares_quotes_and_both_direction_sensitivity(self):
        metrics = snapshot_metrics(sample_dataset()["snapshots"][0])
        self.assertEqual(metrics["share_pct"], {"DAI": "25", "USDC": "25", "USDT": "50"})
        self.assertEqual(metrics["total_nominal_tokens"], "4")
        self.assertEqual(metrics["quote_rates"]["USDC_USDT"]["100000"], "0.99")
        self.assertEqual(metrics["size_sensitivity_bps"]["USDC_USDT"]["100000"], "100")
        self.assertEqual(metrics["size_sensitivity_bps"]["USDT_USDC"]["100000"], "-100")
        self.assertLess(abs(Decimal(metrics["imbalance_l1"]) - Decimal(1) / 6), Decimal("1e-27"))

    def test_share_change_and_evidence_are_exact_and_stable(self):
        dataset = sample_dataset()
        result = calculate(dataset, query())
        self.assertEqual(result["value"], "50")
        self.assertEqual(result["unit"], "percentage_points")
        self.assertEqual(result["evidence_ids"], ["E_H_00", "E_H_02"])
        self.assertEqual(result["calculation_id"], calculate(dataset, query(coin="USDC"))["calculation_id"])
        self.assertNotEqual(result["calculation_id"], calculate(dataset, query(coin="DAI"))["calculation_id"])

    def test_maximum_cites_entire_window_and_peak_block(self):
        result = calculate(sample_dataset(), query(operation="maximum"))
        self.assertEqual(result["value"], "75")
        self.assertEqual(result["evidence_ids"], ["E_H_00", "E_H_01", "E_H_02"])
        self.assertEqual(result["block_number"], 102)
        self.assertEqual(result["observed_at"], "2023-03-10T19:59:59Z")
        self.assertEqual(result["unit"], "percent")

    def test_first_last_quote_units_and_sensitivity_default(self):
        dataset = sample_dataset()
        first = calculate(dataset, query("quote_rate", "first"))
        self.assertEqual(first["value"], "1")
        self.assertEqual(first["unit"], "USDT/USDC")
        self.assertEqual(first["evidence_ids"], ["E_H_00"])
        last = calculate(dataset, query("quote_rate", "last", direction="USDT_USDC", size=100000))
        self.assertEqual(last["value"], "1.01")
        self.assertEqual(last["unit"], "USDC/USDT")
        sensitivity = calculate(dataset, query("size_sensitivity_bps", "first"))
        self.assertEqual(sensitivity["value"], "100")
        self.assertEqual(sensitivity["query"]["size"], 100000)
        self.assertEqual(sensitivity["unit"], "basis_points")

    def test_single_timestamp_change_is_zero_with_unique_evidence(self):
        result = calculate(sample_dataset(), query(end=query()["start"]))
        self.assertEqual(result["value"], "0")
        self.assertEqual(result["evidence_ids"], ["E_H_00"])

    def test_calculate_revalidates_data_and_rejects_hint_fields(self):
        dataset = sample_dataset()
        dataset["snapshots"][0]["raw_quotes"]["USDC_USDT"]["1000"] = "999999"
        with self.assertRaises(DatasetError):
            calculate(dataset, query())
        with self.assertRaises(DatasetError):
            calculate(sample_dataset(), query(expected_value="50"))


class MockArchive:
    """A deterministic miniature chain: block n timestamp is 10*n seconds."""
    def __init__(self):
        self.request_count = 0
        self.calls = []

    def call(self, method, params):
        self.request_count += 1
        return "0x1" if method == "eth_chainId" else "0x64"

    def block(self, number):
        self.request_count += 1
        return {"number": hex(number), "timestamp": hex(number * 10),
                "hash": "0x" + format(number, "064x"), "parentHash": "0x" + format(max(number - 1, 0), "064x")}

    def batch(self, calls):
        self.calls.extend(calls)
        self.request_count += len(calls)
        values = [10**18, 10**6, 2 * 10**6] + [size * 10**6 for _ in DIRECTIONS for size in QUOTE_SIZES]
        return ["0x" + format(value, "064x") for value in values]


class CollectorReplicationTests(unittest.TestCase):
    def test_collection_is_block_pinned_and_checkpoint_resume_matches(self):
        endpoint = MockArchive()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw" / "history.json"
            def stop_after_first(progress):
                raise RuntimeError("intentional test interruption")
            with self.assertRaises(RuntimeError):
                collect(path, "1970-01-01T00:01:00Z", "1970-01-01T00:03:00Z", "https://unused.invalid/secret",
                        interval_minutes=1, client=endpoint, progress_callback=stop_after_first)
            self.assertFalse(path.exists())
            self.assertTrue(path.with_suffix(".json.checkpoint.json").exists())
            result = collect(path, "1970-01-01T00:01:00Z", "1970-01-01T00:03:00Z", "https://unused.invalid/secret",
                             interval_minutes=1, client=endpoint)
            self.assertEqual(result["snapshot_count"], 3)
            self.assertFalse(path.with_suffix(".json.checkpoint.json").exists())
            dataset = load_dataset(path)
            self.assertTrue(dataset["_coverage"]["nearest_prior_verified"])
            self.assertEqual([row["block_number"] for row in dataset["snapshots"]], [6, 12, 18])
            self.assertTrue(all(method == "eth_call" and params[1] in ("0x6", "0xc", "0x12") for method, params in endpoint.calls))
            self.assertNotIn("secret", path.read_text())
            replicated = replicate(dataset, "https://unused.invalid", 1.0, client=MockArchive())
            self.assertEqual(replicated["status"], "PASS")
            self.assertEqual(replicated["checked_blocks"], 3)


if __name__ == "__main__":
    unittest.main()
