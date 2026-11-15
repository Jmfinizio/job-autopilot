"""The three jobs the autopilot does: find jobs, track applications, send the digest."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config as cfg
from . import notify, sources
from .classify import merge_status
from .matching import find_matches
from .models import EmailEvent, Job

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
ACTIVE = ("Recruiter", "Applied", "Assessment", "Interview")


def today_et(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(ET).date()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# ------------------------------------------------------------------ settings
def load_resume(store) -> dict:
    """Resume tab rows: text, skills, roles, file, updated. Skills are re-derived if missing."""
    from .resume import find_skills
    data = {}
    for r in store.table("Resume").records():
        k = (r.get("Field") or "").strip().lower()
        if k:
            data[k] = r.get("Value", "")
    skills = [s.strip() for s in data.get("skills", "").split(",") if s.strip()]
    if not skills and data.get("text"):
        skills = find_skills(data["text"])
    data["skill_list"] = skills
    return data


def load_settings(store) -> tuple[cfg.Settings, list[str]]:
    settings, notes = _load_settings(store)
    try:
        resume = load_resume(store)
    except Exception as e:  # never let a resume problem stop the job search
        resume, notes = {}, notes + [f"Resume tab unreadable: {e}"]
    if resume.get("skill_list"):
        settings.raw.setdefault("search", {})["resume_skills"] = resume["skill_list"]
        notes.append(f"Using {len(resume['skill_list'])} skills from your resume")
    return settings, notes


def _load_settings(store) -> tuple[cfg.Settings, list[str]]:
    settings = cfg.load()
    table = store.table("Settings")
    rows = [r for r in table.raw_rows() if any(str(c).strip() for c in r)]
    if len(rows) <= 1:  # first run (or tab cleared): write the template so you have something to edit
        table.replace_all(cfg.sheet_template_rows(settings))
        return settings, ["Settings tab created from config.yaml; edit it to change your search"]
    return cfg.apply_sheet_overrides(settings, rows[1:])


# ------------------------------------------------------------------ state helpers
def _state(store, kind: str) -> list[dict]:
    return [r for r in store.table("_state").records() if r.get("Kind") == kind]


def _add_state(store, rows: list[tuple[str, str]], now: datetime) -> None:
    if rows:
        store.table("_state").append([{"Kind": k, "Value": v, "When": now.isoformat(timespec="seconds")}
                                      for k, v in rows])


# ------------------------------------------------------------------ 1. find jobs
def run_jobs(store, settings: cfg.Settings, *, get=sources._http_get, now: datetime | None = None,
             send_push: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    jobs_table = store.table("Jobs")
    seen = {r.get("Job ID") for r in jobs_table.records()}
    raw, problems = sources.collect(settings, get=get)
    matches = find_matches(raw, settings.search, seen, now=now)

    found = today_et(now).isoformat()
    jobs_table.append([{
        "Found": found, "Score": j.score, "Company": j.company, "Title": j.title,
        "Location": j.location + (" (Remote)" if j.remote and "remote" not in j.location.lower() else ""),
        "Remote": "Yes" if j.remote else "", "Link": j.url, "Source": j.source,
        "Why": "; ".join(j.reasons), "Status": "", "Job ID": j.key,
    } for j in matches])

    alerts = 0
    if send_push and settings.notify.get("push", True):
        hot = [j for j in matches if j.score >= int(settings.search.get("alert_score", 75))]
        for j in hot[:8]:
            alerts += notify.push_job(j)
        if len(hot) > 8:
            notify.push(f"+{len(hot) - 8} more strong matches", "Open the Jobs tab to see them all.")
    for p in problems:
        log.warning(p)
    return {"scanned": len(raw), "new_matches": len(matches), "alerts": alerts, "problems": problems}


# ------------------------------------------------------------------ 2. track applications
def _find_app(apps: list[dict], company: str, role: str) -> int | None:
    c, r = _norm(company), _norm(role)
    best = None
    for i, a in enumerate(apps):
        if _norm(a.get("Company")) != c:
            continue
        ar = _norm(a.get("Role"))
        if r and ar and (r == ar or r in ar or ar in r):
            return i
        if not r or not ar:
            best = i if best is None else best
    return best


def apply_events(store, events: list[EmailEvent], settings: cfg.Settings, *, now: datetime | None = None,
                 send_push: bool = True) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    table = store.table("Applications")
    apps = table.records()
    follow_days = int(settings.tracker.get("follow_up_days", 10))
    changes, state_rows = [], []

    for ev in events:
        state_rows.append(("email", ev.message_id))
        if ev.company == "Unknown" and ev.status == "Recruiter":
            continue  # cold outreach we can't attribute; not worth a row
        day = ev.date.astimezone(ET).date()
        idx = _find_app(apps, ev.company, ev.role)
        if idx is None:
            row = {
                "Company": ev.company, "Role": ev.role, "Status": ev.status,
                "Applied": day.isoformat() if ev.status != "Recruiter" else "",
                "Last Update": day.isoformat(),
                "Follow Up By": (day + timedelta(days=follow_days)).isoformat() if ev.status in ACTIVE else "",
                "Last Email": ev.subject[:150], "Notes": "", "Key": f"{_norm(ev.company)}|{_norm(ev.role)}",
            }
            table.append([row])
            apps.append(row)
            changes.append({"Company": ev.company, "Role": ev.role, "From": "", "Status": ev.status})
        else:
            row = dict(apps[idx])
            old = row.get("Status", "")
            new = merge_status(old, ev.status)
            row["Last Email"] = ev.subject[:150]
            if ev.role and not row.get("Role"):
                row["Role"] = ev.role
            if new != old:
                row["Status"] = new
                row["Last Update"] = day.isoformat()
                row["Follow Up By"] = ((day + timedelta(days=follow_days)).isoformat()
                                       if new in ACTIVE else "")
                changes.append({"Company": row["Company"], "Role": row.get("Role", ""), "From": old, "Status": new})
            table.update(idx, row)
            apps[idx] = row

    if send_push and settings.notify.get("push", True):
        for c in changes:
            if c["Status"] in ("Interview", "Offer", "Assessment", "Recruiter"):
                notify.push_status(c["Company"], c.get("Role", ""), c["Status"],
                                   next((e.subject for e in events if e.company == c["Company"]), ""))
    _add_state(store, state_rows + [("change", json.dumps(c)) for c in changes], now)
    return changes


def import_marked_jobs(store, settings: cfg.Settings, now: datetime | None = None) -> int:
    """If you type 'Applied' in a job's Status column, add it to Applications."""
    now = now or datetime.now(timezone.utc)
    follow_days = int(settings.tracker.get("follow_up_days", 10))
    jobs, apps_t = store.table("Jobs"), store.table("Applications")
    apps = apps_t.records()
    added = 0
    for i, j in enumerate(jobs.records()):
        if _norm(j.get("Status")) != "applied":
            continue
        if _find_app(apps, j.get("Company", ""), j.get("Title", "")) is not None:
            j["Status"] = "Applied (tracked)"
            jobs.update(i, j)
            continue
        d = today_et(now)
        row = {"Company": j.get("Company", ""), "Role": j.get("Title", ""), "Status": "Applied",
               "Applied": d.isoformat(), "Last Update": d.isoformat(),
               "Follow Up By": (d + timedelta(days=follow_days)).isoformat(),
               "Last Email": "", "Notes": j.get("Link", ""),
               "Key": f"{_norm(j.get('Company'))}|{_norm(j.get('Title'))}"}
        apps_t.append([row])
        apps.append(row)
        j["Status"] = "Applied (tracked)"
        jobs.update(i, j)
        added += 1
    return added


