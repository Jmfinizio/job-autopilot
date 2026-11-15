from datetime import datetime, timedelta, timezone

import pytest

from jobpilot import config as cfg
from jobpilot import tracker
from jobpilot.classify import classify, extract_company, extract_role, merge_status
from jobpilot.matching import find_matches, location_matches, score_job, title_matches
from jobpilot.models import Job
from jobpilot.sources import parse_ashby, parse_greenhouse, parse_lever
from jobpilot.store import LocalStore
from tests.fakes import ASHBY, DEMO_NOW, GREENHOUSE, LEVER, fake_email_fetch, fake_get

SEARCH = {"roles": ["Data Engineer", "Analytics Engineer"], "locations": ["Boston", "Remote"],
          "exclude_titles": ["Principal", "Intern"], "boost_keywords": ["Python", "SQL"],
          "max_age_days": 7, "min_score": 40, "alert_score": 75}


def job(title="Data Engineer", location="Boston, MA", remote=False, days_old=1, desc=""):
    return Job("greenhouse", title + location, "Acme", title, location, "https://x", remote,
               DEMO_NOW - timedelta(days=days_old), desc)


# ---------------------------------------------------------------- parsing
def test_parsers_read_real_api_shapes():
    gh = parse_greenhouse("stripe", GREENHOUSE["stripe"])
    assert gh[0].title == "Data Engineer, Payments Analytics" and gh[0].location == "New York, NY"
    assert "Python" in gh[0].description and "<p>" not in gh[0].description
    assert gh[1].remote  # "Remote in US"
    lv = parse_lever("palantir", LEVER["palantir"])
    assert lv[0].company == "Palantir" and lv[0].posted.year == 2026
    ab = parse_ashby("ramp", ASHBY["ramp"])
    assert ab[1].remote and ab[1].url.endswith("r-10")


# ---------------------------------------------------------------- matching
@pytest.mark.parametrize("title,ok", [
    ("Data Engineer", True), ("Engineer, Data Platform", True), ("Senior Data Engineer", True),
    ("Data Analyst", False), ("Software Engineer", False),
])
def test_title_matching(title, ok):
    assert bool(title_matches(title, SEARCH["roles"])) == ok


def test_location_aliases_and_remote():
    assert location_matches(job(location="Cambridge, MA"), ["Boston"]) == "Boston"
    assert location_matches(job(location="Austin, TX", remote=True), ["Remote"]) == "Remote"
    assert location_matches(job(location="Austin, TX"), ["Boston", "Remote"]) is None


def test_exclusions_age_and_scoring():
    assert score_job(job("Principal Data Engineer"), SEARCH, DEMO_NOW) is None
    assert score_job(job(days_old=30), SEARCH, DEMO_NOW) is None
    junior = score_job(job("Associate Data Engineer", desc="Python and SQL"), SEARCH, DEMO_NOW)
    senior = score_job(job("Senior Data Engineer"), SEARCH, DEMO_NOW)
    assert junior.score > senior.score
    assert any("Python" in r for r in junior.reasons)


def test_already_seen_jobs_are_skipped():
    j = job()
    assert find_matches([j], SEARCH, set(), DEMO_NOW)
    assert not find_matches([j], SEARCH, {j.key}, DEMO_NOW)


# ---------------------------------------------------------------- email rules
@pytest.mark.parametrize("subject,body,status", [
    ("Thank you for applying to Acme", "We received your application.", "Applied"),
    ("Next steps", "We'd like to schedule an interview with you.", "Interview"),
    ("Acme assessment", "Please complete the CodeSignal assessment.", "Assessment"),
    ("Update on your application", "Unfortunately, we will not be moving forward with your candidacy.", "Rejected"),
    ("Offer", "We are pleased to extend an offer for the role.", "Offer"),
    ("Hello", "I'm a recruiter and came across your profile.", "Recruiter"),
])
def test_classify(subject, body, status):
    assert classify("x@acme.com", subject, body)[0] == status


def test_job_alerts_and_unrelated_mail_are_ignored():
    assert classify("alerts@linkedin.com", "New jobs for you", "Job alert: 25 new jobs") is None
    assert classify("friend@gmail.com", "dinner", "Unfortunately I can't make it tonight") is None


def test_company_and_role_extraction():
    assert extract_company('"Stripe Recruiting" <no-reply@greenhouse.io>', "Thanks", "") == "Stripe"
    assert extract_company("Sam <sam@hubspot.com>", "Hi", "") == "Hubspot"
    assert extract_company('"Palantir" <no-reply@hire.lever.co>', "Palantir: next steps", "") == "Palantir"
    assert extract_role("Your application", "thanks for applying for the Data Engineer position") == "Data Engineer"


def test_status_only_moves_forward():
    assert merge_status("Interview", "Applied") == "Interview"
    assert merge_status("Applied", "Interview") == "Interview"
    assert merge_status("Rejected", "Applied") == "Rejected"
    assert merge_status("Interview", "Offer") == "Offer"


