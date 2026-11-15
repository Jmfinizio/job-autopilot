"""Read application emails and send the digest through the Apps Script bridge.

The script runs as you inside your Google Sheet, so there are no Gmail passwords to manage.
Reading is read-only: messages are never marked read, moved, or deleted.
"""
from __future__ import annotations

import email
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from .classify import classify, extract_company, extract_role
from .models import EmailEvent

log = logging.getLogger(__name__)


def build_event(message_id: str, thread_id: str, date: datetime, sender: str, subject: str,
                body: str) -> EmailEvent | None:
    body = re.sub(r"\s+", " ", body or "").strip()[:6000]
    result = classify(sender, subject, body)
    if not result:
        return None
    status, conf = result
    return EmailEvent(
        message_id=message_id, thread_id=thread_id, date=date, sender=sender, subject=subject,
        snippet=body[:200], company=extract_company(sender, subject, body),
        role=extract_role(subject, body), status=status, confidence=conf,
    )


def _parse_iso(value: str) -> datetime:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def fetch_events(bridge, query: str, skip_ids: set[str], limit: int = 100) -> list[EmailEvent]:
    msgs = bridge.call("gmail_search", query=query, limit=limit).get("messages", [])
    log.info("Gmail: %d messages in matching threads", len(msgs))
    events = []
    for m in msgs:
        if m["id"] in skip_ids:
            continue
        ev = build_event(m["id"], m.get("threadId", m["id"]), _parse_iso(m.get("date")),
                         m.get("from", ""), m.get("subject", ""), m.get("body", ""))
        if ev:
            events.append(ev)
    return sorted(events, key=lambda e: e.date)


def send_email(bridge, subject: str, html_body: str, text_body: str, to: str | None = None) -> None:
    bridge.call("send_email", subject=subject, html=html_body, text=text_body, to=to)


# ---- used by tests and the demo to turn a raw .eml into an event
def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def parse_message(raw: bytes, message_id: str, thread_id: str) -> EmailEvent | None:
    msg = email.message_from_bytes(raw)
    body = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/plain":
            body = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
            break
    try:
        date = parsedate_to_datetime(msg.get("Date"))
    except Exception:
        date = datetime.now(timezone.utc)
    return build_event(message_id, thread_id, date, _decode(msg.get("From")), _decode(msg.get("Subject")), body)
