import json

import pytest
from anthropic.lib.tools import ToolError

from sales_agent.store import Store
from sales_agent.tools import build_tools


@pytest.fixture
def tools():
    store = Store()
    yield {t.name: t for t in build_tools(store)}, store
    store.close()


def test_schemas_are_generated(tools):
    t, _ = tools
    schema = t["qualify_lead"].to_dict()["input_schema"]
    assert set(schema["required"]) == {"lead_id", "score", "fit", "rationale"}
    assert schema["additionalProperties"] is False
    assert "Args:" not in t["add_lead"].to_dict()["description"]


def test_add_lead_dedupes(tools):
    t, _ = tools
    first = json.loads(t["add_lead"].call({"company": "Acme", "email": "jo@acme.test"}))
    second = json.loads(t["add_lead"].call({"company": "ACME", "email": "jo@acme.test"}))
    assert first["status"] == "created"
    assert second["status"] == "already_exists"
    assert second["lead"]["id"] == first["lead"]["id"]


def test_qualify_moves_stage_and_logs(tools):
    t, store = tools
    store.add_lead("Acme")
    lead = json.loads(t["qualify_lead"].call({"lead_id": 1, "score": 85, "fit": "strong", "rationale": "ICP match"}))
    assert lead["stage"] == "qualified" and lead["score"] == 85
    assert "ICP match" in store.activities(1)[0]["summary"]

    store.add_lead("Bad Co")
    lead = json.loads(t["qualify_lead"].call({"lead_id": 2, "score": 5, "fit": "disqualified", "rationale": "too small"}))
    assert lead["stage"] == "disqualified"


def test_update_lead_logs_stage_change(tools):
    t, store = tools
    store.add_lead("Acme")
    t["update_lead"].call({"lead_id": 1, "stage": "meeting", "title": "CTO"})
    assert store.get_lead(1)["title"] == "CTO"
    assert "new -> meeting" in store.activities(1)[0]["summary"]


def test_errors_surface_as_tool_errors(tools):
    t, _ = tools
    with pytest.raises(ToolError, match="no lead with id 7"):
        t["get_lead"].call({"lead_id": 7})
    with pytest.raises(ValueError):  # schema validation: fit outside the enum
        t["qualify_lead"].call({"lead_id": 1, "score": 1, "fit": "great", "rationale": ""})


def test_save_draft_and_summary(tools):
    t, store = tools
    store.add_lead("Acme")
    saved = json.loads(t["save_outreach_draft"].call({"lead_id": 1, "channel": "email", "body": "Hi", "subject": "S"}))
    assert saved["status"] == "saved_for_review"
    summary = json.loads(t["pipeline_summary"].call({}))
    assert summary["drafts_awaiting_review"] == 1
    detail = json.loads(t["get_lead"].call({"lead_id": 1}))
    assert detail["drafts"][0]["subject"] == "S"
