from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/pilot_release_contract.py",
    "CAPABILITY_VERSION = 11",
    "CAPABILITY_VERSION = 12",
)

Path("scripts/pilot_control_chain.py").write_text(
    '''"""Replay-resistant lineage helpers for signed pilot control states."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\\n"
    ).encode("utf-8")


def signed_state_sha256(state: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(state)).hexdigest()


def validate_chain_fields(revision: object, history: object) -> list[str]:
    errors: list[str] = []
    if type(revision) is not int or revision < 1:
        errors.append("pilot control state revision must be a positive integer")
        return errors
    if not isinstance(history, list):
        errors.append("pilot control state history must be a list")
        return errors
    if len(history) != revision - 1:
        errors.append("pilot control state history length does not match revision")
    for index, value in enumerate(history, start=1):
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            errors.append(f"pilot control state history hash #{index} is invalid")
    if len(history) != len(set(history)):
        errors.append("pilot control state history contains duplicate hashes")
    return list(dict.fromkeys(errors))


def validate_state_chain(state: Mapping[str, Any]) -> list[str]:
    return validate_chain_fields(state.get("revision"), state.get("state_history_sha256"))


def require_state_chain(state: Mapping[str, Any]) -> None:
    errors = validate_state_chain(state)
    if errors:
        raise ValueError("; ".join(errors))


def state_anchor(state: Mapping[str, Any]) -> dict[str, Any]:
    require_state_chain(state)
    return {
        "revision": int(state["revision"]),
        "sha256": signed_state_sha256(state),
        "history": list(state["state_history_sha256"]),
    }


def validate_anchor_transition(
    *,
    revision: object,
    sha256: object,
    history: object,
    anchored_revision: object,
    anchored_sha256: object,
) -> list[str]:
    errors = validate_chain_fields(revision, history)
    current_sha = str(sha256 or "")
    if not _SHA256.fullmatch(current_sha):
        errors.append("pilot control state SHA-256 is invalid")
    if type(anchored_revision) is not int or anchored_revision < 0:
        errors.append("armed pilot state revision is invalid")
        return list(dict.fromkeys(errors))
    anchor_sha = str(anchored_sha256 or "")
    if anchored_revision == 0:
        if anchor_sha:
            errors.append("uninitialized pilot state anchor contains a SHA-256")
        return list(dict.fromkeys(errors))
    if not _SHA256.fullmatch(anchor_sha):
        errors.append("armed pilot state SHA-256 is invalid")
        return list(dict.fromkeys(errors))
    if errors:
        return list(dict.fromkeys(errors))
    current_revision = int(revision)
    current_history = list(history) if isinstance(history, list) else []
    if current_revision < anchored_revision:
        errors.append("pilot control state revision rollback detected")
    elif current_revision == anchored_revision:
        if current_sha != anchor_sha:
            errors.append("pilot control state hash does not match the armed runtime")
    else:
        index = anchored_revision - 1
        if index >= len(current_history) or current_history[index] != anchor_sha:
            errors.append("pilot control state ancestry does not match the armed runtime")
    return list(dict.fromkeys(errors))


def validate_state_descendant(
    state: Mapping[str, Any],
    *,
    anchored_revision: int,
    anchored_sha256: str,
) -> list[str]:
    return validate_anchor_transition(
        revision=state.get("revision"),
        sha256=signed_state_sha256(state),
        history=state.get("state_history_sha256"),
        anchored_revision=anchored_revision,
        anchored_sha256=anchored_sha256,
    )
''',
    encoding="utf-8",
)

# Pilot state schema v4 and append-only signed lineage.
control = Path("scripts/pilot_control.py")
text = control.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control_binding import build_admission_binding, require_admission_binding\n",
    "from pilot_control_binding import build_admission_binding, require_admission_binding\n"
    "from pilot_control_chain import (\n"
    "    require_state_chain,\n"
    "    signed_state_sha256,\n"
    ")\n",
    1,
)
text = text.replace("SCHEMA_VERSION = 3", "SCHEMA_VERSION = 4", 1)
text = text.replace(
    '        "decision": "NO-GO",\n'
    '        "stop_reasons": [],\n',
    '        "decision": "NO-GO",\n'
    '        "stop_reasons": [],\n'
    '        "revision": 1,\n'
    '        "state_history_sha256": [],\n',
    1,
)
old_schema = '''    if schema == 2:
        raise ValueError(
            "Unsigned pilot state schema 2 cannot be reused. Archive it and initialize "
            "a fresh signed admission-bound pilot state."
        )
    if schema != SCHEMA_VERSION:
'''
new_schema = '''    if schema == 2:
        raise ValueError(
            "Unsigned pilot state schema 2 cannot be reused. Archive it and initialize "
            "a fresh replay-resistant pilot state."
        )
    if schema == 3:
        raise ValueError(
            "Replay-vulnerable pilot state schema 3 cannot be reused. Archive it and "
            "initialize a fresh replay-resistant pilot state."
        )
    if schema != SCHEMA_VERSION:
'''
if text.count(old_schema) != 1:
    raise SystemExit("pilot_control.py schema migration block changed unexpectedly")
