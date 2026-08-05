from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/pilot_control_audit.py",
    "from script_time import utc_timestamp\n",
    "try:\n"
    "    from .script_time import utc_timestamp\n"
    "except ImportError:  # script execution mode\n"
    "    from script_time import utc_timestamp\n",
)
replace_once(
    "scripts/pilot_control_chain.py",
    "from pilot_control_audit import validate_audit_log\n",
    "try:\n"
    "    from .pilot_control_audit import validate_audit_log\n"
    "except ImportError:  # script execution mode\n"
    "    from pilot_control_audit import validate_audit_log\n",
)
