"""Unit and regression tests for deterministic attribution recovery and statistical bounds."""
from __future__ import annotations

from copy import deepcopy
import csv
import filecmp
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(HERE), str(ROOT / "baseline"), str(ROOT / "decision_cost")):
    if p not in sys.path:
        sys.path.insert(0, p)

from bounds import (
    beta_quantile,
    binomial_cdf,
    clopper_pearson_lower,
    clopper_pearson_upper,
    compute_statistical_bounds,
    rate_bounds,
    regularized_incomplete_beta,
)
from curve_audit import verification as verification_mod
from curve_audit.common import read_json
from curve_audit.current_study import digest, publish, template_report
from curve_audit.current_verify import verify_v2
from recovery import (
    publish_with_recovery,
    recover_attribution,
    replay_decision_cost_comparison,
    replay_extension_40,
    replay_faults_32,
    replay_formal_36,
    replay_shared_arithmetic_6,
)
from run_all import build_all


class EvidenceRecoveryAndBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / "baseline"
        prep = base / "results/current_preparation"
        cls.primary = read_json(prep / "dataset.json")
        cls.secondary = read_json(prep / "secondary_dataset.json")
        cls.primary_sha = digest(cls.primary)
        cls.tasks = {t["task_id"]: t for t in read_json(prep / "tasks.json")}
        cls.formal = replay_formal_36(ROOT)
        cls.decision_cmp = replay_decision_cost_comparison(ROOT, cls.formal)
        cls.bounds_report = compute_statistical_bounds(cls.decision_cmp)

    def test_01_recovers_strict_subset_and_preserves_input_immutability(self):
        task = self.tasks["R02"]
        clean, _ = template_report(self.primary, task)
        partial = deepcopy(clean)
        s2 = next(c for c in partial["claims"] if c["slot_id"] == "S2")
        s2["evidence_ids"] = [s2["evidence_ids"][-1]]  # 1/25 strict subset
        before_digest = digest(partial)

        out = publish_with_recovery(
            self.primary, self.secondary, task, partial, expected_primary_sha256=self.primary_sha
        )
        self.assertEqual(digest(partial), before_digest, "Input report must not be mutated")
        self.assertEqual(out["recovery_records"][s2["claim_id"]]["status"], "recovered")
        self.assertEqual(len(out["recovery_records"][s2["claim_id"]]["added_evidence_ids"]), 24)
        kept = {c["claim_id"] for c in out["published"]["claims"]}
        self.assertEqual(kept, {"R02_S1", "R02_S2", "R02_S3"})

    def test_02_adversarial_empty_evidence_ids_rejected(self):
        task = self.tasks["R02"]
        report, _ = template_report(self.primary, task)
        s2 = next(c for c in report["claims"] if c["slot_id"] == "S2")
        s2["evidence_ids"] = []
        out = publish_with_recovery(
            self.primary, self.secondary, task, report, expected_primary_sha256=self.primary_sha
        )
        self.assertEqual(out["recovery_records"][s2["claim_id"]]["status"], "rejected")
        self.assertEqual(out["recovery_records"][s2["claim_id"]]["reason"], "empty_original_evidence_ids")
        kept = {c["claim_id"] for c in out["published"]["claims"]}
        self.assertNotIn("R02_S2", kept)
        self.assertNotIn("R02_S3", kept)  # dependent quote stays withdrawn

    def test_03_adversarial_excluded_or_foreign_evidence_rejected(self):
        task = self.tasks["R03"]
        report, _ = template_report(self.primary, task)
        s2 = next(c for c in report["claims"] if c["slot_id"] == "S2")
        # Include the excluded 22:00Z timestamp alongside a valid one -> NOT a strict subset.
        s2["evidence_ids"] = [s2["evidence_ids"][0], "E_UTC_20230310T220000Z"]
        out = publish_with_recovery(
            self.primary, self.secondary, task, report, expected_primary_sha256=self.primary_sha
        )
        self.assertEqual(out["recovery_records"][s2["claim_id"]]["status"], "rejected")
        self.assertEqual(out["recovery_records"][s2["claim_id"]]["reason"], "original_evidence_not_strict_subset")
        self.assertNotIn("R03_S2", {c["claim_id"] for c in out["published"]["claims"]})

    def test_04_adversarial_mismatched_dataset_or_formula_metadata_rejected(self):
        task = self.tasks["R02"]
        for field, bad_val in (("dataset_sha256", "0" * 64), ("formula_version", "wrong-version")):
            report, _ = template_report(self.primary, task)
            s2 = next(c for c in report["claims"] if c["slot_id"] == "S2")
            s2["evidence_ids"] = s2["evidence_ids"][:1]
            s2[field] = bad_val
            out = publish_with_recovery(
                self.primary, self.secondary, task, report, expected_primary_sha256=self.primary_sha
            )
            self.assertEqual(out["recovery_records"][s2["claim_id"]]["status"], "rejected")
            self.assertEqual(out["recovery_records"][s2["claim_id"]]["reason"], "missing_or_mismatched_metadata")
            self.assertNotIn("R02_S2", {c["claim_id"] for c in out["published"]["claims"]})

    def test_05_adversarial_slight_numeric_error_never_recovered(self):
        task = self.tasks["R02"]
        report, _ = template_report(self.primary, task)
        s2 = next(c for c in report["claims"] if c["slot_id"] == "S2")
        s2["evidence_ids"] = s2["evidence_ids"][:1]
        s2["value"] = str(float(s2["value"]) + 0.01)
        out = publish_with_recovery(
            self.primary, self.secondary, task, report, expected_primary_sha256=self.primary_sha
        )
        self.assertNotIn(s2["claim_id"], out["recovery_records"])
        self.assertEqual(out["verification"][s2["claim_id"]]["verdict"], "FAIL")
        self.assertNotIn("R02_S2", {c["claim_id"] for c in out["published"]["claims"]})

    def test_06_reverse_check_shared_arithmetic_bug_blocked_even_after_recovery(self):
        """Reverse check: unguarded publish() would leak a shared-bug claim after recovery;
        publish_with_recovery() must intercept it via independent exact arithmetic."""
        arith_dir = str(ROOT / "baseline/audit/arithmetic_faults")
        if arith_dir not in sys.path:
            sys.path.insert(0, arith_dir)
        from replay_publication_guard import altered_report, injected_fault

        cases = read_json(ROOT / "baseline/audit/arithmetic_faults/results.json")["observations"]
        case = next(c for c in cases if c["fault"] == "minimum_instead_of_maximum")
        task = self.tasks[case["task"]]
        clean, _ = template_report(self.primary, task)
        wrong = altered_report(self.primary, task, clean, case)
        target = next(c for c in wrong["claims"] if c["slot_id"] == case["slot"])
        cid = target["claim_id"]
        target["evidence_ids"] = target["evidence_ids"][:1]  # Force attribution recovery to trigger

        with injected_fault(verification_mod, case["fault"]):
            recovered_rep, rec_map = recover_attribution(self.primary, task, wrong)
            self.assertEqual(rec_map[cid]["status"], "recovered")
            # Unguarded legacy path leaks the wrong claim once evidence_ids are recovered:
            v2_unguarded = verify_v2(
                self.primary, self.secondary, task, recovered_rep, expected_primary_sha256=self.primary_sha
            )
            pub_unguarded, _ = publish(self.primary, task, recovered_rep, checks_override=v2_unguarded)
            self.assertIn(cid, {c["claim_id"] for c in pub_unguarded["claims"]})

            # Full publish_with_recovery intercepts it via independent exact arithmetic:
            guarded = publish_with_recovery(
                self.primary, self.secondary, task, wrong, expected_primary_sha256=self.primary_sha
            )
            self.assertEqual(guarded["independent_arithmetic"][cid]["verdict"], "FAIL")
            self.assertNotIn(cid, {c["claim_id"] for c in guarded["published"]["claims"]})

    def test_07_formal_36_runs_exact_recovery_and_zero_false_accepts(self):
        by_g = self.formal["by_group"]
        tot = self.formal["totals"]
        self.assertEqual(by_g["G"]["baseline_published_correct"], 31)
        self.assertEqual(by_g["E"]["baseline_published_correct"], 42)
        self.assertEqual(by_g["G"]["recovered_published_correct"], 40)
        self.assertEqual(by_g["E"]["recovered_published_correct"], 45)
        self.assertEqual(tot["direct_attribution_recoveries"], 8)
        self.assertEqual(tot["cascade_dependency_recoveries"], 4)
        self.assertEqual(tot["total_recovered_correct"], 12)
        self.assertEqual(tot["false_accepts_on_non_correct_claims"], 0)
        self.assertEqual(tot["previously_published_lost"], 0)

    def test_08_all_fault_suites_have_zero_false_accepts(self):
        f32 = replay_faults_32(ROOT)
        self.assertEqual((f32["cases_tested"], f32["clean_kept"], f32["false_accepts"]), (32, 12, 0))
        s6 = replay_shared_arithmetic_6(ROOT)
        self.assertEqual((s6["pairs"], s6["clean_kept"], s6["false_accepts"]), (6, 6, 0))
        e40 = replay_extension_40(ROOT)
        self.assertEqual((e40["cases_tested"], e40["clean_kept"], e40["false_accepts"]), (40, 16, 0))

    def test_09_decision_cost_matches_baseline_and_improves_verified_policy(self):
        # Baseline 6 policies must match decision_cost/results/policy-summary.csv to numerical precision.
        with (ROOT / "decision_cost/results/policy-summary.csv").open(encoding="utf-8-sig") as f:
            expected_base = {row["policy"]: row for row in csv.DictReader(f)}
        actual = {row["policy"]: row for row in self.decision_cmp["policy_summaries"]}
        for pol, exp in expected_base.items():
            self.assertEqual(actual[pol]["to_human"], int(exp["to_human"]))
            self.assertEqual(actual[pol]["auto_error"], int(exp["auto_error"]))
            self.assertAlmostEqual(actual[pol]["average_cost"], float(exp["average_cost"]), places=12)

        vr = actual["verified_recovered"]
        self.assertEqual((vr["to_human"], vr["auto_error"], vr["high_risk_auto_error"]), (2, 0, 0))
        self.assertLess(vr["average_cost"], actual["verified"]["average_cost"])
        be = self.decision_cmp["break_even_raw_to_verified_recovered"]
        self.assertEqual((be["additional_reviews"], be["automatic_errors_intercepted"]), (1, 1))
        self.assertAlmostEqual(be["break_even_avoided_loss_perfect_review"], 12.2, places=6)
        self.assertAlmostEqual(be["break_even_avoided_loss_configured_review"], 14.2, places=6)

    def test_10_clopper_pearson_and_beta_closed_form_identities(self):
        # k = 0 analytical identities: two-sided 95% upper = 1 - 0.025^(1/n); one-sided = 1 - 0.05^(1/n)
        for n in (1, 8, 11, 26, 34):
            self.assertAlmostEqual(clopper_pearson_upper(0, n, 0.95, one_sided=False), 1.0 - 0.025 ** (1.0 / n), places=12)
            self.assertAlmostEqual(clopper_pearson_upper(0, n, 0.95, one_sided=True), 1.0 - 0.05 ** (1.0 / n), places=12)
            self.assertEqual(clopper_pearson_lower(0, n, 0.95), 0.0)
            # Uniform Beta(1, 1) posterior with k=0 is Beta(1, n+1), CDF = 1 - (1-x)^(n+1)
            self.assertAlmostEqual(beta_quantile(0.95, 1.0, n + 1.0), 1.0 - 0.05 ** (1.0 / (n + 1.0)), places=11)
            self.assertAlmostEqual(regularized_incomplete_beta(0.2, 1.0, n + 1.0), 1.0 - 0.8 ** (n + 1.0), places=12)

        # k > 0 binomial tail inversion check
        for k, n in ((1, 35), (2, 20), (5, 12)):
            u2 = clopper_pearson_upper(k, n, 0.95, one_sided=False)
            l2 = clopper_pearson_lower(k, n, 0.95, one_sided=False)
            self.assertAlmostEqual(binomial_cdf(k, n, u2), 0.025, places=10)
            self.assertAlmostEqual(1.0 - binomial_cdf(k - 1, n, l2), 0.025, places=10)
            self.assertLess(l2, k / n)
            self.assertGreater(u2, k / n)

        # Exact Beta(0.5, 0.5) arcsine CDF identity: I_x(0.5, 0.5) = (2/pi) * arcsin(sqrt(x))
        import math
        for x in (0.1, 0.25, 0.5, 0.75, 0.9):
            self.assertAlmostEqual(
                regularized_incomplete_beta(x, 0.5, 0.5),
                (2.0 / math.pi) * math.asin(math.sqrt(x)),
                places=11,
            )

    def test_11_bounds_report_clustering_disclosure_and_conservative_prior_stress(self):
        disc = self.bounds_report["clustering_and_independence_disclosure"]
        self.assertEqual(disc["distinct_historical_events"], 2)
        self.assertEqual(disc["scheduled_decisions"], 36)
        self.assertIn("i.i.d.", disc["warning"])

        stress = {r["risk_assumption"]: r for r in self.bounds_report["conservative_high_risk_prior_cost_check"]}
        for conservative_key in ("beta_1_1_posterior_mean", "jeffreys_95_one_sided_upper", "clopper_pearson_95_one_sided_upper"):
            row = stress[conservative_key]
            self.assertEqual(row["lowest_average_cost_policy"], "rule_recovered")
            self.assertLess(row["policies"]["rule_recovered"]["average_cost"], row["policies"]["rule"]["average_cost"])
            self.assertLess(row["policies"]["verified_recovered"]["average_cost"], row["policies"]["rule"]["average_cost"])

    def test_12_build_all_is_byte_for_byte_deterministic(self):
        with TemporaryDirectory() as td:
            out1 = Path(td) / "run1"
            out2 = Path(td) / "run2"
            build_all(out1, root=ROOT)
            build_all(out2, root=ROOT)
            expected_files = [
                "recovery_summary.json",
                "recovery_trace.csv",
                "decision_cost_comparison.csv",
                "statistical_bounds.json",
            ]
            for name in expected_files:
                self.assertTrue((out1 / name).is_file(), f"Missing {name}")
                self.assertTrue(filecmp.cmp(out1 / name, out2 / name, shallow=False), f"Non-deterministic output: {name}")


if __name__ == "__main__":
    unittest.main()
