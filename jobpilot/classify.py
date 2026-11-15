"""Turn an email into an application event (company, role, status) using plain rules.

No AI service is needed. The rules are ordered so the most decisive signal wins:
offer > rejection > interview > assessment > applied > recruiter outreach.
"""
from __future__ import annotations

import re
from email.utils import parseaddr

STATUS_ORDER = ["Recruiter", "Applied", "Assessment", "Interview", "Offer", "Rejected"]
# How far along each status is, so a later email never moves an application backwards
# (e.g. a stray "thanks for applying" reminder after an interview invite).
PROGRESS = {"Recruiter": 0, "Applied": 1, "Assessment": 2, "Interview": 3, "Offer": 4, "Rejected": 4}

RULES: list[tuple[str, float, list[str]]] = [
    ("Offer", 0.9, [
        r"\boffer letter\b", r"\bpleased to (extend|offer)\b", r"\bextend (you )?an offer\b",
        r"\bjob offer\b", r"\bcongratulations\b.{0,80}\boffer\b",
    ]),
    ("Rejected", 0.9, [
        r"\bunfortunately\b", r"\bnot (be )?(moving|move) forward\b", r"\bdecided to (move|proceed) forward with other\b",
        r"\bpursue other candidates\b", r"\bnot selected\b", r"\bposition has been filled\b",
        r"\bwill not be proceeding\b", r"\bregret to inform\b", r"\bother candidates whose\b",
        r"\bno longer (under consideration|being considered)\b",
    ]),
    ("Interview", 0.85, [
        r"\binterview\b", r"\bschedule (a|some) (time|call)\b", r"\bphone screen\b",
        r"\bavailability\b.{0,60}\b(call|chat|conversation|meet)\b", r"\bnext round\b",
        r"\bspeak with (you|the team)\b", r"\bcalendly\.com\b", r"\bgoodtime\.io\b",
    ]),
    ("Assessment", 0.8, [
        r"\b(online |coding |technical )?assessment\b", r"\bhackerrank\b", r"\bcodesignal\b",
        r"\bcoding challenge\b", r"\btake[- ]home\b", r"\bcodility\b",
    ]),
    ("Applied", 0.8, [
        r"\bthank(s| you) for (your )?(applying|application|interest)\b", r"\bapplication (was |has been )?received\b",
        r"\bwe('ve| have) received your application\b", r"\byour application (to|for)\b",
        r"\bapplication (submitted|confirmation)\b", r"\bsuccessfully (applied|submitted)\b",
    ]),
    ("Recruiter", 0.6, [
        r"\b(i'?m|i am) a (technical )?recruiter\b", r"\bcame across your (profile|background|resume)\b",
        r"\bopportunity (that|which) (might|may) (interest|be of interest)\b", r"\bopen to (new )?opportunities\b",
        r"\breaching out (about|regarding)\b.{0,60}\b(role|position|opportunity)\b",
    ]),
]

# Emails that mention jobs but aren't about YOUR applications
NOISE = [
    r"\bjob alert\b", r"\bjobs you may be interested\b", r"\bnew jobs? (for|matching)\b",
    r"\brecommended jobs\b", r"\bunsubscribe from (job|these) alerts\b", r"\bweekly digest\b",
    r"\bnewsletter\b", r"\bwebinar\b",
]

# ATS / platform senders: the real company has to come from the text, not the domain
PLATFORM_DOMAINS = {
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co", "ashbyhq.com", "workday.com",
    "myworkday.com", "myworkdayjobs.com", "smartrecruiters.com", "icims.com", "jobvite.com",
    "linkedin.com", "indeed.com", "indeedemail.com", "ziprecruiter.com", "handshake.com",
    "joinhandshake.com", "gmail.com", "outlook.com", "successfactors.com", "taleo.net",
    "applytojob.com", "breezy.hr", "bamboohr.com", "recruitee.com", "workable.com", "rippling.com",
}
GENERIC_NAMES = {"no-reply", "noreply", "careers", "recruiting", "talent", "jobs", "hr",
                 "talent acquisition", "recruiting team", "careers team", "hiring team", "notifications"}

