"""Job Autopilot command line.

  python run.py all        # find jobs, scan email, send the digest if it's due (what GitHub runs)
  python run.py jobs       # only search for new jobs
  python run.py emails     # only scan Gmail for application updates
  python run.py digest --force   # send the digest email right now
  python run.py check      # test that your secrets and connections work
  python run.py demo       # try it with sample data, no accounts needed (writes demo_state.json)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from jobpilot import store as store_mod
from jobpilot import tracker

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("autopilot")




def cmd_check() -> int:
    ok = True
    def line(good: bool, msg: str):
        nonlocal ok
        ok &= good
        print(("  ✓ " if good else "  ✗ ") + msg)

    print("Secrets")
    for k in ("SHEET_SCRIPT_URL", "SHEET_SCRIPT_SECRET", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NTFY_TOPIC"):
        line(bool(os.environ.get(k)), k)
    for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"):
        print(("  ✓ " if os.environ.get(k) else "  · ") + f"{k} (optional, enables broad search)")

    print("Google Sheet")
    try:
        st = store_mod.open_store()
        line(True, f"connected to '{st.bridge.call('info').get('name')}' and set up tabs: {', '.join(store_mod.TABS)}")
    except Exception as e:
        line(False, f"{type(e).__name__}: {e}")

    print("Gmail")
    try:
        import imaplib
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        line(True, "logged in with the app password")
    except Exception as e:
        line(False, f"{type(e).__name__}: {e}  (use an App Password, not your normal password)")

    print("Phone push")
    from jobpilot import notify
    line(notify.push("Job Autopilot connected", "You'll get job alerts here.", tags="rocket"),
         "sent a test notification (check the ntfy app)")
    print("\nAll good!" if ok else "\nFix the ✗ items above, then run check again.")
    return 0 if ok else 1


def cmd_demo() -> int:
    """Runs the whole pipeline on sample data in tests/fixtures. No accounts, no network."""
    from tests.fakes import fake_get, fake_email_fetch, DEMO_NOW
    path = ROOT / "demo_state.json"
    if path.exists():
        path.unlink()
    st = store_mod.LocalStore(path)
    settings, notes = tracker.load_settings(st)
    for n in notes:
        print("·", n)
    sent = []
    print("\n1) Searching job boards (sample data)…")
    print("  ", tracker.run_jobs(st, settings, get=fake_get, now=DEMO_NOW, send_push=False))
    for j in st.table("Jobs").records():
        print(f"   {j['Score']:>3}  {j['Title']} — {j['Company']} ({j['Location']})")
    print("\n2) Scanning email (sample messages)…")
    print("  ", tracker.run_emails(st, settings, fetch=fake_email_fetch, now=DEMO_NOW, send_push=False))
    for a in st.table("Applications").records():
        print(f"   {a['Status']:<10} {a['Company']:<12} {a['Role'] or '(role unknown)'}   follow up by {a['Follow Up By'] or '-'}")
    print("\n3) Building the digest…")
    res = tracker.run_digest(st, settings, send=lambda s, h, t: sent.append((s, h, t)), now=DEMO_NOW, force=True)
    print("  ", res)
    (ROOT / "demo_digest.html").write_text(sent[0][1])
    print("\nWrote demo_digest.html (open it in a browser) and demo_state.json.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Job Autopilot")
    p.add_argument("command", choices=["all", "jobs", "emails", "digest", "check", "demo"])
    p.add_argument("--force", action="store_true", help="send the digest even if it's early or already sent")
    p.add_argument("--no-push", action="store_true", help="don't send phone notifications")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "check":
        return cmd_check()
    if args.command == "demo":
        return cmd_demo()

    st = store_mod.open_store()
    settings, notes = tracker.load_settings(st)
    for n in notes:
        log.info(n)
    push = not args.no_push
    summary: dict = {}
    failures = 0

    if args.command in ("all", "jobs"):
        try:
            summary["jobs"] = tracker.run_jobs(st, settings, send_push=push)
        except Exception as e:
            failures += 1
            log.exception("job search failed: %s", e)
    if args.command in ("all", "emails"):
        try:
            summary["emails"] = tracker.run_emails(st, settings, send_push=push)
        except Exception as e:
            failures += 1
            log.exception("email scan failed: %s", e)
    if args.command in ("all", "digest"):
        try:
            summary["digest"] = tracker.run_digest(st, settings, force=args.force, sheet_url=st.url())
        except Exception as e:
            failures += 1
            log.exception("digest failed: %s", e)

    print(json.dumps(summary, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
