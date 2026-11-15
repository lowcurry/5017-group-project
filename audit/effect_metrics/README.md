# Curve 3pool effect metrics

Open the [browser report](figures/index.html) locally. The [CSV](figures/metrics.csv) contains one row per metric and analysis slice. The [JSON](figures/metrics.json) adds all 18 paired comparisons, the 50 quote observations, and direct input hashes. The seven figures are generated from those data by [plots.py](plots.py) using Matplotlib.

| Question | Figure | Result in the archived data |
| --- | --- | --- |
| How many correct components survive each stage? | [Coverage](figures/coverage.svg) | Correct and reproducible coverage increases from 34/48 to 43/48 (+18.75 pp). Among completed pairs it increases from 34/43 to 39/43 (+11.63 pp). |
| Are gains spread across paired reports? | [Paired effects](figures/paired-effects.svg) | On 16 completed pairs, E improves 5, ties 10 and trails on 1 for correct and reproducible components. Dropping one entire task at a time leaves a +5.88 to +14.71 pp difference. |
| How often do the verifiers detect a known defect? | [Verifier fault outcomes](figures/verifier-faults.svg) | Among 12 injected faults, V1 returns 7 FAIL and V2 returns 10 FAIL plus 2 UNKNOWN. Both accept 12/12 clean controls. |
| Are shared arithmetic faults contained? | [Shared arithmetic tests](figures/arithmetic-faults.svg) | Incorrect releases fall from 6/6 to 0/6. Correct releases stay at 6/6 and the two dependent quotes are withheld. |
| How much work moves to manual review? | [Research decisions](figures/research-decisions.svg) | Automatic errors fall from 1 to 0. Review referrals rise from 1/36 to 10/36. One of the nine additional referrals was initially wrong. |
| How large are historical quote errors? | [Quote error distribution](figures/quote-errors.svg) | The median absolute gap is 0.029438 USDT, the 90th percentile is 0.137377 USDT, and the maximum is 2.872343 USDT per 100,000 USDC quoted order. Five thresholds each have 0/50 eligibility disagreements. |
| How much model usage goes into a usable result? | [Model usage and yield](figures/efficiency.svg) | G and E recorded 104,166 and 129,007 model tokens. Per correct and reproducible result this is about 3,064 and 3,000 tokens, respectively. The observed difference is 24,841 tokens for 9 additional correct and reproducible components. |

## Metric definitions

- **Coverage and pairing.** Coverage divides correct components by scheduled answerable components. The completed cohort excludes both runs of any pair with a budget stop. Wins, ties and losses compare the count of correct components for the same task and repetition. The leave-one-task-out range is a sensitivity check across six tasks, with no confidence interval interpretation. Exports include results by event and by task.
- **Verifier faults.** Strict detection counts FAIL as a positive on the 12 injected faults and the 12 clean controls. Precision, recall and F1 use that definition. Nonacceptance counts both FAIL and UNKNOWN on injected faults. Other cases, such as intentional refusal and source conflicts, have their own verdict counts.
- **Research decisions.** Automation coverage divides accepted automatic actions by all scheduled opportunities. Automated accuracy divides correct automatic actions by all automatic actions. Report these with the defer rate and the fraction of additional referrals that were initially wrong. Results are also available by group and decision rule.
- **Quote error.** Signed bias is extrapolated minus size-matched output. Relative absolute error uses the size-matched quote as denominator, in basis points. The 90th percentile is linearly interpolated at zero-based index (0.9(n-1)). The ECDF plots the share of archived observations at or below each absolute error. Tested thresholds include absolute, false and missed eligibility comparisons as recorded by the replay.
- **Efficiency.** Token ratios divide aggregate recorded usage by the specified result count. Recorded run time is summed per run. This snapshot covers two historical windows and repeated tasks. Billing, realized returns and live execution are outside its scope.

Percentages show the numerator and denominator where applicable. A zero denominator yields a blank value in CSV and null in JSON.

## Reproduce

Run from the extracted project directory with Python 3.10 or newer. Install the plotting dependency once in the chosen Python environment. The build itself reads only the packaged records and needs no API keys or network.

```sh
python3 -m pip install -r audit/effect_metrics/requirements.txt
python3 -B audit/effect_metrics/build.py --output /tmp/5017-effect-metrics
```

Use a new output directory. Open its `index.html` in a browser. To export the CSV and JSON with only the Python standard library, use `--metrics-only`. The generator checks scheduled counts against the archived reports, outcome counts against individual replay decisions, and quote error and eligibility against source observations.
