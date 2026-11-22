"""Replay publication and verify the archived outputs for all three holdouts."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "baseline"), str(ROOT / "audit/evidence_recovery")]
from curve_audit.current_study import digest
from curve_audit.current_verify import publish_verified, verify_v2
from recovery import publish_with_recovery


def read(path):
    return json.loads(path.read_text())


for event in ("2023-06-15", "2023-10-23", "2024-01-11"):
    folder = HERE / event
    primary, secondary, tasks = (read(folder / f"{name}.json") for name in ("primary", "secondary", "tasks"))
    tasks = {t["task_id"]: t for t in tasks}
    bound = digest(primary)
    runs = read(folder / "runs.json")
    traces = {r["run_id"]: r for r in read(folder / "recovery-trace.json")}
    if len(runs) != 18 or {r["run_id"] for r in runs} != traces.keys():
        raise ValueError(f"Incomplete run set: {event}")
    for run in runs:
        task, report = tasks[run["task_id"]], run["report"]
        trace = traces[run["run_id"]]
        checks = verify_v2(primary, secondary, task, report, expected_primary_sha256=bound)
        base = publish_verified(primary, task, report, checks)[0]
        recovered = publish_with_recovery(primary, secondary, task, report, expected_primary_sha256=bound)["published"]
        if (digest(report) != trace["input_report_sha256"] or
                base != run["published"] or recovered != trace["recovery_publication"]):
            raise ValueError(f"Publication mismatch: {event}/{run['run_id']}")
    print(f"{event}: 18 paired publications reproduced")