text = text.replace(old_schema, new_schema, 1)
text = text.replace(
    '    if not verify_payload_signature(state, secret):\n'
    '        raise ValueError("Pilot control state signature is invalid")\n'
    '    if [item.get("number") for item in _scenario_records(state)]',
    '    if not verify_payload_signature(state, secret):\n'
    '        raise ValueError("Pilot control state signature is invalid")\n'
    '    require_state_chain(state)\n'
    '    if [item.get("number") for item in _scenario_records(state)]',
    1,
)
old_save = '''def save_state(
    path: Path,
    state: dict[str, Any],
    report: dict[str, Any],
    *,
    secret: str,
) -> None:
    state["updated_at"] = utc_timestamp()
    _apply_report(state, report)
    signed_state = sign_payload(state, secret)
    state.clear()
    state.update(signed_state)
    _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\\n")
    _atomic_write_text(path.with_name("live_pilot_summary.md"), render_markdown(state, report))
'''
new_save = '''def save_state(
    path: Path,
    state: dict[str, Any],
    report: dict[str, Any],
    *,
    secret: str,
) -> None:
    if "signature" in state:
        if not verify_payload_signature(state, secret):
            raise ValueError("Pilot control state signature is invalid before update")
        require_state_chain(state)
        previous_hash = signed_state_sha256(state)
        state["state_history_sha256"] = [
            *list(state["state_history_sha256"]),
            previous_hash,
        ]
        state["revision"] = int(state["revision"]) + 1
    else:
        require_state_chain(state)
        if state.get("revision") != 1 or state.get("state_history_sha256") != []:
            raise ValueError("Initial pilot control state lineage is invalid")
    state["updated_at"] = utc_timestamp()
    _apply_report(state, report)
    signed_state = sign_payload(state, secret)
    state.clear()
    state.update(signed_state)
    _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\\n")
    _atomic_write_text(path.with_name("live_pilot_summary.md"), render_markdown(state, report))
'''
if text.count(old_save) != 1:
    raise SystemExit("pilot_control.py save_state block changed unexpectedly")
text = text.replace(old_save, new_save, 1)
text = text.replace(
    "    final: bool = False,\n) -> int:\n"
    "    report = validate_state(state, final=final)\n"
    "    save_state(path, state, report, secret=secret)\n",
    "    final: bool = False,\n"
    "    persist: bool = True,\n"
    ") -> int:\n"
    "    report = validate_state(state, final=final)\n"
    "    if persist:\n"
    "        save_state(path, state, report, secret=secret)\n",
    1,
)
text = text.replace(
    "        secret=args.signing_secret,\n    )\n\n\ndef command_validate",
    "        secret=args.signing_secret,\n"
    "        persist=False,\n"
    "    )\n\n\ndef command_validate",
    1,
)
text = text.replace(
    "        secret=args.signing_secret,\n        final=args.final,\n    )\n\n\ndef build_parser",
    "        secret=args.signing_secret,\n"
    "        final=args.final,\n"
    "        persist=False,\n"
    "    )\n\n\ndef build_parser",
    1,
)
control.write_text(text, encoding="utf-8")

# Database anchor model and migration.
model = Path("backend/pilot_models.py")
text = model.read_text(encoding="utf-8")
text = text.replace(
    '        CheckConstraint(\n'
    '            "accepted_orders >= 0 AND accepted_orders <= max_orders",\n'
    '            name="ck_pilot_runtime_state_accepted_orders",\n'
    '        ),\n',
    '        CheckConstraint(\n'
    '            "accepted_orders >= 0 AND accepted_orders <= max_orders",\n'
    '            name="ck_pilot_runtime_state_accepted_orders",\n'
    '        ),\n'
    '        CheckConstraint(\n'
    '            "pilot_state_revision >= 0",\n'
    '            name="ck_pilot_runtime_state_revision",\n'
    '        ),\n'
    '        CheckConstraint(\n'
    '            "(pilot_state_revision = 0 AND pilot_state_sha256 = \'\') OR "\n'
    '            "(pilot_state_revision >= 1 AND length(pilot_state_sha256) = 64)",\n'
    '            name="ck_pilot_runtime_state_anchor",\n'
    '        ),\n',
    1,
)
text = text.replace(
    '    pilot_state_created_at: Mapped[str] = mapped_column(String(64), default="")\n',
    '    pilot_state_created_at: Mapped[str] = mapped_column(String(64), default="")\n'
    '    pilot_state_revision: Mapped[int] = mapped_column(Integer, default=0)\n'
    '    pilot_state_sha256: Mapped[str] = mapped_column(String(64), default="")\n',
    1,
)
model.write_text(text, encoding="utf-8")

Path("backend/alembic/versions/0023_pilot_state_replay_anchor.py").write_text(
    '''"""add replay-resistant pilot state anchor

Revision ID: 0023_pilot_state_replay_anchor
Revises: 0022_pilot_runtime_guard
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_pilot_state_replay_anchor"
down_revision = "0022_pilot_runtime_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pilot_runtime_state",
        sa.Column("pilot_state_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "pilot_runtime_state",
        sa.Column("pilot_state_sha256", sa.String(length=64), server_default="", nullable=False),
    )
    op.create_check_constraint(
        "ck_pilot_runtime_state_revision",
        "pilot_runtime_state",
        "pilot_state_revision >= 0",
    )
    op.create_check_constraint(
        "ck_pilot_runtime_state_anchor",
        "pilot_runtime_state",
        "(pilot_state_revision = 0 AND pilot_state_sha256 = '') OR "
        "(pilot_state_revision >= 1 AND length(pilot_state_sha256) = 64)",
    )


def downgrade():
    op.drop_constraint(
        "ck_pilot_runtime_state_anchor", "pilot_runtime_state", type_="check"
    )
    op.drop_constraint(
        "ck_pilot_runtime_state_revision", "pilot_runtime_state", type_="check"
    )
    op.drop_column("pilot_runtime_state", "pilot_state_sha256")
    op.drop_column("pilot_runtime_state", "pilot_state_revision")
''',
    encoding="utf-8",
)

