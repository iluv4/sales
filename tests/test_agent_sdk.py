"""Drives the real SDK tool runner against a mocked HTTP transport, to check the agent's
history handling against the runner itself (tools run once, request bodies are well formed)."""

import json

import anthropic
import httpx2
from anthropic import DefaultHttpxClient

from sales_agent.agent import FALLBACK_BETA, SalesAgent
from sales_agent.config import Config
from sales_agent.store import Store


def reply(content, stop_reason):
    return {
        "id": "msg_x",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def test_real_runner_round_trip():
    replies = [
        reply([{"type": "tool_use", "id": "tu_1", "name": "add_lead", "input": {"company": "Acme"}}], "tool_use"),
        reply([{"type": "text", "text": "Added Acme."}], "end_turn"),
        reply([{"type": "text", "text": "Still here."}], "end_turn"),
    ]
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append((request, json.loads(request.content)))
        return httpx2.Response(200, json=replies[len(requests) - 1])

    client = anthropic.Anthropic(
        api_key="test", max_retries=0, http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    )
    store = Store()
    config = Config()
    config.agent.web_research = False
    agent = SalesAgent(config, store, client=client)

    assert agent.send("add Acme").text == "Added Acme."
    assert len(store.search_leads()) == 1  # the tool ran exactly once

    request, body = requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in request.headers["anthropic-beta"]
    assert body["output_config"] == {"effort": "high"}
    second = requests[1][1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[2]["content"][0]["tool_use_id"] == "tu_1"

    assert agent.send("still there?").text == "Still here."
    third = requests[2][1]["messages"]
    assert [m["role"] for m in third] == ["user", "assistant", "user", "assistant", "user"]
    assert len(agent.history) == 6
