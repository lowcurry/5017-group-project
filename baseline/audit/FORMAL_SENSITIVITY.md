# Scheduled runs and completed pairs

The frozen primary comparison includes every scheduled answerable component. The completed-pair comparison keeps task and repetition fixed and excludes both arms of any pair with a stopped run. The two excluded pairs are R01 repetition 1 and R06 repetition 1. In both cases, G stopped at its prepaid input-token limit. These two pairs account for five answerable components and one refusal component per arm.

| Scope | Comparison | Raw correct G / E | Reproducible correct G / E | Correct after publication G / E | Reproducible gap, E minus G |
| --- | --- | --- | --- | --- | ---: |
| Overall | All 18 scheduled pairs | 40/48 · 45/48 | 34/48 · 43/48 | 31/48 · 42/48 | +18.75 pp |
| Overall | 16 completed pairs | 40/43 · 40/43 | 34/43 · 39/43 | 31/43 · 38/43 | +11.63 pp |
| USDC, March 2023 | All 9 scheduled pairs | 18/24 · 22/24 | 15/24 · 22/24 | 14/24 · 22/24 | +29.17 pp |
| USDC, March 2023 | 8 completed pairs | 18/21 · 19/21 | 15/21 · 19/21 | 14/21 · 19/21 | +19.05 pp |
| FTX, November 2022 | All 9 scheduled pairs | 22/24 · 23/24 | 19/24 · 21/24 | 17/24 · 20/24 | +8.33 pp |
| FTX, November 2022 | 8 completed pairs | 22/22 · 21/22 | 19/22 · 20/22 | 17/22 · 19/22 | +4.55 pp |

The reproducible-coverage gap is larger in the USDC window under both counting rules. Across completed pairs, raw correctness is equal overall. The two groups still differ in reproducible correctness, which requires both the right value and complete evidence attribution. The scheduled comparison captures budget stops as part of performance. The completed-pair comparison describes the subset that reached completion in both arms. The same pool, tasks and observations recur across runs, so the figures describe this study and do not provide an inference across events or markets.

Run `python3 -B audit/compare_run_sets.py` from the extracted submission root. The script matches each saved run to the schedule and to its original hash in `publication_replay.json`. It derives window IDs from the frozen task list and checks scheduled totals against `result_summary.json`. Publication counts come from the corrected publication replay.
