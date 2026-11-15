# Curve 3pool effect metrics

Open the [browser report](figures/index.html) locally or download the [CSV](figures/metrics.csv) and [JSON](figures/metrics.json). The JSON contains the 50 quote observations, the study freeze digest and hashes of the directly read input records. The chart SVGs can be opened individually.

| Question | Chart | What the archived results show |
| --- | --- | --- |
| Does evidence guidance improve correct coverage? | [Formal coverage](figures/coverage.svg) | Scheduled correct and reproducible coverage rises from **34/48 to 43/48** (+18.75 pp). Among the 16 completed G/E pairs it rises from **34/43 to 39/43** (+11.63 pp). The exports also break out the two events. |
| Does the new arithmetic check catch shared verifier errors? | [Controlled fault cases](figures/arithmetic-faults.svg) | Published shared errors fall from **6/6 to 0/6**. Correct controls remain **6/6**. The two quotes tied to wrong peak times also go to withdrawal. |
| What does verification change downstream? | [Research decision outcomes](figures/research-decisions.svg) | Of 36 scheduled decisions, automatic wrong decisions fall from **1 to 0** and review referrals rise from **1 to 10**. Eight of the nine additional referrals were originally correct. |
| How much does small-order quote extrapolation overstate output? | [Historical quote errors](figures/quote-errors.svg) | The median absolute gap is **0.029438 USDT**, with a maximum of **2.872343 USDT** per 100,000 USDC quoted order. All five specified minimum-output thresholds show **0/50** disagreements each. |

The formal study records **104,166** model tokens for G and **129,007** for E. This corresponds to about **3,064** and **3,000** tokens per correct and reproducible component, respectively. The token measure is a ratio of aggregate recorded usage to correct components. Billing and live end-to-end time are outside its scope.

For a percentage row, divide `numerator` by `denominator` and multiply by 100. `pp` is a difference between two percentage values. A zero denominator produces a null value in JSON and a blank value in CSV. Research decision accuracy among automated actions excludes deferred actions, so read it alongside the review rate over all scheduled decisions. The formal results share two selected historical windows and repeated tasks. Fault cases are controlled injections. The quote replay measures archived quotes and rule decisions. Actual fills and returns were outside the evaluation.

## Reproduce

From the extracted package root, use Python 3.10 or newer. The script uses the standard library, consumes the included records and writes a new output directory. Open the resulting `index.html` in a browser.

```sh
python3 -B audit/effect_metrics/build.py --output /tmp/5017-effect-metrics
```

The generator cross-checks the scheduled and completed-pair counts against the archived reports, arithmetic case totals against individual decisions, the research replay against the formal publication digest, and the quote gaps and threshold outcomes against the saved observations.
