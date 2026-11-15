"""Job sources. Each fetcher returns (jobs, problems) and never raises on a bad board,
so one broken company can't stop the whole run."""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from .models import Job

log = logging.getLogger(__name__)
TIMEOUT = 20
HEADERS = {"User-Agent": "job-autopilot/1.0 (personal job search)"}

Getter = Callable[[str, dict[str, Any] | None], Any]


def _http_get(url: str, params: dict[str, Any] | None = None) -> Any:
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):  # Lever uses epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _looks_remote(*parts: str) -> bool:
    blob = " ".join(p for p in parts if p).lower()
    return "remote" in blob or "anywhere" in blob


# ---------------------------------------------------------------- Greenhouse
def parse_greenhouse(token: str, data: dict[str, Any]) -> list[Job]:
    jobs = []
    company = data.get("meta", {}).get("company_name") if isinstance(data.get("meta"), dict) else None
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        jobs.append(Job(
            source="greenhouse", source_id=str(j.get("id")),
            company=j.get("company_name") or company or token.replace("-", " ").title(),
            title=j.get("title", "").strip(), location=loc,
            url=j.get("absolute_url", ""),
            remote=_looks_remote(loc, j.get("title", "")),
            posted=_parse_dt(j.get("first_published") or j.get("updated_at")),
            description=_strip_html(j.get("content"))[:4000],
        ))
    return jobs


def fetch_greenhouse(token: str, get: Getter = _http_get) -> list[Job]:
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", {"content": "true"})
    return parse_greenhouse(token, data)


# ---------------------------------------------------------------- Lever
def parse_lever(token: str, data: list[dict[str, Any]]) -> list[Job]:
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ", ".join(cats.get("allLocations", []) or [])
        jobs.append(Job(
            source="lever", source_id=str(j.get("id")),
            company=token.replace("-", " ").title(),
            title=(j.get("text") or "").strip(), location=loc,
            url=j.get("hostedUrl", ""),
            remote=j.get("workplaceType") == "remote" or _looks_remote(loc),
            posted=_parse_dt(j.get("createdAt")),
            description=_strip_html(j.get("descriptionPlain") or j.get("description"))[:4000],
        ))
    return jobs


def fetch_lever(token: str, get: Getter = _http_get) -> list[Job]:
    return parse_lever(token, get(f"https://api.lever.co/v0/postings/{token}", {"mode": "json"}))


# ---------------------------------------------------------------- Ashby
def parse_ashby(token: str, data: dict[str, Any]) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "") or ""
        jobs.append(Job(
            source="ashby", source_id=str(j.get("id") or j.get("jobUrl")),
            company=token.replace("-", " ").title(),
            title=(j.get("title") or "").strip(), location=loc,
            url=j.get("jobUrl") or j.get("applyUrl", ""),
            remote=bool(j.get("isRemote")) or j.get("workplaceType") == "Remote" or _looks_remote(loc),
            posted=_parse_dt(j.get("publishedAt")),
            description=_strip_html(j.get("descriptionPlain") or j.get("descriptionHtml"))[:4000],
        ))
    return jobs


def fetch_ashby(token: str, get: Getter = _http_get) -> list[Job]:
    return parse_ashby(token, get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", None))


# ---------------------------------------------------------------- Adzuna (broad search)
def parse_adzuna(data: dict[str, Any]) -> list[Job]:
    jobs = []
    for j in data.get("results", []):
        loc = (j.get("location") or {}).get("display_name", "")
        title = _strip_html(j.get("title"))
        jobs.append(Job(
            source="adzuna", source_id=str(j.get("id")),
            company=(j.get("company") or {}).get("display_name", "Unknown"),
            title=title, location=loc, url=j.get("redirect_url", ""),
            remote=_looks_remote(loc, title, j.get("description", "")[:300]),
            posted=_parse_dt(j.get("created")),
            description=_strip_html(j.get("description"))[:4000],
        ))
    return jobs


def fetch_adzuna(role: str, where: str, cfg: dict[str, Any], max_age_days: int,
                 get: Getter = _http_get) -> list[Job]:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    params = {
        "app_id": app_id, "app_key": app_key, "what_phrase": role,
        "results_per_page": cfg.get("results_per_query", 50),
        "max_days_old": max_age_days, "sort_by": "date", "content-type": "application/json",
    }
    if where and where.lower() != "remote":
        params["where"] = where
    else:
        params["what_or"] = "remote"
    country = cfg.get("country", "us")
    return parse_adzuna(get(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params))


# ---------------------------------------------------------------- run all
def collect(settings, get: Getter = _http_get) -> tuple[list[Job], list[str]]:
    jobs: list[Job] = []
    problems: list[str] = []
    boards = {
        "greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
    }
    for kind, fetch in boards.items():
        for token in settings.companies.get(kind, []) or []:
            try:
                got = fetch(token.strip(), get)
                jobs.extend(got)
                log.info("%s/%s: %d postings", kind, token, len(got))
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                problems.append(f"{kind} board '{token}' returned HTTP {code} (check the token)")
            except Exception as e:  # network, bad JSON, etc.
                problems.append(f"{kind} board '{token}' failed: {type(e).__name__}: {e}")

    if settings.adzuna.get("enabled"):
        if not (os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY")):
            problems.append("Adzuna search skipped: ADZUNA_APP_ID / ADZUNA_APP_KEY secrets not set")
        else:
            s = settings.search
            for role in s.get("roles", []):
                for where in s.get("locations", []) or [""]:
                    try:
                        jobs.extend(fetch_adzuna(role, where, settings.adzuna,
                                                 int(s.get("max_age_days", 7)), get))
                    except Exception as e:
                        problems.append(f"Adzuna '{role}' in '{where}' failed: {type(e).__name__}")
    return jobs, problems
