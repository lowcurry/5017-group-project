"""Local integration tests only: no model inference or network is performed."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from curve_audit import cli
from curve_audit.common import read_json, write_json
from curve_audit.data import load_dataset
from curve_audit.evidence import build_package
from curve_audit.metrics import calculate

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data/raw/pilot_archive.json"
START, END = "2023-03-10T18:00:00Z", "2023-03-11T18:00:00Z"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.dataset = load_dataset(PILOT)

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, arguments):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = cli.main(["--dataset", str(PILOT), *arguments])
        return result, stream.getvalue()

    def create_demo(self):
        path = self.directory / "demo"
        code, text = self.command(["investigate", "--start", START, "--end", END,
                                   "--question", "USDC占比在采样时点的最大值", "--output", str(path)])
        self.assertEqual(0, code, text)
        return path

    def test_inventory_runs_locally_and_reports_actual_coverage(self):
        code, text = self.command(["inventory"])
        self.assertEqual(0, code)
        result = json.loads(text)
        self.assertEqual(28, result["snapshot_count"])
        self.assertEqual(25, len({s["block_hash"] for s in self.dataset["snapshots"] if s["series"] == "hourly"}))
        self.assertEqual([], result["missing_hourly_targets"])

    def test_deterministic_investigation_and_verify_preserve_peak_evidence_time(self):
        path = self.create_demo()
        report = read_json(path / "evidence/report.json")
        calculations = read_json(path / "evidence/calculations.json")
        expected = calculate(self.dataset, {"metric": "share_pct", "operation": "maximum",
                                           "start": START, "end": END, "coin": "USDC"})
        self.assertEqual(1, len(report["claims"]))
        self.assertEqual(expected["value"], report["claims"][0]["value"])
        self.assertEqual(expected["evidence_ids"], report["claims"][0]["evidence_ids"])
        self.assertEqual(25, len(report["claims"][0]["evidence_ids"]))
        self.assertEqual(expected["observed_at"], calculations[0]["observed_at"])
        self.assertEqual(expected["target_timestamp_utc"], calculations[0]["target_timestamp_utc"])
        self.assertNotEqual(calculations[0]["observed_at"], calculations[0]["target_timestamp_utc"])
        self.assertFalse(read_json(path / "evidence/metadata.json")["inference_performed"])
        code, text = self.command(["verify", str(path / "evidence")])
        self.assertEqual(0, code, text)
        self.assertTrue(json.loads(text)["pass"])

    def test_corrupted_report_returns_nonzero_exit(self):
        package = self.create_demo() / "evidence"
        with (package / "report.md").open("a", encoding="utf-8") as stream:
            stream.write("\nInjected unverified edit\n")
        code, text = self.command(["verify", str(package)])
        self.assertEqual(1, code)
        result = json.loads(text)
        self.assertFalse(result["pass"])
        self.assertTrue(any("hash mismatch" in error for error in result["errors"]))
        self.assertTrue(any("deterministic" in error for error in result["errors"]))

    def test_empty_report_cannot_become_verified_even_with_fresh_hashes(self):
        package = self.directory / "empty_package"
        result = build_package(package, self.dataset,
                               {"task_id": "empty", "answer": "", "claims": [],
                                "refusal_reason": None, "faults": []}, [], [], {})
        self.assertFalse(result["verification"]["pass"])
        code, text = self.command(["verify", str(package)])
        self.assertEqual(1, code)
        self.assertTrue(any("at least one" in e for e in json.loads(text)["errors"]))

    def test_wrong_chain_dataset_is_rejected_before_inventory(self):
        invalid = copy.deepcopy(self.dataset)
        invalid["chain_id"] = 10
        path = self.directory / "wrong_chain.json"
        write_json(path, invalid)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            code = cli.main(["--dataset", str(path), "inventory"])
        self.assertEqual(1, code)
        self.assertEqual("FAILED", json.loads(stream.getvalue())["status"])

    def test_offline_experiment_records_baseline_without_claiming_completed_ai_runs(self):
        destination = self.directory / "offline_experiment"
        code, text = self.command(["experiment", "--provider", "offline", "--output", str(destination)])
        self.assertEqual(0, code, text)
        status = read_json(destination / "status.json")
        self.assertEqual(168, status["planned_outputs"])
        self.assertEqual(24, status["recorded_outputs"])
        self.assertEqual(0, status["failed_outputs"])
        self.assertFalse(status["all_planned_completed"])
        outputs = read_json(destination / "outputs.json")
        self.assertTrue(all(row["condition"] == "fixed_template" and not row["metadata"]["inference_performed"] for row in outputs))
        scores = read_json(destination / "evaluation/scores.json")
        self.assertEqual(1, scores["aggregates"]["fixed_template/all"]["pooled"]["effective_answer_coverage"])
        self.assertEqual("not_performed", scores["human_review_status"])

    def test_all_failed_model_runs_are_retained_scored_and_return_nonzero(self):
        def fake_failure(dataset, tasks, condition, output_dir, model):
            return [{"task_id": task["task_id"], "condition": condition, "status": "failed",
                     "report": {"task_id": task["task_id"], "answer": "", "claims": [], "refusal_reason": None, "faults": []},
                     "metadata": {"inference_performed": False, "error": "test provider unavailable"}}
                    for task in tasks]
        destination = self.directory / "failed_experiment"
        with patch("curve_audit.cli.run_batch", fake_failure):
            code, text = self.command(["experiment", "--provider", "codex-cli", "--repetitions", "1", "--output", str(destination)])
        self.assertEqual(1, code, text)
        status = read_json(destination / "status.json")
        self.assertEqual(72, status["recorded_outputs"])
        self.assertEqual(48, status["failed_outputs"])
        self.assertFalse(status["all_planned_completed"])
        scores = read_json(destination / "evaluation/scores.json")
        for condition in ("ordinary_agent", "evidence_grounded_agent"):
            aggregate = scores["aggregates"][condition + "/all"]
            self.assertEqual(24, aggregate["failed_runs"])
            self.assertEqual(0, aggregate["pooled"]["numeric_accuracy"])
            self.assertEqual(0, aggregate["pooled"]["effective_answer_coverage"])

    def test_replay_rejects_another_valid_dataset_hash(self):
        # Set up saved local artifacts, then change only provenance metadata.
        replay_root = self.directory / "project"
        demo_path = replay_root / "results/demo"
        with contextlib.redirect_stdout(io.StringIO()):
            cli.demo(self.dataset, demo_path, START, END, "USDC占比最大值", "deterministic", "unused")
            cli.experiment(self.dataset, replay_root / "results/formal_experiment", 3, "offline", "unused")
        changed = copy.deepcopy(self.dataset)
        changed["retrieved_at_utc"] = "2026-10-10T00:00:00Z"
        changed_path = self.directory / "valid_but_different.json"
        write_json(changed_path, changed)
        with patch("curve_audit.cli.ROOT", replay_root), contextlib.redirect_stdout(io.StringIO()) as stream:
            code = cli.main(["--dataset", str(changed_path), "replay"])
        self.assertEqual(1, code)
        self.assertIn("differs from the frozen", stream.getvalue())
        self.assertFalse((replay_root / "results/replay_evaluation").exists())


if __name__ == "__main__":
    unittest.main()
