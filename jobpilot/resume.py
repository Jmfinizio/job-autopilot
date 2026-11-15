"""Turn a resume into skills and suggested roles, and score jobs against it.

The same skill list (skills.json) is embedded in the Sheet dashboard, so what you
see when you upload your resume is exactly what the job search uses.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

VOCAB_PATH = Path(__file__).with_name("skills.json")


@lru_cache(maxsize=1)
def vocab() -> dict:
    return json.loads(VOCAB_PATH.read_text())


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[str, re.Pattern]]:
    out = []
    for name, aliases in vocab()["skills"].items():
        parts = [a if a.startswith(r"\b") else rf"(?<![\w+#/]){a}(?![\w+#])" for a in aliases]
        out.append((name, re.compile("|".join(parts), re.I)))
    return out


def find_skills(text: str) -> list[str]:
    text = text or ""
    return [name for name, pat in _patterns() if pat.search(text)]


def suggest_roles(skills: list[str], text: str = "", top: int = 4) -> list[str]:
    have = set(skills)
    scored = []
    for role, signals in vocab()["roles"]:
        hits = len(have & set(signals))
        if re.search(rf"\b{re.escape(role)}\b", text or "", re.I):
            hits += 3  # you've held or targeted this title
        if hits >= 2:
            scored.append((hits, role))
    return [r for _, r in sorted(scored, key=lambda x: (-x[0], x[1]))[:top]]


def resume_match(job_text: str, resume_skills: list[str]) -> tuple[int, list[str]]:
    """Points (0-20) for how many of your resume skills a posting mentions."""
    if not resume_skills:
        return 0, []
    found = set(find_skills(job_text))
    matched = [s for s in resume_skills if s in found]
    return min(20, 4 * len(matched)), matched
