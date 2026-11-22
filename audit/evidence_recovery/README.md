# Deterministic Attribution Recovery & Small-Sample Error Bounds

Offline audit module evaluating two methodological upgrades without altering any frozen files under `baseline/` or `decision_cost/`:

1. **Deterministic Attribution Recovery (`recovery.py`)**: Tests whether recovering missing window-level `evidence_ids` under strict safety invariants can eliminate false withdrawals of numerically correct claims without admitting any injected or shared-arithmetic faults.
2. **Exact Small-Sample Bounds & Clustering Disclosure (`bounds.py`)**: Computes zero-dependency Clopper-Pearson and Beta posterior upper bounds on observed zero/sparse automatic error rates, discloses historical event clustering (`n_events = 2`), and tests policy robustness under conservative high-risk error priors.

---

## 1. Six Safety Invariants (All Required for Recovery)

A claim is eligible for deterministic `evidence_ids` completion **only if** all six conditions hold:

1. `replay_claim(primary, task, claim)` returns `UNKNOWN` with exact reason `incomplete_attribution` (meaning query, unit, value, and target/interval timestamps already matched exact recomputation).
2. `claim["dataset_sha256"] == digest(primary)` (no forged or missing dataset fingerprint).
3. `claim["formula_version"] == VERSION` (no missing or mismatched formula version).
4. `expected["evidence_ids"]` is nonempty.
5. Original `claim["evidence_ids"]` is **nonempty and a strict subset** of `expected["evidence_ids"]` (rejects zero-citation claims and any claim citing excluded or out-of-window snapshots).
6. The recovered copy is **re-verified in full** through `verify_v2` (second-source block/raw-balance check), `publish_verified` (independent exact-fraction arithmetic guard), and task-dependency checks. Original reports are never mutated, and recovered IDs are recorded explicitly.

---

## 2. Offline Empirical Results

### 2.1 Formal 36 Runs (102 Emitted Structured Claims)

| Metric | G (ordinary) | E (evidence-guided) | Total (answerable = 96) |
| --- | ---: | ---: | ---: |
| Raw correct | 40/48 | 45/48 | 85/96 |
| Baseline guarded publication (`publish_verified`) | 31/48 | 42/48 | 73/96 |
| Guarded publication + deterministic recovery | **40/48** | **45/48** | **85/96** |
| Direct attribution recoveries (`incomplete_attribution`) | +6 | +2 | +8 |
| Cascade dependency recoveries (`withdrawn_dependency`) | +3 | +1 | +4 |
| False accepts on non-correct claims | **0** | **0** | **0** |

Offline median overhead across the 36 runs: `13.2 ms/run` (baseline guarded verification) vs. `24.5 ms/run` (with recovery + full re-verification).

### 2.2 Fault Safety Gates (Zero False Accepts Across 90 Cases: 34 Clean + 56 Fault/Non-Clean)

| Suite | Cases | Clean kept | Fault / non-clean cases | False accepts |
| --- | ---: | ---: | ---: | ---: |
| Frozen formal fault batch (`frozen_cases.json`) | 32 | 12/12 | 20 | **0** |
| Shared arithmetic fault pairs (including 6 adversarial partial-evidence variants) | 18 (6 clean + 12 bug) | 6/6 | 12 | **0** |
| Offline verification extension (`verification_extension_20261111`) | 40 | 16/16 | 24 | **0** |

### 2.3 Downstream 36 Decisions & Scenario Cost Impact

| Policy | To human / 36 | Auto errors | Average cost / decision | Raw → policy break-even loss (perfect / configured review) |
| --- | ---: | ---: | ---: | --- |
| `raw` | 1 | 1 | 3.17 | — |
| `verified` (baseline) | 10 | 0 | 2.27 | 52.2 / 67.7 (+9 reviews for 1 caught error) |
| **`verified_recovered`** | **2** | **0** | **0.78** | **12.2 / 14.2** (+1 review for 1 caught error) |
| `rule` / `rule_recovered` (default 5% high-risk prior) | 2 / 2 | 0 / 0 | 0.65 / 0.65 | — |

### 2.4 Small-Sample Bounds & Conservative High-Risk Prior Stress Test

> **Clustering disclosure**: The 36 scheduled decisions stem from **2 historical events** (`ftx_2022`, `usdc_2023`) across 6 task-slot pairs with 3 repeated runs per prompt group. All binomial and Beta bounds below are **nominal upper bounds under an i.i.d. assumption**; true out-of-window error estimation requires the held-out `2023-06-15` confirmation window.

- High-risk `concentration_alert` automatic releases under `raw`/`rule`/`logistic`/`verified_recovered`: **0/11 observed errors**
  - Nominal Clopper-Pearson 95% upper bound: **23.84%** (one-sided) / **28.49%** (two-sided)
  - Uniform `Beta(1, 1)` posterior mean: **7.69%**; 95% one-sided upper: **22.09%**
  - Jeffreys `Beta(0.5, 0.5)` posterior mean: **4.17%**; 95% one-sided upper: **15.69%**
- **Conservative prior cost check** (replacing the 5% scenario assumption for `concentration_alert` with the `Beta(1,1)` mean `7.69%`, Jeffreys 95% upper `15.69%`, or Clopper-Pearson 95% upper `23.84%`):
  - Without recovery, `rule` routes high-risk alerts to verification and suffers false withdrawals, raising referrals `2 → 5` and average cost `0.65 → 1.46`.
  - With recovery, **`rule_recovered`** stays at **2 referrals** and **0.71 average cost** (lowest across all three conservative priors), and even full verification **`verified_recovered`** (**0.78**) beats unrecovered `rule` (**1.46**).

---

## 3. Reproduce & Test

Run from the repository root (Python 3.10+, zero new third-party dependencies, no network or API keys required):

```bash
python3 -B audit/evidence_recovery/run_all.py --output /tmp/5017-recovery-check
diff -rq audit/evidence_recovery/results /tmp/5017-recovery-check
python3 -B -m unittest discover -s audit/evidence_recovery -p "test_*.py" -v
```
