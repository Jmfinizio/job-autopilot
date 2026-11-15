"""Sample data shaped exactly like the real APIs, used by tests and `run.py demo`."""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage

from jobpilot.gmail import parse_message

DEMO_NOW = datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc)  # 9am ET

GREENHOUSE = {"stripe": {"jobs": [
    {"id": 7001, "title": "Data Engineer, Payments Analytics", "location": {"name": "New York, NY"},
     "absolute_url": "https://stripe.com/jobs/listing/7001", "first_published": "2026-11-14T15:00:00Z",
     "content": "&lt;p&gt;Build pipelines in Python, SQL and Spark.&lt;/p&gt;"},
    {"id": 7002, "title": "Senior Staff Data Engineer", "location": {"name": "Remote in US"},
     "absolute_url": "https://stripe.com/jobs/listing/7002", "first_published": "2026-11-14T15:00:00Z", "content": ""},
    {"id": 7003, "title": "Account Executive", "location": {"name": "Boston, MA"},
     "absolute_url": "https://stripe.com/jobs/listing/7003", "first_published": "2026-11-14T15:00:00Z", "content": ""},
]}, "robinhood": {"jobs": [
    {"id": 8101, "title": "Analytics Engineer", "location": {"name": "Remote - US"},
     "absolute_url": "https://boards.greenhouse.io/robinhood/jobs/8101", "first_published": "2026-11-13T12:00:00Z",
     "content": "dbt, SQL, Python, automation of reporting"},
    {"id": 8102, "title": "Data Scientist", "location": {"name": "Menlo Park, CA"},
     "absolute_url": "https://boards.greenhouse.io/robinhood/jobs/8102", "first_published": "2026-11-13T12:00:00Z", "content": "SQL"},
]}}

LEVER = {"palantir": [
    {"id": "abc-1", "text": "Associate Data Engineer", "categories": {"location": "Boston, MA", "team": "Data"},
     "hostedUrl": "https://jobs.lever.co/palantir/abc-1", "createdAt": 1794657600000,
     "descriptionPlain": "Python, Spark, Azure and Databricks pipelines."},
    {"id": "abc-2", "text": "Data Engineer", "categories": {"location": "Denver, CO"},
     "hostedUrl": "https://jobs.lever.co/palantir/abc-2", "createdAt": 1794657600000, "descriptionPlain": ""},
]}

ASHBY = {"ramp": {"jobs": [
    {"id": "r-9", "title": "Data Scientist, Risk", "location": "New York", "isRemote": False,
     "jobUrl": "https://jobs.ashbyhq.com/ramp/r-9", "publishedAt": "2026-10-01T00:00:00Z",  # too old
     "descriptionPlain": "Python SQL"},
    {"id": "r-10", "title": "Data Engineer", "location": "New York", "isRemote": True,
     "jobUrl": "https://jobs.ashbyhq.com/ramp/r-10", "publishedAt": "2026-11-15T09:00:00Z",
     "descriptionPlain": "Python, SQL, Spark, Databricks, automation"},
]}}


class _Resp:
    def __init__(self, code):
        self.status_code = code


def fake_get(url, params=None):
    import requests
    for token, data in GREENHOUSE.items():
        if url.endswith(f"/boards/{token}/jobs"):
            return data
    for token, data in LEVER.items():
        if url.endswith(f"/postings/{token}"):
            return data
    for token, data in ASHBY.items():
        if url.endswith(f"/job-board/{token}"):
            return data
    err = requests.HTTPError("404")
    err.response = _Resp(404)
    raise err


def make_email(sender, subject, body, date="Fri, 14 Nov 2026 10:00:00 -0500") -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, "me@gmail.com", subject, date
    m.set_content(body)
    return bytes(m)


SAMPLE_EMAILS = [
    ("1", make_email('"Stripe Recruiting" <no-reply@greenhouse.io>',
                     "Thank you for applying to Stripe",
                     "Hi Johnny, thanks for applying for the Data Engineer, Payments Analytics position. We'll review it soon.",
                     "Mon, 3 Nov 2026 10:00:00 -0500")),
    ("2", make_email('"Ramp" <talent@ramp.com>', "Ramp | Next steps: interview",
                     "We'd love to schedule a 30 minute phone screen for the Data Engineer role. Share your availability.")),
    ("3", make_email('LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>', "30 new jobs for Data Engineer",
                     "Job alert: new jobs matching your preferences. Unsubscribe from job alerts.")),
    ("4", make_email('"Datadog Careers" <no-reply@us.greenhouse-mail.io>', "Your application to Datadog",
                     "Thank you for your interest. Unfortunately, we have decided to move forward with other candidates for the Data Engineer position.")),
    ("5", make_email('Sam Lee <sam@hubspot.com>', "Quick chat?",
                     "I'm a technical recruiter at HubSpot and came across your profile. Are you open to new opportunities?")),
    ("6", make_email('"Palantir" <no-reply@hire.lever.co>', "Palantir: Coding challenge for Associate Data Engineer",
                     "Please complete the HackerRank assessment within 5 days.")),
]


def fake_email_fetch(query, skip_ids):
    out = []
    for mid, raw in SAMPLE_EMAILS:
        if mid in skip_ids:
            continue
        ev = parse_message(raw, mid, mid)
        if ev:
            out.append(ev)
    return sorted(out, key=lambda e: e.date)
