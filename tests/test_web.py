import json

import pytest
from fakes import FakeClient, message
from fastapi.testclient import TestClient

from sales_agent.cli import _is_loopback, main
from sales_agent.config import Config
from sales_agent.store import Store
from sales_agent.web import create_app


def make_config(tmp_path):
    config = Config(base_dir=tmp_path)
    config.agent.web_research = False
    return config


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@pytest.fixture
def app_client(tmp_path):
    replies = [
        message(
            [{"type": "tool_use", "id": "tu_1", "name": "add_lead", "input": {"company": "Acme"}}],
            stop_reason="tool_use",
        ),
        message([{"type": "text", "text": "Acme를 추가했어요."}]),
    ]
    fake = FakeClient(replies)
    app = create_app(make_config(tmp_path), client_factory=lambda: fake)
    return TestClient(app), fake, tmp_path


def test_index_page(app_client):
    client, _, _ = app_client
    res = client.get("/")
    assert res.status_code == 200 and "세일즈 에이전트" in res.text


def test_chat_streams_steps_and_reply(app_client):
    client, fake, tmp_path = app_client
    out = events(client.post("/api/chat", json={"message": "Acme 추가해줘"}))
    assert out[0]["type"] == "conversation"
    assert {"type": "step", "text": "리드 추가 Acme"} in out
    assert out[-1] == {"type": "reply", "text": "Acme를 추가했어요.", "stop_reason": "end_turn"}

    leads = client.get("/api/leads").json()
    assert [l["company"] for l in leads] == ["Acme"]
    assert client.get("/api/pipeline").json()["counts_by_stage"]["new"] == 1

    # The same conversation continues with its history.
    fake.replies = [message([{"type": "text", "text": "네"}])]
    client.post("/api/chat", json={"message": "확인", "conversation_id": out[0]["id"]})
    assert [m["role"] for m in fake.calls[-1]["messages"]] == ["user", "assistant", "user", "assistant", "user"]


def test_chat_reports_errors(tmp_path):
    app = create_app(make_config(tmp_path), client_factory=lambda: FakeClient(error=RuntimeError("boom")))
    out = events(TestClient(app).post("/api/chat", json={"message": "hi"}))
    assert out[-1]["type"] == "error"


def test_empty_message_rejected(app_client):
    client, _, _ = app_client
    assert client.post("/api/chat", json={"message": "  "}).status_code == 400


def test_lead_detail_and_draft_status(app_client):
    client, _, tmp_path = app_client
    store = Store(tmp_path / "sales.db")
    lead_id = store.add_lead("Beta")
    draft_id = store.save_draft(lead_id, "email", "안녕하세요", "제목")
    store.close()

    assert client.get(f"/api/leads/{lead_id}").json()["drafts"][0]["subject"] == "제목"
    assert client.get("/api/leads/999").status_code == 404
    assert len(client.get("/api/drafts").json()) == 1
    assert client.post(f"/api/drafts/{draft_id}/status", json={"status": "bogus"}).status_code == 400
    assert client.post(f"/api/drafts/{draft_id}/status", json={"status": "sent"}).json() == {"ok": True}
    assert client.get("/api/drafts").json() == []


def test_password_required_when_set(tmp_path):
    app = create_app(make_config(tmp_path), password="s3cret", client_factory=FakeClient)
    client = TestClient(app)
    assert client.get("/").status_code == 401
    assert client.get("/api/leads", auth=("me", "wrong")).status_code == 401
    assert client.get("/api/leads", auth=("me", "s3cret")).status_code == 200


def test_web_refuses_public_host_without_password(monkeypatch):
    monkeypatch.delenv("SALES_AGENT_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="without a password"):
        main(["web", "--host", "0.0.0.0"])
    assert _is_loopback("127.0.0.1") and _is_loopback("localhost") and not _is_loopback("0.0.0.0")
