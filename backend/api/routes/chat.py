"""
Legacy chat routes.

Kept for the existing frontend's contract. All the thinking now happens in
`ai.conversation`, which the richer /api/ai routes also use — this module only
maps the old request/response shape onto it.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ai.conversation import conversation

router = APIRouter()


class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = "default"


class ChatResponse(BaseModel):
    response: str
    data: Optional[dict] = None
    suggested_actions: Optional[List[str]] = None
    follow_up_questions: Optional[List[str]] = None


@router.post("/message", response_model=ChatResponse)
async def send_message(request: ChatMessage) -> ChatResponse:
    result = conversation.respond(
        request.message, session_id=request.session_id or "default"
    )
    return ChatResponse(
        response=result["response"],
        data=result.get("data"),
        suggested_actions=[f"View {s}" for s in result.get("symbols", [])] or None,
        follow_up_questions=result.get("follow_up_questions"),
    )


@router.get("/history")
async def get_chat_history(session_id: str = "default", limit: int = 50) -> dict[str, Any]:
    messages = conversation.history(session_id, limit)
    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
    }
