from pathlib import Path

path = Path("backend/tests/test_pilot_runtime.py")
text = path.read_text(encoding="utf-8")
old = '            "version": 8,\n'
new = '            "version": 9,\n'
if text.count(old) != 1:
    raise SystemExit(f"expected one runtime capability v8 fixture, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
