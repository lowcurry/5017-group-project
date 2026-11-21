# Scope, provenance and limitations

## Evidence and human review

- The study covers one Curve 3pool deployment, two historical windows, six tasks and one DeepSeek Flash model. Repeated model outputs share the same historical observations.
- The freeze used a consolidated reference workbook with two calculation paths. The team attested to the workbook's provenance. The freeze records `independent_dual_review_verified: false`. The workbook lacks reviewer identity, date and signature fields. The post-freeze workbooks record A matching 18/18 and B matching 17/18 after unit normalization. He Yuequn accepted A for R06/S2. B's file includes A's formulas and saved values. The timing of B's access to A's work is unverified.
- Two team members supplied 123 and 120 original annotations across the same 34 reports. They used different claim boundaries in 19 of 34 reports. The team confirmed the second reviewer's name, date and time because the source fields are blank. The 62 reconciliation and explanation rows and the 143-unit follow-up packet are team/course materials. A common-list agreement rate and adjudicated unsupported-claim rate are pending completed judgments.
- The separate rule-based initial audit counted at least one unsupported assertion in 11/16 G and 6/18 E reports. The annotation workbooks and reconciliation record use a different unit of review. See `audit/PROSE_AUDIT.md` and `audit/prose_review_20261110/REVIEW.md`.

## Verification and reproducibility

- V1 uses verifier arithmetic separate from the agent's calculation. V2 uses the same verifier arithmetic over a second archived RPC capture. The captured inputs agree on the bound fields and V2 changed none of the 102 formal claim verdicts. The 32-case fault suite measures the benefit of source checks through injected block-identity cases. It provides no direct test of bugs in the shared arithmetic implementation.
- On the 12 injected faults, V1 rejected seven and V2 rejected ten. V2's three additional detections involved block identity. Two missing-data cases yielded `UNKNOWN`. For source-conflict cases U3 and U4, use `experiments/formal_v1/verifier_replay_20261105.json`, which supersedes the two mismatches in the original summary.
- A separate arithmetic fault experiment tested six controls and six wrong reports across both events. Both current verifiers accepted all controls and rejected all wrong reports. Under deliberately shared arithmetic bugs, both accepted the six matching wrong reports. An exact-fraction calculation outside the mutated verifier disagreed in all six cases. See `audit/arithmetic_faults/README.md`.
- The current publication commands use an additional exact calculation for covered balance claims. In the P2 fault replay they withheld 6/6 shared errors and two dependent quotes while retaining 6/6 controls. The added arithmetic covers balance claims and uses the existing captured evidence. Quote arithmetic continues under V2. The fault replay does not measure live fault frequency.
- The packaged implementation matches 25 of 31 freeze-time module hashes. The six known revisions and the added arithmetic module are documented with their digests in `docs/code_provenance.json`. The package replay checks the recorded inputs and outputs. The separate `frozen_sources.zip` holds the freeze-time source files.
- Run `python3 -B docs/replay_formal_faults.py .` for a standalone fault replay. It uses the same checks as the full replay, including the original fault generator's digest, and verifies every recomputed verdict and reason before emitting the corrected results. The archived generator and its outputs remain part of the historical record.

## Decision replay, costs and chain record

- The research replay measures rule-based actions on 36 scheduled opportunities. The quote replay tests minimum-output screens at 50 historical blocks. Gas, fills, actual analyst decisions, net returns and live execution are outside the observed outcomes.
- Saved model receipts report 104,166 tokens for G and 129,007 for E. The full offline replay of the verification and publication cost sample had a 0.901-second median. The documented review and adjudication took seven person-hours. Billing and historical live end-to-end timings remain unrecorded.
- The Ganache exercise records a manifest digest on a local chain. Arithmetic checks and source verification occur in the verifier.
