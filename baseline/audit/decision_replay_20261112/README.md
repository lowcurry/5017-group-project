# Historical research and quote-screening decisions

This retrospective evaluation uses the formal archive's Ethereum Curve 3pool observations from the USDC and FTX stress windows. It measures two practical workflows using explicit decision rules. The original experiment and its scores are kept as recorded.

## Research decisions from model reports

Apply the same rules to each raw structured report and to its archived publication after V2 verification and dependency withdrawal. Derive benchmark decisions separately from the frozen task definitions and integer balances and quotes. Recompute the references on both archived source datasets and compare with the frozen references.

| Workflow | Task and slot | Rule |
| --- | --- | --- |
| Label net change for a research note | R02/S1 and R05/S1 | Mark nominal USDC share as increasing, decreasing or unchanged using the sign of its window change. |
| Choose a follow-up hour | R02/S2 and R05/S2 | Use the reported UTC hour of peak sampled imbalance to select the episode for review. Compare the selected hour exactly. |
| Flag a concentration episode | R03/S2 and R06/S2 | Escalate when the largest eligible one-hour rise in nominal USDC share is at least 1 percentage point. |

The 36 decisions comprise six task-slot pairs, two model groups and three runs per pair and group. The denominator includes the scheduled R06 budget stop. Both workflows send missing, ambiguous or unusable input to review. The raw workflow checks the submitted slot, status, unit and decision field. The verified workflow also requires that the archived publisher retained the claim. A correct decision can come from a nonconforming input that happens to give the same action. The trace records the archived input score separately from the action and names the reasons for withdrawals. Correct, wrong and deferred decisions are mutually exclusive. A deferral represents a request for manual checking, with no assumption about the eventual result or time spent.

## Quote screening at the historical blocks

For each of the 25 hourly targets in each formal event window, screen a hypothetical 100,000 USDC to USDT order against a minimum quoted output. Compare two methods using the same block and direction.

1. Small-order proxy. Multiply the quoted USDT output for 1,000 USDC by 100.
2. Size-matched quote. Use the archived contract quote for the full 100,000 USDC input.

With a haircut of \(h\) basis points below nominal 1:1 parity, a quote passes if its implied output is at least \(100{,}000 - 10h\) USDT. Evaluate 1, 5, 10, 25 and 50 bps, highlighting 10 bps as an illustrative case. False eligibility means the proxy would pass a quote that the size-matched contract quote places below the floor. Missed eligibility is the reverse. Report the largest quoted shortfall below the floor among false eligibilities, in USDT per hypothetical order. The benchmark itself uses the size-matched quote, so the comparison isolates the cost of extrapolating a smaller quote. There are 50 historical targets per threshold. The extra follow-up snapshot beyond the USDC event window is outside this screen.

The quote comparison measures the size-aware data tool. The research comparison measures the downstream effect of applying the verification gate to the same reports. Both outcomes describe rule-based actions in one pool, one trade direction and two previously selected windows. The sample includes repeated model outputs. Analyst behavior, actual fills, gas, transaction ordering, alternative venues, returns and net profit are outside the measured outcome.

## Results

| Research rule | Raw correct / wrong / deferred | Verified correct / wrong / deferred |
| --- | ---: | ---: |
| Direction label | 11 / 1 / 0 | 11 / 0 / 1 |
| Follow-up hour | 12 / 0 / 0 | 7 / 0 / 5 |
| Concentration alert | 11 / 0 / 1 | 8 / 0 / 4 |
| All 36 decisions | **34 / 1 / 1** | **26 / 0 / 10** |

The gate routed a wrong decreasing label to review. The frozen endpoints show a rise of 18.42049 percentage points for that case. It also routed eight correct decisions to review because their citations did not cover the required observations. Five of those were peak-hour decisions. Among the nine additional referrals, one would have been an incorrect automated decision. Manual review duration and outcomes were outside this replay.

| Haircut below parity, bps | Minimum output, USDT | Eligible with proxy / size-matched | Differing decisions |
| ---: | ---: | ---: | ---: |
| 1 | 99,990 | 40 / 40 | 0/50 |
| 5 | 99,950 | 47 / 47 | 0/50 |
| 10 | 99,900 | 47 / 47 | 0/50 |
| 25 | 99,750 | 48 / 48 | 0/50 |
| 50 | 99,500 | 48 / 48 | 0/50 |

The small-order proxy overestimated output at all 50 historical targets. The median difference was 0.029438 USDT, and the maximum was 2.872343 USDT per 100,000 USDC. At block 16,801,143, the proxy indicated 99,205.340800 USDT while the size-matched quote indicated 99,202.468457 USDT. Only floors strictly above the latter and at or below the former would change the action at this block. All five illustrated floors give the same decision in both workflows. This sample demonstrates quote estimation error and quantifies the decisions where verification shifted work to review.

## Reproduce

Run from the extracted submission root.

```sh
python3 -B audit/decision_replay_20261112/replay.py .
```

The JSON printed to standard output matches `results.json`. It includes all research actions, the exact quote comparisons and cutoff results for each target, event totals and input digests. It requires neither credentials nor network access. Use `python3 -B -m curve_audit replay` to reproduce and verify the upstream publication record.
