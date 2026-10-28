"""A small SQLite CRM: leads, the activity log for each lead, and outreach drafts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGES = (
    "new",
    "researching",
    "qualified",
    "contacted",
    "meeting",
    "proposal",
    "won",
    "lost",
    "disqualified",
)
OPEN_STAGES = STAGES[:6]
DRAFT_STATUSES = ("draft", "sent", "discarded")

LEAD_FIELDS = (
    "company",
    "contact_name",
    "title",
    "email",
    "website",
    "linkedin",
    "industry",
    "company_size",
    "location",
    "source",
    "notes",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL,
    contact_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    linkedin TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL DEFAULT '',
    company_size TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'new',
    score INTEGER,
    fit TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_lead ON activities(lead_id);
CREATE INDEX IF NOT EXISTS idx_drafts_lead ON drafts(lead_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class NotFound(LookupError):
    pass


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # Leads

    def find_duplicate(self, company: str, contact_name: str = "", email: str = "") -> dict[str, Any] | None:
        """An existing lead for the same email, or else the same company and contact name."""
        if email.strip():
            row = self.conn.execute(
                "SELECT * FROM leads WHERE lower(email) = lower(?)", (email.strip(),)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM leads WHERE lower(company) = lower(?) AND lower(contact_name) = lower(?)",
                (company.strip(), contact_name.strip()),
            ).fetchone()
        return dict(row) if row else None

    def add_lead(self, company: str, **fields: Any) -> int:
        company = company.strip()
        if not company:
            raise ValueError("company is required")
        values = {k: (fields.get(k) or "").strip() for k in LEAD_FIELDS if k != "company"}
        unknown = set(fields) - set(LEAD_FIELDS)
        if unknown:
            raise ValueError(f"unknown lead field(s): {', '.join(sorted(unknown))}")
        now = _now()
        cols = ["company", *values, "created_at", "updated_at"]
        cur = self.conn.execute(
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [company, *values.values(), now, now],
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_lead(self, lead_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise NotFound(f"no lead with id {lead_id}")
        return dict(row)

    def update_lead(self, lead_id: int, **fields: Any) -> dict[str, Any]:
        self.get_lead(lead_id)
        allowed = set(LEAD_FIELDS) | {"stage", "score", "fit"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown lead field(s): {', '.join(sorted(unknown))}")
        if "stage" in fields and fields["stage"] not in STAGES:
            raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
        if "score" in fields and fields["score"] is not None and not 0 <= int(fields["score"]) <= 100:
            raise ValueError("score must be between 0 and 100")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(
                f"UPDATE leads SET {sets}, updated_at = ? WHERE id = ?",
                [*fields.values(), _now(), lead_id],
            )
            self.conn.commit()
        return self.get_lead(lead_id)

    def search_leads(self, query: str = "", stage: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM leads WHERE 1=1"
        args: list[Any] = []
        if query:
            like = f"%{query.lower()}%"
            sql += " AND (lower(company) LIKE ? OR lower(contact_name) LIKE ? OR lower(email) LIKE ?"
            sql += " OR lower(industry) LIKE ? OR lower(notes) LIKE ?)"
            args += [like] * 5
        if stage:
            if stage not in STAGES:
                raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
            sql += " AND stage = ?"
            args.append(stage)
        sql += " ORDER BY score IS NULL, score DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    # Activities

    def log_activity(self, lead_id: int, kind: str, summary: str) -> int:
        self.get_lead(lead_id)
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO activities (lead_id, kind, summary, created_at) VALUES (?, ?, ?, ?)",
            (lead_id, kind, summary, now),
        )
        self.conn.execute("UPDATE leads SET updated_at = ? WHERE id = ?", (now, lead_id))
        self.conn.commit()
        return int(cur.lastrowid)

    def activities(self, lead_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM activities WHERE lead_id = ? ORDER BY id", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # Drafts

    def save_draft(self, lead_id: int, channel: str, body: str, subject: str = "") -> int:
        self.get_lead(lead_id)
        cur = self.conn.execute(
            "INSERT INTO drafts (lead_id, channel, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (lead_id, channel, subject, body, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def drafts(self, lead_id: int | None = None, status: str = "") -> list[dict[str, Any]]:
        sql = "SELECT d.*, l.company, l.contact_name FROM drafts d JOIN leads l ON l.id = d.lead_id WHERE 1=1"
        args: list[Any] = []
        if lead_id is not None:
            sql += " AND d.lead_id = ?"
            args.append(lead_id)
        if status:
            sql += " AND d.status = ?"
            args.append(status)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY d.id", args)]

    def set_draft_status(self, draft_id: int, status: str) -> None:
        if status not in DRAFT_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(DRAFT_STATUSES)}")
        cur = self.conn.execute("UPDATE drafts SET status = ? WHERE id = ?", (status, draft_id))
        if cur.rowcount == 0:
            raise NotFound(f"no draft with id {draft_id}")
        self.conn.commit()

    # Pipeline

    def pipeline_summary(self, stale_days: int = 14) -> dict[str, Any]:
        counts = {stage: 0 for stage in STAGES}
        for row in self.conn.execute("SELECT stage, COUNT(*) AS n FROM leads GROUP BY stage"):
            counts[row["stage"]] = row["n"]
        open_marks = ", ".join("?" * len(OPEN_STAGES))
        top = self.conn.execute(
            f"SELECT id, company, contact_name, stage, score FROM leads"
            f" WHERE stage IN ({open_marks}) AND score IS NOT NULL ORDER BY score DESC LIMIT 10",
            OPEN_STAGES,
        ).fetchall()
        stale = self.conn.execute(
            f"SELECT id, company, stage, updated_at FROM leads WHERE stage IN ({open_marks})"
            f" AND julianday('now') - julianday(updated_at) > ? ORDER BY updated_at LIMIT 20",
            [*OPEN_STAGES, stale_days],
        ).fetchall()
        pending = self.conn.execute("SELECT COUNT(*) FROM drafts WHERE status = 'draft'").fetchone()[0]
        return {
            "counts_by_stage": counts,
            "open_leads": sum(counts[s] for s in OPEN_STAGES),
            "top_scored_open_leads": [dict(r) for r in top],
            f"stale_leads_no_activity_{stale_days}d": [dict(r) for r in stale],
            "drafts_awaiting_review": pending,
        }
