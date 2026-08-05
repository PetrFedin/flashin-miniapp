from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "scripts/pilot_control.py"
old = '''def save_state(
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
new = '''def save_state(
    path: Path,
    state: dict[str, Any],
    report: dict[str, Any],
    *,
    secret: str,
) -> None:
    if "signature" in state:
        try:
            parent_state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("Pilot control parent state is missing before update") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("Pilot control parent state is invalid JSON") from exc
        if not isinstance(parent_state, dict):
            raise ValueError("Pilot control parent state must contain a JSON object")
        if not verify_payload_signature(parent_state, secret):
            raise ValueError("Pilot control parent state signature is invalid")
        require_state_chain(parent_state)
        if (
            state.get("signature") != parent_state.get("signature")
            or state.get("revision") != parent_state.get("revision")
            or state.get("state_history_sha256")
            != parent_state.get("state_history_sha256")
        ):
            raise ValueError("Pilot control state changed concurrently before update")
        previous_hash = signed_state_sha256(parent_state)
        state["state_history_sha256"] = [
            *list(parent_state["state_history_sha256"]),
            previous_hash,
        ]
        state["revision"] = int(parent_state["revision"]) + 1
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
replace_once(path, old, new)

for target in (
    "scripts/pilot_release_capability.py",
    "backend/tests/test_pilot_release_capability.py",
):
    file = Path(target)
    text = file.read_text(encoding="utf-8")
    text = text.replace(
        "previous_hash = signed_state_sha256(state)",
        "previous_hash = signed_state_sha256(parent_state)",
    )
    file.write_text(text, encoding="utf-8")

signature_tests = Path("backend/tests/test_pilot_control_signature.py")
text = signature_tests.read_text(encoding="utf-8")
text += '''


def test_concurrent_parent_replacement_is_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    stale = _signed_state(path)
    current = json.loads(path.read_text(encoding="utf-8"))
    current["scenarios"][1]["result"] = "running"
    save_state(path, current, validate_state(current, final=False), secret=SECRET)

    stale["scenarios"][0]["result"] = "running"
    with pytest.raises(ValueError, match="changed concurrently"):
        save_state(path, stale, validate_state(stale, final=False), secret=SECRET)
'''
signature_tests.write_text(text, encoding="utf-8")
