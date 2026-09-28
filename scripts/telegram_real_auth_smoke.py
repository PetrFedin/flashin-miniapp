#!/usr/bin/env python3
"""Prove deployed Telegram initData authentication without persisting credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "docs" / "pilot" / "evidence"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP_OPENER = urllib.request.build_opener(_NoRedirect)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_api_base(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("API_PUBLIC_URL is required")
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API_PUBLIC_URL is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("API_PUBLIC_URL must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API_PUBLIC_URL contains unsupported URL components")
    if port not in (None, 443):
        raise ValueError("API_PUBLIC_URL must use the standard HTTPS port")
    if parsed.path not in ("", "/"):
        raise ValueError("API_PUBLIC_URL must not contain a path")
    host = parsed.hostname.rstrip(".").lower()
    return f"https://{host}"


def request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    bearer: str | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "flashin-telegram-live-auth-smoke/1.0",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with HTTP_OPENER.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(exc.__class__.__name__) from exc
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid JSON response") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("invalid JSON response")
    return decoded


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_private_evidence(payload: dict[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(EVIDENCE_DIR, 0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = EVIDENCE_DIR / f"telegram_real_auth_{stamp}.json"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=EVIDENCE_DIR,
        prefix=".telegram_real_auth_",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    try:
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        if os.name == "posix":
            os.chmod(target, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--acknowledge-customer-provisioning",
        action="store_true",
        help="Acknowledge that /api/auth/telegram may create or update the pilot customer/CRM profile.",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.acknowledge_customer_provisioning:
        print("Telegram live auth smoke failed: explicit customer-provisioning acknowledgement is required")
        return 1

    init_data = os.getenv("TELEGRAM_INIT_DATA", "").strip()
    expected_user_id = os.getenv("TELEGRAM_EXPECTED_USER_ID", "").strip()
    api_public_url = os.getenv("API_PUBLIC_URL", "").strip()
    if not init_data:
        print("Telegram live auth smoke failed: TELEGRAM_INIT_DATA is required")
        return 1
    if len(init_data.encode("utf-8")) > 16_384:
        print("Telegram live auth smoke failed: TELEGRAM_INIT_DATA is unexpectedly large")
        return 1
    if not expected_user_id or not expected_user_id.isdigit():
        print("Telegram live auth smoke failed: TELEGRAM_EXPECTED_USER_ID must be a numeric Telegram id")
        return 1

    try:
        api_base = normalize_api_base(api_public_url)
        auth = request_json(
            f"{api_base}/api/auth/telegram",
            method="POST",
            payload={"init_data": init_data},
        )
        token = auth.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("authentication response has no access token")
        me = request_json(
            f"{api_base}/api/auth/me",
            method="GET",
            bearer=token,
        )
        if str(me.get("telegram_id") or "") != expected_user_id:
            raise RuntimeError("authenticated Telegram identity does not match expected pilot identity")

        observed_at = utc_timestamp()
        evidence = {
            "schema_version": 1,
            "scenario": "telegram_real_auth",
            "observed_at": observed_at,
            "signed_init_data_accepted": True,
            "customer_session_verified": True,
            "expected_identity_verified": True,
        }
        evidence_path = write_private_evidence(evidence)
        relative = evidence_path.relative_to(ROOT).as_posix()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "scenario": "telegram_real_auth",
                    "observed_at": observed_at,
                    "evidence_path": relative,
                    "evidence_sha256": sha256_file(evidence_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Telegram live auth smoke failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
