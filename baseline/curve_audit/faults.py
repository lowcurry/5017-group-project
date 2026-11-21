"""Single-fault package mutations; source evidence is never edited."""
from decimal import Decimal
from pathlib import Path
import json
import shutil

from .evidence import REQUIRED_FILES, _canonical, _hash, render_report, verify_package
from .verification import verify_claims


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_bytes(_canonical(value))


def _rehash(directory):
    manifest_path = directory / "manifest.json"
    manifest = _load(manifest_path)
    for name in list(manifest["files"]):
        source = directory / name
        if source.is_file() and not source.is_symlink():
            manifest["files"][name] = _hash(source.read_bytes())
    _write(manifest_path, manifest)


def _change_claim(directory, mutation):
    report = _load(directory / "report.json")
    candidates = [c for c in report["claims"] if c.get("status") in ("supported", "conflicting")]
    if not candidates:
        raise ValueError("fault suite needs a clean package with at least one supported numeric claim")
    mutation(candidates[0])
    _write(directory / "report.json", report)
    (directory / "report.md").write_text(render_report(report), encoding="utf-8")
    _rehash(directory)


def _source_mutation(directory, mutation):
    dataset = _load(directory / "dataset.json")
    report = _load(directory / "report.json")
    numeric = next(c for c in report["claims"] if c.get("status") in ("supported", "conflicting"))
    sid = numeric["evidence_ids"][0][2:]
    source = next(s for s in dataset["snapshots"] if s["id"] == sid)
    mutation(source)
    _write(directory / "dataset.json", dataset)
    _rehash(directory)


def _tool_mutation(directory, mutation):
    calls = _load(directory / "tool_calls.json")
    mutation(calls)
    _write(directory / "tool_calls.json", calls)
    _rehash(directory)


def _successful_calculate(calls):
    for call in calls:
        if call.get("tool") == "calculate":
            returned = call.get("result", {})
            if isinstance(returned, dict) and returned.get("ok") is True:
                return call, returned["result"]
            if isinstance(returned, dict) and "calculation_id" in returned:
                return call, returned
    raise ValueError("trajectory fault requires a successful retained calculate call")


