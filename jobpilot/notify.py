"""Phone push (ntfy) and the daily digest email."""
from __future__ import annotations

import html
import logging
import os
from datetime import datetime

import requests

from .models import Job

log = logging.getLogger(__name__)


def push(title: str, message: str, url: str | None = None, priority: int = 3,
         tags: str = "briefcase") -> bool:
    """Send a phone notification through ntfy.sh. Needs the NTFY_TOPIC secret."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log.warning("NTFY_TOPIC not set; skipping push: %s", title)
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"),
               "Priority": str(priority), "Tags": tags}
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open posting, {url}"
    try:
        r = requests.post(f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("push failed: %s", e)
        return False


def push_job(job: Job) -> bool:
    where = "Remote" if job.remote and "remote" not in job.location.lower() else job.location
    return push(f"{job.score} · {job.title}", f"{job.company} · {where}", url=job.url,
                priority=4 if job.score >= 85 else 3, tags="dart")


STATUS_EMOJI_TAG = {"Interview": "calendar", "Offer": "tada", "Assessment": "pencil2",
                    "Rejected": "x", "Recruiter": "wave", "Applied": "white_check_mark"}


def push_status(company: str, role: str, status: str, subject: str) -> bool:
    prio = 5 if status in ("Offer", "Interview") else 3
    label = f"{company}" + (f" · {role}" if role else "")
    return push(f"{status}: {label}", subject[:180], priority=prio, tags=STATUS_EMOJI_TAG.get(status, "email"))


# ------------------------------------------------------------------ digest
def _e(s) -> str:
    return html.escape(str(s or ""))


def build_digest(new_jobs: list[dict], changes: list[dict], followups: list[dict],
                 pipeline: dict[str, int], sheet_url: str | None, today: datetime) -> tuple[str, str, str]:
    subject = (f"Job digest · {today:%a %b %d}: {len(new_jobs)} new, "
               f"{len(changes)} updates, {len(followups)} follow-ups")

    def section(title: str, body: str) -> str:
        return (f'<h2 style="font:600 16px system-ui;margin:24px 0 8px;color:#15181d">{title}</h2>{body}')

    row_style = 'style="padding:6px 8px;border-bottom:1px solid #e3e5e8;font:14px system-ui;vertical-align:top"'
    if new_jobs:
        rows = "".join(
            f'<tr><td {row_style}><b>{_e(j["Score"])}</b></td>'
            f'<td {row_style}><a href="{_e(j["Link"])}" style="color:#1d4fd8">{_e(j["Title"])}</a><br>'
            f'<span style="color:#5b616b">{_e(j["Company"])} · {_e(j["Location"])}</span></td></tr>'
            for j in new_jobs[:25])
        jobs_html = f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
        if len(new_jobs) > 25:
            jobs_html += f'<p style="font:13px system-ui;color:#5b616b">+{len(new_jobs) - 25} more in the sheet</p>'
    else:
        jobs_html = '<p style="font:14px system-ui;color:#5b616b">No new matches in the last day.</p>'

    def bullet_list(items: list[str]) -> str:
        return ('<ul style="font:14px system-ui;padding-left:18px">'
                + "".join(f"<li style='margin:4px 0'>{i}</li>" for i in items) + "</ul>")

    changes_html = bullet_list([
        f'<b>{_e(c["Company"])}</b>{" · " + _e(c["Role"]) if c.get("Role") else ""}: '
        f'{_e(c.get("From") or "new")} → <b>{_e(c["Status"])}</b>' for c in changes
    ]) if changes else '<p style="font:14px system-ui;color:#5b616b">No application emails since yesterday.</p>'

    follow_html = bullet_list([
        f'<b>{_e(f["Company"])}</b>{" · " + _e(f["Role"]) if f.get("Role") else ""}: '
        f'{_e(f["Status"])} since {_e(f["Last Update"])}' for f in followups
    ]) if followups else '<p style="font:14px system-ui;color:#5b616b">Nothing due.</p>'

    pipe = " · ".join(f"{k}: {v}" for k, v in pipeline.items() if v) or "No applications tracked yet."
    link = f'<p style="font:14px system-ui"><a href="{_e(sheet_url)}" style="color:#1d4fd8">Open the tracker sheet →</a></p>' if sheet_url else ""

    body_html = (
        '<div style="max-width:640px;margin:0 auto;padding:8px">'
        f'<h1 style="font:700 20px system-ui;color:#15181d;margin:0 0 4px">Job digest</h1>'
        f'<p style="font:13px system-ui;color:#5b616b;margin:0">{today:%A, %B %d} · Pipeline: {_e(pipe)}</p>'
        + section(f"New matches ({len(new_jobs)})", jobs_html)
        + section(f"Application updates ({len(changes)})", changes_html)
        + section(f"Follow-ups due ({len(followups)})", follow_html)
        + link + "</div>")

    text = [subject, "", f"Pipeline: {pipe}", "", "NEW MATCHES"]
    text += [f'  {j["Score"]}  {j["Title"]} — {j["Company"]} ({j["Location"]})  {j["Link"]}' for j in new_jobs[:25]] or ["  none"]
    text += ["", "UPDATES"] + ([f'  {c["Company"]}: {c.get("From") or "new"} -> {c["Status"]}' for c in changes] or ["  none"])
    text += ["", "FOLLOW-UPS DUE"] + ([f'  {f["Company"]} ({f["Status"]} since {f["Last Update"]})' for f in followups] or ["  none"])
    if sheet_url:
        text += ["", sheet_url]
    return subject, body_html, "\n".join(text)
