"""Check retained tool history consistency, not proof of actual execution.

Schema 2.0 binds task/step identity and an ordered dependency graph. Legacy
histories retain weaker task checks. Numerical replay uses verification.py only.
"""
from collections import defaultdict

from .verification import normalize_query, recompute, verify_claims


def _same_query(left, right):
    try:
        return normalize_query(left) == normalize_query(right)
    except (ValueError, TypeError, KeyError):
        return False


def _same_calculation(left, right):
    # JSON tool interfaces may expand irrelevant query defaults. All other
    # provenance/value fields must exactly equal the checked inventory.
    return (isinstance(left, dict) and isinstance(right, dict) and
            _same_query(left.get("query"), right.get("query")) and
            {k: v for k, v in left.items() if k != "query"} ==
            {k: v for k, v in right.items() if k != "query"})


def _claim_task(claim, report, task_ids):
    explicit = claim.get("task_id")
    if explicit is not None:
        return explicit
    if report.get("task_id") in task_ids:
        return report["task_id"]
    claim_id = claim.get("claim_id", "")
    if isinstance(claim_id, str):
        for task_id in sorted(task_ids, key=len, reverse=True):
            if claim_id.startswith(task_id + "_"):
                return task_id
    return None


def verify_trajectory(dataset, report, tool_calls, calculations, metadata):
    """Replay trace structure after the caller independently checks calculations.

    Every successful return must equal an independently checked retained
    calculation, and every retained calculation needs a logged producer.
    """
    errors = []
    result = {"pass": False, "errors": errors,
              "schema": metadata.get("trajectory_schema", "legacy") if isinstance(metadata, dict) else "legacy",
              "scope": "Internal arguments, results, report linkage and step dependencies; not proof of actual tool execution or live chain authenticity."}
    if not isinstance(tool_calls, list) or not tool_calls:
        errors.append("trajectory: tool_calls must be a nonempty list")
        return result
    if not isinstance(metadata, dict):
        errors.append("trajectory: metadata must be an object")
        return result
    schema = metadata.get("trajectory_schema")
    if schema not in (None, "1.0", "2.0"):
        errors.append("trajectory: unsupported trajectory_schema")
        return result
    strict = schema == "2.0"
    inventory = calculations.get("calculations", []) if isinstance(calculations, dict) else calculations
    if not isinstance(inventory, list):
        errors.append("trajectory: calculations inventory must be a list")
        return result
    indexed = {c.get("calculation_id"): c for c in inventory if isinstance(c, dict)}
    initial_reports = metadata.get("initial_reports", [])
    initials = {}
    if strict:
        if not isinstance(initial_reports, list):
            errors.append("trajectory: initial_reports must be a list")
            initial_reports = []
        for initial in initial_reports:
            tid = initial.get("task_id") if isinstance(initial, dict) else None
            if not isinstance(tid, str) or not tid or tid in initials:
                errors.append("trajectory: initial_reports contain missing or duplicate task_id")
            else:
                initials[tid] = initial
    step_index, produced = {}, set()
    task_calculations, task_steps = defaultdict(set), defaultdict(set)
    verifier_tasks = set()
    legacy_last_calculation = None
    legacy_associations = []
    strict_task_ids = set()
    for number, call in enumerate(tool_calls):
        label = f"trajectory[{number}]"
        if not isinstance(call, dict):
            errors.append(label + ": tool call must be an object")
            continue
        tool, args = call.get("tool"), call.get("arguments")
        tid, sid, dependencies = call.get("task_id"), call.get("step_id"), call.get("depends_on")
        if strict:
            if not isinstance(tid, str) or not tid:
                errors.append(label + ": missing task_id")
                tid = f"__invalid_task_{number}"
            else:
                strict_task_ids.add(tid)
            if not isinstance(sid, str) or not sid or sid in step_index:
                errors.append(label + ": missing or duplicate step_id")
                sid = f"__invalid_step_{number}"
            if (not isinstance(dependencies, list) or
                    any(not isinstance(d, str) for d in dependencies) or
                    len(set(dependencies)) != len(dependencies)):
                errors.append(label + ": depends_on must contain unique step IDs")
                dependencies = []
            for dependency in dependencies:
                parent = step_index.get(dependency)
                if parent is None or parent["task_id"] != tid:
                    errors.append(label + ": dependency must reference an earlier step in the same task")
        if tool == "calculate":
            returned = call.get("result")
            failed = False
            if isinstance(returned, dict) and "ok" in returned:
                if returned.get("ok") is True:
                    returned = returned.get("result")
                elif returned.get("ok") is False and isinstance(returned.get("error"), str) and returned["error"].strip():
                    failed = True
                else:
                    errors.append(label + ": malformed calculate result envelope")
            elif returned is None and isinstance(call.get("error"), str) and call["error"].strip():
                failed = True
            if failed:
                try:
                    recompute(dataset, args)
                except (ValueError, KeyError, TypeError, ArithmeticError):
                    pass
                else:
                    errors.append(label + ": logged calculate failure is independently computable")
                legacy_last_calculation = None
            elif not isinstance(returned, dict):
                errors.append(label + ": calculate must return a calculation object")
                legacy_last_calculation = None
            else:
                cid = returned.get("calculation_id")
                if not isinstance(cid, str) or cid not in indexed:
                    errors.append(label + ": returned calculation is absent from calculations inventory")
                elif not _same_calculation(returned, indexed[cid]):
                    errors.append(label + ": calculate return disagrees with independently checked calculation")
                if not _same_query(args, returned.get("query")):
                    errors.append(label + ": calculate arguments disagree with returned query")
                if isinstance(cid, str):
                    produced.add(cid)
                    if strict:
                        task_calculations[tid].add(cid)
                        task_steps[(tid, cid)].add(sid)
                    legacy_last_calculation = cid
        elif tool == "independent_verifier":
            initial = args.get("report") if isinstance(args, dict) else None
            if not isinstance(initial, dict):
                errors.append(label + ": verifier requires an initial report argument")
            else:
                initial_tid = initial.get("task_id")
                expected = verify_claims(dataset, initial)
                if call.get("result") != expected:
                    errors.append(label + ": verifier feedback differs from independent replay of its report argument")
                if strict:
                    verifier_tasks.add(tid)
                    if initial_tid != tid:
                        errors.append(label + ": verifier report task_id disagrees with tool task_id")
                    if initials.get(tid) != initial:
                        errors.append(label + ": verifier report does not match retained initial_reports task")
                    # Unknown references in a faulty draft are legitimate verifier
                    # inputs; their replayed feedback must faithfully reject them.
                    claims = initial.get("claims", [])
                    for claim in claims if isinstance(claims, list) else []:
                        if not isinstance(claim, dict):
                            continue
                        cid = claim.get("calculation_id")
                        required = task_steps.get((tid, cid), set())
                        if required and not required.intersection(dependencies):
                            errors.append(label + ": verifier dependency omits a referenced calculate step")
                else:
                    legacy_associations.append((initial_tid, legacy_last_calculation, label))
        else:
            errors.append(label + ": unsupported tool " + repr(tool))
        if strict:
            step_index[sid] = {"task_id": tid, "tool": tool}
    for cid in indexed:
        if cid not in produced:
            errors.append("trajectory: calculation has no successful calculate producer: " + str(cid))
    if strict:
        if set(initials) != verifier_tasks:
            errors.append("trajectory: initial_reports task inventory differs from verifier tool task inventory")
        for claim in report.get("claims", []):
            if not isinstance(claim, dict) or claim.get("status") == "unsupported":
                continue
            tid = _claim_task(claim, report, strict_task_ids)
            if tid not in strict_task_ids:
                errors.append("trajectory: final claim has no unambiguous task association: " + str(claim.get("claim_id")))
            elif claim.get("calculation_id") not in task_calculations[tid]:
                errors.append("trajectory: final claim calculation was not produced by its task: " + str(claim.get("claim_id")))
    else:
        legacy_ids = {tid for tid, _, _ in legacy_associations if isinstance(tid, str)}
        final_by_task = defaultdict(set)
        for claim in report.get("claims", []):
            if isinstance(claim, dict) and claim.get("status") != "unsupported":
                tid = _claim_task(claim, report, legacy_ids)
                if tid:
                    final_by_task[tid].add(claim.get("calculation_id"))
        for tid, cid, label in legacy_associations:
            if tid not in final_by_task or cid not in final_by_task[tid]:
                errors.append(label + ": legacy verifier task does not bind to its preceding calculation and final task claims")
    result["pass"] = not errors
    return result
