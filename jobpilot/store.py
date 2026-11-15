"""Where everything is kept: a Google Sheet (normal runs) or a local JSON file (dry runs/tests).

Google Sheet tabs (created automatically on the first run):
  Jobs          - every matching posting found, newest on top of each run
  Applications  - one row per application, kept up to date from your email
  Settings      - your search settings (edit these!)
  _state        - bookkeeping (processed email ids, last digest date). Safe to hide.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

JOB_COLS = ["Found", "Score", "Company", "Title", "Location", "Remote", "Link", "Source", "Why", "Status", "Job ID"]
APP_COLS = ["Company", "Role", "Status", "Applied", "Last Update", "Follow Up By", "Last Email", "Notes", "Key"]
STATE_COLS = ["Kind", "Value", "When"]
SETTINGS_COLS = ["Setting", "Value", "What it does"]
RESUME_COLS = ["Field", "Value"]

TABS = {"Jobs": JOB_COLS, "Applications": APP_COLS, "_state": STATE_COLS, "Settings": SETTINGS_COLS,
        "Resume": RESUME_COLS}


class Table:
    """Minimal table API shared by the Sheet and JSON back ends."""

    def records(self) -> list[dict[str, Any]]: ...
    def append(self, rows: list[dict[str, Any]]) -> None: ...
    def update(self, index: int, row: dict[str, Any]) -> None: ...
    def raw_rows(self) -> list[list[str]]: ...
    def replace_all(self, rows: list[list[str]]) -> None: ...


# --------------------------------------------------------------- local JSON
class JsonTable(Table):
    def __init__(self, store: "LocalStore", name: str):
        self.store, self.name = store, name

    @property
    def _rows(self) -> list[dict[str, Any]]:
        return self.store.data.setdefault(self.name, [])

    def records(self):
        return [dict(r) for r in self._rows]

    def append(self, rows):
        self._rows.extend(dict(r) for r in rows)
        self.store.save()

    def update(self, index, row):
        self._rows[index] = dict(row)
        self.store.save()

    def raw_rows(self):
        cols = TABS[self.name]
        return [cols] + [[str(r.get(c, "")) for c in cols] for r in self._rows]

    def replace_all(self, rows):
        cols = rows[0]
        self.store.data[self.name] = [dict(zip(cols, r)) for r in rows[1:]]
        self.store.save()


class LocalStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, list] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2, default=str))

    def table(self, name: str) -> Table:
        return JsonTable(self, name)


# --------------------------------------------------------------- Google Sheet (via Apps Script)
class BridgeError(RuntimeError):
    pass


class SheetBridge:
    """Talks to the Apps Script in apps_script/Code.gs, which writes to your Sheet as you.
    No Google Cloud project or service account needed."""

    def __init__(self, url: str, secret: str, post=None):
        import requests
        self.url, self.secret = url, secret
        self._post = post or (lambda payload: requests.post(url, json=payload, timeout=60))

    def call(self, op: str, **kw) -> dict:
        r = self._post({"secret": self.secret, "op": op, **kw})
        try:
            data = r.json()
        except ValueError:
            raise BridgeError("The Sheet script didn't return JSON. Check that SHEET_SCRIPT_URL is the "
                              "web app URL ending in /exec and that access is set to 'Anyone'.")
        if data.get("error"):
            raise BridgeError(f"Sheet script error: {data['error']}")
        return data


class SheetTable(Table):
    def __init__(self, bridge: SheetBridge, name: str, cols: list[str]):
        self.bridge, self.name, self.cols = bridge, name, cols

    def raw_rows(self):
        return self.bridge.call("values", tab=self.name).get("values", [])

    def records(self):
        values = self.raw_rows()
        if not values:
            return []
        header = values[0]
        return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in values[1:]]

    def append(self, rows):
        if rows:
            self.bridge.call("append", tab=self.name,
                             rows=[[str(r.get(c, "")) for c in self.cols] for r in rows])

    def update(self, index, row):
        # +2: 1-based rows and the header row
        self.bridge.call("update", tab=self.name, row=index + 2,
                         values=[str(row.get(c, "")) for c in self.cols])

    def replace_all(self, rows):
        self.bridge.call("replace", tab=self.name, rows=[[str(v) for v in r] for r in rows])


class SheetStore:
    def __init__(self, url: str, secret: str, post=None):
        self.bridge = SheetBridge(url, secret, post)
        self.bridge.call("ensure", tabs=TABS)

    def table(self, name: str) -> Table:
        return SheetTable(self.bridge, name, TABS[name])

    def url(self) -> str | None:
        try:
            return self.bridge.call("info").get("url")
        except Exception:
            return None


def open_store(dry_run_path: str | None = None):
    if dry_run_path:
        return LocalStore(Path(dry_run_path))
    return SheetStore(os.environ["SHEET_SCRIPT_URL"], os.environ["SHEET_SCRIPT_SECRET"])