# Runtime verification compares the current signed lineage against the DB anchor.
runtime = Path("backend/services/pilot_runtime.py")
text = runtime.read_text(encoding="utf-8")
text = text.replace(
    "from scripts.pilot_control_binding import build_admission_binding, validate_admission_binding\n",
    "from scripts.pilot_control_binding import build_admission_binding, validate_admission_binding\n"
    "from scripts.pilot_control_chain import (\n"
    "    state_anchor,\n"
    "    validate_state_chain,\n"
    "    validate_state_descendant,\n"
    ")\n",
    1,
)
text = text.replace(
    '    env: Mapping[str, str] | None = None,\n) -> list[str]:',
    '    env: Mapping[str, str] | None = None,\n'
    '    validated_anchor: dict[str, Any] | None = None,\n'
    ') -> list[str]:',
    1,
)
old_state_block = '''    if pilot_state.get("schema_version") != 3:
        errors.append("pilot control state schema is unsupported")
    elif not verify_payload_signature(pilot_state, secret):
        errors.append("pilot control state signature is invalid")
    else:
        try:
            expected_binding = build_admission_binding(manifest_path, manifest)
            errors.extend(validate_admission_binding(pilot_state, expected_binding))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
'''
new_state_block = '''    if pilot_state.get("schema_version") != 4:
        errors.append("pilot control state schema is unsupported")
    elif not verify_payload_signature(pilot_state, secret):
        errors.append("pilot control state signature is invalid")
    else:
        chain_errors = validate_state_chain(pilot_state)
        errors.extend(chain_errors)
        try:
            expected_binding = build_admission_binding(manifest_path, manifest)
            errors.extend(validate_admission_binding(pilot_state, expected_binding))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        if state.status in {"active", "stopped"} and (
            state.pilot_state_revision < 1 or len(state.pilot_state_sha256) != 64
        ):
            errors.append("armed runtime pilot state replay anchor is missing")
        elif not chain_errors:
            errors.extend(
                validate_state_descendant(
                    pilot_state,
                    anchored_revision=state.pilot_state_revision,
                    anchored_sha256=state.pilot_state_sha256,
                )
            )
'''
if text.count(old_state_block) != 1:
    raise SystemExit("backend runtime state validation block changed unexpectedly")
text = text.replace(old_state_block, new_state_block, 1)
text = text.replace(
    "    return list(dict.fromkeys(errors))\n\n\ndef _blocked()",
    "    unique_errors = list(dict.fromkeys(errors))\n"
    "    if not unique_errors and validated_anchor is not None:\n"
    "        validated_anchor.update(state_anchor(pilot_state))\n"
    "    return unique_errors\n\n\ndef _blocked()",
    1,
)
text = text.replace(
    "    file_errors = validate_runtime_files(state, settings, env=env)\n",
    "    current_anchor: dict[str, Any] = {}\n"
    "    file_errors = validate_runtime_files(\n"
    "        state, settings, env=env, validated_anchor=current_anchor\n"
    "    )\n",
    1,
)
text = text.replace(
    "    allowlist, allowlist_errors = _parse_allowlist(state.allowed_telegram_ids)\n",
    "    state.pilot_state_revision = int(current_anchor[\"revision\"])\n"
    "    state.pilot_state_sha256 = str(current_anchor[\"sha256\"])\n"
    "    state.updated_at = utcnow_naive()\n\n"
    "    allowlist, allowlist_errors = _parse_allowlist(state.allowed_telegram_ids)\n",
    1,
)
runtime.write_text(text, encoding="utf-8")

