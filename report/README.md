# Build the final report

From the repository root, use Python 3 and ReportLab:

```sh
python3 -m pip install -r report/requirements.txt
python3 report/build_report.py
```

The default output is `MSBD5017_Final_Project_Report.pdf` at the repository root.
Use `--output /path/to/report.pdf` to build a separate copy. The build reads the
checked-in historical decision replay, recovery results and holdout summaries and
requires no model credentials or network access. See `NUMERICAL_CHECKLIST.md`
for the archived inputs behind the report’s headline numbers.
