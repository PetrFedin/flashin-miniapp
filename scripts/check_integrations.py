#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

checks = []

def run(name, cmd, required=True):
    print(f"== {name} ==")
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        ok = res.returncode == 0
        print(res.stdout.strip())
        if res.stderr.strip():
            print(res.stderr.strip())
        checks.append({"name": name, "ok": ok, "required": required})
        if required and not ok:
            return False
        return ok
    except Exception as exc:
        print(exc)
        checks.append({"name": name, "ok": False, "required": required})
        return not required

run("preflight", "python3 scripts/preflight.py --require-env")
run("env", "python3 scripts/validate_env.py")
run("domain", "python3 scripts/check_domains.py", required=False)
run("r2_s3", "python3 scripts/check_r2_s3.py", required=False)
run("yookassa", "python3 scripts/check_yookassa_test.py", required=False)
run("health", "python3 tests/e2e_smoke.py", required=False)

Path("docs/integration_check_report.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
required_failed = [c for c in checks if c["required"] and not c["ok"]]
if required_failed:
    print("Required checks failed:", required_failed)
    sys.exit(1)
print("Integration check completed")