# Runtime arm transports and validates the exact current lineage.
runtime_cli = Path("scripts/pilot_runtime.py")
text = runtime_cli.read_text(encoding="utf-8")
text = text.replace(
    "from typing import Any, Mapping, Sequence\n",
    "from typing import Any, Mapping, Sequence\n\n"
    "from pilot_control_chain import (\n"
    "    state_anchor,\n"
    "    validate_anchor_transition,\n"
    "    validate_state_chain,\n"
    ")\n",
    1,
)
text = text.replace(
    '        if pilot_state.get("schema_version") != 3:\n'
    '            raise ValueError("Pilot control state schema is unsupported")\n',
    '        if pilot_state.get("schema_version") != 4:\n'
    '            raise ValueError("Pilot control state schema is unsupported")\n',
    1,
)
text = text.replace(
    '        if not verify_payload_signature(pilot_state, secret):\n'
    '            raise ValueError("Pilot control state signature is invalid")\n'
    '        require_admission_binding(\n',
    '        if not verify_payload_signature(pilot_state, secret):\n'
    '            raise ValueError("Pilot control state signature is invalid")\n'
    '        chain_errors = validate_state_chain(pilot_state)\n'
    '        if chain_errors:\n'
    '            raise ValueError("; ".join(chain_errors))\n'
    '        pilot_anchor = state_anchor(pilot_state)\n'
    '        require_admission_binding(\n',
    1,
)
text = text.replace(
    '        "pilot_state_created_at": created_at,\n'
    '        "max_orders": max_orders,\n',
    '        "pilot_state_created_at": created_at,\n'
    '        "pilot_state_revision": pilot_anchor["revision"],\n'
    '        "pilot_state_sha256": pilot_anchor["sha256"],\n'
    '        "pilot_state_history": pilot_anchor["history"],\n'
    '        "max_orders": max_orders,\n',
    1,
)
text = text.replace(
    '        pilot_created_at = str(payload.get("pilot_state_created_at", ""))\n'
    '        max_orders = int(payload.get("max_orders", 0))\n',
    '        pilot_created_at = str(payload.get("pilot_state_created_at", ""))\n'
    '        pilot_revision = int(payload.get("pilot_state_revision", 0))\n'
    '        pilot_sha = str(payload.get("pilot_state_sha256", ""))\n'
    '        pilot_history = payload.get("pilot_state_history")\n'
    '        max_orders = int(payload.get("max_orders", 0))\n',
    1,
)
text = text.replace(
    '        if max_orders != 20:\n'
    '            raise ValueError("Pilot runtime must be limited to exactly 20 orders")\n',
    '        if max_orders != 20:\n'
    '            raise ValueError("Pilot runtime must be limited to exactly 20 orders")\n'
    '        anchor_errors = validate_anchor_transition(\n'
    '            revision=pilot_revision,\n'
    '            sha256=pilot_sha,\n'
    '            history=pilot_history,\n'
    '            anchored_revision=0,\n'
    '            anchored_sha256="",\n'
    '        )\n'
    '        if anchor_errors:\n'
    '            raise ValueError("; ".join(anchor_errors))\n',
    1,
)
old_active = '''        if state.status == "active":
            exact = (
                state.admission_sha256 == admission_sha
                and state.release_sha256 == release_sha
                and state.pilot_state_created_at == pilot_created_at
                and state.allowed_telegram_ids == allowlist_json
                and state.max_orders == max_orders
            )
            if not exact:
                raise ValueError("Active pilot runtime differs from the requested arm state")
            errors = validate_runtime_files(state, settings)
            if errors:
                raise ValueError("Active pilot runtime evidence is invalid: " + "; ".join(errors))
            db.commit()
'''
new_active = '''        if state.status == "active":
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
            errors = validate_runtime_files(
                state, settings, validated_anchor=verified_anchor
            )
            if errors:
                raise ValueError("Active pilot runtime evidence is invalid: " + "; ".join(errors))
            if verified_anchor.get("revision") != pilot_revision or verified_anchor.get("sha256") != pilot_sha:
                raise ValueError("Host pilot state anchor does not match runtime evidence")
            state.pilot_state_revision = pilot_revision
            state.pilot_state_sha256 = pilot_sha
            state.updated_at = utcnow_naive()
            db.commit()
'''
if text.count(old_active) != 1:
    raise SystemExit("pilot_runtime.py active arm block changed unexpectedly")
text = text.replace(old_active, new_active, 1)
text = text.replace(
    '        if state.status == "stopped" and state.accepted_orders >= state.max_orders:\n'
    '            raise ValueError("Stopped pilot runtime has no remaining order slots")\n\n'
    '        if state.status == "closed":\n',
    '        if state.status == "stopped" and state.accepted_orders >= state.max_orders:\n'
    '            raise ValueError("Stopped pilot runtime has no remaining order slots")\n'
    '        if state.status == "stopped":\n'
    '            same_lineage = (\n'
    '                state.admission_sha256 == admission_sha\n'
    '                and state.release_sha256 == release_sha\n'
    '                and state.pilot_state_created_at == pilot_created_at\n'
    '                and state.max_orders == max_orders\n'
    '            )\n'
    '            if not same_lineage:\n'
    '                raise ValueError("Stopped pilot runtime cannot change admission or release lineage")\n'
    '            transition_errors = validate_anchor_transition(\n'
    '                revision=pilot_revision,\n'
    '                sha256=pilot_sha,\n'
    '                history=pilot_history,\n'
    '                anchored_revision=state.pilot_state_revision,\n'
    '                anchored_sha256=state.pilot_state_sha256,\n'
    '            )\n'
    '            if transition_errors:\n'
    '                raise ValueError("Stopped pilot state lineage is invalid: " + "; ".join(transition_errors))\n\n'
    '        if state.status == "closed":\n',
    1,
)
text = text.replace(
    '        state.pilot_state_created_at = pilot_created_at\n'
    '        state.max_orders = max_orders\n',
    '        state.pilot_state_created_at = pilot_created_at\n'
    '        state.pilot_state_revision = pilot_revision\n'
    '        state.pilot_state_sha256 = pilot_sha\n'
    '        state.max_orders = max_orders\n',
    1,
)
text = text.replace(
    '        errors = validate_runtime_files(state, settings)\n'
    '        if errors:\n'
    '            raise ValueError("Pilot runtime evidence is invalid: " + "; ".join(errors))\n'
    '        db.commit()\n',
    '        verified_anchor: dict[str, Any] = {}\n'
    '        errors = validate_runtime_files(\n'
    '            state, settings, validated_anchor=verified_anchor\n'
    '        )\n'
    '        if errors:\n'
    '            raise ValueError("Pilot runtime evidence is invalid: " + "; ".join(errors))\n'
    '        if verified_anchor.get("revision") != pilot_revision or verified_anchor.get("sha256") != pilot_sha:\n'
    '            raise ValueError("Host pilot state anchor does not match runtime evidence")\n'
    '        db.commit()\n',
    1,
)
runtime_cli.write_text(text, encoding="utf-8")

