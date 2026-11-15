"""Load settings from config.yaml, then let the Google Sheet "Settings" tab override them.

The Sheet override is what lets you change roles, locations, etc. from your phone.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Sheet "Settings" key -> (section, field, type)
SHEET_KEYS: dict[str, tuple[str, str, str]] = {
    "roles": ("search", "roles", "list"),
    "locations": ("search", "locations", "list"),
    "exclude_titles": ("search", "exclude_titles", "list"),
    "boost_keywords": ("search", "boost_keywords", "list"),
    "max_age_days": ("search", "max_age_days", "int"),
    "alert_score": ("search", "alert_score", "int"),
    "min_score": ("search", "min_score", "int"),
    "greenhouse_companies": ("companies", "greenhouse", "list"),
    "lever_companies": ("companies", "lever", "list"),
    "ashby_companies": ("companies", "ashby", "list"),
    "follow_up_days": ("tracker", "follow_up_days", "int"),
}


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)

    # convenience accessors
    @property
    def search(self) -> dict[str, Any]:
        return self.raw.get("search", {})

    @property
    def companies(self) -> dict[str, list[str]]:
        return self.raw.get("companies", {})

    @property
    def adzuna(self) -> dict[str, Any]:
        return self.raw.get("adzuna", {})

    @property
    def tracker(self) -> dict[str, Any]:
        return self.raw.get("tracker", {})

    @property
    def notify(self) -> dict[str, Any]:
        return self.raw.get("notify", {})


def _split_list(value: str) -> list[str]:
    return [p.strip() for p in str(value).replace("\n", ",").split(",") if p.strip()]


def load(path: Path | None = None) -> Settings:
    path = path or ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return Settings(raw=yaml.safe_load(f) or {})


def apply_sheet_overrides(settings: Settings, rows: list[list[str]]) -> tuple[Settings, list[str]]:
    """rows = [[key, value], ...] from the Settings tab. Blank values are ignored.

    Returns the merged settings and a list of human-readable notes about what changed
    or what was ignored, for the run log.
    """
    merged = copy.deepcopy(settings.raw)
    notes: list[str] = []
    for row in rows:
        if len(row) < 2:
            continue
        key = str(row[0]).strip().lower().replace(" ", "_")
        value = str(row[1]).strip()
        if not key or key == "setting" or not value:
            continue
        if key not in SHEET_KEYS:
            notes.append(f"Settings tab: unknown setting '{row[0]}' ignored")
            continue
        section, name, kind = SHEET_KEYS[key]
        try:
            parsed: Any = _split_list(value) if kind == "list" else int(float(value))
        except ValueError:
            notes.append(f"Settings tab: '{row[0]}' should be a number, got '{value}'; ignored")
            continue
        merged.setdefault(section, {})[name] = parsed
        notes.append(f"Settings tab: {key} = {value}")
    return Settings(raw=merged), notes


def sheet_template_rows(settings: Settings) -> list[list[str]]:
    """Initial contents for the Settings tab, pre-filled from config.yaml."""
    s, c, t = settings.search, settings.companies, settings.tracker
    def j(v: Any) -> str:
        return ", ".join(v) if isinstance(v, list) else str(v)
    return [
        ["Setting", "Value", "What it does"],
        ["roles", j(s.get("roles", [])), "Job titles to look for (comma-separated)"],
        ["locations", j(s.get("locations", [])), "Cities or 'Remote' (comma-separated)"],
        ["exclude_titles", j(s.get("exclude_titles", [])), "Skip titles with these words"],
        ["boost_keywords", j(s.get("boost_keywords", [])), "Extra points if a posting mentions these"],
        ["max_age_days", j(s.get("max_age_days", 30)), "Only jobs posted in the last N days"],
        ["alert_score", j(s.get("alert_score", 75)), "Score that triggers an instant phone alert"],
        ["min_score", j(s.get("min_score", 40)), "Lowest score saved to the sheet"],
        ["greenhouse_companies", j(c.get("greenhouse", [])), "Greenhouse board tokens to watch"],
        ["lever_companies", j(c.get("lever", [])), "Lever board tokens to watch"],
        ["ashby_companies", j(c.get("ashby", [])), "Ashby board tokens to watch"],
        ["follow_up_days", j(t.get("follow_up_days", 10)), "Remind me to follow up after N quiet days"],
    ]
