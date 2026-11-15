"""Builds apps_script/Dashboard.html from dashboard.src.html + jobpilot/skills.json.

Run after editing either file:  python tools/build_dashboard.py
"""
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
src = (root / "apps_script" / "dashboard.src.html").read_text()
vocab = json.loads((root / "jobpilot" / "skills.json").read_text())
out = src.replace("__SKILLS_JSON__", json.dumps(vocab, separators=(",", ":")))
(root / "apps_script" / "Dashboard.html").write_text(out)
print(f"wrote apps_script/Dashboard.html ({len(out):,} bytes)")
