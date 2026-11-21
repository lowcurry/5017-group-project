# Arithmetic fault experiment

This follow-up uses archived Curve 3pool observations from both historical events. It pairs a correct control with each of three injected errors per event. Both RPC recordings and all formal records remain frozen. The experiment uses generated fixtures and launches no live RPC or model calls.

| Injection | Changed quantity | Formal slots |
| --- | --- | --- |
| Denominator omission | Leave USDT out of the denominator of the nominal DAI share | R01/S1, R04/S1 |
| Token scale | Decode DAI with six decimals rather than 18 in the share calculation | R01/S1, R04/S1 |
| Extrema selection | Take the minimum sampled imbalance and its timestamp in a maximum query | R02/S2, R05/S2 |

For each pair, the script derives the correct value and time with exact fractions from raw integer balances. It checks the archived task, valid evidence, and publication outcome for three conditions. The shared-bug condition mutates the verification arithmetic in memory while preserving query metadata and the original archived inputs. The exact-fraction check remains outside the patched path. The denominator and scaling mutations use Decimal arithmetic. The extrema mutation changes the selection rule while retaining the correct data-window checks.

| Condition | V1 and V2, each | Published | Disagreed with the independent arithmetic reference |
| --- | --- | ---: | ---: |
| Correct controls | 6 PASS | 6/6 | 0/6 |
| Wrong report, current verifier | 6 FAIL | 0/6 | 6/6 |
| Wrong report, same injected bug in both verifier paths | 6 PASS | 6/6 | 6/6 |

Both verifier paths accepted the matching wrong values when they shared the injected bug. The independent reference detected all six disagreements. This is a counterfactual stress test, with two cases for each error type. The experiment does not measure how often these faults occur in live reports.

From the extracted package directory, run `python3 -B audit/arithmetic_faults/run_arithmetic_faults.py .`. Standard output reproduces `results.json`, including each verdict, reason and publication result.

## Independent arithmetic before publication

The updated report verification and batch commands both require an additional exact calculation for supported balance claims that pass V2. This covers nominal share, endpoint share change, maximum adjacent hourly share change and sampled imbalance maxima. The check uses the task request and allowed raw observations. It compares values, units, applicable times and evidence IDs. A numerical or time mismatch yields `FAIL`. Unavailable or incomplete evidence yields `UNKNOWN`. The publisher withholds both and applies its existing dependency rules. Quote arithmetic continues under V2.

| Same six P2 pairs | Published before | Published with the added check |
| --- | ---: | ---: |
| Correct controls | 6/6 | 6/6 |
| Wrong report, healthy V1 and V2 | 0/6 | 0/6 |
| Wrong report, shared V1 and V2 bug | 6/6 | 0/6 |

For both peak injections, a quote tied to the incorrect peak time also passed V2 and was withdrawn because its prerequisite failed the additional check. The batch regression uses the same twelve case inputs with archived data and a simulated model response. Run `python3 -B audit/arithmetic_faults/replay_publication_guard.py .` to reproduce `publication_guard_results.json`, or run `python3 -B -m unittest discover -s tests -p test_publication_arithmetic.py` for the batch and arithmetic checks.

The experiments share the same frozen observations. The new arithmetic check addresses shared verifier calculations and depends on the correctness of the captured inputs and metric definitions.
