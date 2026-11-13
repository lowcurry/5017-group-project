# MSBD 5017 · Curve 3pool Evidence and Verification

The 13 November 2026 handoff incorporates the report and reproducibility corrections raised in [PR #1](https://github.com/lowcurry/5017-group-project/pull/1). The 36 scheduled model runs and the historical decision replay retain their recorded results.

- [Public handoff ZIP](MSBD5017_Public_Handoff.zip) and [SHA-256 checksum](SHA256SUMS.txt)
- [Technical report](MSBD5017_Final_Project_Report.pdf) and [incorporated report corrections](REPORT_ERRATA.md)
- [Scope and provenance](LIMITATIONS.md) and [team handoff](5017_GROUP_PROJECT_HANDOFF.md)
- [Presentation](MSBD5017_Project_Presentation.pptx)
- [Slide wording for a future deck revision](SLIDE_TEXT_PATCH.md)

The public archive contains the shared engineering, evidence and replay files. The course ZIP also contains the underlying reviewer and adjudication records and the common-list follow-up materials. Two original prose review sheets are available separately at the GitHub repository root.

Extract the ZIP, enter its `MSBD5017_Public_Handoff` directory and run the checks below with Python 3.10 or later. For the report embedded in the ZIP, open `report/MSBD5017_Final_Report.pdf`.

```sh
python3 -B docs/verify_delivery.py .
python3 -B -m curve_audit replay
python3 -B docs/replay_formal_faults.py .
python3 -B docs/check_code_provenance.py .
python3 -B audit/arithmetic_faults/run_arithmetic_faults.py .
python3 -B audit/arithmetic_faults/replay_publication_guard.py .
python3 -B audit/decision_replay_20261112/replay.py .
python3 -B docs/reproduce_current.py
python3 -B -m unittest discover -s tests
```

The standalone fault command checks the frozen generator and each recomputed verdict before emitting the corrected record. The archived generator and its historical outputs are part of the evidence. See [the report corrections](REPORT_ERRATA.md) for the distinction between the original summary and the corrected verdicts.

Both publication commands now use independent exact arithmetic for covered balance claims. In the P2 replay, the updated publisher withheld six wrong claims that passed both verifiers, along with two quotes tied to erroneous peak times. All six clean controls were published. Quote arithmetic continues under V2. See page 4 of the technical report and `audit/arithmetic_faults/README.md` in the extracted package.
