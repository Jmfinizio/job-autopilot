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

TABS = {"Jobs": JOB_COLS, "Applications": APP_COLS, "_state": STATE_COLS, "Settings": SETTINGS_COLS}


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


# --------------------------------------------------------------- Google Sheet
class SheetTable(Table):
    def __init__(self, ws, cols: list[str]):
        self.ws, self.cols = ws, cols

    def records(self):
        values = self.ws.get_all_values()
        if not values:
            return []
        header = values[0]
        return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in values[1:]]

    def append(self, rows):
        if rows:
            self.ws.append_rows([[r.get(c, "") for c in self.cols] for r in rows],
                                value_input_option="USER_ENTERED")

    def update(self, index, row):
        # +2: 1-based rows and the header row
        self.ws.update([[row.get(c, "") for c in self.cols]], f"A{index + 2}",
                       value_input_option="USER_ENTERED")

    def raw_rows(self):
        return self.ws.get_all_values()

    def replace_all(self, rows):
        self.ws.clear()
        self.ws.update(rows, "A1", value_input_option="USER_ENTERED")


class SheetStore:
    def __init__(self, sheet_id: str, service_account_json: str):
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            json.loads(service_account_json),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.book = gspread.authorize(creds).open_by_key(sheet_id)
        self._ensure_tabs()

    def _ensure_tabs(self):
        existing = {ws.title: ws for ws in self.book.worksheets()}
        for name, cols in TABS.items():
            if name not in existing:
                ws = self.book.add_worksheet(title=name, rows=1000, cols=len(cols))
                ws.update([cols], "A1")
                ws.freeze(rows=1)
                ws.format("1:1", {"textFormat": {"bold": True}})
                existing[name] = ws
            elif not existing[name].row_values(1):
                existing[name].update([cols], "A1")
        # drop the default empty "Sheet1" if it's still blank
        s1 = existing.get("Sheet1")
        if s1 and not any(s1.get_all_values()):
            self.book.del_worksheet(s1)
        self._ws = {n: self.book.worksheet(n) for n in TABS}

    def table(self, name: str) -> Table:
        return SheetTable(self._ws[name], TABS[name])


def open_store(dry_run_path: str | None = None):
    if dry_run_path:
        return LocalStore(Path(dry_run_path))
    return SheetStore(os.environ["SHEET_ID"], os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
