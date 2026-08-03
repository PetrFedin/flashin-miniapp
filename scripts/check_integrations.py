#!/usr/bin/env python3
"""Run live external-provider probes and write a private pilot evidence report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_readiness import read_env
from script_time import utc_timestamp

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs/pilot/integration_check_report.json"
SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "BOT_TOKEN",
    "YOOKASSA_SECRET_KEY",
    "MOYSKLAD_TOKEN",
    "MOYSKLAD_PASSWORD",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "MEILISEARCH_MASTER_KEY",
}


@dataclass(frozen=True)
class Probe:
    name: str
    script: str
    timeout: int = 60
    condition: str | None = None


PROBES = (
    Probe("telegram", "check_telegram_bot.py", 30),
    Probe("yookassa", "check_yookassa_test.py", 45),
    Probe("moysklad", "check_moysklad.py", 45),
    Probe("r2_s3", "check_r2_s3.py", 60, condition="durable_media"),
    Probe("meilisearch", "check_meilisearch.py", 30, condition="search_enabled"),
)


def build_probe_plan(env: Mapping[str, str]) -> list[dict[str, Any]]:
    media_storage = str(env.get("MEDIA_STORAGE", "local")).strip().lower()
    search_enabled = str(env.get("MEILISEARCH_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    plan: list[dict[str, Any]] = []
    for probe in PROBES:
        enabled = True
        reason = "required for live pilot"
        if probe.condition == "durable_media":
            enabled = media_storage in {"s3", "r2"}
            reason = (
                f"MEDIA_STORAGE={media_storage or 'missing'}"
                if enabled
                else "durable media storage is not configured"
            )
        elif probe.condition == "search_enabled":
            enabled = search_enabled
            reason = "MEILISEARCH_ENABLED=true" if enabled else "search is disabled"
        plan.append({"probe": probe, "enabled": enabled, "reason": reason})
    return plan


def _redaction_values(env: Mapping[str, str]) -> list[str]:
    values = []
    for key in SECRET_KEYS:
        value = str(env.get(key, "")).strip()
        if len(value) >= 4:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact(text: str, env: Mapping[str, str]) -> str:
    result = text
    for secret in _redaction_values(env):
        result = result.replace(secret, "<redacted>")
    return result


def _command(probe: Probe, *, host_python: bool) -> list[str]:
    script_path = f"scripts/{probe.script}"
    if host_python:
        return [sys.executable, script_path]
    return ["docker", "compose", "exec", "-T", "backend", "python", script_path]


def run_probe(
    probe: Probe,
    *,
    env: Mapping[str, str],
    host_python: bool,
    runner=subprocess.run,
) -> dict[str, Any]:
    command = _command(probe, host_python=host_python)
    process_env = dict(os.environ)
    if host_python:
        process_env.update(env)
    try:
        completed = runner(
            command,
            cwd=ROOT,
            env=process_env,
            capture_output=True,
            text=True,
            timeout=probe.timeout,
            check=False,
        )
        stdout = redact((completed.stdout or "").strip(), env)[-4000:]
        stderr = redact((completed.stderr or "").strip(), env)[-4000:]
        return {
            "name": probe.name,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": probe.name,
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": redact(f"{exc.__class__.__name__}: {exc}", env),
        }


def build_report(
    results: Sequence[Mapping[str, Any]],
    *,
    strict: bool,
    host_python: bool,
) -> dict[str, Any]:
    failed = [result for result in results if not result.get("ok")]
    return {
        "schema_version": 1,
        "created_at": utc_timestamp(),
        "mode": "strict" if strict else "advisory",
        "execution": "host-python" if host_python else "backend-container",
        "go": not failed if strict else True,
        "summary": {
            "total": len(results),
            "passed": sum(1 for result in results if result.get("ok")),
            "failed": len(failed),
        },
        "results": list(results),
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_report(path: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    json_path = path
    markdown_path = path.with_suffix(".md")
    _atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# FLASHIN provider integration probes",
        "",
        f"Decision: **{'GO' if report.get('go') else 'NO-GO'}**",
        "",
        f"Created: `{report.get('created_at')}`",
        "",
    ]
    for result in report.get("results", []):
        lines.append(
            f"- [{'x' if result.get('ok') else ' '}] `{result.get('name')}` — "
            f"exit={result.get('returncode')}"
        )
    _atomic_write(markdown_path, "\n".join(lines) + "\n")
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Collect evidence without blocking on failures; strict is the default",
    )
    parser.add_argument(
        "--host-python",
        action="store_true",
        help="Run probes on the host instead of the live backend container",
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args(argv)

    env = read_env(ROOT / ".env")
    strict = not args.advisory
    results: list[dict[str, Any]] = []
    for item in build_probe_plan(env):
        probe = item["probe"]
        if not item["enabled"]:
            results.append(
                {
                    "name": probe.name,
                    "ok": False,
                    "returncode": None,
                    "stdout": "",
                    "stderr": item["reason"],
                }
            )
            continue
        results.append(run_probe(probe, env=env, host_python=args.host_python))

    report = build_report(results, strict=strict, host_python=args.host_python)
    json_path, markdown_path = write_report(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print({"json": str(json_path), "markdown": str(markdown_path)})
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
