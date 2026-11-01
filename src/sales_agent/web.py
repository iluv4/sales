"""Web chat service: a browser chat with the agent, plus views of the leads and drafts it keeps."""

from __future__ import annotations

import json
import queue
import secrets
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Callable, Iterator

import anthropic
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .agent import SalesAgent
from .config import Config
from .store import DRAFT_STATUSES, STAGES, NotFound, Store

MAX_CONVERSATIONS = 200

STEP_LABELS = {
    "add_lead": "리드 추가",
    "update_lead": "리드 수정",
    "get_lead": "리드 조회",
    "search_leads": "리드 검색",
    "qualify_lead": "적합도 평가",
    "log_activity": "활동 기록",
    "save_outreach_draft": "메일 초안 저장",
    "pipeline_summary": "파이프라인 확인",
    "web_search": "웹 검색",
    "web_fetch": "웹페이지 읽기",
}


def describe_steps(message: Any) -> list[str]:
    """One short Korean line per tool call in an assistant message."""
    steps = []
    for block in message.content:
        if block.type not in ("tool_use", "server_tool_use"):
            continue
        label = STEP_LABELS.get(block.name, block.name)
        args = block.input if isinstance(block.input, dict) else {}
        detail = args.get("query") or args.get("url") or args.get("company") or ""
        if not detail and "lead_id" in args:
            detail = f"#{args['lead_id']}"
        steps.append(f"{label} {detail}".strip())
    return steps


def describe_error(exc: Exception) -> str:
    if isinstance(exc, anthropic.AuthenticationError) or (
        isinstance(exc, TypeError) and "authentication method" in str(exc)
    ):
        return "API 키가 없거나 잘못됐어요. 서버에 ANTHROPIC_API_KEY를 설정해주세요."
    if isinstance(exc, anthropic.RateLimitError):
        return "요청이 너무 많아요. 잠시 후 다시 시도해주세요."
    if isinstance(exc, anthropic.APIStatusError):
        return f"API 오류가 났어요 ({exc.status_code}). 잠시 후 다시 시도해주세요."
    if isinstance(exc, anthropic.APIConnectionError):
        return "API 서버에 연결하지 못했어요. 네트워크를 확인해주세요."
    return "처리 중 오류가 났어요. 다시 시도해주세요."


@dataclass
class Conversation:
    agent: SalesAgent
    store: Store
    lock: threading.Lock = field(default_factory=threading.Lock)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ResetRequest(BaseModel):
    conversation_id: str


class DraftStatusRequest(BaseModel):
    status: str


def create_app(
    config: Config,
    password: str | None = None,
    client_factory: Callable[[], Any] = anthropic.Anthropic,
) -> FastAPI:
    app = FastAPI(title="Sales Agent", docs_url=None, redoc_url=None)
    security = HTTPBasic(auto_error=False)
    client = client_factory()
    conversations: OrderedDict[str, Conversation] = OrderedDict()
    registry_lock = threading.Lock()

    def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
        if not password:
            return
        if credentials is None or not secrets.compare_digest(
            credentials.password.encode(), password.encode()
        ):
            raise HTTPException(401, "로그인이 필요해요", headers={"WWW-Authenticate": "Basic"})

    def open_store() -> Store:
        return Store(config.db_file)

    def get_conversation(conversation_id: str | None) -> tuple[str, Conversation]:
        with registry_lock:
            if conversation_id and conversation_id in conversations:
                conversations.move_to_end(conversation_id)
                return conversation_id, conversations[conversation_id]
            new_id = uuid.uuid4().hex
            store = open_store()
            conversations[new_id] = Conversation(SalesAgent(config, store, client=client), store)
            while len(conversations) > MAX_CONVERSATIONS:
                _, old = conversations.popitem(last=False)
                old.store.close()
            return new_id, conversations[new_id]

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
    def index() -> str:
        return resources.files("sales_agent").joinpath("static/index.html").read_text(encoding="utf-8")

    @app.post("/api/chat", dependencies=[Depends(require_auth)])
    def chat(request: ChatRequest) -> StreamingResponse:
        text = request.message.strip()
        if not text:
            raise HTTPException(400, "메시지를 입력해주세요")
        conversation_id, conversation = get_conversation(request.conversation_id)
        if not conversation.lock.acquire(blocking=False):
            raise HTTPException(409, "이전 요청을 아직 처리하고 있어요")

        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def report_steps(message: Any) -> None:
            for step in describe_steps(message):
                events.put({"type": "step", "text": step})

        def run() -> None:
            try:
                result = conversation.agent.send(text, on_message=report_steps)
                events.put({"type": "reply", "text": result.text, "stop_reason": result.stop_reason})
            except Exception as exc:  # reported to the browser; the agent already rolled the turn back
                events.put({"type": "error", "text": describe_error(exc)})
            finally:
                conversation.lock.release()
                events.put(None)

        threading.Thread(target=run, daemon=True).start()

        def stream() -> Iterator[str]:
            yield json.dumps({"type": "conversation", "id": conversation_id}) + "\n"
            while (event := events.get()) is not None:
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/reset", dependencies=[Depends(require_auth)])
    def reset(request: ResetRequest) -> dict[str, bool]:
        with registry_lock:
            conversation = conversations.pop(request.conversation_id, None)
        if conversation:
            conversation.store.close()
        return {"ok": True}

    @app.get("/api/leads", dependencies=[Depends(require_auth)])
    def leads(query: str = "", stage: str = "") -> list[dict[str, Any]]:
        if stage and stage not in STAGES:
            raise HTTPException(400, "알 수 없는 단계예요")
        store = open_store()
        try:
            return store.search_leads(query, stage, 200)
        finally:
            store.close()

    @app.get("/api/leads/{lead_id}", dependencies=[Depends(require_auth)])
    def lead(lead_id: int) -> dict[str, Any]:
        store = open_store()
        try:
            return {
                "lead": store.get_lead(lead_id),
                "activities": store.activities(lead_id),
                "drafts": store.drafts(lead_id=lead_id),
            }
        except NotFound:
            raise HTTPException(404, "리드를 찾을 수 없어요")
        finally:
            store.close()

    @app.get("/api/drafts", dependencies=[Depends(require_auth)])
    def drafts(status: str = "draft") -> list[dict[str, Any]]:
        store = open_store()
        try:
            return store.drafts(status=status)
        finally:
            store.close()

    @app.post("/api/drafts/{draft_id}/status", dependencies=[Depends(require_auth)])
    def draft_status(draft_id: int, request: DraftStatusRequest) -> dict[str, bool]:
        if request.status not in DRAFT_STATUSES:
            raise HTTPException(400, "알 수 없는 상태예요")
        store = open_store()
        try:
            store.set_draft_status(draft_id, request.status)
        except NotFound:
            raise HTTPException(404, "초안을 찾을 수 없어요")
        finally:
            store.close()
        return {"ok": True}

    @app.get("/api/pipeline", dependencies=[Depends(require_auth)])
    def pipeline() -> dict[str, Any]:
        store = open_store()
        try:
            return store.pipeline_summary()
        finally:
            store.close()

    return app
