from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .common import ROOT, canonical, read_json, write_json, sha256_bytes, now_utc
from .data import load_dataset, coverage, export_processed, collect, read_rpc_url, replicate
from .metrics import calculate
from .experiment import freeze_tasks, public_task, apply_task_dataset, fixed_template, planned_run_manifest, _question
from .scoring import score_outputs
from .evidence import build_package, render_report, verify_package
from .faults import run_fault_suite
from .agent import run_batch, parse_question, normalized_query, model_task


def default_dataset() -> Path:
    expanded = ROOT / "data/raw/expanded_archive.json"
    return expanded if expanded.exists() else ROOT / "data/raw/pilot_archive.json"


def experiment(dataset: dict, destination: Path, repetitions: int, provider: str,
               model: str, ablation: bool = False, development: bool = False) -> dict:
    destination = Path(destination)
    tasks = freeze_tasks(dataset, destination / "frozen")
    settings = {"provider": provider, "model": model, "repetitions": repetitions, "ablation": ablation,
                "development": development, "budgets": read_json(ROOT / "config/budgets.json"),
                "generation_code_sha256": {path.name: sha256_bytes(path.read_bytes())
                                           for path in sorted((ROOT / "curve_audit").glob("*.py"))}}
    settings_path = destination / "run_settings.json"
    if settings_path.exists() and read_json(settings_path) != settings:
        raise ValueError("Frozen run settings differ; choose a new experiment directory")
    write_json(settings_path, settings)
    selected = [task for task in tasks if task["split"] == ("dev" if development else "test")]
    plan = planned_run_manifest([dict(task, split="test") for task in selected], repetitions, ablation)
    write_json(destination / "planned_runs.json", plan)
    outputs = []
    for task in selected:
        started = time.monotonic()
        variant = apply_task_dataset(dataset, task)
        report = fixed_template(variant, public_task(task))
        outputs.append({"task_id": task["task_id"], "condition": "fixed_template", "repetition": 1,
                        "status": "completed", "report": report,
                        "metadata": {"inference_performed": False, "runtime_seconds": time.monotonic() - started,
                                     "runtime_scope": "isolated deterministic baseline", "usage": {"input_tokens": 0, "output_tokens": 0}}})
    conditions = ["ordinary_agent", "evidence_grounded_agent"] + (["links_only_agent"] if ablation else [])
    if provider == "codex-cli":
        for repetition in range(1, repetitions + 1):
            # Interleave conditions rather than finish all ordinary runs first.
            for condition in conditions:
                run_dir = destination / "runs" / f"{condition}_r{repetition}"
                if (run_dir / "outputs.json").exists():
                    saved = read_json(run_dir / "outputs.json")
                    # Resume only actual completed results bound to the same frozen input.
                    if (run_dir / "public_input.json").exists() and read_json(run_dir / "public_input.json") == [model_task(t) for t in selected] and all(row["status"] == "completed" for row in saved):
                        rows = saved
                    else:
                        raise ValueError("Existing incomplete/different run preserved; use a new experiment output directory")
                else:
                    rows = run_batch(dataset, selected, condition, run_dir, model=model)
                for row in rows:
                    row["repetition"] = repetition
                outputs.extend(rows)
                write_json(destination / "outputs.json", outputs)
                print(json.dumps({"condition": condition, "repetition": repetition,
                                  "completed": sum(r["status"] == "completed" for r in rows), "tasks": len(rows)}), flush=True)
    write_json(destination / "outputs.json", outputs)
    scores = score_outputs(tasks, outputs, destination / "evaluation", dataset=dataset)
    batches = []
    for path in sorted((destination / "runs").glob("*/batch_metadata.json")):
        row = read_json(path)
        batches.append({"run": path.parent.name, **row})
    status = {"created_at": now_utc(), "provider": provider, "model": model,
              "data_sha256": sha256_bytes(canonical(dataset).encode()),
              "planned_outputs": len(plan) if not development else len(selected) * (1 + len(conditions) * repetitions),
              "recorded_outputs": len(outputs), "failed_outputs": sum(r["status"] != "completed" for r in outputs),
              "all_planned_completed": provider == "codex-cli" and not any(r["status"] != "completed" for r in outputs) and len(outputs) == len(selected) * (1 + len(conditions) * repetitions),
              "human_review": "pending independent human reviewers; no fabricated scores",
              "costs": {"scope": "actual batch totals, never multiplied per task",
                        "input_tokens": sum(b.get("actual_usage", {}).get("input_tokens", 0) for b in batches),
                        "output_tokens": sum(b.get("actual_usage", {}).get("output_tokens", 0) for b in batches),
                        "batch_wall_seconds": sum(b.get("wall_seconds", 0) for b in batches), "money_cost": None},
              "batches": batches}
    write_json(destination / "status.json", status)
    return status


