from pathlib import Path

path = Path("backend/tests/test_pilot_runtime.py")
text = path.read_text(encoding="utf-8")
old = '            "version": 12,\n'
new = '            "version": 13,\n'
if text.count(old) != 1:
    raise SystemExit(f"expected one runtime capability v12 fixture, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
