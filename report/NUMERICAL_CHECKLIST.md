# MSBD5017 report numerical checks

Experimental archives are those in GitHub commit `e6a23a99bb8ff2ce788e133757e80ec01d4bca01`. Report edition: 27 November 2026.
All paths below are relative to that repository.

| Report location | Verified result | Archive |
|---|---|---|
| Pages 3 and 7 | Original replay, **36** scheduled decisions. Counts for correct automatic, wrong automatic and referred cases were **34 / 1 / 1** for raw reports and **26 / 0 / 10** with V2 publication. Eight of the nine additional referrals would have been correct. Recovery reduced referrals from **10 to 2** on the same cases. | `baseline/audit/decision_replay_20261112/results.json`, `audit/evidence_recovery/results/recovery_summary.json` |
| Pages 1 and 4 | Scheduled correct and reproducible coverage: G **34/48**, E **43/48**. G completed **16/18** scheduled reports, E **18/18**. | `baseline/experiments/formal_v1/result_summary.json` |
| Page 7 | June example H02_G_1: recovered **25** evidence IDs for the **09:00 UTC** sampled peak and restored the **10:00 UTC** quote of **1.002886 USDT/USDC** (rounded). The follow-up action switched from referral to the correct hour. | `audit/holdout_validation/2023-06-15/recovery-trace.json`, `decisions.csv` |
| Pages 1 and 6 | **36** historical decisions; fixed rule and logistic regression each **0.65** scenario units per decision; both neural variants **1.18**. The routing assumptions are **25%** referral after verification and **10%** remaining direct-use error loss. | `decision_cost/results/policy-summary.csv`, `decision_cost/scenario.json` |
| Pages 7 and 10 | Historical recovery restored **12** correct claims. The **90** fault cases include **56** non-clean cases and **34** clean controls. **0** false accepts. | `audit/evidence_recovery/results/recovery_summary.json` |
| Pages 1 and 7 | **54** paired holdout decisions; referrals **12 to 3**, correctly published claims **120 to 132**, observed automatic errors **0 to 0**. | `audit/holdout_validation/<date>/decisions.csv`, `published-claims.csv` and `summary.json` |
| Page 7 | False alerts **0/6** in both paths with zero negative referrals. All reference negatives came from repeated runs in June. October and January had no reference negatives, so their rates are undefined. | `audit/holdout_validation/alert-metrics.json` |
| Page 8 | **CNY 0.34073796** billed for three holdouts; **15 min 06 s** across two June reviews; median added wall time **3.82 ms** and **3.81 ms** per report in the newer windows. These measurements are reported separately. | `audit/holdout_validation/billing-reconciliation.json`, `human-timing-observed.json`, `timing-summary.json` |
| Page 2 | On 27 November 2026 the Morpho dashboard displayed 41 curators. Five distinct names had visible USDC or USDT vaults: Steakhouse Financial, Gauntlet, Sentora, Galaxy Curation and Keyrock. This is an initial screen of an adjacent market, with demand for the prototype unmeasured. | [Morpho Curation](https://data.morpho.org/curation), [Morpho Vaults](https://app.morpho.org/vaults) |

## Checks performed

- Matched the headline numbers to the stored evidence. The October proposal describes the initial design. The historical replay records the first measured system after the written proposal feedback.
- Replayed both publication paths for all 54 holdout reports and reproduced the archived alert metrics exactly.
- Rebuilt the historical evidence recovery outputs and compared them byte for byte with the archive.
- Checked the ten-page layout, Figure 1, Tables 1 to 9, and source links. Rebuilding the report produces an identical PDF.
- Ran all seven commands extracted from the PDF. The original decision replay, recovery summary, displayed scenario costs and alert metrics matched the stored results.

## Build the report

Use Python with `reportlab==4.4.9` and point the source at a checkout of the evidence snapshot.

```sh
python3 -m pip install reportlab==4.4.9
python3 build_report.py --repo /path/to/5017-group-project --output MSBD5017_Final_Project_Report.pdf
```
