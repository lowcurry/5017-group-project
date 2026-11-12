# Slide text patch — 12 November deck

The PowerPoint predates the final provenance review. This PR does not edit the binary PPTX; apply these text changes if you revise the deck.

## Slide 4 — Formal results

Add a footer or speaker note:

> Verification withheld 12 numerically correct but unattributed answers (G 9, E 3).

Speaker note addition:

> The published column is not free: nine G answers and three E answers were numerically correct but failed attribution or dependency checks. E used 23.8% more tokens in total, but 3,072 tokens per published correct answer versus G's 3,360.

## Slide 5 — Worked example

Replace the footer:

> Secondary audit: 90 of 91 numeric bundles matched the frozen values.

with:

> Secondary audit: 90/91 numeric bundles matched; 11/16 G vs 6/18 E reports contain at least one unsupported prose assertion.

Speaker note addition:

> The prose audit is rule-based and single-coder, not the human annotation workbook. It is the only cross-report unsupported-assertion count we report.

## Slide 6 — Verifier stress test

Replace the title:

> The second source caught more faults

with:

> V2 caught three more block-identity faults

Keep `7/12`, `10/12`, `12/12`, and replace the last line:

> On all 102 emitted formal claims, V1 and V2 gave the same verdicts.

with:

> V2 changed no verdict on the 102 formal claims because the two captures are identical at the bound fields.

Speaker note replacement:

> V1 recalculates with the independent verifier arithmetic; V2 uses the same arithmetic over the second capture. The second capture is identical to the primary at the bound fields, so V2 adds block-identity and source-state detection, not a second arithmetic. V1 rejects 7/12 injected faults, V2 10/12; the extra three are block-identity cases. The two injected missing-data cases are UNKNOWN. On the 102 formal claims V2 changes nothing; that is expected, not independent corroboration.

See `LIMITATIONS.md` §3 for the full verification limits.
