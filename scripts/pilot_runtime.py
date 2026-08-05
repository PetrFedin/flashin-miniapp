#!/usr/bin/env python3
"""Arm, stop and inspect the database-backed FLASHIN pilot checkout runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_control_audit import approved_operators, validate_audit_log
from pilot_control_chain import (
    state_anchor,
    validate_anchor_transition,
    validate_state_chain,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/pilot/pilot_admission_manifest.json"
DEFAULT_RELEASE = ROOT / "deploy/release/runtime/current_release.json"
DEFAULT_PILOT_STATE = ROOT / "docs/pilot/live_pilot_state.json"
TELEGRAM_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,19}$")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _true(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_ids(values: Sequence[str]) -> list[str]:
    normalized = [str(value).strip() for value in values]
    if not normalized:
        raise ValueError("At least one --telegram-id is required")
    invalid = [value for value in normalized if not TELEGRAM_ID_PATTERN.fullmatch(value)]
    if invalid:
        raise ValueError("Telegram IDs must be positive numeric identifiers")
    unique = sorted(set(normalized), key=int)
    if len(unique) != len(normalized):
        raise ValueError("Telegram allowlist contains duplicates")
    if len(unique) > 50:
        raise ValueError("Telegram allowlist may contain at most 50 participants")
    return unique


def _compose_exec(
    internal_command: str,
    *,
    payload: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> int:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "backend",
        "python",
        "scripts/pilot_runtime.py",
        internal_command,
    ]
    if reason is not None:
        command.extend(["--reason", reason])
    process_env = dict(os.environ)
    process_env.setdefault("COMPOSE_FILE", "docker-compose.yml:docker-compose.production.yml")
    process_env.setdefault("COMPOSE_PROFILES", "production,workers,scheduler,search")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=process_env,
        input=(json.dumps(payload, ensure_ascii=False) if payload is not None else None),
        text=True,
        check=False,
    )
    return completed.returncode


def _host_arm(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    from pilot_admission import verify_default_admission
    from pilot_control_binding import build_admission_binding, require_admission_binding
    from pilot_evidence import require_signing_secret, verify_payload_signature

    admission_errors = verify_default_admission(ROOT)
    if admission_errors:
        print(json.dumps({"ok": False, "errors": admission_errors}, ensure_ascii=False))
        return 1

    env = _read_env(ROOT / ".env")
    if not _true(env.get("PILOT_RUNTIME_ENFORCED")):
        print(json.dumps({"ok": False, "errors": ["PILOT_RUNTIME_ENFORCED must be true"]}))
        return 1
    try:
        max_orders = int(env.get("PILOT_RUNTIME_MAX_ORDERS", "20"))
    except ValueError:
        print(json.dumps({"ok": False, "errors": ["PILOT_RUNTIME_MAX_ORDERS must be an integer"]}))
        return 1
    if max_orders != 20:
        print(json.dumps({"ok": False, "errors": ["Pilot runtime must be limited to exactly 20 orders"]}))
        return 1

    try:
        secret = require_signing_secret(env)
        telegram_ids = _normalize_ids(args.telegram_id)
        manifest = _load_json(DEFAULT_MANIFEST, "pilot admission manifest")
        current = _load_json(DEFAULT_RELEASE, "current release pointer")
        pilot_state = _load_json(DEFAULT_PILOT_STATE, "pilot control state")
        if pilot_state.get("schema_version") != 7:
            raise ValueError("Pilot control state schema is unsupported")
        if pilot_state.get("database_evidence_contract") != 1:
            raise ValueError("Pilot database evidence contract is missing or unsupported")
        if pilot_state.get("inventory_evidence_contract") != 1:
            raise ValueError("Pilot inventory evidence contract is missing or unsupported")
        if not verify_payload_signature(pilot_state, secret):
            raise ValueError("Pilot control state signature is invalid")
        chain_errors = validate_state_chain(pilot_state)
        if chain_errors:
            raise ValueError("; ".join(chain_errors))
        audit_errors = validate_audit_log(
            pilot_state, approvals=approved_operators(manifest)
        )
        if audit_errors:
            raise ValueError("; ".join(audit_errors))
        pilot_anchor = state_anchor(pilot_state)
        require_admission_binding(
            pilot_state, build_admission_binding(DEFAULT_MANIFEST, manifest)
        )
        if pilot_state.get("decision") == "STOP":
            raise ValueError("Pilot control decision is STOP")
        scenarios = pilot_state.get("scenarios")
        if not isinstance(scenarios, list) or len(scenarios) != 20:
            raise ValueError("Pilot control state must contain exactly 20 scenarios")
        created_at = str(pilot_state.get("created_at", "")).strip()
        if not created_at:
            raise ValueError("Pilot control created_at is missing")
        release_sha = str(current.get("sha256", "")).strip()
        if len(release_sha) != 64:
            raise ValueError("Current release SHA-256 is invalid")
        manifest_sha = _sha256(DEFAULT_MANIFEST)
        release_binding = manifest.get("release")
        if not isinstance(release_binding, Mapping) or release_binding.get("sha256") != release_sha:
            raise ValueError("Admission manifest is not bound to the current release")
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1

    payload = {
        "telegram_ids": telegram_ids,
        "admission_sha256": manifest_sha,
        "release_sha256": release_sha,
        "pilot_state_created_at": created_at,
        "pilot_state_revision": pilot_anchor["revision"],
        "pilot_state_sha256": pilot_anchor["sha256"],
        "pilot_state_history": pilot_anchor["history"],
        "max_orders": max_orders,
        "resume": bool(args.resume),
    }
    return _compose_exec("_apply-arm", payload=payload)


def _internal_imports():
    from backend import models as _models  # noqa: F401
    from backend import pilot_models as _pilot_models  # noqa: F401
    from backend.config import get_settings
    from backend.database import SessionLocal, utcnow_naive
    from backend.pilot_models import PilotOrderSlot, PilotRuntimeState
    from backend.services.pilot_runtime import validate_runtime_files

    return (
        get_settings,
        SessionLocal,
        utcnow_naive,
        PilotOrderSlot,
        PilotRuntimeState,
        validate_runtime_files,
    )


def _internal_apply_arm() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("Arm payload must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}))
        return 1

    (
        get_settings,
        SessionLocal,
        utcnow_naive,
        PilotOrderSlot,
        PilotRuntimeState,
        validate_runtime_files,
    ) = _internal_imports()
    from backend.services.pilot_database_evidence import (
        validate_pilot_database_evidence,
    )

    settings = get_settings()
    if not settings.pilot_runtime_enforced or settings.pilot_runtime_max_orders != 20:
        print(json.dumps({"ok": False, "errors": ["Production pilot runtime is not fail-closed"]}))
        return 1

    try:
        telegram_ids = _normalize_ids(payload.get("telegram_ids") or [])
        admission_sha = str(payload.get("admission_sha256", ""))
        release_sha = str(payload.get("release_sha256", ""))
        pilot_created_at = str(payload.get("pilot_state_created_at", ""))
        pilot_revision = int(payload.get("pilot_state_revision", 0))
        pilot_sha = str(payload.get("pilot_state_sha256", ""))
        pilot_history = payload.get("pilot_state_history")
        max_orders = int(payload.get("max_orders", 0))
        resume = payload.get("resume") is True
        if len(admission_sha) != 64 or len(release_sha) != 64:
            raise ValueError("Runtime evidence hashes are invalid")
        if not pilot_created_at:
            raise ValueError("Pilot state created_at is required")
        if max_orders != 20:
            raise ValueError("Pilot runtime must be limited to exactly 20 orders")
        anchor_errors = validate_anchor_transition(
            revision=pilot_revision,
            sha256=pilot_sha,
            history=pilot_history,
            anchored_revision=0,
            anchored_sha256="",
        )
        if anchor_errors:
            raise ValueError("; ".join(anchor_errors))
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1

    db = SessionLocal()
    try:
        state = (
            db.query(PilotRuntimeState)
            .filter(PilotRuntimeState.id == 1)
            .with_for_update()
            .first()
        )
        if state is None:
            state = PilotRuntimeState(id=1)
            db.add(state)
            db.flush()

        slot_count = (
            db.query(PilotOrderSlot)
            .filter(PilotOrderSlot.run_id == state.run_id)
            .count()
            if state.run_id
            else 0
        )
        if slot_count != state.accepted_orders:
            raise ValueError("Pilot slot count does not match the runtime counter")
        allowlist_json = json.dumps(telegram_ids, ensure_ascii=False, separators=(",", ":"))

        if state.status == "completed":
            raise ValueError("The first-20-order pilot is already completed")
        if state.status == "active":
            exact = (
                state.admission_sha256 == admission_sha
                and state.release_sha256 == release_sha
                and state.pilot_state_created_at == pilot_created_at
                and state.allowed_telegram_ids == allowlist_json
                and state.max_orders == max_orders
            )
            if not exact:
                raise ValueError("Active pilot runtime differs from the requested arm state")
            transition_errors = validate_anchor_transition(
                revision=pilot_revision,
                sha256=pilot_sha,
                history=pilot_history,
                anchored_revision=state.pilot_state_revision,
                anchored_sha256=state.pilot_state_sha256,
            )
            if transition_errors:
                raise ValueError("Active pilot state lineage is invalid: " + "; ".join(transition_errors))
            verified_anchor: dict[str, Any] = {}
            verified_pilot_state: dict[str, Any] = {}
            errors = validate_runtime_files(
                state,
                settings,
                validated_anchor=verified_anchor,
                validated_pilot_state=verified_pilot_state,
            )
            if errors:
                raise ValueError("Active pilot runtime evidence is invalid: " + "; ".join(errors))
            database_errors = validate_pilot_database_evidence(
                db,
                verified_pilot_state,
                state,
                final=False,
            )
            if database_errors:
                raise ValueError(
                    "Active pilot database evidence is invalid: "
                    + "; ".join(database_errors)
                )
            if verified_anchor.get("revision") != pilot_revision or verified_anchor.get("sha256") != pilot_sha:
                raise ValueError("Host pilot state anchor does not match runtime evidence")
            state.pilot_state_revision = pilot_revision
            state.pilot_state_sha256 = pilot_sha
            state.updated_at = utcnow_naive()
            db.commit()
            print(
                json.dumps(
                    {
                        "ok": True,
                        "reused": True,
                        "status": state.status,
                        "run_id": state.run_id,
                        "accepted_orders": state.accepted_orders,
                        "max_orders": state.max_orders,
                        "allowlist_count": len(telegram_ids),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if state.status == "stopped" and not resume:
            raise ValueError("Stopped pilot runtime requires --resume and a fresh valid admission")
        if state.status == "closed" and resume:
            raise ValueError("--resume cannot be used before the first pilot arm")
        if state.status == "closed" and (state.accepted_orders or slot_count):
            raise ValueError("Closed pilot runtime contains historical order slots")
        if state.status == "stopped" and state.accepted_orders >= state.max_orders:
            raise ValueError("Stopped pilot runtime has no remaining order slots")
        if state.status == "stopped":
            same_lineage = (
                state.admission_sha256 == admission_sha
                and state.release_sha256 == release_sha
                and state.pilot_state_created_at == pilot_created_at
                and state.max_orders == max_orders
            )
            if not same_lineage:
                raise ValueError("Stopped pilot runtime cannot change admission or release lineage")
            transition_errors = validate_anchor_transition(
                revision=pilot_revision,
                sha256=pilot_sha,
                history=pilot_history,
                anchored_revision=state.pilot_state_revision,
                anchored_sha256=state.pilot_state_sha256,
            )
            if transition_errors:
                raise ValueError("Stopped pilot state lineage is invalid: " + "; ".join(transition_errors))

        if state.status == "closed":
            state.run_id = uuid.uuid4().hex
            state.accepted_orders = 0
            state.opened_at = utcnow_naive()
        state.status = "active"
        state.admission_sha256 = admission_sha
        state.release_sha256 = release_sha
        state.pilot_state_created_at = pilot_created_at
        state.pilot_state_revision = pilot_revision
        state.pilot_state_sha256 = pilot_sha
        state.max_orders = max_orders
        state.allowed_telegram_ids = allowlist_json
        state.stopped_at = None
        state.stop_reason = ""
        state.updated_at = utcnow_naive()

        verified_anchor: dict[str, Any] = {}
        verified_pilot_state: dict[str, Any] = {}
        errors = validate_runtime_files(
            state,
            settings,
            validated_anchor=verified_anchor,
            validated_pilot_state=verified_pilot_state,
        )
        if errors:
            raise ValueError("Pilot runtime evidence is invalid: " + "; ".join(errors))
        database_errors = validate_pilot_database_evidence(
            db,
            verified_pilot_state,
            state,
            final=False,
        )
        if database_errors:
            raise ValueError(
                "Pilot database evidence is invalid: "
                + "; ".join(database_errors)
            )
        if verified_anchor.get("revision") != pilot_revision or verified_anchor.get("sha256") != pilot_sha:
            raise ValueError("Host pilot state anchor does not match runtime evidence")
        db.commit()
        print(
            json.dumps(
                {
                    "ok": True,
                    "reused": False,
                    "resumed": resume,
                    "status": state.status,
                    "run_id": state.run_id,
                    "accepted_orders": state.accepted_orders,
                    "max_orders": state.max_orders,
                    "allowlist_count": len(telegram_ids),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        db.rollback()
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    finally:
        db.close()


def _internal_stop(reason: str) -> int:
    (
        _get_settings,
        SessionLocal,
        utcnow_naive,
        _PilotOrderSlot,
        PilotRuntimeState,
        _validate_runtime_files,
    ) = _internal_imports()
    clean_reason = " ".join(str(reason or "operator stop").split())[:500]
    db = SessionLocal()
    try:
        state = (
            db.query(PilotRuntimeState)
            .filter(PilotRuntimeState.id == 1)
            .with_for_update()
            .first()
        )
        if state is None:
            print(json.dumps({"ok": True, "status": "closed", "changed": False}))
            return 0
        changed = state.status == "active"
        if changed:
            state.status = "stopped"
            state.stopped_at = utcnow_naive()
            state.stop_reason = clean_reason
            state.updated_at = utcnow_naive()
            db.commit()
        else:
            db.rollback()
        print(
            json.dumps(
                {
                    "ok": True,
                    "status": state.status,
                    "changed": changed,
                    "accepted_orders": state.accepted_orders,
                    "max_orders": state.max_orders,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        db.rollback()
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    finally:
        db.close()


def _internal_status() -> int:
    (
        get_settings,
        SessionLocal,
        _utcnow_naive,
        PilotOrderSlot,
        PilotRuntimeState,
        validate_runtime_files,
    ) = _internal_imports()
    settings = get_settings()
    db = SessionLocal()
    try:
        state = db.query(PilotRuntimeState).filter(PilotRuntimeState.id == 1).first()
        if state is None:
            print(json.dumps({"ok": True, "status": "closed", "accepted_orders": 0, "max_orders": 20}))
            return 0
        slot_count = (
            db.query(PilotOrderSlot).filter(PilotOrderSlot.run_id == state.run_id).count()
            if state.run_id
            else 0
        )
        allowlist_count = 0
        try:
            allowlist = json.loads(state.allowed_telegram_ids or "[]")
            if isinstance(allowlist, list):
                allowlist_count = len(allowlist)
        except json.JSONDecodeError:
            pass
        errors: list[str] = []
        if slot_count != state.accepted_orders:
            errors.append("pilot slot count does not match runtime counter")
        if state.status == "active":
            errors.extend(validate_runtime_files(state, settings))
        result = {
            "ok": not errors,
            "status": state.status,
            "run_id": state.run_id or None,
            "accepted_orders": state.accepted_orders,
            "max_orders": state.max_orders,
            "remaining_orders": max(state.max_orders - state.accepted_orders, 0),
            "slot_count": slot_count,
            "allowlist_count": allowlist_count,
            "errors": list(dict.fromkeys(errors)),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not errors else 1
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    arm = sub.add_parser("arm", help="Verify admission and open checkout for allowlisted pilot users")
    arm.add_argument("--telegram-id", action="append", default=[], required=True)
    arm.add_argument("--resume", action="store_true")

    sub.add_parser("status", help="Show safe runtime state and integrity status")
    stop = sub.add_parser("stop", help="Immediately block new pilot checkouts")
    stop.add_argument("--reason", required=True)

    sub.add_parser("_apply-arm", help=argparse.SUPPRESS)
    sub.add_parser("_status", help=argparse.SUPPRESS)
    internal_stop = sub.add_parser("_stop", help=argparse.SUPPRESS)
    internal_stop.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "arm":
        return _host_arm(args)
    if args.command == "status":
        return _compose_exec("_status")
    if args.command == "stop":
        return _compose_exec("_stop", reason=args.reason)
    if args.command == "_apply-arm":
        return _internal_apply_arm()
    if args.command == "_status":
        return _internal_status()
    if args.command == "_stop":
        return _internal_stop(args.reason)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