COMPANY_PATTERNS = [
    r"(?:applying|application|interest) (?:to|at|with) ([A-Z][\w&.\- ]{1,40}?)(?:[!.,]| for | -|$)",
    r"(?:position|role|opportunity) (?:at|with) ([A-Z][\w&.\- ]{1,40}?)(?:[!.,]| -|$)",
    r"^([A-Z][\w&.\- ]{1,40}?) (?:application|careers|recruiting|talent)\b",
    r"\bfrom ([A-Z][\w&.\- ]{1,40}?) (?:recruiting|talent|careers)\b",
    r"(?:join|joining) ([A-Z][\w&.\- ]{1,40}?)(?:[!.,]| as |$)",
    r"^([A-Z][\w&.\-]{1,30}(?: [A-Z][\w&.\-]{1,30})?) ?[:|\-–] ",   # "Ramp | Next steps", "Palantir: ..."
]
ROLE_PATTERNS = [
    r"(?:for|as) (?:the |a |an |our )?([A-Z][\w/&,\- ]{2,60}?) (?:position|role|opening)\b",
    r"(?:position|role|opening)[:\-] ?([A-Z][\w/&,\- ]{2,60}?)(?:[.!,(]| at | with |$)",
    r"application for (?:the )?([A-Z][\w/&,\- ]{2,60}?)(?: at | with |[.!,(]|$)",
    r"(?:challenge|assessment|interview|screen) for (?:the |a |an )?([A-Z][\w/&,\- ]{2,60}?)(?: at | with |[.!,(]|$)",
]


def _clean_company(name: str) -> str:
    name = re.sub(r"\b(inc|llc|ltd|corp|corporation|co)\.?$", "", name.strip(), flags=re.I)
    name = re.sub(r"\s+(team|careers|recruiting|talent( acquisition)?|hr|jobs)$", "", name.strip(), flags=re.I)
    return name.strip(" -,.")


def company_from_sender(sender: str) -> str | None:
    display, addr = parseaddr(sender)
    domain = addr.split("@")[-1].lower() if "@" in addr else ""
    root = ".".join(domain.split(".")[-2:]) if domain else ""
    disp = display.strip().strip('"')
    if disp and disp.lower() not in GENERIC_NAMES:
        # "Stripe Recruiting" -> "Stripe"; "Jane Doe at Ramp" -> "Ramp"
        m = re.search(r"\b(?:at|@|from)\s+([A-Z][\w&.\- ]{1,40})$", disp)
        if m:
            return _clean_company(m.group(1))
        if re.search(r"\b(recruiting|careers|talent|hiring|jobs|hr)\b", disp, re.I):
            return _clean_company(disp)
        # A platform sender (Lever, Greenhouse...) whose display name is just the company
        if (root in PLATFORM_DOMAINS or domain in PLATFORM_DOMAINS) and root not in {"gmail.com", "outlook.com"} \
                and len(disp.split()) <= 3 and not re.search(r"\b(no.?reply|notifications?|team)\b", disp, re.I):
            return _clean_company(disp)
    if root and root not in PLATFORM_DOMAINS and domain not in PLATFORM_DOMAINS:
        base = root.split(".")[0]
        return base.capitalize() if len(base) > 2 else base.upper()
    return None


def extract_company(sender: str, subject: str, body: str) -> str:
    for text in (subject, body[:600]):
        for pat in COMPANY_PATTERNS:
            m = re.search(pat, text)
            if m:
                c = _clean_company(m.group(1))
                if c and c.lower() not in {"the", "our", "us", "this", "a"}:
                    return c
    return company_from_sender(sender) or "Unknown"


def extract_role(subject: str, body: str) -> str:
    for text in (subject, body[:800]):
        for pat in ROLE_PATTERNS:
            m = re.search(pat, text)
            if m:
                role = m.group(1).strip(" -,")
                if 2 < len(role) <= 60 and not role.lower().startswith(("this", "the ", "our")):
                    return role
    return ""


def classify(sender: str, subject: str, body: str) -> tuple[str, float] | None:
    text = f"{subject}\n{body}"
    low = text.lower()
    if any(re.search(p, low) for p in NOISE):
        return None
    for status, conf, patterns in RULES:
        if any(re.search(p, low) for p in patterns):
            # "Unfortunately" alone is weak; require it near application words
            if status == "Rejected" and not re.search(r"applica|candida|position|role|interview|consider", low):
                continue
            return status, conf
    return None


def merge_status(current: str | None, new: str) -> str:
    """Only move an application forward. Rejected/Offer are final unless replaced by each other."""
    if not current:
        return new
    if current in ("Rejected", "Offer"):
        return current if new not in ("Offer",) else new
    return new if PROGRESS.get(new, 0) >= PROGRESS.get(current, 0) else current
