# MSBD5017 report numerical checks

Evidence snapshot: GitHub `main` at `53833d20b39f92e27e9dd3ddb1a46a90e63651e1`.
All paths below are relative to that repository.

| Report location | Verified result | Archive |
|---|---|---|
| Pages 1 and 3 | Scheduled correct and reproducible coverage: G **34/48**, E **43/48**. G completed **16/18** scheduled reports, E **18/18**. | `baseline/experiments/formal_v1/result_summary.json` |
| Page 2 | June example H02_G_1: recovered **25** evidence IDs for the **09:00 UTC** sampled peak and restored the **10:00 UTC** quote of **1.002886 USDT/USDC** (rounded). The follow-up action switched from referral to the correct hour. | `audit/holdout_validation/2023-06-15/recovery-trace.json`, `decisions.csv` |
| Page 4 | **36** historical decisions; fixed rule and logistic regression each **0.65** scenario units per decision; both neural variants **1.18**. | `decision_cost/results/policy-summary.csv` |
| Pages 5 and 8 | Historical recovery restored **12** correct claims. The **90** fault cases include **56** non-clean cases and **34** clean controls. **0** false accepts. | `audit/evidence_recovery/results/recovery_summary.json` |
| Pages 1 and 5 | **54** paired holdout decisions; referrals **12 to 3**, correctly published claims **120 to 132**, observed automatic errors **0 to 0**. | `audit/holdout_validation/<date>/decisions.csv`, `published-claims.csv` and `summary.json` |
| Page 5 | False alerts **0/6** in both paths with zero negative referrals. All reference negatives came from repeated runs in June. October and January had no reference negatives, so their rates are undefined. | `audit/holdout_validation/alert-metrics.json` |
| Page 6 | **CNY 0.34073796** billed for three holdouts; **15 min 06 s** across two June reviews; median added wall time **3.82 ms** and **3.81 ms** per report in the newer windows. These measurements are reported separately. | `audit/holdout_validation/billing-reconciliation.json`, `human-timing-observed.json`, `timing-summary.json` |

## Checks performed

- Matched the headline numbers in the generated PDF to the stored evidence.
- Replayed both publication paths for all 54 holdout reports and reproduced the archived alert metrics exactly.
- Rebuilt the historical evidence recovery outputs and compared them byte for byte with the archive.
- Rendered and reviewed all eight pages. A second PDF build produced identical bytes.

## Build the report

Use Python with `reportlab==4.4.9` and point the source at a checkout of the evidence snapshot.

```sh
python3 -m pip install reportlab==4.4.9
python3 build_report.py --repo /path/to/5017-group-project --output MSBD5017_Final_Project_Report.pdf
```
