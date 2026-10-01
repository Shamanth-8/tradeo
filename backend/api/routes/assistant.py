"""
AI routes — the front door to Tradeo's brain.

Everything the interface asks of the intelligence layer comes through here:
conversation, streaming for the voice console, sentiment, per-symbol verdicts,
and provider health so the HUD can show which mind is answering.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai.analyst import brief, evaluate_opportunity
from ai.brain import brain
from ai.conversation import conversation
from ai.providers import ProviderError
from ai.sentiment import analyze_symbol
from ai.symbols import display_name, resolve
from core.config import settings

router = APIRouter()


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = "default"
    channel: Literal["screen", "voice"] = "screen"
    remember: bool = True


class AskResponse(BaseModel):
    response: str
    intent: str
    symbols: list[str]
    data: dict[str, Any] | None = None
    meta: dict[str, Any] = {}
    follow_up_questions: list[str] = []


@router.get("/status")
async def status() -> dict[str, Any]:
    """Which minds are online, and how requests are being routed."""
    return {
        "assistant": settings.assistant_name,
        "operator_title": settings.user_title,
        **brain.status(),
    }


@router.get("/models")
async def models() -> dict[str, Any]:
    """Models each provider will accept — useful for picking OLLAMA_MODEL or OPENROUTER_MODEL."""
    return {
        "local": brain.local.list_models(),
        "cloud": brain.cloud.list_models(),
        "active": {"local": brain.local.model, "cloud": brain.cloud.model},
    }


@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    result = conversation.respond(
        request.message,
        session_id=request.session_id,
        register=request.channel,
        remember=request.remember,
    )
    return AskResponse(**result)


@router.post("/stream")
async def stream(request: AskRequest) -> StreamingResponse:
    """
    Server-sent events, so the HUD can render tokens as they arrive.

    Context assembly is done up front (it needs network calls), then the
    completion streams through.
    """
    def events():
        prepared = conversation.prepare(request.message)
        yield _sse(
            {
                "type": "meta",
                "intent": prepared["intent"],
                "symbols": prepared["symbols"],
                "context_loaded": bool(prepared["context_block"]),
            }
        )

        prompt_parts = []
        if prepared["context_block"]:
            prompt_parts.append(f"LIVE DATA:\n{prepared['context_block']}")
        prompt_parts.append(f"OPERATOR ASKS: {request.message}")

        buffer: list[str] = []
        for chunk in brain.stream(
            "\n\n".join(prompt_parts),
            task="voice" if request.channel == "voice" else "chat",
            register=request.channel,
            max_tokens=350 if request.channel == "voice" else 1100,
        ):
            buffer.append(chunk)
            yield _sse({"type": "token", "text": chunk})

        answer = "".join(buffer)
        if request.remember and answer:
            conversation.remember_turn(request.session_id, request.message, answer)

        yield _sse({"type": "done", "text": answer, "data": prepared["data"] or None})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/sentiment/{symbol}")
async def sentiment(
    symbol: str,
    use_llm: bool = Query(True, description="Blend the reasoning model's read in"),
) -> dict[str, Any]:
    """Blended sentiment: VADER over headlines + the reasoning model."""
    return analyze_symbol(symbol.upper(), display_name(symbol), use_llm=use_llm)


@router.get("/opportunity/{symbol}")
async def opportunity(symbol: str, exchange: str = "NSE") -> dict[str, Any]:
    """A structured buy/watch/avoid verdict with entry, stop and targets."""
    return evaluate_opportunity(symbol.upper(), exchange)


@router.get("/brief/{symbol}")
async def briefing(
    symbol: str,
    horizon: Literal["intraday", "swing", "positional", "long_term"] = "swing",
    channel: Literal["screen", "voice"] = "screen",
    exchange: str = "NSE",
) -> dict[str, Any]:
    return brief(symbol.upper(), horizon=horizon, exchange=exchange, register=channel)


@router.get("/resolve")
async def resolve_symbols(q: str) -> dict[str, Any]:
    """What symbols does this phrase refer to? Used by the voice parser."""
    hits = resolve(q, limit=5)
    return {"query": q, "matches": [{"symbol": s, "name": display_name(s)} for s in hits]}


@router.get("/history")
async def history(session_id: str = "default", limit: int = 50) -> dict[str, Any]:
    return {"session_id": session_id, "messages": conversation.history(session_id, limit)}


@router.delete("/history")
async def clear_history(session_id: str = "default") -> dict[str, Any]:
    deleted = conversation.clear(session_id)
    return {"session_id": session_id, "deleted": deleted}


@router.post("/cache/clear")
async def clear_cache() -> dict[str, str]:
    brain.clear_cache()
    return {"status": "cleared"}


# ---- Voice ----------------------------------------------------------------


@router.post("/voice/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict[str, Any]:
    """
    Transcribe recorded audio locally with Whisper.

    Only needed when the browser's own recogniser isn't good enough — Firefox
    lacks it entirely, and Chrome's struggles with Indian ticker names. Nothing
    leaves this machine either way.
    """
    import tempfile
    from pathlib import Path

    from voice.engine import whisper

    if not whisper.installed:
        raise HTTPException(
            status_code=503,
            detail="Local speech recognition is not installed. "
            "Run: pip install faster-whisper — or use the browser recogniser.",
        )

    raw = await audio.read()
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio larger than 25MB")

    suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(raw)
        temp_path = Path(handle.name)

    try:
        return whisper.transcribe(temp_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


class SpeakRequest(BaseModel):
    text: str


@router.post("/voice/prepare")
async def prepare_speech(request: SpeakRequest) -> dict[str, Any]:
    """
    Reshape an answer for text-to-speech.

    Markdown read aloud is unbearable ("star star BUY star star") and ₹/%
    are pronounced inconsistently, so both get expanded to words first.
    """
    from voice.engine import strip_for_speech

    spoken = strip_for_speech(request.text)
    return {"text": spoken, "characters": len(spoken)}


@router.post("/voice/speak")
async def speak(request: SpeakRequest):
    """Speak text with the local Piper voice; returns WAV audio."""
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import Response

    from voice.engine import piper

    if not piper.installed:
        raise HTTPException(status_code=503, detail="Local speech (piper-tts) is not installed")
    text = request.text.strip()[:2000]
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to say")
    try:
        audio = await run_in_threadpool(piper.synthesize, text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Speech failed: {exc}") from exc
    return Response(content=audio, media_type="audio/wav")


@router.get("/voice/status")
async def voice_status() -> dict[str, Any]:
    """Which recogniser and voice the HUD should use."""
    from voice.engine import status

    return status()


@router.post("/voice/ask")
async def voice_ask(request: AskRequest) -> dict[str, Any]:
    """
    One round trip for the voice loop: clean the transcript, answer, shape for TTS.

    The HUD sends whatever the recogniser heard; this handles the wake word,
    the mangled tickers ("N S E", "dee mat") and the spoken-form answer.
    """
    from voice.engine import clean_transcript, strip_for_speech

    cleaned = clean_transcript(request.message)
    if not cleaned:
        return {
            "heard": request.message,
            "cleaned": "",
            "response": "",
            "speech": "I didn't catch that.",
            "symbols": [],
        }

    result = conversation.respond(
        cleaned, session_id=request.session_id, register="voice", remember=request.remember
    )
    return {
        "heard": request.message,
        "cleaned": cleaned,
        "response": result["response"],
        "speech": strip_for_speech(result["response"]),
        "intent": result["intent"],
        "symbols": result["symbols"],
        "data": result.get("data"),
        "meta": result.get("meta"),
    }


# ---- voice command routing -------------------------------------------------


class VoiceCommandRequest(BaseModel):
    text: str
    fallback_to_chat: bool = True


@router.get("/voice/commands")
async def voice_commands() -> dict[str, Any]:
    """
    Every voice command, so the UI can render them as buttons.

    This is the manual fallback: identical actions through identical code,
    with no microphone involved.
    """
    from voice import commands

    return commands.describe()


@router.post("/voice/command")
async def voice_command(request: VoiceCommandRequest) -> dict[str, Any]:
    """
    Route an utterance to an action.

    Rules first — "start the feed" resolves in milliseconds, offline. Only
    unmatched speech falls through to the conversational brain, which is the
    slow path.
    """
    from voice import commands
    from voice.engine import clean_transcript, strip_for_speech

    cleaned = clean_transcript(request.text)
    result = commands.route(cleaned)

    if result.handled:
        payload = result.as_dict()
        payload["transcript"] = cleaned
        payload["source"] = "command"
        payload["response"] = payload["speech"]  # the screen shows this; speech is TTS-shaped
        payload["speech"] = strip_for_speech(payload["speech"])
        return payload

    if not request.fallback_to_chat:
        return {
            **result.as_dict(),
            "transcript": cleaned,
            "source": "none",
            "speech": "I did not recognise that command. Say 'help' for the list.",
        }

    answer = conversation.respond(cleaned, register="voice")
    text = str(answer.get("response") or "")
    return {
        "handled": True,
        "action": "chat",
        "source": "chat",
        "transcript": cleaned,
        # Full text for the transcript; `speech` is the TTS-shaped version.
        "response": text,
        "symbols": answer.get("symbols") or [],
        "meta": answer.get("meta") or {},
        "speech": strip_for_speech(text) or "I have no answer for that.",
        "data": answer,
        "navigate": None,
        "error": None,
    }