def run_fault_suite(package_dir, output_dir):
    source, output = Path(package_dir).resolve(), Path(output_dir).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("fault output and source package must be separate sibling locations")
    baseline = verify_package(source)
    if not baseline["pass"]:
        raise ValueError("fault-suite control package must independently verify before mutation")
    output.mkdir(parents=True, exist_ok=True)
    cases = []

    def run(name, expected_pass, mutate=None):
        directory = output / name
        if directory.is_symlink():
            raise ValueError("fault output case must not be a symlink")
        if directory.exists():
            shutil.rmtree(directory)
        shutil.copytree(source, directory)
        if mutate:
            mutate(directory)
        verification = verify_package(directory)
        cases.append({"case_id": name, "expected_pass": expected_pass,
                      "actual_pass": verification["pass"],
                      "matched_expectation": verification["pass"] == expected_pass,
                      "errors": verification["errors"], "package_dir": str(directory)})

    run("clean_control", True)
    run("clean_canonical_rehash", True, _rehash)

    def hash_tamper(directory):
        with (directory / "report.md").open("a", encoding="utf-8") as handle:
            handle.write("\nInjected unverified body.\n")
    run("hash_tamper", False, hash_tamper)
    run("rehashed_unit", False, lambda d: _change_claim(d, lambda c: c.update(unit="USD")))
    run("rehashed_value", False, lambda d: _change_claim(d, lambda c: c.update(value=str(Decimal(c["value"]) + 1))))
    run("rehashed_window", False, lambda d: _change_claim(d, lambda c: c["query"].update(end="2099-01-01T00:00:00Z")))
    run("rehashed_evidence", False, lambda d: _change_claim(d, lambda c: c.update(evidence_ids=["E_NONEXISTENT"])))
    run("rehashed_calculation_reference", False, lambda d: _change_claim(d, lambda c: c.update(calculation_id="C_NONEXISTENT")))
    run("rehashed_calldata", False, lambda d: _source_mutation(d, lambda s: s["calldata"]["quotes"]["USDC_USDT"].update({"1000": "0x5e0d443f" + "0" * 192})))
    run("rehashed_raw_normalized", False, lambda d: _source_mutation(d, lambda s: s["balances"].update(USDC=str(Decimal(s["balances"]["USDC"]) + 1))))
    run("rehashed_empty_tool_history", False, lambda d: _tool_mutation(d, lambda calls: calls.clear()))

    def wrong_return(calls):
        _, returned = _successful_calculate(calls)
        returned["value"] = str(Decimal(returned["value"]) + 1)
    run("rehashed_tool_return", False, lambda d: _tool_mutation(d, wrong_return))

    def wrong_arguments(calls):
        call, _ = _successful_calculate(calls)
        call["arguments"]["end"] = "2099-01-01T00:00:00Z"
    run("rehashed_tool_arguments", False, lambda d: _tool_mutation(d, wrong_arguments))

    source_calls = _load(source / "tool_calls.json")
    if any(call.get("tool") == "independent_verifier" for call in source_calls):
        def wrong_feedback(calls):
            verifier = next(c for c in calls if c.get("tool") == "independent_verifier")
            verifier["result"]["pass"] = not verifier["result"]["pass"]
        run("rehashed_verifier_feedback", False, lambda d: _tool_mutation(d, wrong_feedback))

        def wrong_report_link(directory):
            dataset = _load(directory / "dataset.json")
            def change(calls):
                verifier = next(c for c in calls if c.get("tool") == "independent_verifier")
                verifier["arguments"]["report"]["task_id"] = "UNRELATED_REVIEW_TASK"
                verifier["result"] = verify_claims(dataset, verifier["arguments"]["report"])
            _tool_mutation(directory, change)
        run("rehashed_verifier_report_task", False, wrong_report_link)
    if _load(source / "metadata.json").get("trajectory_schema") == "2.0":
        def self_dependency(calls):
            call, _ = _successful_calculate(calls)
            call["depends_on"] = [call["step_id"]]
        run("rehashed_step_self_dependency", False, lambda d: _tool_mutation(d, self_dependency))

    def rehashed_body(directory):
        hash_tamper(directory)
        _rehash(directory)
    run("rehashed_report_body", False, rehashed_body)

    def path_traversal(directory):
        manifest = _load(directory / "manifest.json")
        manifest["files"]["../outside.json"] = "0" * 64
        _write(directory / "manifest.json", manifest)
    run("path_traversal", False, path_traversal)

    def missing_file(directory):
        (directory / "calculations.json").unlink()
        manifest = _load(directory / "manifest.json")
        del manifest["files"]["calculations.json"]
        _write(directory / "manifest.json", manifest)
    run("rehashed_missing_required_file", False, missing_file)

    def symlink(directory):
        (directory / "tool_calls.json").unlink()
        (directory / "tool_calls.json").symlink_to("metadata.json")
    run("symlink_file", False, symlink)

    def noncanonical(directory):
        with (directory / "manifest.json").open("ab") as handle:
            handle.write(b"\n")
    run("manifest_noncanonical_bytes", False, noncanonical)
    fault_cases = [c for c in cases if not c["expected_pass"]]
    controls = [c for c in cases if c["expected_pass"]]
    result = {"schema_version": "1.0", "source_package": str(source),
              "source_package_hash": baseline["package_hash"],
              "mutation_policy": "One named defect per copied package; semantic cases recompute file hashes. Source package remains unchanged.",
              "fault_count": len(fault_cases), "detected_count": sum(not c["actual_pass"] for c in fault_cases),
              "clean_control_count": len(controls), "clean_control_passed": sum(c["actual_pass"] for c in controls),
              "pass": all(c["matched_expectation"] for c in cases), "cases": cases,
              "limits": "Injected faults test file integrity and retained trajectory consistency; they do not prove actual tool execution, factual correctness of all prose, or archived RPC authenticity."}
    _write(output / "fault_results.json", result)
    return result
