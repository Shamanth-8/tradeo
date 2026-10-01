"""
Conversation orchestration.

A message comes in; this decides which symbols it is about, what data the brain
needs to answer well, fetches only that, and keeps the session history. The
same path serves the typed chat panel and the voice channel — only the
`register` changes, so spoken answers stay short and markdown-free.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.config import settings
from data.storage.database import get_db_connection

from .analyst import build_context, render_context
from .brain import brain
from .providers import ProviderError
from .symbols import display_name, resolve

log = logging.getLogger("tradeo.conversation")

MAX_HISTORY_TURNS = 6

# Intent -> what context to assemble. Keyword matching beats asking a 3B model
# to classify: it is instant, deterministic, and wrong in predictable ways.
INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("portfolio", re.compile(r"\b(my|our)\s+(portfolio|holdings|positions|investments)|\bhow am i doing\b|\bmy p&?l\b", re.I)),
    ("price", re.compile(r"\b(price|quote|ltp|trading at|how much is|current value)\b", re.I)),
    ("trade_call", re.compile(r"\b(buy|sell|entry|exit|short|long|target|stop\s?loss|book profit)\b", re.I)),
    ("analysis", re.compile(r"\b(analy[sz]e|analysis|view on|opinion|thoughts on|outlook|forecast|worth)\b", re.I)),
    ("sentiment", re.compile(r"\b(sentiment|news|buzz|mood|talking about|hype)\b", re.I)),
    ("compare", re.compile(r"\b(vs|versus|compare|better than|or)\b", re.I)),
    ("education", re.compile(r"\b(what is|what are|explain|how does|how do|difference between|meaning of|should i learn)\b", re.I)),
    ("screen", re.compile(r"\b(screen|find|suggest|recommend|opportunit|ideas|what should i)\b", re.I)),
]


def classify(message: str) -> str:
    for intent, pattern in INTENT_PATTERNS:
        if pattern.search(message):
            return intent
    return "general"


# Intents that justify the cost of a full data pull.
_NEEDS_FULL_CONTEXT = {"analysis", "trade_call", "compare", "screen"}
_NEEDS_LIGHT_CONTEXT = {"price", "sentiment"}


class Conversation:
    """Stateful chat over the brain, backed by the chat_history table."""

    def history(self, session_id: str, limit: int = MAX_HISTORY_TURNS * 2) -> list[dict[str, str]]:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT role, message FROM chat_history WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"role": r["role"], "message": r["message"]} for r in reversed(rows)]

    def _remember(self, session_id: str, role: str, message: str) -> None:
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO chat_history (session_id, role, message) VALUES (?, ?, ?)",
                (session_id, role, message),
            )
            conn.commit()
        finally:
            conn.close()

    def clear(self, session_id: str) -> int:
        conn = get_db_connection()
        try:
            cur = conn.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ---- context assembly -------------------------------------------------

    def _gather(self, intent: str, symbols: list[str]) -> tuple[str, dict[str, Any]]:
        """Return (rendered context block, structured data for the UI)."""
        if not symbols:
            if intent == "portfolio":
                return self._portfolio_context()
            return "", {}

        blocks: list[str] = []
        data: dict[str, Any] = {"symbols": {}}

        full = intent in _NEEDS_FULL_CONTEXT
        light = intent in _NEEDS_LIGHT_CONTEXT

        for symbol in symbols[: 2 if full else 3]:
            try:
                ctx = build_context(
                    symbol,
                    include_sentiment=full or intent == "sentiment",
                    include_fundamentals=full,
                )
            except Exception as exc:
                log.warning("context build failed for %s: %s", symbol, exc)
                continue

            blocks.append(render_context(ctx))
            data["symbols"][symbol] = {
                "name": display_name(symbol),
                "price": ctx.get("price"),
                "technicals": ctx.get("technicals") if full or light else None,
                "quality": ctx.get("quality"),
                "sentiment": ctx.get("sentiment"),
            }

        return "\n\n".join(blocks), data

    def _portfolio_context(self) -> tuple[str, dict[str, Any]]:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT symbol, quantity, avg_buy_price, investment_strategy FROM portfolio"
            ).fetchall()
        except Exception as exc:
            log.warning("could not read portfolio: %s", exc)
            return "", {}
        finally:
            conn.close()

        if not rows:
            return "PORTFOLIO: empty — nothing recorded yet.", {"portfolio": []}

        lines = ["PORTFOLIO HOLDINGS:"]
        holdings = []
        for row in rows:
            invested = (row["quantity"] or 0) * (row["avg_buy_price"] or 0)
            lines.append(
                f"  {row['symbol']}: {row['quantity']} @ ₹{row['avg_buy_price']} "
                f"= ₹{invested:,.0f} ({row['investment_strategy'] or 'unspecified'})"
            )
            holdings.append(dict(row))
        return "\n".join(lines), {"portfolio": holdings}

    def remember_turn(self, session_id: str, user_message: str, answer: str) -> None:
        """Persist one exchange. Used by the streaming path, which can't call respond()."""
        self._remember(session_id, "user", user_message)
        self._remember(session_id, "assistant", answer)

    def prepare(self, message: str) -> dict[str, Any]:
        """
        Everything needed to prompt the brain, without running inference.

        The streaming endpoint needs this split out: context assembly makes
        network calls and must finish before the first token can be emitted.
        """
        intent = classify(message)
        symbols = resolve(message)
        context_block, data = self._gather(intent, symbols)
        return {
            "intent": intent,
            "symbols": symbols,
            "context_block": context_block,
            "data": data,
        }

    # ---- main entry point -------------------------------------------------

    def respond(
        self,
        message: str,
        session_id: str = "default",
        register: str = "screen",
        remember: bool = True,
    ) -> dict[str, Any]:
        intent = classify(message)
        symbols = resolve(message)

        context_block, data = self._gather(intent, symbols)
        history = self.history(session_id) if remember else []

        parts: list[str] = []
        if history:
            transcript = "\n".join(
                f"{'Operator' if h['role'] == 'user' else settings.assistant_name}: {h['message'][:400]}"
                for h in history[-MAX_HISTORY_TURNS * 2 :]
            )
            parts.append(f"RECENT CONVERSATION:\n{transcript}")

        if context_block:
            parts.append(f"LIVE DATA:\n{context_block}")
        elif symbols:
            parts.append(
                f"NOTE: could not fetch live data for {', '.join(symbols)}. "
                "Say so rather than guessing numbers."
            )

        parts.append(f"OPERATOR ASKS: {message}")
        prompt = "\n\n".join(parts)

        task = {
            "analysis": "deep_analysis",
            "trade_call": "deep_analysis",
            "compare": "deep_analysis",
            "screen": "opportunity",
            "sentiment": "sentiment",
            "education": "education",
            "price": "quick",
        }.get(intent, "chat")

        if register == "voice":
            task = "voice"

        try:
            response = brain.think(
                prompt,
                task=task,
                register=register,
                max_tokens=350 if register == "voice" else 1100,
                use_cache=False,
            )
            text = response.text
            meta = response.as_dict()
        except ProviderError as exc:
            log.error("no provider answered: %s", exc)
            text = (
                "I have no reasoning engine online. Start Ollama (ollama serve), or set OPENROUTER_API_KEY "
                "in backend/.env, and ask me again."
            )
            meta = {"provider": "none", "error": str(exc)}

        if remember:
            self._remember(session_id, "user", message)
            self._remember(session_id, "assistant", text)

        return {
            "response": text,
            "intent": intent,
            "symbols": symbols,
            "data": data or None,
            "meta": meta,
            "follow_up_questions": _follow_ups(intent, symbols),
        }


def _follow_ups(intent: str, symbols: list[str]) -> list[str]:
    """Cheap, deterministic next-step prompts — no extra inference needed."""
    primary = symbols[0] if symbols else None
    if primary:
        return {
            "price": [f"Full analysis of {primary}", f"What's the sentiment on {primary}?"],
            "analysis": [f"Where would you exit {primary}?", f"Compare {primary} with its sector"],
            "trade_call": [f"What's my stop loss on {primary}?", f"Size this position for my risk profile"],
            "sentiment": [f"Does the chart agree on {primary}?", f"Any REIT or bond alternative to {primary}?"],
        }.get(intent, [f"Analyse {primary}", f"Should I hold {primary} long term?"])
    return [
        "Show me my consolidated portfolio",
        "What opportunities are live right now?",
        "Explain REITs and InvITs to me",
    ]


conversation = Conversation()
