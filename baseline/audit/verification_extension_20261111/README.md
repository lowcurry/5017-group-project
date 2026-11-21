# Offline verification extension

This follow-up checks deterministic reports and scripted failures against archived observations. The four two-source subwindows use Alchemy and dRPC captures already bound in the frozen study. Each event contributes 03:00–09:00 and 15:00–21:00 UTC on its frozen start date. The endpoint and hourly-change fixtures exclude the midpoint. The follow-up quote fixtures use the complete subwindow and the hour after the computed peak.

The separate single-source checks use `data/raw/expanded_archive.json` for 9 March and 12 March 2023, 00:00–06:00 UTC. These dates sit outside the formal USDC window. This earlier archive uses the pool's internal `balances(i)` method. The formal study uses ERC-20 `balanceOf(pool)`. The two data definitions and source availability differ, so the single-source outcomes are reported separately from the two-source outcomes.

| Scope | Cases | V1 | V2 | Publication |
| --- | ---: | --- | --- | --- |
| Valid masked endpoint and hourly changes, valid follow-up quote, or a secondary change at an excluded point | 16 | PASS | PASS | Kept |
| Reported hourly interval crosses the excluded point | 4 | FAIL | FAIL | Withheld |
| Secondary archive lacks a required endpoint | 4 | PASS | UNKNOWN | Withheld |
| Secondary archive disagrees on a required balance | 4 | PASS | UNKNOWN | Withheld |
| Primary archive has a scripted balance change and the secondary still matches the bound dataset | 4 | PASS | FAIL | Withheld |
| Follow-up is individually verifiable but its required upstream claim is withdrawn | 4 | PASS | PASS | Withheld |
| Single-source legacy windows, endpoint change and spot quote per window | 4 | PASS | UNKNOWN | Withheld |

The balance changes are controlled in-memory mutations. The conflict checks invoke the verifier on fixture datasets after the original provider captures have been bound. Source binding for an actual release uses the recorded hashes. The publication check also removes the report's declared dependency to verify that the task-defined dependency still applies.

Eight local mock submissions exercise the current prompt and scorer for both events and both groups. The prompt requests the exact `sampling_resolution_insufficient` code in the claim explanation, with a natural-language rationale in the report. All four submissions with the literal code earn refusal credit. All four with a prose-only explanation receive zero credit under the exact structured rubric. These are mocked protocol checks. Model adherence would need a new run.

From an extracted submission root, regenerate the result with Python 3.10 or newer:

```sh
python3 -B audit/verification_extension_20261111/run_extension.py > /tmp/5017-verification-extension.json
cmp audit/verification_extension_20261111/results.json /tmp/5017-verification-extension.json
```

The 40 verifier cases and eight mock submissions are follow-up engineering checks. All outcomes matched their expectations. No live model or RPC calls took place, and the formal scores are maintained in their own archive. Further two-source testing across new historical periods requires independently captured sources and a new evaluation plan.
