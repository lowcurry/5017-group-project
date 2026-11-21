# Offline timing and cost records

Run from the extracted submission directory with Python 3.10 or later.

```sh
python3 -B audit/cost_trace_20261111/replay_cost.py
```

The command prints JSON. Use `--samples 5 --output /tmp/5017-offline-cost.json` to save five samples to a new file. The replay writes one publication file per sample to a temporary directory and compares it with the archived verified projection. It verifies the two bound RPC captures and all 36 archived model runs, including 102 emitted claims. Each sample uses zero new RPC or model calls.

## Measured replay

On macOS arm64 and Python 3.12.14, the median time across five serial samples was **0.901 s**. The timings start with input loading and end when the written publication matches the archived projection. The start point follows interpreter startup and temporary directory creation. The publication write uses the packaged atomic JSON writer, so this measures local serialization and writing. Per-phase medians are computed independently.

| Phase | Median seconds |
| --- | ---: |
| Load and check archived inputs | 0.009 |
| Load and bind recorded RPC captures | 0.400 |
| Run V1 | 0.210 |
| Run V2 | 0.280 |
| Build and write publication JSON | 0.005 |
| Compare verdicts and publication against the archive | 0.001 |

`offline_timing.json` preserves the five individual measurements, the freeze hash and the relevant code hashes. The [resource record](../RESOURCE_USE.md) preserves historical RPC attempts, model receipts and human review time separately. The original live workflow's end-to-end time and billed amounts remain unmeasured.

## Recording future runs

The standard-library `RunLedger` in `ledger.py` captures elapsed run and stage time, dispatched RPC and model attempts, provider-reported token usage and actual attributable charges. Create it immediately before the first phase in a future runner and take a snapshot in a `finally` block, so interrupted runs retain their recorded phases. Write that snapshot to a new JSON file after the last phase. The existing offline replay demonstrates the stage timing.

| Method | Record |
| --- | --- |
| `phase(name)` | Wrap each stage in `with ledger.phase(name)`. Use stages for live acquisition, generation, verification and publication. A failed stage retains its elapsed time and error status. The run clock also captures time between stages and retry delays. |
| `attempt(kind, provider, operation_id, number, outcome, seconds=..., usage=...)` | Record each dispatched attempt, including timeouts and failed calls. For retries, reuse the logical operation ID and increment the attempt number. Give later model steps their own operation IDs. Record the elapsed seconds if measured. For model usage pass `input_tokens`, `output_tokens` and, when the receipt reports it, `cached_input_tokens`. Leave usage absent if the receipt is missing. |
| `charge(provider, amount, currency, evidence_id)` | Record each attributable actual charge once, using decimal text, a currency code and an invoice or receipt reference. Keep currencies separate. An empty charge map indicates that the ledger contains no billing evidence. |
| `snapshot()` | Export the run clock, timed stages, raw observations, retries, known token totals and counts of incomplete receipts. Keep separate ledgers for different runs. Use one writer per ledger and use provider names or sanitized IDs in place of connection strings or request bodies. |

Count RPC network attempts and supplemental JSON-RPC items with distinct units. For a batch, use one ledger attempt for the dispatch and retain its individual request count in the RPC transcript. A budget stop before dispatch belongs in the generation stage outcome and usage summary, while attempt counts track requests that were sent. Add an invoice reference only when a charge can be tied to the run or allocated with a recorded method.
