"""Fake Anthropic client and messages, for tests that must not touch the network."""

from types import SimpleNamespace

from anthropic.types.beta import BetaMessage


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
