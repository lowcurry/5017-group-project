# MSBD5017 report numerical checks

Evidence snapshot: GitHub `main` at `8dc906d65bc065ec579c3f3d6fbdf0049c599864`.
All paths below are relative to that repository.

| Report location | Verified result | Archive |
|---|---|---|
| Pages 1, 2 and 6 | Original replay, **36** scheduled decisions. Counts for correct automatic, wrong automatic and referred cases were **34 / 1 / 1** for raw reports and **26 / 0 / 10** with V2 publication. Eight of the nine additional referrals would have been correct. Recovery reduced referrals from **10 to 2** on the same cases. | `baseline/audit/decision_replay_20261112/results.json`, `audit/evidence_recovery/results/recovery_summary.json` |
| Pages 1 and 3 | Scheduled correct and reproducible coverage: G **34/48**, E **43/48**. G completed **16/18** scheduled reports, E **18/18**. | `baseline/experiments/formal_v1/result_summary.json` |
| Page 4 | June example H02_G_1: recovered **25** evidence IDs for the **09:00 UTC** sampled peak and restored the **10:00 UTC** quote of **1.002886 USDT/USDC** (rounded). The follow-up action switched from referral to the correct hour. | `audit/holdout_validation/2023-06-15/recovery-trace.json`, `decisions.csv` |
| Page 5 | **36** historical decisions; fixed rule and logistic regression each **0.65** scenario units per decision; both neural variants **1.18**. The routing assumptions are **25%** referral after verification and **10%** remaining direct-use error loss. | `decision_cost/results/policy-summary.csv`, `decision_cost/scenario.json` |
| Pages 6 and 9 | Historical recovery restored **12** correct claims. The **90** fault cases include **56** non-clean cases and **34** clean controls. **0** false accepts. | `audit/evidence_recovery/results/recovery_summary.json` |
| Pages 1 and 6 | **54** paired holdout decisions; referrals **12 to 3**, correctly published claims **120 to 132**, observed automatic errors **0 to 0**. | `audit/holdout_validation/<date>/decisions.csv`, `published-claims.csv` and `summary.json` |
| Page 6 | False alerts **0/6** in both paths with zero negative referrals. All reference negatives came from repeated runs in June. October and January had no reference negatives, so their rates are undefined. | `audit/holdout_validation/alert-metrics.json` |
| Page 7 | **CNY 0.34073796** billed for three holdouts; **15 min 06 s** across two June reviews; median added wall time **3.82 ms** and **3.81 ms** per report in the newer windows. These measurements are reported separately. | `audit/holdout_validation/billing-reconciliation.json`, `human-timing-observed.json`, `timing-summary.json` |

## Checks performed

- Matched the headline numbers to the stored evidence. The October proposal describes the initial design. The historical replay records the first measured system after the written proposal feedback.
- Replayed both publication paths for all 54 holdout reports and reproduced the archived alert metrics exactly.
- Rebuilt the historical evidence recovery outputs and compared them byte for byte with the archive.
- Rendered and reviewed all nine pages, including Figure 1 and Tables 1 to 8. A second PDF build produced identical bytes.
- Ran all seven commands extracted from the PDF. The original decision replay, recovery summary, displayed scenario costs and alert metrics matched the stored results.

## Build the report

Use Python with `reportlab==4.4.9` and point the source at a checkout of the evidence snapshot.

```sh
python3 -m pip install reportlab==4.4.9
python3 build_report.py --repo /path/to/5017-group-project --output MSBD5017_Final_Project_Report.pdf
```
