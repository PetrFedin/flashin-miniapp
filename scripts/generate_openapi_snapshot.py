#!/usr/bin/env python3
import json
from pathlib import Path
from backend.main import app

out = Path("docs/openapi_snapshot.json")
out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
print({"written": str(out)})
