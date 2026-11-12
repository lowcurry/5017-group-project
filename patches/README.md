# P1 patches

The frozen `MSBD5017_Public_Handoff.zip` is intentionally untouched by this PR. These patches are for the next package build. Apply them to an extracted `MSBD5017_Public_Handoff/` copy, not to the ZIP in this repository.

## 0001 — make `fault_batch.py` rerunnable

The archived script has three defects:

1. It hard-codes an author path (`/Users/lowcurry/...`) as its package root.
2. It passes `{'claims': [claim]}` to `verify_v2`, which expects a complete report envelope (`task_id`, `answer`, `claims`) and `allow_partial=True` for a single claim.
3. It does not pass `expected_primary_sha256`, so block-identity faults are reported as source conflicts instead of `secondary_source_state_mismatch`.

It also turns the script-hash assertion into a warning, so a patched script can rerun; the warning makes the provenance loss visible.

Apply and run in a **copy** of the extracted package:

```sh
cd MSBD5017_Public_Handoff
patch -p1 < ../patches/0001-fault-batch-rerun.patch
python3 -B experiments/formal_v1/fault_batch.py verify
```

Expected output (matches `experiments/formal_v1/verifier_replay_20261105.json` and the report's p.4 table):

- V1: 7 FAIL / 5 PASS on the 12 injected faults; 12/12 clean accepted.
- V2: 10 FAIL / 2 UNKNOWN on the 12 injected faults; 12/12 clean accepted.
- Source-conflict cases U3/U4: UNKNOWN.
- `expectation_mismatches`: empty.

Running `verify` overwrites `experiments/formal_v1/fault_batch/observed.json` and `summary.json` in the copy. Keep the archived files if you need the original record; the archived `summary.json` is a superseded record (it contains the U3/U4 mismatches).

## Rebuilding the ZIP

If you apply this patch to the package, the 222-file inventory, `DELIVERY_MANIFEST.json`, `DELIVERY_SHA256SUMS.txt` and the repository-level `SHA256SUMS.txt` all change. Rebuild them with the team's packaging process; do not hand-edit the manifest. The current ZIP and its SHA-256 remain the frozen 12 November artifact.

## What this patch does not do

- It does not change the 36 formal model runs, the reference answers or any reported score.
- It does not add arithmetic fault cases; the 32-case suite still covers metadata, block identity, windows, missing secondary data, a wrong value and a direction error, not formula or decimal bugs.
- It does not make V2 a second arithmetic; V2 still uses the shared `verification.recompute` over a second capture.
