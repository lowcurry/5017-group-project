# Model prose review and adjudication

The team supplied two original annotation workbooks and a reconciliation workbook. The review ledger in `正文审阅仲裁稿.xlsx` matches the 42 anonymous records in `experiments/formal_v1/prose_review_input.json` by report ID, task and text. The two source workbooks each cover the same 34 submitted model reports. The six deterministic templates and two budget stops remain in the ledger.

| Source | Reports with annotations | Original annotation rows | Recorded reviewer metadata |
| --- | ---: | ---: | --- |
| `正文审阅表-草稿-A.xlsx` | 34 | 123 | WANG Hanyang, 9 November 2026, 150 minutes |
| `正文审阅表_已完成.xlsx` | 34 | 120 | WU Yuxuan, 9 November 2026, 150 minutes, as confirmed by the team |

The team describes the two submissions as independent reviews. Both workbook tabs carry the template title A. The reconciliation uses the source filenames to distinguish submissions. All 243 annotation rows in the original-judgment sheet match their source rows, and the decision sheet's cited row numbers and judgments match those records. The team's supplemental confirmation identifies the second reviewer, date and duration because those fields are blank in the original workbook. The files do not show whether either reviewer saw the other's entries before submitting.

The decision sheet records He Yuequn as the confirmer on 10 November 2026. The team separately confirmed on 11 November that He Yuequn spent 120 minutes on prose adjudication. The two reviews account for 300 minutes, bringing the recorded time for the three participants to 420 minutes, or 7 person-hours.

Its 62 rows span 31 report IDs and cover additional statements, evidence coverage, query mismatches and counting rules. The original decision column is titled `拟议裁决`. As the sheet notes, its rows are reconciliation and explanation records. The two annotation sets use different claim boundaries, and the workbook leaves overall claim counts and unsupported-claim rates unaggregated. Reviewer agreement rates require an aligned claim set.

## Fixed components

The 102 fixed-component rows cover 34 reports with three components each. The workbook matches the reproducible prose review for all numerical statuses, reported evidence coverage and refusals. The counts below group the rows using the archived report conditions.

| Measure | G ordinary | E evidence | Total |
| --- | ---: | ---: | ---: |
| Numerical components matching frozen data | 42/43 | 48/48 | 90/91 |
| Numerical components with incomplete original citations | 6/43 | 2/48 | 8/91 |
| Supported refusals observed | 5/5 | 6/6 | 11/11 |

The workbook also identifies five original structured query mismatches among otherwise correct prose components. P41/S1 is the one wrong numerical component. The original text reports a fall, while the frozen endpoints show an increase of about 18.42049 percentage points. P06's quote value agrees with the frozen data, but its description of a discount reverses the direction implied by the quote.

The formal structured scores use the frozen evaluation rules and scheduled denominators. The preliminary additional-assertion counts in `audit/prose_audit_summary.json` use the initial review's claim definitions and remain separate from the adjudication workbook.

## Original file hashes (SHA-256)

- `正文审阅仲裁稿.xlsx` `faa08f0f9a2fb8822b8ef9387b267f2a99bf36b9002223b4e63aa97853a8cc59`
- `正文审阅表-草稿-A.xlsx` `0cdfbca92e34856f37e9b12a69a662a4b056ff7f18bba0bc07695f6660043448`
- `正文审阅表_已完成.xlsx` `a2f544b76448a1da0711c2eed4bc995f28980bdb0a0d4f290580fd76882ff3b4`

The team retains the original post-freeze workbooks. This public archive includes the review summary.
