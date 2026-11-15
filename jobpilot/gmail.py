"""Read application emails from Gmail (IMAP) and send the digest (SMTP).

Uses a Gmail *app password* (Google Account -> Security -> 2-Step Verification ->
App passwords). Secrets: GMAIL_ADDRESS, GMAIL_APP_PASSWORD.
Reading is read-only: messages are never marked read, moved, or deleted.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import smtplib
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from .classify import classify, extract_company, extract_role
from .models import EmailEvent

log = logging.getLogger(__name__)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_text(msg: email.message.Message) -> str:
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        if ctype in ("text/plain", "text/html"):
            try:
                payload = part.get_payload(decode=True) or b""
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                continue
            if ctype == "text/html":
                text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
            parts.append(text)
            if ctype == "text/plain":
                break  # plain text is enough when present
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def parse_message(raw: bytes, message_id: str, thread_id: str) -> EmailEvent | None:
    msg = email.message_from_bytes(raw)
    sender = _decode(msg.get("From"))
    subject = _decode(msg.get("Subject"))
    body = _body_text(msg)[:6000]
    try:
        date = parsedate_to_datetime(msg.get("Date"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except Exception:
        date = datetime.now(timezone.utc)
    result = classify(sender, subject, body)
    if not result:
        return None
    status, conf = result
    return EmailEvent(
        message_id=message_id, thread_id=thread_id, date=date, sender=sender, subject=subject,
        snippet=body[:200], company=extract_company(sender, subject, body),
        role=extract_role(subject, body), status=status, confidence=conf,
    )


def fetch_events(query: str, skip_ids: set[str], limit: int = 200) -> list[EmailEvent]:
    address, password = os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"]
    events: list[EmailEvent] = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        # X-GM-RAW lets us use normal Gmail search syntax
        typ, data = imap.uid("SEARCH", "X-GM-RAW", '"' + query.replace('"', '\\"') + '"')
        uids = (data[0] or b"").split()[-limit:]
        log.info("Gmail: %d candidate emails", len(uids))
        for uid in uids:
            typ, meta = imap.uid("FETCH", uid, "(X-GM-MSGID X-GM-THRID)")
            header = meta[0].decode() if meta and isinstance(meta[0], bytes) else str(meta[0])
            mid = re.search(r"X-GM-MSGID (\d+)", header)
            tid = re.search(r"X-GM-THRID (\d+)", header)
            message_id = mid.group(1) if mid else uid.decode()
            if message_id in skip_ids:
                continue
            typ, msg_data = imap.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK = don't mark as read
            raw = next((p[1] for p in msg_data if isinstance(p, tuple)), None)
            if not raw:
                continue
            ev = parse_message(raw, message_id, tid.group(1) if tid else message_id)
            if ev:
                events.append(ev)
    return sorted(events, key=lambda e: e.date)


def send_email(subject: str, html_body: str, text_body: str) -> None:
    address, password = os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"]
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = os.environ.get("DIGEST_TO", address)
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(address, password)
        s.send_message(msg)
