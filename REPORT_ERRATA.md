# Report errata — 12 November 2026 PDF

The 12 November 2026 report predates the final provenance review. The statements below are superseded by the evidence in the package. Use the corrected position in this file, or the next PDF revision, when citing the report.

| Location | Statement in the PDF | Corrected position |
|---|---|---|
| p.2 | "The consolidated human review workbook used for the formal freeze…" | The freeze used an attested consolidated reference workbook built from two calculation paths. It has no reviewer name, date, signature or adjudication field and is not an independent human hand calculation. See `LIMITATIONS.md` §2. |
| p.4 | "V2 … independently recomputing the value" | V2 uses the same verifier arithmetic over a second capture. The two captures are identical at the bound fields, so V2 changes no value verdict. Its added value is block-identity and source-state detection. |
| p.4 | "V1 and V2 gave identical verdicts on all 102 emitted structured claims." | V2 did not change any verdict. This is expected because the captures are identical at the bound fields; it is not independent corroboration. |
| p.4 | "Unresolved source conflict … V2 0/0/2" (with `[5]` citing both `fault_batch/summary.json` and `verifier_replay_20261105.json`) | The original summary records two expectation mismatches on U3/U4. The corrected replay treats both as UNKNOWN and matches the table. Cite `verifier_replay_20261105.json`; the original summary is superseded. |
| p.5 | "The workbook preserves 243 original annotations and 62 reconciliation and explanation rows." | The two workbooks contain 123 and 120 annotation rows. The 62-row reconciliation is retained by the team and summarised in the audit record; it is not in the public archive. |
| p.5 | "A common claim list is ready for both reviewers." | The two reviews use different claim boundaries; 19/34 reports have different claim counts. A common list is not in the public archive and the rates are not aggregated. |
| p.5 | No unsupported-claim row | The rule-based single-coder audit records at least one unsupported prose assertion in 11/16 G reports and 6/18 E reports; additional assertions are 15 unsupported/8 ambiguous for G and 7 unsupported/4 ambiguous for E. It is not human-adjudicated. |
| p.6 | No code provenance note | The packaged verifier is a later revision. Freeze-time hashes are in `experiments/formal_v1/freeze.json`; `tools/check_code_provenance.py` reports 25/31 code files matching and 6 differing (including `current_verify.py` and `current_study.py`). The replay checks archived inputs and outputs, not the original execution environment. |

The underlying data and the 36 formal model runs are unchanged. This errata corrects provenance and wording, not the reported scores.

See `LIMITATIONS.md` for the full scope and provenance record.
