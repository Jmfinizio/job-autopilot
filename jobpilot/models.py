from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    source: str          # greenhouse | lever | ashby | adzuna
    source_id: str       # id within that source
    company: str
    title: str
    location: str
    url: str
    remote: bool = False
    posted: datetime | None = None
    description: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable id used to avoid alerting on the same job twice."""
        return f"{self.source}:{self.source_id}"


@dataclass
class EmailEvent:
    message_id: str
    thread_id: str
    date: datetime
    sender: str
    subject: str
    snippet: str
    company: str
    role: str
    status: str          # Applied | Assessment | Interview | Offer | Rejected | Recruiter
    confidence: float
