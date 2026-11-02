"""The conversational sales agent: a Claude tool runner over the CRM tools and web research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import anthropic

from .config import Config
from .prompts import build_system_prompt
from .store import Store
from .tools import build_tools, web_tools

MAX_TOKENS = 16000
MAX_ITERATIONS = 40

# Models that take the server-side refusal fallback in its "default" form.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class TurnResult:
    text: str
    stop_reason: str | None
    messages: int


class SalesAgent:
    """Holds one conversation. Each call to `send` runs the tool loop until Claude is done."""

    def __init__(self, config: Config, store: Store, client: anthropic.Anthropic | None = None):
        self.config = config
        self.store = store
        self.client = client or anthropic.Anthropic()
        self.tools: list[Any] = build_tools(store)
        if config.agent.web_research:
            self.tools += web_tools(config.agent.max_web_searches)
        self.system = build_system_prompt(config, config.agent.web_research)
        self.history: list[dict[str, Any]] = []

    def _request_params(self) -> dict[str, Any]:
        model = self.config.agent.model
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": self.system,
            "tools": self.tools,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.config.agent.effort},
            # Caches the system prompt, tools and history prefix across turns.
            "cache_control": {"type": "ephemeral"},
            "max_iterations": MAX_ITERATIONS,
            # We keep the history ourselves and send it whole each turn.
            "thread": False,
        }
        if model in FALLBACK_MODELS:
            params["fallbacks"] = "default"
            params["betas"] = [FALLBACK_BETA]
        return params

    def send(self, text: str, on_message: Callable[[Any], None] | None = None) -> TurnResult:
        """Send one user message and run tools until Claude finishes its reply.

        `on_message` is called with each assistant message as it arrives, so a UI can show the
        tool calls in progress.
        """
        if not self.history:
            text = f"(Today's date: {date.today().isoformat()})\n\n{text}"
        checkpoint = len(self.history)
        self.history.append({"role": "user", "content": text})

        final = None
        count = 0
        try:
            runner = self.client.beta.messages.tool_runner(messages=list(self.history), **self._request_params())
            for message in runner:
                count += 1
                final = message
                self.history.append(message.to_param())
                if on_message:
                    on_message(message)
                # Cached by the runner, so the tools run once even though we ask for the results here.
                tool_results = runner.generate_tool_call_response()
                if tool_results is not None:
                    self.history.append(tool_results)
        except BaseException:
            # Leave the conversation as it was before this turn so the next one starts clean.
            del self.history[checkpoint:]
            raise

        if final is None:
            del self.history[checkpoint:]
            return TurnResult("", None, 0)

        if final.stop_reason == "refusal":
            del self.history[checkpoint:]
            category = getattr(final.stop_details, "category", None) if final.stop_details else None
            note = f" (category: {category})" if category else ""
            return TurnResult(f"Claude declined this request{note}. Try rephrasing it.", "refusal", count)

        reply = "\n".join(b.text for b in final.content if b.type == "text").strip()
        if final.stop_reason == "max_tokens":
            reply += "\n\n[Reply cut off at the output limit. Ask me to continue.]"
        elif final.stop_reason in ("tool_use", "pause_turn"):
            reply += f"\n\n[Stopped after {MAX_ITERATIONS} steps. Ask me to continue.]"
        return TurnResult(reply, final.stop_reason, count)

    def reset(self) -> None:
        self.history.clear()