def run_emails(store, settings: cfg.Settings, fetch=None, *, now: datetime | None = None,
               send_push: bool = True) -> dict:
    if fetch is None:
        from . import gmail
        fetch = lambda q, skip: gmail.fetch_events(store.bridge, q, skip)
    done = {r["Value"] for r in _state(store, "email")}
    events = fetch(settings.tracker.get("gmail_query", "newer_than:3d"), done)
    changes = apply_events(store, events, settings, now=now, send_push=send_push)
    imported = import_marked_jobs(store, settings, now=now)
    return {"emails_matched": len(events), "status_changes": len(changes), "imported_from_jobs_tab": imported}


# ------------------------------------------------------------------ 3. follow-ups + digest
def followups_due(store, now: datetime | None = None) -> list[dict]:
    today = today_et(now).isoformat()
    return [a for a in store.table("Applications").records()
            if a.get("Status") in ACTIVE and a.get("Follow Up By") and a["Follow Up By"] <= today]


def pipeline_counts(store) -> dict[str, int]:
    counts = {s: 0 for s in ("Applied", "Assessment", "Interview", "Offer", "Rejected", "Recruiter")}
    for a in store.table("Applications").records():
        if a.get("Status") in counts:
            counts[a["Status"]] += 1
    return counts


def run_digest(store, settings: cfg.Settings, send=None, *, now: datetime | None = None,
               force: bool = False, sheet_url: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = today_et(now)
    if not force:
        if now.astimezone(ET).hour < int(settings.notify.get("digest_hour_et", 8)):
            return {"sent": False, "reason": "too early"}
        if any(r["Value"] == today.isoformat() for r in _state(store, "digest")):
            return {"sent": False, "reason": "already sent today"}
    since = (today - timedelta(days=1)).isoformat()
    new_jobs = sorted([j for j in store.table("Jobs").records() if j.get("Found", "") >= since],
                      key=lambda j: -int(j.get("Score") or 0))
    cutoff = now - timedelta(hours=26)
    changes = [json.loads(r["Value"]) for r in _state(store, "change")
               if datetime.fromisoformat(r["When"]) >= cutoff]
    follow = followups_due(store, now)
    subject, body_html, body_text = notify.build_digest(new_jobs, changes, follow, pipeline_counts(store),
                                                        sheet_url, now.astimezone(ET))
    if settings.notify.get("digest", True):
        if send is None:
            import os
            from .gmail import send_email
            send = lambda s_, h, t: send_email(store.bridge, s_, h, t, to=os.environ.get("DIGEST_TO"))
        send(subject, body_html, body_text)
    if follow and settings.notify.get("push", True):
        names = ", ".join(f["Company"] for f in follow[:4])
        notify.push(f"{len(follow)} follow-up{'s' if len(follow) > 1 else ''} due", names, tags="bell")
    _add_state(store, [("digest", today.isoformat())], now)
    return {"sent": True, "subject": subject, "new_jobs": len(new_jobs), "changes": len(changes),
            "followups": len(follow)}
