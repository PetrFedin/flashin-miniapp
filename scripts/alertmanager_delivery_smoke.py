#!/usr/bin/env python3
"""Prove that Alertmanager can deliver one isolated pilot alert successfully."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://alertmanager:9093"
_TOTAL_METRIC = "alertmanager_notifications_total"
_FAILED_METRIC = "alertmanager_notifications_failed_total"
_SAMPLE_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>[-+0-9.eE]+)$")


def _base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise ValueError(f"Alertmanager base URL is invalid: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Alertmanager base URL must be an HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Alertmanager base URL must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("Alertmanager base URL must not contain a path")
    return raw


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    timeout: float = 5.0,
) -> tuple[int, bytes]:
    body = None
    headers = {"User-Agent": "flashin-alertmanager-smoke/1.0"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(1_048_577)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"Alertmanager HTTP {exc.code}: {detail}") from exc


def _wait_ready(base_url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, _body = _request(f"{base_url}/-/ready", timeout=3.0)
            if status == 200:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    if last_error is not None:
        raise RuntimeError(f"Alertmanager did not become ready: {last_error}")
    raise RuntimeError("Alertmanager did not become ready")


def _metric_sum(text: str, metric_name: str) -> float:
    total = 0.0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.fullmatch(line)
        if match and match.group("name") == metric_name:
            total += float(match.group("value"))
    return total


def _notification_counters(base_url: str) -> tuple[float, float]:
    status, body = _request(f"{base_url}/metrics", timeout=5.0)
    if status != 200 or len(body) > 1_048_576:
        raise RuntimeError("Alertmanager metrics endpoint is not healthy")
    text = body.decode("utf-8", errors="strict")
    return _metric_sum(text, _TOTAL_METRIC), _metric_sum(text, _FAILED_METRIC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _post_alert(base_url: str, smoke_id: str, *, resolved: bool) -> None:
    now = datetime.now(UTC)
    ends_at = now if resolved else now + timedelta(minutes=2)
    payload = [
        {
            "labels": {
                "alertname": "FlashinPilotAlertDeliverySmoke",
                "severity": "warning",
                "service": "flashin-deploy",
                "component": "alerting",
                "smoke_id": smoke_id,
            },
            "annotations": {
                "summary": "FLASHIN pilot alert delivery smoke",
                "description": "Synthetic deployment alert; no customer or payment data is included.",
            },
            "startsAt": _iso(now - timedelta(seconds=1)),
            "endsAt": _iso(ends_at),
        }
    ]
    status, _body = _request(
        f"{base_url}/api/v2/alerts",
        method="POST",
        payload=payload,
        timeout=5.0,
    )
    if status not in {200, 202}:
        raise RuntimeError(f"Alertmanager rejected smoke alert with status {status}")


def run_smoke(base_url: str, *, timeout_seconds: int = 45) -> dict[str, object]:
    normalized = _base_url(base_url)
    if not 10 <= timeout_seconds <= 180:
        raise ValueError("timeout_seconds must be between 10 and 180")
    _wait_ready(normalized, min(timeout_seconds, 60))
    before_total, before_failed = _notification_counters(normalized)
    smoke_id = uuid.uuid4().hex[:16]
    _post_alert(normalized, smoke_id, resolved=False)

    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            observed_total, observed_failed = _notification_counters(normalized)
            if observed_failed > before_failed:
                raise RuntimeError("Alertmanager reported a failed external notification")
            if observed_total > before_total:
                time.sleep(2)
                final_total, final_failed = _notification_counters(normalized)
                if final_failed > before_failed:
                    raise RuntimeError("Alertmanager delivery failed after initial notification attempt")
                return {
                    "ok": True,
                    "smoke_id": smoke_id,
                    "notification_delta": final_total - before_total,
                    "failure_delta": final_failed - before_failed,
                    "receiver_verified": True,
                }
            time.sleep(1)
        raise RuntimeError(
            "Alertmanager did not complete an external notification within the smoke timeout"
        )
    finally:
        try:
            _post_alert(normalized, smoke_id, resolved=True)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="FLASHIN Alertmanager delivery smoke")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ALERTMANAGER_INTERNAL_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument("--timeout-seconds", type=int, default=45)
    args = parser.parse_args()

    result = run_smoke(args.base_url, timeout_seconds=args.timeout_seconds)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