def demo(dataset: dict, output: Path, start: str, end: str, question: str | None, provider: str, model: str) -> dict:
    if question:
        queries = [parse_question(question, start, end)]
    else:
        queries = [normalized_query({"metric": "share_pct", "operation": "change", "start": start, "end": end, "coin": coin}) for coin in ("DAI", "USDC", "USDT")]
        queries += [normalized_query({"metric": "quote_rate", "operation": "last", "start": start, "end": end, "direction": direction, "size": size})
                    for direction in ("USDC_USDT", "USDT_USDC") for size in (1000, 10000, 100000)]
        queries += [normalized_query({"metric": "size_sensitivity_bps", "operation": "last", "start": start, "end": end, "direction": direction, "size": 100000}) for direction in ("USDC_USDT", "USDT_USDC")]
        queries += [normalized_query({"metric": "share_pct", "operation": "maximum", "start": start, "end": end, "coin": "USDC"})]
    tasks = [{"task_id": f"Q{index:02d}", "question": question or _question(query), "query": query}
             for index, query in enumerate(queries, 1)]
    reports, calculations, tools = [], [], []
    if provider == "codex-cli":
        rows = run_batch(dataset, tasks, "evidence_grounded_agent", output / "model", model=model)
        if any(row["status"] != "completed" for row in rows):
            raise RuntimeError("Actual inference failed; saved failures are retained")
        reports = [row["report"] for row in rows]
        calculations = [calc for row in rows for calc in row["calculations"]]
        tools = [tool for row in rows for tool in row["tool_calls"]]
    else:
        for task in tasks:
            report = fixed_template(dataset, task)
            reports.append(report)
            try:
                result = calculate(dataset, task["query"])
                calculations.append(result)
                tools.append({"tool": "calculate", "arguments": task["query"], "result": result})
            except ValueError as exc:
                tools.append({"tool": "calculate", "arguments": task["query"], "error": str(exc)})
    claims = []
    for report in reports:
        for claim in report["claims"]:
            claim["claim_id"] = report["task_id"] + "_" + claim["claim_id"]
            claims.append(claim)
    merged = {"task_id": "investigation", "answer": "\n".join(report["answer"] for report in reports),
              "claims": claims, "refusal_reason": None,
              "faults": [fault for report in reports for fault in report.get("faults", [])]}
    metadata = {"created_at": now_utc(), "provider": provider, "inference_performed": provider == "codex-cli",
                "requested_window": {"start": start, "end": end}, "question": question,
                "disclosure": "Actual model-selected tools" if provider == "codex-cli" else "Deterministic baseline demonstration, not an AI experiment",
                "limits": ["sampled historical pool state", "nominal amounts not dollar values", "quotes not actual fills", "prose not automatically certified"]}
    result = build_package(output / "evidence", dataset, merged, tools, calculations, metadata)
    write_json(output / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Historical Curve liquidity investigation and audit")
    parser.add_argument("--dataset", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    collect_cmd = sub.add_parser("collect")
    collect_cmd.add_argument("--start", default="2023-03-09T00:00:00Z")
    collect_cmd.add_argument("--end", default="2023-03-15T00:00:00Z")
    collect_cmd.add_argument("--env-file", type=Path)
    collect_cmd.add_argument("--output", type=Path, default=ROOT / "data/raw/expanded_archive.json")
    replication = sub.add_parser("replicate")
    replication.add_argument("--env-file", type=Path)
    replication.add_argument("--rpc-url", default=None)
    replication.add_argument("--output", type=Path, default=ROOT / "results/replication.json")
    freezing = sub.add_parser("freeze")
    freezing.add_argument("--output", type=Path, default=ROOT / "config/frozen")
    evaluation = sub.add_parser("experiment")
    evaluation.add_argument("--provider", choices=["offline", "codex-cli"], default="offline")
    evaluation.add_argument("--model", default="gpt-6-luna")
    evaluation.add_argument("--repetitions", type=int, default=3)
    evaluation.add_argument("--ablation", action="store_true")
    evaluation.add_argument("--development", action="store_true")
    evaluation.add_argument("--output", type=Path, default=ROOT / "results/formal_experiment")
    investigate = sub.add_parser("investigate")
    investigate.add_argument("--start", default="2023-03-10T18:00:00Z")
    investigate.add_argument("--end", default="2023-03-11T18:00:00Z")
    investigate.add_argument("--question")
    investigate.add_argument("--provider", choices=["deterministic", "codex-cli"], default="deterministic")
    investigate.add_argument("--model", default="gpt-6-luna")
    investigate.add_argument("--output", type=Path, default=ROOT / "results/demo")
    verifying = sub.add_parser("verify")
    verifying.add_argument("package", type=Path)
    verifying.add_argument("--anchor", type=Path)
    faulting = sub.add_parser("faults")
    faulting.add_argument("package", type=Path)
    faulting.add_argument("--output", type=Path, default=ROOT / "results/faults")
    sub.add_parser("replay")
    multi = sub.add_parser("experiment-v2")
    multi.add_argument("--provider", choices=["offline", "codex-cli"], default="offline")
    multi.add_argument("--model", default="gpt-6-luna")
    multi.add_argument("--repetitions", type=int, default=3)
    multi.add_argument("--output", type=Path, default=ROOT / "results/investigation_experiment_v2")
    multi_replay = sub.add_parser("replay-v2")
    multi_replay.add_argument("--experiment", type=Path, default=ROOT / "results/investigation_experiment_v2")
    multi_replay.add_argument("--output", type=Path, default=ROOT / "results/replay_v2")
    multi_investigate = sub.add_parser("investigate-v2")
    multi_investigate.add_argument("--start", default="2023-03-10T12:00:00Z")
    multi_investigate.add_argument("--end", default="2023-03-11T18:00:00Z")
    multi_investigate.add_argument("--question", required=True)
    multi_investigate.add_argument("--model", default="gpt-6-luna")
    multi_investigate.add_argument("--output", type=Path, default=ROOT / "results/new_multistep_investigation")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_package(args.package, args.anchor)
        elif args.command == "faults":
            result = run_fault_suite(args.package, args.output)
        elif args.command == "collect":
            result = collect(args.output, args.start, args.end, read_rpc_url(args.env_file),
                             progress_callback=lambda p: print(json.dumps(p), flush=True) if p["collected"] % 12 == 0 else None)
        else:
            dataset = load_dataset(args.dataset or default_dataset())
            if args.command == "inventory":
                result = coverage(dataset)
            elif args.command == "replicate":
                result = replicate(dataset, args.rpc_url or read_rpc_url(args.env_file))
                write_json(args.output, result)
            elif args.command == "freeze":
                tasks = freeze_tasks(dataset, args.output)
                result = {"tasks": len(tasks), "output": str(args.output)}
            elif args.command == "experiment":
                result = experiment(dataset, args.output, args.repetitions, args.provider, args.model, args.ablation, args.development)
                result = {key: value for key, value in result.items() if key != "batches"}
            elif args.command == "investigate":
                result = demo(dataset, args.output, args.start, args.end, args.question, args.provider, args.model)
            elif args.command == "experiment-v2":
                from .investigation_experiment import run_experiment
                result = run_experiment(dataset, args.output, args.repetitions, args.model, args.provider)
                result = {k: v for k, v in result.items() if k != "batches"}
            elif args.command == "replay-v2":
                from .investigation_experiment import replay
                scores = replay(dataset, args.experiment, args.output)
                saved = read_json(args.experiment / "evaluation/scores.json")
                package = ROOT / "results/investigation_evidence_v2"
                anchor = ROOT / "results/chain/investigation_anchor_v2.json"
                evidence = verify_package(package, anchor if anchor.exists() else None) if package.exists() else None
                result = {"pass": canonical(scores) == canonical(saved) and (evidence is None or evidence["pass"]),
                          "scores_identical": canonical(scores) == canonical(saved), "outputs_rescored": len(scores["per_output"]),
                          "evidence_pass": evidence["pass"] if evidence is not None else None, "inference_performed": False,
                          "human_review": scores["human_review_status"], "scope": "Offline rescoring and retained package validation, not new inference or source collection"}
            elif args.command == "investigate-v2":
                from .investigation import run_investigation_batch
                task = {"task_id": "USER_V2", "question": args.question,
                        "window": {"start": args.start, "end": args.end}, "_private": {}}
                rows = run_investigation_batch(dataset, [task], "evidence_grounded_agent", args.output / "model", args.model)
                if rows[0]["status"] != "completed":
                    raise RuntimeError("Actual investigation failed; retained traces describe the failure")
                row = rows[0]
                result = build_package(args.output / "evidence", dataset, row["report"], row["tool_calls"], row["calculations"],
                                       {"created_at": now_utc(), "trajectory_schema": "2.0", "initial_reports": [row["initial_report"]],
                                        "inference_performed": True, "requested_question": args.question,
                                        "scope": "Recorded trajectory consistency and numeric evidence; free prose and chain authenticity require separate review"})
                write_json(args.output / "summary.json", result)
            elif args.command == "replay":
                package_result = verify_package(ROOT / "results/demo/evidence", ROOT / "results/chain/anchor.json" if (ROOT / "results/chain/anchor.json").exists() else None)
                folder = ROOT / "results/formal_experiment"
                frozen = read_json(folder / "frozen/manifest.json")
                if frozen["dataset_sha256"] != sha256_bytes(canonical(dataset).encode()):
                    raise ValueError("Replay dataset differs from the frozen experiment dataset")
                scores = score_outputs(read_json(folder / "frozen/tasks.private.json"), read_json(folder / "outputs.json"),
                                      ROOT / "results/replay_evaluation", dataset=dataset)
                result = {"evidence_pass": package_result["pass"], "outputs_rescored": len(scores["per_output"]),
                          "inference_performed": False, "note": "Independent deterministic replay of saved outputs; their original inference mode remains recorded"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed = (result.get("pass") is False or result.get("status") in {"FAIL", "FAILED"}
                  or result.get("evidence_pass") is False or result.get("verification", {}).get("pass") is False
                  or (args.command in {"experiment", "experiment-v2"} and args.provider == "codex-cli" and not result.get("all_planned_completed")))
        return int(failed)
    except (ValueError, RuntimeError, FileNotFoundError, KeyError) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
