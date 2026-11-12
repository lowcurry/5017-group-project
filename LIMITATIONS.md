# Scope, provenance and known limits

This file records what the 12 November 2026 handoff does and does not establish. It is part of the public handoff. If a statement in the report, slide deck or README conflicts with this file, this file is the current public position.

## 1. Study scope

- One pool (Curve 3pool on Ethereum), two historical windows (FTX, November 2022; USDC depeg, March 2023), six tasks with 18 required components (16 answerable, two non-answerable), one model (DeepSeek Flash), and three scheduled runs per model condition.
- A descriptive case study. The results do not generalise across protocols, markets or model families, and the repeated runs are not independent historical events.
- No live trading, fills, gas, P&L, issuer reserve claims or price predictions.

## 2. Reference answers and human review

- The formal freeze used an attested consolidated reference workbook built from two calculation paths. The package includes `results/current_preparation/reference_review/程序复核.xlsx` and `review_attestation.json`.
- The attestation records `record_identity_scope: user_attested_consolidated_human_review` and `independent_dual_review_verified: false`. The workbook contains two calculation columns and no reviewer name, date, signature or adjudication field. It is not an independent human hand calculation.
- After the freeze, two team members checked the 18 components: A matched 18/18; B matched 17/18 after converting three proportions to percentages; R06/S2 was adjudicated in favour of A. The review was not blind: the second workbook contains the first reviewer's formulas and saved values, and the files do not establish when the second reviewer first saw them. No external independent reviewer took part.
- The prose review consists of two single-reviewer workbooks with 123 and 120 annotation rows. They use different claim boundaries; 19 of 34 reports have different claim counts. The 62-row reconciliation is retained by the team and summarised in `audit/prose_review_20261110/REVIEW.md`; it is not in the public archive. The two reviews are not aggregated into a single unsupported-claim rate or agreement rate.
- The rule-based audit in `audit/prose_audit_summary.json` is a separate single-coder measure. It reports at least one unsupported prose assertion in 11/16 G reports and 6/18 E reports. At the additional-assertion level it records 15 unsupported and 8 ambiguous for G, and 7 unsupported and 4 ambiguous for E. It is not human-adjudicated.

## 3. Verification

- V1 recomputes claims from the frozen primary archive with a verifier path that does not import the agent's metric functions (`curve_audit/verification.py`).
- V2 uses the same verifier arithmetic over a second RPC capture. The primary and secondary datasets have the same SHA-256 (`79a6be…`) and are identical at the bound fields. V2 therefore does not test a second arithmetic; it adds block-identity and source-state detection.
- V2 changes no verdict on the 102 formal structured claims. On the 32-case fault suite, V1 rejects 7/12 injected faults; V2 rejects 10/12, and the three extra detections are block-identity cases. The two injected missing-data cases are UNKNOWN.
- The 32-case suite covers unit labels, block identity, time windows, missing secondary data, one wrong value and one direction error. It does not inject formula, decimal, denominator or other arithmetic bugs. A shared arithmetic bug in V1/V2 would not be detected by this suite; the reference workbooks are the only independent arithmetic check.
- The original `fault_batch/summary.json` records two expectation mismatches on source-conflict cases (U3/U4). `experiments/formal_v1/verifier_replay_20261105.json` records the corrected verdicts (UNKNOWN) and matches the report's table. The original summary is retained as a superseded record.

## 4. Reproducibility and code provenance

- `docs/verify_delivery.py` checks 222 file hashes; `curve_audit replay` reproduces the archived formal outcomes; the 280-test suite passes offline.
- The packaged verifier is a later revision. `experiments/formal_v1/freeze.json` records freeze-time hashes for 31 modules. `tools/check_code_provenance.py` reports 25 matches and 6 differences: `__main__.py`, `balance_sources.py`, `current_runner.py`, `current_study.py`, `current_verify.py` and `deepseek_adapter.py`. For the two main verifier files, `current_verify.py` is `aeb3e9fd…` at freeze and `c5d27c2a…` in the package; `current_study.py` is `ceb7d42d…` and `3f6928bd…`. The replay checks archived inputs and outputs, not the original execution environment.
- `experiments/formal_v1/fault_batch.py` contains a hard-coded author path and an incomplete report envelope; it is not rerunnable as shipped. `patches/0001-fault-batch-rerun.patch` fixes both and reproduces the corrected replay. Running it overwrites `observed.json` and `summary.json`; run it in a copy.
- The ZIP is the frozen 12 November package. Root-level documents (this file, `report/ERRATA.md`, `README.md`, `5017_GROUP_PROJECT_HANDOFF.md`) are the current public entry point and may be newer than the copies inside the ZIP.

## 5. Cost

- Tokens: G 104,166, E 129,007. Model calls: 63/70. Generation wall time: 191.602 s/234.587 s.
- Per published correct answer: G 3,360 tokens, E 3,072. Per reproducible correct answer: G 3,064, E 3,000.
- Offline verifier medians: V1 0.216 s, V2 0.283 s, publication 0.002 s; full replay 0.901 s. Human review and adjudication: 7 person-hours.
- No billing receipts, live end-to-end timing or total project labour are available. Token counts are not a dollar cost.

## 6. On-chain component

- `contracts/EvidenceRegistry.sol` is a minimal local Ganache registry (chain ID 1337, block 2). It records a manifest digest and rejects duplicate registration.
- It establishes an append-only local record, not data truth, public-chain timestamping, consensus security or a production trust model. Arithmetic and factual checks are separate.

## 7. What would strengthen the claims

- An external blind review of the 18 reference components, or of a common claim list, with names, dates and hashes recorded before comparison.
- Arithmetic fault injection (denominator, decimals, direction, maximum/minimum) or mutation testing of `verification.recompute`.
- A second arithmetic path for V2, if the "independently recomputing" wording is retained.
- A data dictionary and a versioned code manifest for the freeze-time verifier.
