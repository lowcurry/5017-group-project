# Presentation rehearsal and demo

The PowerPoint contains speaker notes. This is a five-minute practice version; adjust the timing to the course instructions when the presentation slot is assigned.

| Time | Slide | Speaking cue |
| --- | --- | --- |
| 0:00–0:30 | 1 | Can someone else reconstruct a stablecoin investigation’s key numbers? Show how the system preserves the evidence and gates publication. |
| 0:30–1:12 | 2 | One Ethereum pool, two event windows, 51 hourly snapshots, six tasks and 18 fixed answer components. A pool share or a directional quote cannot establish market-wide price or causality. |
| 1:12–1:59 | 3 | T applies known formulas. G and E share a model and limits. E gets guidance on citations, units, times and interpretation. V1 replays primary evidence and V2 checks the second source. The publisher checks dependencies and exposes verified fields. The archive keeps the original prose. |
| 1:59–2:46 | 4 | E covered 43/48 correct and replayable components versus G’s 34/48. Five answerable slots from the two G budget stops stay in G’s denominator. A sixth omitted slot was non-answerable. Excluding the two pairs with a G budget stop, both arms had 40/43 raw correct. Replayable correctness was G 34/43 and E 39/43. E used more model tokens. |
| 2:46–3:28 | 5 | P41 says −18.42 percentage points in its structured answer and prose. The frozen endpoints give about +18.42049 percentage points. Both verifiers rejected the sign error. The prose audit separately checks all 34 submitted model texts. |
| 3:28–4:08 | 6 | V1 rejected 7/12 faults; V2 rejected 10/12 and marked two unknown when source evidence was missing. Both accepted 12/12 clean cases. V1 and V2 agreed on all 102 formal claims. |
| 4:08–5:00 | 7 | Run the offline replay and point to the group counts. End with the evidence limits: one pool, two events and a small, fixed task set. |

## Live demo

Extract the submission ZIP. Open Terminal in the extracted project directory and run:

```sh
python3 -B docs/verify_delivery.py .
python3 -B -m curve_audit replay
```

Point to `pass: true`, `network_used: false`, `new_model_calls: 0`, 36 frozen model runs, six template runs, 64 fault verdicts and the G/E reproducible-correct counts. The replay uses archived model outputs. If screen sharing or Terminal fails, use the saved output at `results/submission_validation/formal_replay.json` and keep slide 5 as the worked example. To rehearse the separate engineering demo, run `python3 -B docs/reproduce_current.py`.

## Likely questions

**Did changing the metric after the pilot make it easier to get a positive result?** The local pilot showed a citation replayability contrast and no clear difference in unsupported statements. That outcome informed metric selection before the formal freeze. The frozen score requires both raw correctness and complete attribution, which the E prompt explicitly requests. It counts each scheduled answerable component, including omissions at budget stops. Excluding the two pairs with a G budget stop, raw correctness ties and the replayable gap is 11.63 percentage points. E had four replayable answers in the excluded pairs. The prose audit reports a separate descriptive outcome for the submitted texts.

**Why use a template baseline?** T shows what deterministic formulas achieve for the six predefined tasks. Its 16 answerable slots represent one pass per task, while each model condition repeats the tasks three times and has 48 slots. T’s perfect result is useful context for this fixed study; the comparison of prompt conditions is between G and E.

**What makes the verification independent?** V1 recalculates using archived inputs. V2 uses a separately captured RPC source for the relevant observations. Both providers still represent the same Ethereum state. Missing or conflicting evidence is surfaced rather than treated as agreement. In these formal reports V1 and V2 gave the same claim verdicts; the additional detections came from three injected block-identity faults. The local chain is an optional integrity demonstration.

**What do the A/B reference reviews establish?** The formal freeze used a consolidated human reference workbook. The additional workbooks arrived on 9 November after the freeze. WANG Hanyang (A) matched 18/18 and WU Yuxuan (B) matched 17/18 after three proportion-to-percentage conversions. On R06/S2, B recorded 1.11692608956 percentage points. He Yuequn adjudicated the discrepancy on 9 November 2026 and accepted A's value of 1.16926089569553 percentage points, which matches the frozen reference used for scoring. The decision is recorded in `audit/reference_review_20261109/REVIEW.md`. The workbook preserves the reviewers' original entries. Review isolation remains unverified.

**What did the prose reviewers check?** WANG Hanyang and WU Yuxuan each reviewed all 34 submitted reports on 9 November 2026 for 150 minutes. The team supplied WU Yuxuan's details separately. He Yuequn confirmed the reconciliation workbook on 10 November 2026. Of 91 numerical components, 90 match the frozen data and eight have incomplete original citations. All 11 observed refusals fit the evidence limits. The 62 reconciliation and explanation rows use different claim boundaries from the initial audit. Overall unsupported-claim and agreement rates remain unreported in the reconciliation. See `audit/prose_review_20261110/REVIEW.md`.

**Why no weak-model comparison?** The formal study holds the model fixed to isolate evidence guidance under shared limits. The study does not estimate how model strength changes the effect.

**What did verification cost?** Archived generation wall times sum to 191.602 s for G and 234.587 s for E. For 36 preloaded records, median offline times were 0.216 s for V1, 0.283 s for V2 and 0.002 s for in-memory publication. Two prose reviewers spent 150 minutes each and He Yuequn spent 120 minutes on adjudication. That is 7 person-hours across three people. Billing amounts and end-to-end elapsed time are unavailable. See `audit/RESOURCE_USE.md` for the separate RPC counts and timing details.