# Replay-chain unit tests.
Path("backend/tests/test_pilot_control_signature.py").write_text(
    '''import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control import load_state, new_state, save_state, validate_state  # noqa: E402
from pilot_control_chain import (  # noqa: E402
    state_anchor,
    validate_anchor_transition,
)
from pilot_evidence import verify_payload_signature  # noqa: E402

SECRET = "s" * 48
BINDING = {
    "manifest_sha256": "a" * 64,
    "created_at": "2026-08-05T12:00:00Z",
    "configuration_fingerprint": "b" * 64,
    "release": {
        "release_id": "release-a",
        "git_commit": "c" * 40,
        "sha256": "d" * 64,
    },
}


def _signed_state(path: Path) -> dict:
    state = new_state(BINDING)
    save_state(path, state, validate_state(state, final=False), secret=SECRET)
    return state


def test_state_write_is_signed_and_exact_state_loads(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = _signed_state(path)
    assert state["schema_version"] == 4
    assert state["revision"] == 1
    assert state["state_history_sha256"] == []
    assert verify_payload_signature(state, SECRET)
    loaded = load_state(path, expected_admission=BINDING, secret=SECRET)
    assert verify_payload_signature(loaded, SECRET)


def test_authorized_write_advances_revision_and_preserves_exact_parent_hash(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = _signed_state(path)
    parent = state_anchor(state)
    state["scenarios"][0]["result"] = "running"
    save_state(path, state, validate_state(state, final=False), secret=SECRET)
    child = state_anchor(state)

    assert child["revision"] == 2
    assert child["history"] == [parent["sha256"]]
    assert validate_anchor_transition(
        revision=child["revision"],
        sha256=child["sha256"],
        history=child["history"],
        anchored_revision=parent["revision"],
        anchored_sha256=parent["sha256"],
    ) == []


def test_replayed_or_unrelated_signed_branch_is_rejected_by_anchor(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    parent_state = _signed_state(path)
    parent = state_anchor(parent_state)
    parent_state["scenarios"][0]["result"] = "running"
    save_state(path, parent_state, validate_state(parent_state, final=False), secret=SECRET)
    child = state_anchor(parent_state)

    replay_errors = validate_anchor_transition(
        revision=parent["revision"],
        sha256=parent["sha256"],
        history=parent["history"],
        anchored_revision=child["revision"],
        anchored_sha256=child["sha256"],
    )
    assert any("rollback" in item for item in replay_errors)

    fork_errors = validate_anchor_transition(
        revision=2,
        sha256="f" * 64,
        history=["e" * 64],
        anchored_revision=parent["revision"],
        anchored_sha256=parent["sha256"],
    )
    assert any("ancestry" in item for item in fork_errors)


def test_tampered_scenario_or_decision_is_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scenarios"][0]["result"] = "pass"
    payload["decision"] = "GO"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Pilot control state signature is invalid"):
        load_state(path, expected_admission=BINDING, secret=SECRET)


def test_wrong_secret_and_legacy_schemas_are_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    with pytest.raises(ValueError, match="Pilot control state signature is invalid"):
        load_state(path, expected_admission=BINDING, secret="x" * 48)

    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsigned pilot state schema 2 cannot be reused"):
        load_state(path, secret=SECRET)

    path.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")
    with pytest.raises(ValueError, match="Replay-vulnerable pilot state schema 3 cannot be reused"):
        load_state(path, secret=SECRET)
''',
    encoding="utf-8",
)

# Runtime tests issue schema v4 states and exercise descendant/replay behavior.
runtime_tests = Path("backend/tests/test_pilot_runtime.py")
text = runtime_tests.read_text(encoding="utf-8")
text = text.replace(
    "from scripts.pilot_control_binding import build_admission_binding\n",
    "from scripts.pilot_control_binding import build_admission_binding\n"
    "from scripts.pilot_control_chain import state_anchor\n",
    1,
)
text = text.replace('"version": 11,', '"version": 12,', 1)
text = text.replace(
    '        "schema_version": 3,\n'
    '        "created_at": pilot_created_at,\n',
    '        "schema_version": 4,\n'
    '        "revision": 1,\n'
    '        "state_history_sha256": [],\n'
    '        "created_at": pilot_created_at,\n',
    1,
)
text = text.replace(
    '    pilot_path.write_text(\n'
    '        json.dumps(sign_payload(pilot_payload, secret)), encoding="utf-8"\n'
    '    )\n',
    '    signed_pilot = sign_payload(pilot_payload, secret)\n'
    '    pilot_path.write_text(json.dumps(signed_pilot), encoding="utf-8")\n'
    '    pilot_anchor = state_anchor(signed_pilot)\n',
    1,
)
text = text.replace(
    '        pilot_state_created_at=pilot_created_at,\n'
    '        max_orders=20,\n',
    '        pilot_state_created_at=pilot_created_at,\n'
    '        pilot_state_revision=pilot_anchor["revision"],\n'
    '        pilot_state_sha256=pilot_anchor["sha256"],\n'
    '        max_orders=20,\n',
    1,
)
helper_anchor = 'def test_allowlisted_checkout_consumes_one_atomic_slot(tmp_path):\n'
helper = '''def _next_signed_state(payload: dict, secret: str) -> dict:
    parent = state_anchor(payload)
    child = dict(payload)
    child.pop("signature", None)
    child["revision"] = parent["revision"] + 1
    child["state_history_sha256"] = [*parent["history"], parent["sha256"]]
    return sign_payload(child, secret)


'''
if text.count(helper_anchor) != 1:
    raise SystemExit("runtime test insertion anchor changed unexpectedly")
