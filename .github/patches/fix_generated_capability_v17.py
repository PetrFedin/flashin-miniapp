from pathlib import Path

path = Path("scripts/pilot_release_capability.py")
content = path.read_text(encoding="utf-8")
replacements = (
    (
        '"revision = "0024_inventory_movement_ledger""',
        '"0024_inventory_movement_ledger"',
    ),
    (
        '"down_revision = "0023_pilot_state_replay_anchor""',
        '"0023_pilot_state_replay_anchor"',
    ),
    (
        '"kind="reserve""',
        '"kind=\\"reserve\\""',
    ),
    (
        '"kind="release""',
        '"kind=\\"release\\""',
    ),
    (
        '"kind="commit""',
        '"kind=\\"commit\\""',
    ),
)
for old, new in replacements:
    if content.count(old) != 1:
        raise SystemExit(
            f"Expected one generated capability marker {old!r}; found {content.count(old)}"
        )
    content = content.replace(old, new, 1)
path.write_text(content, encoding="utf-8")
