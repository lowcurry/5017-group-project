"""Bounded actual model adapter. Transcripts distinguish inference from replay."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .common import ROOT, read_json, write_json


class ProviderUnavailable(RuntimeError):
    pass


def codex_binary() -> str:
    for candidate in [os.environ.get("CODEX_CLI"), shutil.which("codex"),
                      "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"]:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise ProviderUnavailable("Codex CLI not found. Set CODEX_CLI to its executable.")


def complete(prompt: str, schema: dict, output_dir: Path, model: str = "gpt-6-luna",
             timeout: int = 120) -> tuple[dict, dict]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    schema_path = output_dir / "schema.json"
    result_path = output_dir / "response.json"
    if result_path.exists():
        raise ProviderUnavailable("Existing inference response preserved; choose a fresh output directory")
    write_json(schema_path, schema)
    (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    command = [codex_binary(), "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
               "--sandbox", "read-only", "--json", "--model", model,
               "-c", 'model_reasoning_effort="low"',
               "--output-schema", str(schema_path.resolve()), "--output-last-message", str(result_path.resolve()), "-"]
    started = time.monotonic()
    try:
        # Preserve login location, but do not give the inference subprocess RPC/API credentials.
        allowed_env = {"PATH", "HOME", "CODEX_HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL",
                       "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        child_env = {key: value for key, value in os.environ.items() if key in allowed_env}
        with tempfile.TemporaryDirectory(prefix="curve-audit-inference-") as isolated:
            process = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                     cwd=isolated, env=child_env, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        (output_dir / "failure.txt").write_text("Model timeout. No result fabricated.\n")
        write_json(output_dir / "metadata.json", {"inference_attempted": True, "inference_performed": None,
                   "runtime_seconds": time.monotonic() - started, "usage": {}, "status": "timeout"})
        raise ProviderUnavailable("Model request timed out") from exc
    elapsed = time.monotonic() - started
    (output_dir / "transcript.jsonl").write_text(process.stdout, encoding="utf-8")
    (output_dir / "diagnostics.txt").write_text(process.stderr, encoding="utf-8")
    usage, native_tools, observed_model = {}, [], None
    for line in process.stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        if event.get("model"):
            observed_model = event["model"]
        item = event.get("item", {})
        if isinstance(item, dict) and item.get("type") and item["type"] not in {"reasoning", "agent_message"}:
            native_tools.append(item.get("type"))
    metadata = {"inference_attempted": True, "inference_performed": bool(usage or result_path.exists()), "provider": "codex-cli", "model_requested": model,
                "model_observed": observed_model, "runtime_seconds": elapsed, "usage": usage,
                "native_tools_used": native_tools, "return_code": process.returncode}
    write_json(output_dir / "metadata.json", metadata)
    if process.returncode or not result_path.exists():
        raise ProviderUnavailable(f"Model request failed (exit {process.returncode}); see saved diagnostics")
    if native_tools:
        raise ProviderUnavailable("Model used native tools outside experiment tool budget; run rejected")
    return read_json(result_path), metadata


def probe(output_dir: Path, model: str = "gpt-6-luna") -> dict:
    schema = {"type": "object", "properties": {"ready": {"type": "boolean"}},
              "required": ["ready"], "additionalProperties": False}
    try:
        response, metadata = complete("Return JSON {\"ready\":true}. Do not use any tools or read any files.",
                                      schema, output_dir, model, timeout=60)
        return {"available": response.get("ready") is True, "metadata": metadata}
    except (ProviderUnavailable, OSError, ValueError) as exc:
        return {"available": False, "reason": str(exc)}
