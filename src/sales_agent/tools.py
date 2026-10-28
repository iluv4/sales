"""The CRM tools the agent can call. All of them read or write the local store only;
nothing here sends a message to a prospect."""

from __future__ import annotations

import functools
import json
from typing import Any, Literal, Optional

from anthropic import beta_tool
from anthropic.lib.tools import ToolError

from .store import NotFound, Store

Stage = Literal[
    "new", "researching", "qualified", "contacted", "meeting", "proposal", "won", "lost", "disqualified"
]
Fit = Literal["strong", "moderate", "weak", "disqualified"]
ActivityKind = Literal["research", "note", "email", "call", "meeting", "linkedin", "other"]
Channel = Literal["email", "linkedin", "call_script", "follow_up"]


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def build_tools(store: Store) -> list:
    """The agent's tool functions, bound to one store."""

    def guarded(fn):
        # Turn store errors into tool errors the model can read and recover from.
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (NotFound, ValueError) as exc:
                raise ToolError(str(exc)) from exc

        return wrapper

    @beta_tool
    @guarded
    def add_lead(
        company: str,
        contact_name: str = "",
        title: str = "",
        email: str = "",
        website: str = "",
        linkedin: str = "",
        industry: str = "",
        company_size: str = "",
        location: str = "",
        source: str = "",
        notes: str = "",
    ) -> str:
        """Add a lead (a company, optionally with a contact person) to the CRM.

        If the same lead already exists (same email, or same company and contact name), the existing
        lead is returned instead of creating a duplicate.

        Args:
            company: Company name.
            contact_name: Full name of the person, if known.
            title: The person's job title.
            email: Work email, only if the user gave it or a public source lists it. Never guess one.
            website: Company website URL.
            linkedin: LinkedIn URL of the person or company.
            industry: Industry or vertical.
            company_size: Employee count or range, e.g. "200-500".
            location: City, region or country.
            source: Where the lead came from, e.g. "user", "web research", "referral".
            notes: Anything else worth keeping.
        """
        existing = store.find_duplicate(company, contact_name, email)
        if existing:
            return _json({"status": "already_exists", "lead": existing})
        lead_id = store.add_lead(
            company,
            contact_name=contact_name,
            title=title,
            email=email,
            website=website,
            linkedin=linkedin,
            industry=industry,
            company_size=company_size,
            location=location,
            source=source,
            notes=notes,
        )
        return _json({"status": "created", "lead": store.get_lead(lead_id)})

    @beta_tool
    @guarded
    def update_lead(
        lead_id: int,
        stage: Optional[Stage] = None,
        contact_name: Optional[str] = None,
        title: Optional[str] = None,
        email: Optional[str] = None,
        website: Optional[str] = None,
        linkedin: Optional[str] = None,
        industry: Optional[str] = None,
        company_size: Optional[str] = None,
        location: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        """Update fields on an existing lead. Only the fields you pass are changed.

        Args:
            lead_id: The lead's id.
            stage: New pipeline stage.
            contact_name: Full name of the person.
            title: The person's job title.
            email: Work email, only from the user or a public source. Never guess one.
            website: Company website URL.
            linkedin: LinkedIn URL.
            industry: Industry or vertical.
            company_size: Employee count or range.
            location: City, region or country.
            notes: Replaces the lead's notes; include anything worth keeping from the old notes.
        """
        fields = {
            k: v
            for k, v in {
                "stage": stage,
                "contact_name": contact_name,
                "title": title,
                "email": email,
                "website": website,
                "linkedin": linkedin,
                "industry": industry,
                "company_size": company_size,
                "location": location,
                "notes": notes,
            }.items()
            if v is not None
        }
        before = store.get_lead(lead_id)
        lead = store.update_lead(lead_id, **fields)
        if stage and stage != before["stage"]:
            store.log_activity(lead_id, "note", f"Stage changed: {before['stage']} -> {stage}")
        return _json(lead)

    @beta_tool
    @guarded
    def get_lead(lead_id: int) -> str:
        """Get one lead with its full activity history and outreach drafts.

        Args:
            lead_id: The lead's id.
        """
        return _json(
            {
                "lead": store.get_lead(lead_id),
                "activities": store.activities(lead_id),
                "drafts": store.drafts(lead_id=lead_id),
            }
        )

    @beta_tool
    @guarded
    def search_leads(query: str = "", stage: Optional[Stage] = None, limit: int = 25) -> str:
        """Search the CRM. Matches company, contact name, email, industry and notes.
        Results are sorted by qualification score, highest first.

        Args:
            query: Text to match; empty matches everything.
            stage: Only leads in this stage.
            limit: Maximum number of results (1-100).
        """
        leads = store.search_leads(query, stage or "", max(1, min(limit, 100)))
        return _json({"count": len(leads), "leads": leads})

    @beta_tool
    @guarded
    def qualify_lead(lead_id: int, score: int, fit: Fit, rationale: str) -> str:
        """Record a qualification verdict for a lead against the ideal customer profile.

        Sets the score and fit, moves the lead to "qualified" (strong or moderate fit) or
        "disqualified", and logs the rationale. Weak fits keep their current stage.

        Args:
            lead_id: The lead's id.
            score: 0-100. 80+ strong fit, 50-79 moderate, below 50 weak.
            fit: Overall verdict. Use "disqualified" when any disqualifier applies.
            rationale: Evidence for the verdict: which ICP criteria match or miss, with sources.
        """
        lead = store.get_lead(lead_id)
        fields: dict[str, Any] = {"score": score, "fit": fit}
        if fit in ("strong", "moderate") and lead["stage"] in ("new", "researching"):
            fields["stage"] = "qualified"
        elif fit == "disqualified":
            fields["stage"] = "disqualified"
        updated = store.update_lead(lead_id, **fields)
        store.log_activity(lead_id, "research", f"Qualified: {fit} ({score}/100). {rationale}")
        return _json(updated)

    @beta_tool
    @guarded
    def log_activity(lead_id: int, kind: ActivityKind, summary: str) -> str:
        """Add an entry to a lead's activity log: research findings, a call the user had, a reply, a note.

        Args:
            lead_id: The lead's id.
            kind: Type of activity.
            summary: What happened or what was learned, with sources for research.
        """
        activity_id = store.log_activity(lead_id, kind, summary)
        return _json({"status": "logged", "activity_id": activity_id})

    @beta_tool
    @guarded
    def save_outreach_draft(lead_id: int, channel: Channel, body: str, subject: str = "") -> str:
        """Save an outreach draft for the user to review. This does not send anything;
        the user reviews and sends drafts themselves.

        Args:
            lead_id: The lead the draft is for.
            channel: Where the message would go.
            body: The full message text, ready to send.
            subject: Subject line, for email.
        """
        draft_id = store.save_draft(lead_id, channel, body, subject)
        return _json({"status": "saved_for_review", "draft_id": draft_id})

    @beta_tool
    @guarded
    def pipeline_summary(stale_days: int = 14) -> str:
        """Pipeline overview: lead counts by stage, top-scored open leads, open leads with no
        activity for a while, and how many drafts await review.

        Args:
            stale_days: Days without activity after which an open lead counts as stale.
        """
        return _json(store.pipeline_summary(stale_days))

    return [
        add_lead,
        update_lead,
        get_lead,
        search_leads,
        qualify_lead,
        log_activity,
        save_outreach_draft,
        pipeline_summary,
    ]


def web_tools(max_searches: int) -> list[dict[str, Any]]:
    """Anthropic-hosted web search and fetch, for prospect research."""
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_searches},
    ]
