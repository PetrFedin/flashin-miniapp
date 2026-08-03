#!/usr/bin/env python3
"""Shared, executable pilot-readiness checks for FLASHIN.

The module intentionally uses only the Python standard library so it can run on a
fresh production host before application containers are started.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlparse

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
PUBLIC_URL_KEYS = ("API_PUBLIC_URL", "MINI_APP_URL", "ADMIN_URL")
COMMON_SECURITY_HEADERS = {
    "strict-transport-security": "max-age=",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
}


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


def check_env_exact(
    name: str,
    value: str | None,
    expected: str,
    *,
    critical: bool = True,
) -> CheckResult:
    actual = str(value or "").strip()
    ok = actual.lower() == expected.lower()
    return CheckResult(name, ok, critical, f"value={actual or 'missing'}, expected={expected}")


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


def check_public_https_url(name: str, value: str | None, *, critical: bool = True) -> CheckResult:
    raw = str(value or "").strip()
    if not raw:
        return CheckResult(name, False, critical, "missing")

    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        return CheckResult(name, False, critical, f"invalid URL: {exc}")

    if parsed.scheme.lower() != "https":
        return CheckResult(name, False, critical, "production URL must use https")
    if not parsed.hostname:
        return CheckResult(name, False, critical, "hostname is missing")
    if parsed.username or parsed.password:
        return CheckResult(name, False, critical, "embedded credentials are forbidden")
    if parsed.query or parsed.fragment:
        return CheckResult(name, False, critical, "base URL must not contain query or fragment")
    if parsed.path not in ("", "/"):
        return CheckResult(name, False, critical, "base URL must not contain a path")
    if port not in (None, 443):
        return CheckResult(name, False, critical, f"unexpected HTTPS port: {port}")

    host = parsed.hostname.rstrip(".").lower()
    blocked_suffixes = (".localhost", ".local", ".internal", ".test", ".invalid", ".example")
    if host == "localhost" or host.endswith(blocked_suffixes):
        return CheckResult(name, False, critical, f"non-public hostname: {host}")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            return CheckResult(name, False, critical, f"unqualified hostname: {host}")
    else:
        if not address.is_global:
            return CheckResult(name, False, critical, f"non-public IP address: {host}")

    return CheckResult(name, True, critical, f"public HTTPS base URL: {host}")


def check_distinct_hosts(
    name: str,
    values: Mapping[str, str],
    *,
    critical: bool = True,
) -> CheckResult:
    hosts: dict[str, str] = {}
    missing: list[str] = []
    for key, value in values.items():
        try:
            host = (urlparse(value).hostname or "").rstrip(".").lower() if value else ""
        except ValueError:
            host = ""
        if not host:
            missing.append(key)
        else:
            hosts[key] = host
    duplicates = sorted(host for host, count in Counter(hosts.values()).items() if count > 1)
    ok = not missing and not duplicates and len(hosts) == len(values)
    detail_parts = []
    if missing:
        detail_parts.append("missing: " + ", ".join(sorted(missing)))
    if duplicates:
        detail_parts.append("duplicate hosts: " + ", ".join(duplicates))
    if ok:
        detail_parts.append(", ".join(f"{key}={host}" for key, host in sorted(hosts.items())))
    return CheckResult(name, ok, critical, "; ".join(detail_parts) or "invalid public hosts")


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


def _fetch_http(url: str, *, timeout: int) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "User-Agent": "flashin-pilot-readiness/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read(1_048_577)
            headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read(1_048_577)
        headers = {key.lower(): value for key, value in exc.headers.items()}
    return status, body, headers


def check_http(
    name: str,
    url: str,
    *,
    expected_status: int = 200,
    critical: bool = True,
    timeout: int = 15,
    minimum_body_bytes: int = 1,
    expected_content_type: str | None = None,
    required_headers: Mapping[str, str | None] | None = None,
    expected_json: Mapping[str, object] | None = None,
    expected_json_type: type | tuple[type, ...] | None = None,
    require_non_empty_json: bool = False,
) -> CheckResult:
    try:
        status, body, headers = _fetch_http(url, timeout=timeout)
    except Exception as exc:
        return CheckResult(name, False, critical, f"{exc.__class__.__name__}: {exc}")

    failures: list[str] = []
    if status != expected_status:
        failures.append(f"status={status}, expected={expected_status}")
    if len(body) < minimum_body_bytes:
        failures.append(f"body too short: {len(body)} < {minimum_body_bytes}")
    if len(body) > 1_048_576:
        failures.append("body exceeds 1 MiB gate limit")

    content_type = headers.get("content-type", "")
    if expected_content_type and expected_content_type.lower() not in content_type.lower():
        failures.append(f"content-type={content_type or 'missing'}, expected {expected_content_type}")

    for header_name, expected_value in (required_headers or {}).items():
        actual = headers.get(header_name.lower(), "")
        if not actual:
            failures.append(f"missing header {header_name}")
        elif expected_value and expected_value.lower() not in actual.lower():
            failures.append(f"header {header_name}={actual!r}, expected to contain {expected_value!r}")

    payload: object | None = None
    needs_json = expected_json is not None or expected_json_type is not None or require_non_empty_json
    if needs_json:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            failures.append(f"invalid JSON: {exc}")
        else:
            if expected_json_type is not None and not isinstance(payload, expected_json_type):
                failures.append(f"JSON type={type(payload).__name__}, expected={expected_json_type}")
            if expected_json is not None:
                if not isinstance(payload, Mapping):
                    failures.append("JSON payload is not an object")
                else:
                    for key, expected in expected_json.items():
                        if payload.get(key) != expected:
                            failures.append(f"JSON {key}={payload.get(key)!r}, expected={expected!r}")
            if require_non_empty_json and not payload:
                failures.append("JSON payload is empty")

    detail = f"status={status}, bytes={len(body)}, content-type={content_type or 'missing'}"
    if failures:
        detail += "; " + "; ".join(failures)
    return CheckResult(name=name, ok=not failures, critical=critical, detail=detail)


def _public_urls(values: Mapping[str, str]) -> dict[str, str]:
    return {key: str(values.get(key, "")).rstrip("/") for key in PUBLIC_URL_KEYS}


def build_predeploy_checks(root: Path) -> list[CheckResult]:
    values = read_env(root / ".env")
    public_urls = _public_urls(values)
    checks = [
        check_required_file(root, ".env"),
        check_required_file(root, "docker-compose.yml"),
        check_required_file(root, "docker-compose.production.yml"),
        check_required_file(root, "deploy/Caddyfile"),
        check_required_file(root, "scripts/backup_postgres.sh"),
        check_required_file(root, "scripts/restore_postgres.sh"),
        check_required_file(root, "scripts/rollback.sh"),
        check_required_file(root, "docs/runbook_index.md"),
        check_env_exact("env:APP_ENV", values.get("APP_ENV"), "production"),
        check_env_exact("env:USE_CREATE_ALL", values.get("USE_CREATE_ALL"), "false"),
        check_env_exact("env:ENABLE_SEED", values.get("ENABLE_SEED"), "false"),
    ]
    checks.extend(check_public_https_url(f"env:{key}", value) for key, value in public_urls.items())
    checks.append(check_distinct_hosts("env:public_hosts_distinct", public_urls))
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
    public_urls = _public_urls(values)
    checks = [check_public_https_url(f"live:url:{key}", value) for key, value in public_urls.items()]
    checks.append(check_distinct_hosts("live:public_hosts_distinct", public_urls))

    api_base = public_urls["API_PUBLIC_URL"]
    mini_app_url = public_urls["MINI_APP_URL"]
    admin_url = public_urls["ADMIN_URL"]
    valid = {key: check_public_https_url(key, value).ok for key, value in public_urls.items()}

    api_headers = {
        **COMMON_SECURITY_HEADERS,
        "x-frame-options": "deny",
        "cache-control": "no-store",
    }
    if valid["API_PUBLIC_URL"]:
        checks.extend(
            [
                check_http(
                    "live:api_health",
                    f"{api_base}/health",
                    expected_content_type="application/json",
                    required_headers=api_headers,
                    expected_json={"status": "ok"},
                ),
                check_http(
                    "live:api_ready",
                    f"{api_base}/ready",
                    expected_content_type="application/json",
                    required_headers=api_headers,
                    expected_json={"status": "ready", "database": "ok", "migrations": "current"},
                ),
                check_http(
                    "live:catalog",
                    f"{api_base}/api/products",
                    expected_content_type="application/json",
                    required_headers=api_headers,
                    expected_json_type=list,
                    require_non_empty_json=True,
                ),
                check_http(
                    "live:looks",
                    f"{api_base}/api/looks",
                    expected_content_type="application/json",
                    required_headers=api_headers,
                    expected_json_type=list,
                ),
            ]
        )
    if valid["MINI_APP_URL"]:
        checks.append(
            check_http(
                "live:mini_app",
                f"{mini_app_url}/",
                minimum_body_bytes=100,
                expected_content_type="text/html",
                required_headers={**COMMON_SECURITY_HEADERS, "x-frame-options": "sameorigin"},
            )
        )
    if valid["ADMIN_URL"]:
        checks.append(
            check_http(
                "live:admin",
                f"{admin_url}/",
                minimum_body_bytes=100,
                expected_content_type="text/html",
                required_headers={
                    **COMMON_SECURITY_HEADERS,
                    "x-frame-options": "deny",
                    "cache-control": "no-store",
                },
            )
        )
    checks.append(
        run_command(
            "live:provider_integrations",
            ["python3", "scripts/check_integrations.py"],
            root=root,
            timeout=300,
        )
    )
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