text = text.replace(helper_anchor, helper + helper_anchor, 1)
text = text.replace(
    '        json.dumps(sign_payload(payload, env["PILOT_EVIDENCE_SIGNING_SECRET"])),\n'
    '        encoding="utf-8",\n'
    '    )',
    '        json.dumps(_next_signed_state(payload, env["PILOT_EVIDENCE_SIGNING_SECRET"])),\n'
    '        encoding="utf-8",\n'
    '    )',
    2,
)
text += '''


def test_runtime_anchor_advances_to_descendant_and_rejects_replay(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    original_bytes = pilot_path.read_bytes()
    original = json.loads(original_bytes)
    descendant = _next_signed_state(original, env["PILOT_EVIDENCE_SIGNING_SECRET"])
    descendant["scenarios"][0]["result"] = "running"
    descendant = sign_payload(descendant, env["PILOT_EVIDENCE_SIGNING_SECRET"])
    pilot_path.write_text(json.dumps(descendant), encoding="utf-8")

    acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    db.commit()
    runtime = db.get(PilotRuntimeState, 1)
    child_anchor = state_anchor(descendant)
    assert runtime.pilot_state_revision == child_anchor["revision"]
    assert runtime.pilot_state_sha256 == child_anchor["sha256"]

    pilot_path.write_bytes(original_bytes)
    with pytest.raises(HTTPException) as replay:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert replay.value.status_code == 503


def test_unrelated_valid_signed_state_branch_fails_closed(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    current = json.loads(pilot_path.read_text(encoding="utf-8"))
    fork = dict(current)
    fork.pop("signature", None)
    fork["revision"] = 2
    fork["state_history_sha256"] = ["f" * 64]
    fork = sign_payload(fork, env["PILOT_EVIDENCE_SIGNING_SECRET"])
    pilot_path.write_text(json.dumps(fork), encoding="utf-8")

    with pytest.raises(HTTPException) as unrelated:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert unrelated.value.status_code == 503
'''
runtime_tests.write_text(text, encoding="utf-8")

runtime_cli_tests = Path("backend/tests/test_pilot_runtime_cli.py")
text = runtime_cli_tests.read_text(encoding="utf-8")
text = text.replace(
    'def test_host_arm_requires_signed_schema_v3_control_state():\n',
    'def test_host_arm_requires_replay_resistant_schema_v4_control_state():\n',
    1,
)
text = text.replace(
    'assert \'pilot_state.get("schema_version") != 3\' in source',
    'assert \'pilot_state.get("schema_version") != 4\' in source',
    1,
)
text += '''


def test_runtime_arm_transports_and_validates_state_lineage_anchor():
    source = (ROOT / "scripts/pilot_runtime.py").read_text(encoding="utf-8")
    for marker in (
        '"pilot_state_revision": pilot_anchor["revision"]',
        '"pilot_state_sha256": pilot_anchor["sha256"]',
        '"pilot_state_history": pilot_anchor["history"]',
        "validate_anchor_transition(",
        "Stopped pilot runtime cannot change admission or release lineage",
        "Host pilot state anchor does not match runtime evidence",
    ):
        assert marker in source
'''
runtime_cli_tests.write_text(text, encoding="utf-8")

Path("backend/tests/test_pilot_state_replay_migration.py").write_text(
    '''from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_replay_anchor_migration_extends_current_pilot_runtime_head():
    source = (
        ROOT / "backend/alembic/versions/0023_pilot_state_replay_anchor.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "0023_pilot_state_replay_anchor"' in source
    assert 'down_revision = "0022_pilot_runtime_guard"' in source
    assert '"pilot_state_revision"' in source
    assert '"pilot_state_sha256"' in source
    assert "ck_pilot_runtime_state_anchor" in source
''',
    encoding="utf-8",
)