# ---------------------------------------------------------------- settings tab
def test_sheet_settings_override_config():
    base = cfg.Settings(raw={"search": {"roles": ["X"], "max_age_days": 7}})
    merged, notes = cfg.apply_sheet_overrides(base, [
        ["roles", "Data Engineer, BI Engineer"], ["max_age_days", "3"], ["max_age_days", ""],
        ["bogus", "1"], ["alert_score", "high"]])
    assert merged.search["roles"] == ["Data Engineer", "BI Engineer"]
    assert merged.search["max_age_days"] == 3
    assert any("unknown" in n for n in notes) and any("number" in n for n in notes)


# ---------------------------------------------------------------- end to end
@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "state.json")


def test_full_run_is_idempotent(store):
    settings, _ = tracker.load_settings(store)
    r1 = tracker.run_jobs(store, settings, get=fake_get, now=DEMO_NOW, send_push=False)
    r2 = tracker.run_jobs(store, settings, get=fake_get, now=DEMO_NOW, send_push=False)
    assert r1["new_matches"] == 4 and r2["new_matches"] == 0
    assert any("datadog" in p for p in r1["problems"])  # bad board reported, run continues

    e1 = tracker.run_emails(store, settings, fetch=fake_email_fetch, now=DEMO_NOW, send_push=False)
    e2 = tracker.run_emails(store, settings, fetch=fake_email_fetch, now=DEMO_NOW, send_push=False)
    assert e1["status_changes"] == 5 and e2["emails_matched"] == 0
    statuses = {a["Company"]: a["Status"] for a in store.table("Applications").records()}
    assert statuses == {"Stripe": "Applied", "Ramp": "Interview", "Datadog": "Rejected",
                        "Hubspot": "Recruiter", "Palantir": "Assessment"}


def test_marking_a_job_applied_adds_it_to_applications(store):
    settings, _ = tracker.load_settings(store)
    tracker.run_jobs(store, settings, get=fake_get, now=DEMO_NOW, send_push=False)
    jobs = store.table("Jobs")
    rows = jobs.records()
    rows[0]["Status"] = "applied"
    jobs.update(0, rows[0])
    assert tracker.import_marked_jobs(store, settings, DEMO_NOW) == 1
    assert store.table("Jobs").records()[0]["Status"] == "Applied (tracked)"
    assert tracker.import_marked_jobs(store, settings, DEMO_NOW) == 0


def test_digest_once_per_day_and_followups(store):
    settings, _ = tracker.load_settings(store)
    tracker.run_jobs(store, settings, get=fake_get, now=DEMO_NOW, send_push=False)
    tracker.run_emails(store, settings, fetch=fake_email_fetch, now=DEMO_NOW, send_push=False)
    sent = []
    send = lambda s, h, t: sent.append(s)
    early = DEMO_NOW.replace(hour=11)  # 6am ET, before digest hour
    assert tracker.run_digest(store, settings, send, now=early)["sent"] is False
    res = tracker.run_digest(store, settings, send, now=DEMO_NOW)
    assert res["sent"] and res["followups"] == 1  # Stripe applied Nov 3, 10-day reminder
    assert tracker.run_digest(store, settings, send, now=DEMO_NOW + timedelta(hours=2))["sent"] is False
    assert len(sent) == 1 and "4 new" in sent[0]


# ---------------------------------------------------------------- Apps Script bridge
def test_pipeline_through_apps_script_bridge():
    from jobpilot.store import BridgeError, SheetStore
    from tests.fakes import FakeAppsScript, inbox_as_script_messages

    script = FakeAppsScript(inbox=inbox_as_script_messages())
    with pytest.raises(BridgeError, match="unauthorized"):
        SheetStore("https://script.google.com/x/exec", "wrong", post=script)

    st = SheetStore("https://script.google.com/x/exec", "s3cret", post=script)
    assert "Sheet1" not in script.sheets and {"Jobs", "Applications", "Settings", "_state"} <= set(script.sheets)
    settings, notes = tracker.load_settings(st)
    assert script.sheets["Settings"][1][0] == "roles"  # template written for you to edit

    # change the search from the Sheet, like you would on your phone
    script.sheets["Settings"][1][1] = "Analytics Engineer"
    settings, notes = tracker.load_settings(st)
    assert settings.search["roles"] == ["Analytics Engineer"]

    r = tracker.run_jobs(st, settings, get=fake_get, now=DEMO_NOW, send_push=False)
    titles = {row[3] for row in script.sheets["Jobs"][1:]}
    assert titles == {"Analytics Engineer", "Data Engineer, Payments Analytics"}  # both words present
    # Gmail is read through the script too: same 5 applications as the raw-email path
    r = tracker.run_emails(st, settings, now=DEMO_NOW, send_push=False)
    assert r["status_changes"] == 5 and len(script.sheets["Applications"]) == 6  # header + 5
    assert tracker.run_emails(st, settings, now=DEMO_NOW, send_push=False)["emails_matched"] == 0
    res = tracker.run_digest(st, settings, now=DEMO_NOW, sheet_url=st.url())
    assert res["sent"] and st.url().endswith("FAKE")
    assert len(script.sent) == 1 and "Job digest" in script.sent[0]["subject"]
