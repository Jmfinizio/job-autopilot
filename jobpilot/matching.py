"""Decide which postings fit, and how well (0-100)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Job

# Common ways a location is written, so "Boston" also catches "Cambridge, MA", etc.
LOCATION_ALIASES = {
    "boston": ["boston", "cambridge, ma", "somerville", "quincy, ma", "waltham", "massachusetts", ", ma"],
    "new york": ["new york", "nyc", "brooklyn", "manhattan", "jersey city", ", ny"],
    "philadelphia": ["philadelphia", "philly", ", pa", "pennsylvania", "wilmington, de"],
    "remote": ["remote", "anywhere", "work from home", "distributed"],
}

SENIORITY_PENALTY = re.compile(r"\b(sr\.?|senior|lead|staff|principal|head|director|vp|manager)\b", re.I)
ENTRY_BONUS = re.compile(r"\b(associate|junior|jr\.?|entry|new grad|graduate|early career|i)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def title_matches(title: str, roles: list[str]) -> str | None:
    t = _norm(title)
    for role in roles:
        r = _norm(role)
        if r and r in t:
            return role
    # loose match: all words of the role appear (handles "Engineer, Data Platform")
    for role in roles:
        words = [w for w in _norm(role).split() if len(w) > 2]
        if words and all(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
            return role
    return None


def location_matches(job: Job, locations: list[str]) -> str | None:
    if not locations:
        return "any"
    loc = _norm(job.location)
    for want in locations:
        w = _norm(want)
        if w == "remote":
            if job.remote:
                return "Remote"
            continue
        aliases = LOCATION_ALIASES.get(w, [w])
        if any(a in loc for a in aliases):
            return want
    return None


def score_job(job: Job, search: dict, now: datetime | None = None) -> Job | None:
    """Returns the job with score and reasons filled in, or None if it doesn't fit."""
    now = now or datetime.now(timezone.utc)
    roles = search.get("roles", [])
    title_hit = title_matches(job.title, roles)
    if not title_hit:
        return None
    for bad in search.get("exclude_titles", []):
        if bad and _norm(bad) in _norm(job.title):
            return None
    loc_hit = location_matches(job, search.get("locations", []))
    if not loc_hit:
        return None
    max_age = int(search.get("max_age_days", 7))
    if job.posted and job.posted < now - timedelta(days=max_age):
        return None

    score, reasons = 50, [f"title matches '{title_hit}'", f"location: {loc_hit}"]
    if _norm(title_hit) in _norm(job.title):
        score += 10
    if SENIORITY_PENALTY.search(job.title):
        score -= 15
        reasons.append("senior-level title (-15)")
    elif ENTRY_BONUS.search(job.title):
        score += 10
        reasons.append("early-career title (+10)")

    text = _norm(job.title + " " + job.description)
    hits = [k for k in search.get("boost_keywords", []) if k and re.search(rf"\b{re.escape(_norm(k))}\b", text)]
    if hits:
        add = min(25, 5 * len(hits))
        score += add
        reasons.append(f"mentions {', '.join(hits[:5])} (+{add})")

    resume_skills = search.get("resume_skills") or []
    if resume_skills:
        from .resume import resume_match
        pts, matched = resume_match(job.title + " " + job.description, resume_skills)
        if pts:
            score += pts
            reasons.append(f"matches your resume: {', '.join(matched[:6])} (+{pts})")

    if job.posted:
        age_days = (now - job.posted).total_seconds() / 86400
        if age_days <= 2:
            score += 10
            reasons.append("posted in the last 2 days (+10)")

    job.score = max(0, min(100, score))
    job.reasons = reasons
    return job


def dedupe(jobs: list[Job]) -> list[Job]:
    """Same job often appears on a company board AND on Adzuna; keep the board version."""
    seen: dict[tuple[str, str], Job] = {}
    rank = {"greenhouse": 0, "lever": 0, "ashby": 0, "adzuna": 1}
    for j in jobs:
        k = (_norm(j.company), _norm(re.sub(r"[^a-z0-9 ]", "", j.title.lower())))
        if k not in seen or rank.get(j.source, 2) < rank.get(seen[k].source, 2):
            seen[k] = j
    return list(seen.values())


def find_matches(jobs: list[Job], search: dict, already_seen: set[str],
                 now: datetime | None = None) -> list[Job]:
    scored = [s for j in jobs if (s := score_job(j, search, now))]
    scored = [j for j in dedupe(scored)
              if j.key not in already_seen and j.score >= int(search.get("min_score", 40))]
    return sorted(scored, key=lambda j: (-j.score, j.company))


def funnel(jobs: list[Job], search: dict, now: datetime | None = None) -> dict:
    """How many postings survive each filter, with examples, to tune settings."""
    now = now or datetime.now(timezone.utc)
    stages = {"total": len(jobs), "title": 0, "not_excluded": 0, "location": 0, "recent": 0}
    examples = {"title_hits": [], "dropped_location": [], "dropped_age": []}
    max_age = int(search.get("max_age_days", 7))
    for j in jobs:
        if not title_matches(j.title, search.get("roles", [])):
            continue
        stages["title"] += 1
        if any(b and _norm(b) in _norm(j.title) for b in search.get("exclude_titles", [])):
            continue
        stages["not_excluded"] += 1
        if len(examples["title_hits"]) < 12:
            examples["title_hits"].append(f"{j.company}: {j.title} [{j.location}]")
        if not location_matches(j, search.get("locations", [])):
            if len(examples["dropped_location"]) < 12:
                examples["dropped_location"].append(f"{j.title} [{j.location}]")
            continue
        stages["location"] += 1
        if j.posted and j.posted < now - timedelta(days=max_age):
            if len(examples["dropped_age"]) < 12:
                examples["dropped_age"].append(f"{j.title} [{j.location}] posted {j.posted:%Y-%m-%d}")
            continue
        stages["recent"] += 1
    return {"stages": stages, "examples": examples}
