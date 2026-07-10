#!/usr/bin/env python3
from pathlib import Path

readme = Path("README.md").read_text(encoding="utf-8")
sections = [line for line in readme.splitlines() if line.startswith("## v")]
out = Path("docs/generated_release_notes.md")
out.write_text("# Generated Release Notes\n\n" + "\n".join(sections) + "\n", encoding="utf-8")
print({"written": str(out), "versions": len(sections)})
