"""Portable evidence packages with deterministic hashes and independent checks."""
from decimal import Decimal, DecimalException
from pathlib import Path, PurePosixPath
import hashlib
import json
import re

from .verification import normalize_query, recompute, verify_claims
from .trajectory import verify_trajectory

REQUIRED_FILES = {"dataset.json", "report.json", "report.md", "tool_calls.json",
                  "calculations.json", "metadata.json"}
CANONICALIZATION = "UTF-8 JSON; sort_keys=true; separators=(',',':'); ensure_ascii=false; no trailing newline"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, value):
    path.write_bytes(_canonical(value))


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _cell(value):
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", "<br>").replace("\r", "")


def render_report(report):
    """Render exactly the packaged claims; prose is explicitly outside certification."""
    lines = ["# Curve 3pool 调查报告", "", "任务：" + _cell(report.get("task_id", "")), "",
             "验证范围：结构化主张、金额单位、数据内部一致性和证据引用。链上真实性及自由文本的事实正确性需另行审核。", "",
             "## 叙述（未由数值验证器认证）", "", str(report.get("answer", "")), "",
             "## 结构化主张", "",
             "| 主张 | 状态 | 指标 / 操作 | UTC 窗口 | 数值 | 单位 | 证据 | 计算 |",
             "|---|---|---|---|---|---|---|---|"]
    for claim in report.get("claims", []):
        query = claim.get("query") or {}
        fields = [claim.get("claim_id"), claim.get("status"),
                  f"{query.get('metric', '')} / {query.get('operation', '')}",
                  f"{query.get('start', '')} → {query.get('end', '')}",
                  claim.get("value"), claim.get("unit"),
                  ", ".join(map(str, claim.get("evidence_ids", []))), claim.get("calculation_id")]
        lines.append("| " + " | ".join(_cell(x) for x in fields) + " |")
    lines.extend(["", "## 查询参数和说明（说明文本需人工审核）", ""])
    for claim in report.get("claims", []):
        query_json = _canonical(claim.get("query")).decode("utf-8")
        lines.extend(["- " + _cell(claim.get("claim_id")) + "：`" + query_json.replace("`", "\\`") + "`",
                      "  " + _cell(claim.get("explanation", ""))])
    if report.get("narrative_claims") is not None:
        lines.extend(["", "## 结构化边界判断，正文仍需审核", "",
                      "下表保留模型对证据边界的分类；分类、解释及正文含义不由数值验证器认证。", "",
                      "| 类型 | 判断 | 解释（需审核） | 证据 |",
                      "|---|---|---|---|"])
        for boundary in report.get("narrative_claims", []):
            if isinstance(boundary, dict):
                fields = [boundary.get("kind"), boundary.get("stance"),
                          boundary.get("explanation"),
                          ", ".join(map(str, boundary.get("evidence_ids", [])))]
                lines.append("| " + " | ".join(_cell(x) for x in fields) + " |")
    if report.get("refusal_reason"):
        lines.extend(["", "保留 / 拒答理由：" + str(report["refusal_reason"])])
    if report.get("faults"):
        lines.extend(["", "## 报告记录的冲突", "", "```json",
                      json.dumps(report["faults"], ensure_ascii=False, sort_keys=True, indent=2), "```"])
    lines.append("")
    return "\n".join(lines)


