import pytest

from sales_agent.store import NotFound, Store


@pytest.fixture
def store():
    s = Store()
    yield s
    s.close()


def test_add_and_get_lead(store):
    lead_id = store.add_lead("Acme", contact_name="Jo Park", email="jo@acme.test")
    lead = store.get_lead(lead_id)
    assert lead["company"] == "Acme"
    assert lead["stage"] == "new"
    assert lead["score"] is None


def test_add_lead_requires_company(store):
    with pytest.raises(ValueError):
        store.add_lead("  ")


def test_find_duplicate_by_email_and_by_name(store):
    store.add_lead("Acme", contact_name="Jo Park", email="jo@acme.test")
    assert store.find_duplicate("Other", email="JO@acme.test")
    assert store.find_duplicate("acme", contact_name="jo park")
    assert store.find_duplicate("Acme", contact_name="Sam Lee") is None


def test_update_validates_stage_and_score(store):
    lead_id = store.add_lead("Acme")
    with pytest.raises(ValueError):
        store.update_lead(lead_id, stage="bogus")
    with pytest.raises(ValueError):
        store.update_lead(lead_id, score=101)
    with pytest.raises(ValueError):
        store.update_lead(lead_id, password="x")
    assert store.update_lead(lead_id, stage="contacted")["stage"] == "contacted"


def test_missing_lead_raises(store):
    with pytest.raises(NotFound):
        store.get_lead(42)
    with pytest.raises(NotFound):
        store.log_activity(42, "note", "x")


def test_search_orders_by_score(store):
    a = store.add_lead("Alpha", industry="fintech")
    b = store.add_lead("Beta", industry="fintech")
    store.add_lead("Gamma", industry="retail")
    store.update_lead(a, score=40)
    store.update_lead(b, score=90)
    assert [l["company"] for l in store.search_leads("fintech")] == ["Beta", "Alpha"]
    assert store.search_leads(stage="won") == []


def test_drafts_and_status(store):
    lead_id = store.add_lead("Acme", contact_name="Jo")
    draft_id = store.save_draft(lead_id, "email", "Hi Jo", subject="Hello")
    assert store.drafts(status="draft")[0]["company"] == "Acme"
    store.set_draft_status(draft_id, "sent")
    assert store.drafts(status="draft") == []
    with pytest.raises(ValueError):
        store.set_draft_status(draft_id, "teleported")
    with pytest.raises(NotFound):
        store.set_draft_status(999, "sent")


def test_pipeline_summary(store):
    a = store.add_lead("Alpha")
    store.update_lead(a, score=80, stage="qualified")
    store.add_lead("Beta")
    store.save_draft(a, "email", "hi")
    summary = store.pipeline_summary()
    assert summary["counts_by_stage"]["qualified"] == 1
    assert summary["open_leads"] == 2
    assert summary["top_scored_open_leads"][0]["company"] == "Alpha"
    assert summary["drafts_awaiting_review"] == 1
