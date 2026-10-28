"""Agent tests with a fake client: no network, no API key."""

from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaMessage

from sales_agent.agent import FALLBACK_BETA, SalesAgent
from sales_agent.config import Config
from sales_agent.store import Store


def message(content, stop_reason="end_turn", stop_details=None):
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_details": stop_details,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


class FakeRunner:
    """Mimics the SDK runner: yields messages and runs the tools a message asks for."""

    def __init__(self, replies, tools):
        self.replies = replies
        self.tools = {t.name: t for t in tools if hasattr(t, "call")}
        self.current = None

    def __iter__(self):
        for reply in self.replies:
            self.current = reply
            yield reply

    def generate_tool_call_response(self):
        calls = [b for b in self.current.content if b.type == "tool_use"]
        if not calls:
            return None
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": c.id, "content": self.tools[c.name].call(c.input)}
                for c in calls
            ],
        }


class FakeClient:
    def __init__(self, replies=None, error=None):
        self.replies, self.error, self.calls = replies or [], error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self.tool_runner))

    def tool_runner(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        return FakeRunner(self.replies, params["tools"])


@pytest.fixture
def store():
    s = Store()
    yield s
    s.close()


def test_tool_loop_records_history_and_runs_tools(store):
    client = FakeClient(
        [
            message(
                [{"type": "tool_use", "id": "tu_1", "name": "add_lead", "input": {"company": "Acme"}}],
                stop_reason="tool_use",
            ),
            message([{"type": "text", "text": "Added Acme (id 1)."}]),
        ]
    )
    agent = SalesAgent(Config(), store, client=client)
    seen = []
    result = agent.send("add Acme", on_message=seen.append)

    assert result.text == "Added Acme (id 1)."
    assert result.messages == 2 and len(seen) == 2
    assert store.get_lead(1)["company"] == "Acme"
    roles = [m["role"] for m in agent.history]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert agent.history[2]["content"][0]["type"] == "tool_result"
    assert agent.history[0]["content"].startswith("(Today's date:")


def test_request_params(store):
    client = FakeClient([message([{"type": "text", "text": "ok"}])])
    agent = SalesAgent(Config(), store, client=client)
    agent.send("hi")
    params = client.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "high"}
    assert params["fallbacks"] == "default" and params["betas"] == [FALLBACK_BETA]
    assert params["thread"] is False
    names = [t["name"] if isinstance(t, dict) else t.name for t in params["tools"]]
    assert "web_search" in names and "qualify_lead" in names
    assert "temperature" not in params


def test_no_fallback_or_web_when_not_applicable(store):
    config = Config()
    config.agent.model = "claude-haiku-5-5"
    config.agent.web_research = False
    client = FakeClient([message([{"type": "text", "text": "ok"}])])
    SalesAgent(config, store, client=client).send("hi")
    params = client.calls[0]
    assert "fallbacks" not in params and "betas" not in params
    assert all(not isinstance(t, dict) for t in params["tools"])


def test_second_turn_sends_full_history(store):
    client = FakeClient([message([{"type": "text", "text": "ok"}])])
    agent = SalesAgent(Config(), store, client=client)
    agent.send("one")
    agent.send("two")
    sent = client.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[-1]["content"] == "two"


def test_refusal_rolls_back_the_turn(store):
    client = FakeClient(
        [message([], stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None})]
    )
    agent = SalesAgent(Config(), store, client=client)
    result = agent.send("something")
    assert result.stop_reason == "refusal"
    assert "cyber" in result.text
    assert agent.history == []


def test_api_error_rolls_back_the_turn(store):
    agent = SalesAgent(Config(), store, client=FakeClient(error=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        agent.send("hi")
    assert agent.history == []


def test_max_tokens_is_flagged(store):
    client = FakeClient([message([{"type": "text", "text": "partial"}], stop_reason="max_tokens")])
    result = SalesAgent(Config(), store, client=client).send("hi")
    assert result.text.startswith("partial") and "cut off" in result.text
