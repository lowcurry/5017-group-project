# Slide text patch — 12 November deck

The PowerPoint predates the final provenance review. This PR does not edit the binary PPTX; apply these text changes if you revise the deck.

## Slide 4 — Formal results

Add a footer or speaker note:

> Verification withheld 12 correct answers (G 9, E 3) due to missing attribution or a withdrawn dependency.

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

> V1 uses arithmetic separate from the agent. V2 applies the same arithmetic to a second capture and checks block identity and source state. V1 rejects 7/12 injected faults and V2 rejects 10/12. The three additional detections involved block identity. The other two injected cases returned UNKNOWN because data were missing. The two captures agree on the bound fields, and all 102 formal claims received the same verdict from V1 and V2.

See [verification and reproducibility](LIMITATIONS.md#verification-and-reproducibility).
