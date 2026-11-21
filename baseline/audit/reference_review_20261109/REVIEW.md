# Post-freeze reference review

The team supplied these workbooks on 9 November 2026. The A entries name WANG Hanyang and are dated 8 November on all 18 rows. The B entries name Wu Yuxuan. The B date appears only in O2 and is 9 November. The first workbook records A. The second contains A's formulas and saved values alongside the B entries. The files do not establish when B first saw the A values.

Both workbooks match the 51 frozen observations for the fields shown, including target times, block numbers and hashes, three raw balances and three raw quotes. The 16 answer formulas in A's column and both intermediate formula sheets match across the workbooks. The first workbook has empty formula caches. This comparison checks the second workbook's saved A values against a separate calculation from the frozen raw data. Both reviewers recorded “不可确定” for the two continuous-peak queries. The shared notes identify hourly sampling as the reason.

A matches all 18 components. B matches 12 of 16 numeric values as entered. Three shares in M2, M8 and M11 are entered as 0–1 fractions in General format. Multiplying those three by 100 gives 15 of 16 numeric matches and 17 of 18 overall, using the frozen absolute tolerance of 1e-8. The reference values below are rounded for display. Four labels in E5, E9, E14 and E18 say “percent” for changes measured in percentage points.

| Component | Frozen unit | Frozen reference | A saved value | B as entered | Finding |
| --- | --- | ---: | ---: | ---: | --- |
| R01/S1 | percent | 30.87816545172 | 30.8781654517176 | 0.308781654517176 | B fraction × 100 |
| R01/S2 | USDT/USDC | 1.000036999 | 1.000036999 | 1.000036999 | Matches |
| R01/S3 | basis_points | 0.002924291804128 | 0.00292429180406486 | 0.0029242918 | Matches |
| R02/S1 | percentage_points | 18.42049050066 | 18.4204905006627 | 18.4204905006 | Matches |
| R02/S2 | dimensionless | 0.2776961146025 | 0.277696114602477 | 0.2776961146 | Matches |
| R02/S3 | USDT/USDC | 0.9897830738 | 0.9897830738 | 0.9897830738 | Matches |
| R03/S1 | percent | 29.67964770039 | 29.6796477003924 | 0.296796477 | B fraction × 100 |
| R03/S2 | percentage_points | 2.579202362334 | 2.57920236233429 | 2.5792023623 | Matches |
| R03/S3 | Non-answerable | Insufficient temporal resolution | 不可确定 | 不可确定 | Both agree with refusal |
| R04/S1 | percent | 33.71274634437 | 33.7127463443718 | 0.33712746344 | B fraction × 100 |
| R04/S2 | USDT/USDC | 0.999897263 | 0.999897263 | 0.999897263 | Matches |
| R04/S3 | basis_points | 0.001928398117837 | 0.00192839811750527 | 0.001928398 | Matches |
| R05/S1 | percentage_points | -18.38389632757 | -18.3838963275728 | -18.3838963275 | Matches |
| R05/S2 | dimensionless | 0.3478647975424 | 0.34786479754238 | 0.3478647975 | Matches |
| R05/S3 | USDT/USDC | 1.0012227852 | 1.0012227852 | 1.0012227852 | Matches |
| R06/S1 | percent | 34.51091129826 | 34.5109112982569 | 34.51091129 | Matches |
| R06/S2 | percentage_points | 1.169260895696 | 1.16926089569554 | 1.11692608956 | Adjudicated in favor of A |
| R06/S3 | Non-answerable | Insufficient temporal resolution | 不可确定 | 不可确定 | Both agree with refusal |

R06/S2 was the only substantive discrepancy. B entered 1.11692608956 percentage points in M18. The frozen calculation gives 1.169260895695533 percentage points and agrees with A's formula result within tolerance. The maximum comes from 19:00–20:00 UTC on 9 November 2022, using blocks 15934412 and 15934707.

The team confirmed that He Yuequn adjudicated R06/S2 on 9 November 2026 and accepted A's result of 1.169260895695533 percentage points at the precision shown above. The attached workbook records the original inputs, with “Y” in P18 and blank cells in Q18:S18. This paragraph records the subsequent decision. The counts above describe the entries before adjudication.

The original frozen review attestation remains the provenance for the formal runs. The two workbooks document a subsequent review of the 18 reference components. The separate prose audit retains its own records and denominator.

## Workbook hashes

- `reference-review-template1.xlsx` SHA-256 `e6c679fa4c98cdab0b03eaeb859a9e7a32b8bafc575486e9c3e33c835d9494a8`
- `reference-review-template2.xlsx` SHA-256 `e88dfd092d313cd05cdac5d421cb8540b7d022b3653c8c007b4247e4e8992ebd`

The team retains the original post-freeze workbooks. This public archive includes the review summary.
