# Recorded resource use

## Formal generation

The 36 scheduled G/E records and 133 model receipts give these totals. G and E shared the same per-run limits. Generation wall time sums the individual recorded runs, including the two G budget stops. The template used zero model calls.

| Group | Model calls | Input tokens | Cached input tokens | Output tokens | Total tokens | Generation wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| G | 63 | 85,883 | 66,176 | 18,283 | 104,166 | 191.602 s |
| E | 70 | 106,443 | 83,317 | 22,564 | 129,007 | 234.587 s |

## Shared historical acquisition

The formal runs shared pre-acquired data. The four bound Alchemy and dRPC collections record 2,250 base RPC attempts and 306 supplemental ERC-20 JSON-RPC request items. The two PublicNode attempts to collect the windows are shown separately because those records did not supply the formal snapshots.

| Provider | Base RPC attempts | Base retries | Sum of logged base attempt times | Supplemental RPC items |
| --- | ---: | ---: | ---: | ---: |
| Alchemy | 1,125 | 0 | 821.629 s | 153 |
| dRPC | 1,125 | 0 | 918.308 s | 153 |
| PublicNode, unbound attempts | 615 | 0 | 476.839 s | — |

Base retries are transcript rows with an attempt number greater than one. Supplemental items were batched and the supplemental records supply item counts without comparable per-attempt timing or retry fields. The base attempt times measure recorded RPC activity, so they cannot be used as the total acquisition wall time or allocated to each model run.

## Packaged verification and publication

Five batches per stage used the 36 preloaded records and 102 emitted claims on macOS arm64, Python 3.12.14. Median wall time was 0.216 s for V1, 0.283 s for V2 and 0.002 s for in-memory publication from the saved V2 verdicts. Each batch matched the saved outcomes. The timing starts after file loading and source binding and ends before output serialization and comparison. It used zero model or RPC calls. The timed stages are separate from the archived generation and acquisition records.

`resource_use_20261109.json` records the measurements and current source hashes. To recompute from the submission root, run `python3 -B audit/recompute_resource_use.py`. The earlier `results/submission_validation/verifier_timing.json` remains a diagnostic for the earlier verifier revision.

## Human review and adjudication

Three team members took part. WANG Hanyang and WU Yuxuan each spent 150 minutes reviewing the prose. The team confirmed that He Yuequn spent 120 minutes on prose adjudication. These activities total 420 minutes, or 7 person-hours. The record covers this review and adjudication and provides no estimate of overall project labor. See [the prose review records](prose_review_20261110/REVIEW.md).

The original live workflow's billing amounts and end-to-end time remain unavailable. A later [serial offline replay](cost_trace_20261111/README.md) measured a median **0.901 s** from loading recorded inputs through checked publication across five samples. Its stage times and reusable cost ledger are under `audit/cost_trace_20261111/`.