# Immutable release capability v12.
capability = Path("scripts/pilot_release_capability.py")
text = capability.read_text(encoding="utf-8")
text = text.replace(
    '    "scripts/pilot_control_binding.py",\n'
    '    "scripts/pilot_control.py",\n',
    '    "scripts/pilot_control_binding.py",\n'
    '    "scripts/pilot_control_chain.py",\n'
    '    "scripts/pilot_control.py",\n'
    '    "backend/pilot_models.py",\n'
    '    "backend/alembic/versions/0023_pilot_state_replay_anchor.py",\n',
    1,
)
text = text.replace(
    '    "backend/tests/test_pilot_control_signature.py",\n'
    '    "backend/tests/test_pilot_runtime.py",\n',
    '    "backend/tests/test_pilot_control_signature.py",\n'
    '    "backend/tests/test_pilot_runtime.py",\n'
    '    "backend/tests/test_pilot_state_replay_migration.py",\n',
    1,
)
text = text.replace('("CAPABILITY_VERSION = 11",)', '("CAPABILITY_VERSION = 12",)', 1)
text = text.replace(
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 3", "def verified_admission_binding(", "def pilot_signing_secret(", "verify_payload_signature(state, secret)", "signed_state = sign_payload(state, secret)", "Unsigned pilot state schema 2 cannot be reused", "secret=args.signing_secret"), errors)',
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 4", "def verified_admission_binding(", "def pilot_signing_secret(", "require_state_chain(state)", "previous_hash = signed_state_sha256(state)", "Replay-vulnerable pilot state schema 3 cannot be reused", "persist=False"), errors)',
    1,
)
text = text.replace(
    '_require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_admission_binding(pilot_state, expected_binding)", "verify_payload_signature(pilot_state, secret)", "pilot control state signature is invalid"), errors)',
    '_require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_state_descendant(", "validated_anchor.update(state_anchor(pilot_state))", "state.pilot_state_revision", "state.pilot_state_sha256", "armed runtime pilot state replay anchor is missing"), errors)',
    1,
)
text = text.replace(
    '_require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "require_admission_binding(", "verify_payload_signature(pilot_state, secret)", "Pilot control state signature is invalid"), errors)',
    '_require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "pilot_state_revision", "pilot_state_sha256", "pilot_state_history", "validate_anchor_transition(", "Stopped pilot runtime cannot change admission or release lineage"), errors)',
    1,
)
anchor = '            _require_markers(bundle, files, "backend/tests/test_pilot_control_signature.py", ("test_state_write_is_signed_and_exact_state_loads", "test_tampered_scenario_or_decision_is_rejected", "test_wrong_secret_and_unsigned_schema_v2_are_rejected"), errors)\n'
replacement = (
    '            _require_markers(bundle, files, "scripts/pilot_control_chain.py", '
    '("def signed_state_sha256(", "def validate_anchor_transition(", '
    '"pilot control state revision rollback detected", "pilot control state ancestry does not match the armed runtime"), errors)\n'
    '            _require_markers(bundle, files, "backend/pilot_models.py", '
    '("pilot_state_revision", "pilot_state_sha256", "ck_pilot_runtime_state_anchor"), errors)\n'
    '            _require_markers(bundle, files, "backend/alembic/versions/0023_pilot_state_replay_anchor.py", '
    '("0023_pilot_state_replay_anchor", "0022_pilot_runtime_guard", "pilot_state_revision", "pilot_state_sha256"), errors)\n'
    '            _require_markers(bundle, files, "backend/tests/test_pilot_control_signature.py", '
    '("test_authorized_write_advances_revision_and_preserves_exact_parent_hash", '
    '"test_replayed_or_unrelated_signed_branch_is_rejected_by_anchor", '
    '"test_wrong_secret_and_legacy_schemas_are_rejected"), errors)\n'
)
if text.count(anchor) != 1:
    raise SystemExit("capability signed-state test marker changed unexpectedly")
text = text.replace(anchor, replacement, 1)
text = text.replace(
    '_require_markers(bundle, files, "backend/tests/test_pilot_runtime.py", ("test_tampered_pilot_control_state_fails_closed_on_checkout", "sign_payload(pilot_payload, secret)"), errors)',
    '_require_markers(bundle, files, "backend/tests/test_pilot_runtime.py", ("test_tampered_pilot_control_state_fails_closed_on_checkout", "test_runtime_anchor_advances_to_descendant_and_rejects_replay", "test_unrelated_valid_signed_state_branch_fails_closed"), errors)',
    1,
)
capability.write_text(text, encoding="utf-8")

capability_tests = Path("backend/tests/test_pilot_release_capability.py")
text = capability_tests.read_text(encoding="utf-8")
text = text.replace("CAPABILITY_VERSION = 11", "CAPABILITY_VERSION = 12")
text = text.replace("assert CAPABILITY_VERSION == 11", "assert CAPABILITY_VERSION == 12")
text = text.replace(
    '        "validate_admission_binding(pilot_state, expected_binding)\\n"\n'
    '        "verify_payload_signature(pilot_state, secret)\\n"\n'
    '        "pilot control state signature is invalid\\n"\n',
    '        "validate_state_descendant(\\n"\n'
    '        "validated_anchor.update(state_anchor(pilot_state))\\n"\n'
    '        "state.pilot_state_revision\\nstate.pilot_state_sha256\\n"\n'
    '        "armed runtime pilot state replay anchor is missing\\n"\n',
    1,
)
old_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 3\\ndef verified_admission_binding(): pass\\n"
        "def pilot_signing_secret(): pass\\n"
        "verify_payload_signature(state, secret)\\n"
        "signed_state = sign_payload(state, secret)\\n"
        "Unsigned pilot state schema 2 cannot be reused\\n"
        "secret=args.signing_secret\\n"
    ),
'''
new_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 4\\ndef verified_admission_binding(): pass\\n"
        "def pilot_signing_secret(): pass\\n"
        "require_state_chain(state)\\n"
        "previous_hash = signed_state_sha256(state)\\n"
        "Replay-vulnerable pilot state schema 3 cannot be reused\\n"
        "persist=False\\n"
    ),
    "scripts/pilot_control_chain.py": (
        "def signed_state_sha256(): pass\\n"
        "def validate_anchor_transition(): pass\\n"
        "pilot control state revision rollback detected\\n"
        "pilot control state ancestry does not match the armed runtime\\n"
    ),
    "backend/pilot_models.py": (
        "pilot_state_revision\\npilot_state_sha256\\nck_pilot_runtime_state_anchor\\n"
    ),
    "backend/alembic/versions/0023_pilot_state_replay_anchor.py": (
        "0023_pilot_state_replay_anchor\\n0022_pilot_runtime_guard\\n"
        "pilot_state_revision\\npilot_state_sha256\\n"
    ),
'''
if text.count(old_control_fixture) != 1:
    raise SystemExit("capability pilot control fixture changed unexpectedly")
text = text.replace(old_control_fixture, new_control_fixture, 1)
text = text.replace(
    '    "scripts/pilot_runtime.py": (\n'
    '        "build_admission_binding(DEFAULT_MANIFEST, manifest)\\n"\n'
    '        "require_admission_binding(\\n"\n'
    '        "verify_payload_signature(pilot_state, secret)\\n"\n'
    '        "Pilot control state signature is invalid\\n"\n'
    '    ),\n',
    '    "scripts/pilot_runtime.py": (\n'
    '        "build_admission_binding(DEFAULT_MANIFEST, manifest)\\n"\n'
    '        "pilot_state_revision\\npilot_state_sha256\\npilot_state_history\\n"\n'
    '        "validate_anchor_transition(\\n"\n'
    '        "Stopped pilot runtime cannot change admission or release lineage\\n"\n'
    '    ),\n',
    1,
)
old_signature_fixture = '''    "backend/tests/test_pilot_control_signature.py": (
        "test_state_write_is_signed_and_exact_state_loads\\n"
        "test_tampered_scenario_or_decision_is_rejected\\n"
        "test_wrong_secret_and_unsigned_schema_v2_are_rejected\\n"
    ),
'''
new_signature_fixture = '''    "backend/tests/test_pilot_control_signature.py": (
        "test_authorized_write_advances_revision_and_preserves_exact_parent_hash\\n"
        "test_replayed_or_unrelated_signed_branch_is_rejected_by_anchor\\n"
        "test_wrong_secret_and_legacy_schemas_are_rejected\\n"
    ),
'''
if text.count(old_signature_fixture) != 1:
    raise SystemExit("capability signature test fixture changed unexpectedly")
text = text.replace(old_signature_fixture, new_signature_fixture, 1)
text = text.replace(
    '    "backend/tests/test_pilot_runtime.py": (\n'
    '        "test_tampered_pilot_control_state_fails_closed_on_checkout\\n"\n'
    '        "sign_payload(pilot_payload, secret)\\n"\n'
    '    ),\n',
    '    "backend/tests/test_pilot_runtime.py": (\n'
    '        "test_tampered_pilot_control_state_fails_closed_on_checkout\\n"\n'
    '        "test_runtime_anchor_advances_to_descendant_and_rejects_replay\\n"\n'
    '        "test_unrelated_valid_signed_state_branch_fails_closed\\n"\n'
    '    ),\n'
    '    "backend/tests/test_pilot_state_replay_migration.py": (\n'
    '        "test_replay_anchor_migration_extends_current_pilot_runtime_head\\n"\n'
    '    ),\n',
    1,
)
capability_tests.write_text(text, encoding="utf-8")

repository_test = Path("backend/tests/test_pilot_release_capability_repository.py")
text = repository_test.read_text(encoding="utf-8").replace("v11", "v12")
repository_test.write_text(text, encoding="utf-8")

# Documentation.
runbook = Path("docs/pilot/admission_bound_state_migration.md")
text = runbook.read_text(encoding="utf-8")
text = text.replace("state schema v3", "state schema v4")
text = text.replace("fresh schema v3 state", "fresh schema v4 state")
text = text.replace("fresh signed schema v3 state", "fresh replay-resistant schema v4 state")
text = text.replace(
    "Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Both are intentionally rejected and are never migrated in place.",
    "Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Schema v3 is signed but has no database-anchored replay lineage. All three are intentionally rejected and are never migrated in place.",
)
text = text.replace(
    "Every target revalidates the signed admission, verifies the current state signature before reading it, and writes a new signature after an authorized state change.",
    "Every target revalidates the signed admission and verifies the current state signature. Authorized record changes append the exact parent-state SHA-256, increment the revision and write a new signature; status and final validation are read-only so they cannot inflate or fork the lineage.",
)
text = text.replace(
    "Runtime arm and every checkout independently verify the state HMAC and compare the state with the exact signed admission. A signature or binding mismatch keeps checkout closed.",
    "Runtime arm stores the current state revision and SHA-256 in PostgreSQL. Every checkout independently verifies the HMAC, admission binding and append-only ancestry against that database anchor. The same state or a signed descendant is accepted and advances the anchor; an older revision or unrelated signed branch keeps checkout closed.",
)
runbook.write_text(text, encoding="utf-8")

matrix = Path("docs/pilot/end_to_end_coverage_matrix.md")
text = matrix.read_text(encoding="utf-8")
text = text.replace(
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed human admission -> signed 20-scenario state -> first 20 orders -> automatic STOP | Capability v11, state-signature/admission/runtime tests and circuit-breaker tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed admission -> replay-resistant 20-scenario lineage -> first 20 orders -> automatic STOP | Capability v12, state lineage/DB-anchor/admission/runtime tests and circuit-breaker tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
)
text = text.replace(
    "Legacy unbound schema v1, unsigned schema v2, manual edits and wrong-secret signatures fail closed.",
    "Legacy unbound schema v1, unsigned schema v2, replay-vulnerable schema v3, manual edits and wrong-secret signatures fail closed. PostgreSQL anchors the last accepted revision/hash, so replaying an older valid signature or presenting an unrelated signed branch is also rejected.",
)
matrix.write_text(text, encoding="utf-8")