def build_package(output_dir, dataset, report, tool_calls, calculations, metadata):
    directory = Path(output_dir)
    if directory.is_symlink():
        raise ValueError("package output must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in directory.iterdir()}
    if existing - (REQUIRED_FILES | {"manifest.json"}):
        raise ValueError("package directory contains unowned files; choose a fresh output directory")
    if any(p.is_symlink() or not p.is_file() for p in directory.iterdir()):
        raise ValueError("package output contains symlink or directory")
    # _coverage is derived transiently by the loader, never a primary data assertion.
    dataset = {k: v for k, v in dataset.items() if k != "_coverage"}
    for name, value in [("dataset.json", dataset), ("report.json", report),
                        ("tool_calls.json", tool_calls), ("calculations.json", calculations),
                        ("metadata.json", metadata)]:
        _write(directory / name, value)
    (directory / "report.md").write_bytes(render_report(report).encode("utf-8"))
    manifest = {"schema_version": "1.0", "hash_algorithm": "sha256",
                "canonicalization": CANONICALIZATION,
                "files": {name: _hash((directory / name).read_bytes()) for name in sorted(REQUIRED_FILES)}}
    manifest_bytes = _canonical(manifest)
    (directory / "manifest.json").write_bytes(manifest_bytes)
    return {"package_dir": str(directory.resolve()),
            "manifest_path": str((directory / "manifest.json").resolve()),
            "package_hash": _hash(manifest_bytes), "verification": verify_package(directory)}


def _safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    return (not path.is_absolute() and str(path) == name and
            all(part not in (".", "..", "") for part in path.parts) and name != "manifest.json")


def _calculation_checks(dataset, report, calculations):
    errors, indexed = [], {}
    if isinstance(calculations, dict):
        calculations = calculations.get("calculations")
    if not isinstance(calculations, list):
        return ["calculations must be a list"]
    for i, calculation in enumerate(calculations):
        if not isinstance(calculation, dict):
            errors.append(f"calculation[{i}] must be an object")
            continue
        cid = calculation.get("calculation_id")
        if not isinstance(cid, str) or not cid:
            errors.append(f"calculation[{i}] has no calculation_id")
            continue
        if cid in indexed:
            if indexed[cid] != calculation:
                errors.append(f"calculation ID collision: {cid}")
            continue
        indexed[cid] = calculation
        try:
            expected = recompute(dataset, calculation.get("query"))
            value = Decimal(calculation["value"])
            if not value.is_finite() or abs(value - Decimal(expected["value"])) > Decimal("1e-12"):
                errors.append(f"{cid}: calculation value does not reproduce from raw integers")
            for key in ("unit", "evidence_ids"):
                if calculation.get(key) != expected[key]:
                    errors.append(f"{cid}: calculation {key} disagrees with independent recomputation")
            for key in ("observed_at", "block_number", "block_hash", "target_timestamp_utc", "snapshot_id", "sample_count"):
                if key in calculation and calculation[key] != expected.get(key):
                    errors.append(f"{cid}: calculation {key} disagrees with source block")
            observations = calculation.get("observations")
            if observations is not None:
                if not isinstance(observations, list):
                    errors.append(f"{cid}: observations must be a list")
                else:
                    by_id = {s["id"]: s for s in dataset["snapshots"]}
                    observation_ids = []
                    for observation in observations:
                        if not isinstance(observation, dict):
                            errors.append(f"{cid}: malformed observation")
                            continue
                        sid = observation.get("id", observation.get("snapshot_id"))
                        if sid is None and observation.get("evidence_id", "").startswith("E_"):
                            sid = observation["evidence_id"][2:]
                        observation_ids.append("E_" + str(sid))
                        source = by_id.get(sid)
                        if source is None or any(key in observation and observation[key] != source[key]
                                                 for key in ("block_number", "block_hash", "target_timestamp_utc")):
                            errors.append(f"{cid}: observation does not bind to the source block")
                        elif "observed_at" in observation and observation["observed_at"] != source["timestamp_utc"]:
                            errors.append(f"{cid}: observation timestamp disagrees with source block")
                    if observation_ids != expected["evidence_ids"]:
                        errors.append(f"{cid}: observation IDs disagree with operation evidence")
        except (ValueError, KeyError, TypeError, DecimalException) as exc:
            errors.append(f"{cid}: malformed or unreproducible calculation: {exc}")
    for claim in report.get("claims", []):
        if not isinstance(claim, dict) or claim.get("status") == "unsupported":
            continue
        cid = claim.get("calculation_id")
        calculation = indexed.get(cid) if isinstance(cid, str) else None
        if calculation is None:
            errors.append(f"{claim.get('claim_id')}: unresolved calculation reference {cid}")
            continue
        try:
            if normalize_query(claim.get("query")) != normalize_query(calculation.get("query")):
                errors.append(f"{claim.get('claim_id')}: calculation query does not match claim arguments/window")
            for key in ("unit", "evidence_ids"):
                if claim.get(key) != calculation.get(key):
                    errors.append(f"{claim.get('claim_id')}: claim {key} differs from referenced calculation")
            if abs(Decimal(claim["value"]) - Decimal(calculation["value"])) > Decimal("1e-12"):
                errors.append(f"{claim.get('claim_id')}: claim value differs from referenced calculation")
        except (ValueError, KeyError, TypeError, DecimalException) as exc:
            errors.append(f"{claim.get('claim_id')}: malformed calculation reference: {exc}")
    return errors


def verify_package(path, anchor_path=None):
    directory = Path(path)
    errors, checks = [], []
    result = {"pass": False, "errors": errors, "checks": checks, "package_hash": None,
              "scope": "Hashes, structured arithmetic, source consistency and retained tool-history consistency; narrative truth, actual tool execution and live chain state are not certified."}
    if directory.is_symlink() or not directory.is_dir():
        errors.append("package must be a real directory, not a symlink")
        return result
    files = list(directory.rglob("*"))
    if any(p.is_symlink() for p in files):
        errors.append("package contains a symlink")
        return result
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        errors.append("missing manifest.json")
        return result
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        if manifest_bytes != _canonical(manifest):
            errors.append("manifest is not canonical JSON bytes (no trailing newline)")
        result["package_hash"] = _hash(_canonical(manifest))
        if (manifest.get("schema_version") != "1.0" or manifest.get("hash_algorithm") != "sha256" or
                manifest.get("canonicalization") != CANONICALIZATION):
            errors.append("unsupported manifest schema/hash/canonicalization")
        entries = manifest.get("files")
        if not isinstance(entries, dict):
            errors.append("manifest files must be an object")
            return result
        if not REQUIRED_FILES.issubset(entries):
            errors.append("manifest omits required files: " + ", ".join(sorted(REQUIRED_FILES - set(entries))))
        actual = {p.relative_to(directory).as_posix() for p in files if p.is_file() and p != manifest_path}
        if set(entries) != actual:
            errors.append("manifest file inventory does not exactly match package contents")
        for name, digest in entries.items():
            if not _safe_name(name):
                errors.append("unsafe manifest path: " + repr(name))
                continue
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append("invalid SHA-256 digest for " + name)
                continue
            source = directory.joinpath(*PurePosixPath(name).parts)
            if not source.is_file():
                errors.append("missing packaged file: " + name)
            elif _hash(source.read_bytes()) != digest:
                errors.append("hash mismatch: " + name)
        checks.append("safe paths, no symlinks, complete inventory and SHA-256 file hashes")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append("invalid manifest: " + str(exc))
        return result
    try:
        dataset, report = _read(directory / "dataset.json"), _read(directory / "report.json")
        calculations = _read(directory / "calculations.json")
        tool_calls, metadata = _read(directory / "tool_calls.json"), _read(directory / "metadata.json")
        if not isinstance(tool_calls, list):
            errors.append("tool_calls must be a list")
        if not isinstance(metadata, dict):
            errors.append("metadata must be an object")
        if not isinstance(report, dict):
            raise ValueError("report must be an object")
        if (directory / "report.md").read_bytes() != render_report(report).encode("utf-8"):
            errors.append("report.md differs from deterministic structured-report rendering")
        checks.append("deterministic report body matches report.json")
        claim_check = verify_claims(dataset, report)
        result["claim_verification"] = claim_check
        errors.extend(claim_check["errors"])
        errors.extend(_calculation_checks(dataset, report, calculations))
        checks.append("independent claim/calculation arithmetic, exact evidence and calculation references")
        trajectory_check = verify_trajectory(dataset, report, tool_calls, calculations, metadata)
        result["trajectory_verification"] = trajectory_check
        errors.extend(trajectory_check["errors"])
        checks.append("retained calculate arguments/results and independently replayed verifier feedback")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append("unreadable or malformed package content: " + str(exc))
    if anchor_path is not None:
        try:
            anchor = _read(Path(anchor_path))
            for key in ("package_hash", "registered_hash", "read_back_hash"):
                if anchor.get(key) != result["package_hash"]:
                    errors.append("saved local-chain anchor mismatch: " + key)
            checks.append("saved local-chain registration/read-back record matches package hash (offline record only)")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append("invalid local-chain anchor record: " + str(exc))
    result["pass"] = not errors
    return result
