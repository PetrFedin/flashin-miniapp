#!/usr/bin/env python3
"""Shared, executable pilot-readiness checks for FLASHIN.

The module intentionally uses only the Python standard library so it can run on a
fresh production host before application containers are started.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

LEGAL_DOCUMENTS = (
    "frontend/public/legal/offer.html",
    "frontend/public/legal/privacy.html",
    "frontend/public/legal/returns.html",
)
LEGAL_PLACEHOLDER_MARKERS = (
    "является шаблоном",
    "шаблон публичной оферты",
    "должен быть проверен юристом",
    "должна быть проверена юристом",
    "укажите юридические реквизиты",
    "укажите email",
    "укажите адрес",
    "todo",
    "replace me",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    critical: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check_required_file(root: Path, relative_path: str, *, critical: bool = True) -> CheckResult:
    path = root / relative_path
    return CheckResult(
        name=f"file:{relative_path}",
        ok=path.is_file(),
        critical=critical,
        detail="present" if path.is_file() else "missing",
    )


def check_legal_document(root: Path, relative_path: str) -> CheckResult:
    path = root / relative_path
    if not path.is_file():
        return CheckResult(f"legal:{relative_path}", False, True, "file is missing")

    content = path.read_text(encoding="utf-8").strip()
    normalized = " ".join(content.lower().split())
    markers = [marker for marker in LEGAL_PLACEHOLDER_MARKERS if marker in normalized]
    if len(content) < 500:
        return CheckResult(
            f"legal:{relative_path}",
            False,
            True,
            f"document is too short ({len(content)} characters)",
        )
    if markers:
        return CheckResult(
            f"legal:{relative_path}",
            False,
            True,
            "placeholder markers: " + ", ".join(markers),
        )
    return CheckResult(f"legal:{relative_path}", True, True, "final text detected")


def run_command(
    name: str,
    command: Sequence[str],
    *,
    root: Path,
    critical: bool = True,
    timeout: int = 180,
) -> CheckResult:
    try:
        result = subprocess.run(
            list(command),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(name, False, critical, f"{exc.__class__.__name__}: {exc}")

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if len(output) > 2000:
        output = output[-2000:]
    return CheckResult(
        name=name,
        ok=result.returncode == 0,
        critical=critical,
        detail=output or f"exit={result.returncode}",
    )


def check_http(
    name: str,
    url: str,
    *,
    expected_status: int = 200,
    critical: bool = True,
    timeout: int = 15,
) -> CheckResult:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "User-Agent": "flashin-pilot-readiness/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read(4096)
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read(4096)
    except Exception as exc:
        return CheckResult(name, False, critical, f"{exc.__class__.__name__}: {exc}")

    ok = status == expected_status and bool(body)
    return CheckResult(
        name=name,
        ok=ok,
        critical=critical,
        detail=f"status={status}, bytes={len(body)}",
    )


def build_predeploy_checks(root: Path) -> list[CheckResult]:
    checks = [
        check_required_file(root, ".env"),
        check_required_file(root, "docker-compose.yml"),
        check_required_file(root, "docker-compose.production.yml"),
        check_required_file(root, "deploy/Caddyfile"),
        check_required_file(root, "scripts/backup_postgres.sh"),
        check_required_file(root, "scripts/restore_postgres.sh"),
        check_required_file(root, "scripts/rollback.sh"),
        check_required_file(root, "docs/runbook_index.md"),
    ]
    checks.extend(check_legal_document(root, path) for path in LEGAL_DOCUMENTS)
    checks.extend(
        [
            run_command("preflight", ["python3", "scripts/preflight.py"], root=root),
            run_command("environment", ["python3", "scripts/validate_env.py"], root=root),
            run_command(
                "production_compose",
                ["python3", "scripts/check_production_compose.py"],
                root=root,
            ),
        ]
    )
    return checks


def build_live_checks(root: Path, env: Mapping[str, str] | None = None) -> list[CheckResult]:
    values = dict(env or read_env(root / ".env"))
    api_base = values.get("API_PUBLIC_URL", "").rstrip("/")
    mini_app_url = values.get("MINI_APP_URL", "").rstrip("/")
    admin_url = values.get("ADMIN_URL", "").rstrip("/")

    checks: list[CheckResult] = []
    for key, value in (
        ("API_PUBLIC_URL", api_base),
        ("MINI_APP_URL", mini_app_url),
        ("ADMIN_URL", admin_url),
    ):
        checks.append(CheckResult(f"env:{key}", bool(value), True, value or "missing"))

    if api_base:
        checks.extend(
            [
                check_http("live:api_health", f"{api_base}/health"),
                check_http("live:api_ready", f"{api_base}/ready"),
                check_http("live:catalog", f"{api_base}/api/products"),
                check_http("live:looks", f"{api_base}/api/looks"),
            ]
        )
    if mini_app_url:
        checks.append(check_http("live:mini_app", f"{mini_app_url}/"))
    if admin_url:
        checks.append(check_http("live:admin", f"{admin_url}/"))
    return checks


def build_report(phase: str, checks: Iterable[CheckResult]) -> dict[str, object]:
    materialized = list(checks)
    critical_failed = [check for check in materialized if check.critical and not check.ok]
    optional_failed = [check for check in materialized if not check.critical and not check.ok]
    return {
        "phase": phase,
        "go": not critical_failed,
        "summary": {
            "total": len(materialized),
            "passed": sum(1 for check in materialized if check.ok),
            "critical_failed": len(critical_failed),
            "optional_failed": len(optional_failed),
        },
        "critical_failed": [check.to_dict() for check in critical_failed],
        "checks": [check.to_dict() for check in materialized],
    }


def render_markdown(report: Mapping[str, object]) -> str:
    go = bool(report.get("go"))
    checks = report.get("checks") or []
    lines = [
        "# FLASHIN Pilot Readiness Gate",
        "",
        f"Phase: `{report.get('phase', 'unknown')}`",
        "",
        f"Decision: **{'GO' if go else 'NO-GO'}**",
        "",
    ]
    for raw in checks:
        if not isinstance(raw, Mapping):
            continue
        ok = bool(raw.get("ok"))
        critical = bool(raw.get("critical", True))
        detail = str(raw.get("detail") or "").replace("\n", " ")
        lines.append(
            f"- [{'x' if ok else ' '}] `{raw.get('name')}` "
            f"({'critical' if critical else 'optional'}) — {detail}"
        )
    return "\n".join(lines) + "\n"


def write_report(root: Path, report: Mapping[str, object], *, stem: str) -> tuple[Path, Path]:
    json_path = root / "docs" / f"{stem}.json"
    markdown_path = root / "docs" / f"{stem}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
