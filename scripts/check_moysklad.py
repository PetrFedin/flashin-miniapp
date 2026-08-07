#!/usr/bin/env python3
"""Read-only MoySklad credential, catalog, and outbound-target readiness probe."""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TRUE_VALUES = {"1", "true", "yes", "on"}
_TARGETS = (
    ("organization", "MOYSKLAD_ORGANIZATION_ID", "organization"),
    ("agent", "MOYSKLAD_AGENT_ID", "counterparty"),
    ("store", "MOYSKLAD_STORE_ID", "store"),
    ("delivery_service", "MOYSKLAD_DELIVERY_SERVICE_ID", "service"),
)


class ProbeError(ValueError):
    """Bounded readiness failure safe to include in signed pilot evidence."""


def _authorization() -> str:
    token = (os.getenv("MOYSKLAD_TOKEN") or "").strip()
    if token:
        return f"Bearer {token}"
    login = (os.getenv("MOYSKLAD_LOGIN") or "").strip()
    password = os.getenv("MOYSKLAD_PASSWORD") or ""
    if login and password:
        encoded = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
    return ""


def _export_enabled() -> bool:
    return (os.getenv("MOYSKLAD_ORDER_EXPORT_ENABLED") or "").strip().lower() in _TRUE_VALUES


def _target_ids() -> tuple[tuple[str, str, str], ...]:
    if not _export_enabled():
        raise ProbeError("outbound export is disabled")

    values: list[tuple[str, str, str]] = []
    for label, env_name, entity_type in _TARGETS:
        value = (os.getenv(env_name) or "").strip()
        if not value:
            raise ProbeError(f"{label} target is not configured")
        if not _UUID_RE.fullmatch(value):
            raise ProbeError(f"{label} target is not a valid UUID")
        values.append((label, entity_type, value))
    return tuple(values)


def _request_json(
    base_url: str,
    authorization: str,
    path: str,
    *,
    label: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        headers={
            "Accept": "application/json;charset=utf-8",
            "Authorization": authorization,
            "User-Agent": "flashin-moysklad-readiness/2.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ProbeError(f"{label} probe returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError(f"{label} probe failed with {exc.__class__.__name__}") from None

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProbeError(f"{label} probe returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise ProbeError(f"{label} probe returned an invalid object")
    return payload


def _validate_catalog(payload: dict[str, Any]) -> None:
    rows = payload.get("rows")
    meta = payload.get("meta")
    if not isinstance(rows, list) or not isinstance(meta, dict):
        raise ProbeError("catalog probe returned an invalid collection")
    total_products = meta.get("size")
    if not isinstance(total_products, int) or isinstance(total_products, bool) or total_products <= 0:
        raise ProbeError("product catalog is empty")


def _validate_target(
    payload: dict[str, Any],
    *,
    label: str,
    entity_type: str,
    expected_id: str,
) -> None:
    if str(payload.get("id") or "").lower() != expected_id.lower():
        raise ProbeError(f"{label} target identity mismatch")
    meta = payload.get("meta")
    if not isinstance(meta, dict) or str(meta.get("type") or "") != entity_type:
        raise ProbeError(f"{label} target type mismatch")
    if payload.get("archived") is True:
        raise ProbeError(f"{label} target is archived")


def main() -> int:
    base_url = (
        os.getenv("MOYSKLAD_BASE_URL")
        or "https://api.moysklad.ru/api/remap/1.2"
    ).rstrip("/")
    authorization = _authorization()
    if not authorization:
        print("MoySklad probe failed: configure token or a complete login/password pair")
        return 1

    try:
        targets = _target_ids()
        catalog = _request_json(
            base_url,
            authorization,
            "/entity/product?limit=1",
            label="catalog",
        )
        _validate_catalog(catalog)
        for label, entity_type, target_id in targets:
            payload = _request_json(
                base_url,
                authorization,
                f"/entity/{entity_type}/{target_id}",
                label=label,
            )
            _validate_target(
                payload,
                label=label,
                entity_type=entity_type,
                expected_id=target_id,
            )
    except ProbeError as exc:
        print(f"MoySklad probe failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "catalog": "reachable",
                "outbound_targets": [item[0] for item in _TARGETS],
                "write_operations": 0,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
