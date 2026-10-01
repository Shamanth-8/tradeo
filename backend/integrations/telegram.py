"""
Telegram bot — Tradeo's channel to the operator when they're away from the HUD.

Deliberately dependency-free: the Bot API is plain HTTPS, so this is `requests`
plus a long-polling loop in a daemon thread. Two directions:

  push  — the scanner hands it opportunities as they're found
  pull  — the operator asks questions and runs commands from their phone

Anything the operator types that isn't a command goes to the same brain the web
UI uses, so the answers are identical wherever they ask.
"""

from __future__ import annotations

import html
import logging
import threading

from typing import Any, Callable

import requests

from core.config import settings
from realtime import store

log = logging.getLogger("tradeo.telegram")

API_ROOT = "https://api.telegram.org"
POLL_TIMEOUT = 30  # seconds Telegram holds the long-poll open
MAX_MESSAGE_CHARS = 4000  # Telegram's hard limit is 4096


class TelegramBot:
    """Long-polling Telegram client with a small command set."""

    def __init__(self, token: str | None = None, default_chat_id: str | None = None) -> None:
        self.token = token or settings.telegram_bot_token
        self.default_chat_id = default_chat_id or settings.telegram_chat_id
        self._offset: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._commands: dict[str, Callable[[str, list[str]], str]] = {}
        self._register_commands()

    # ---- plumbing ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self.token}/{method}"

    def _call(self, method: str, http_timeout: int = 20, **params: Any) -> dict[str, Any] | None:
        """
        Call a Bot API method. `http_timeout` is ours; everything else in
        **params goes to Telegram — including its own `timeout` for long polling.
        """
        if not self.enabled:
            return None
        try:
            resp = requests.post(self._url(method), json=params, timeout=http_timeout)
            data = resp.json()
            if not data.get("ok"):
                log.warning("telegram %s failed: %s", method, data.get("description"))
                return None
            return data.get("result")
        except requests.RequestException as exc:
            log.warning("telegram %s error: %s", method, exc)
            return None
        except ValueError as exc:
            log.warning("telegram %s returned non-JSON: %s", method, exc)
            return None

    def verify(self) -> dict[str, Any]:
        """Check the token works and report who we are."""
        if not self.enabled:
            return {"configured": False, "error": "TELEGRAM_BOT_TOKEN not set"}
        me = self._call("getMe", http_timeout=10)
        if not me:
            return {"configured": True, "valid": False, "error": "token rejected by Telegram"}
        return {
            "configured": True,
            "valid": True,
            "username": me.get("username"),
            "name": me.get("first_name"),
            "subscribers": len(store.active_subscribers()),
            "polling": self.is_running,
        }

    # ---- sending ----------------------------------------------------------

    def discover_chat_id(self) -> str | None:
        """
        Learn the chat ID from whoever last messaged the bot.

        A bot cannot start a conversation on Telegram — the human has to send
        the first message. That makes "find your chat ID" the step everyone
        gets stuck on, so once anyone has messaged the bot we pick it up
        automatically and persist it.
        """
        if not self.enabled:
            return None

        updates = self._call("getUpdates", http_timeout=10, limit=10) or []
        for update in reversed(updates):
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id:
                self.default_chat_id = str(chat_id)
                try:
                    from core import credentials

                    credentials.save({"TELEGRAM_CHAT_ID": str(chat_id)})
                except Exception as exc:
                    log.warning("could not persist discovered chat id: %s", exc)
                log.info("telegram chat id discovered: %s", chat_id)
                return str(chat_id)
        return None

    def send(self, text: str, chat_id: str | None = None, silent: bool = False) -> bool:
        chat_id = chat_id or self.default_chat_id

        # Fall back to anyone who has subscribed via /start, then to whoever
        # last messaged the bot. Without this, a perfectly working bot with a
        # live subscriber silently drops every alert because no explicit
        # TELEGRAM_CHAT_ID was ever set.
        if not chat_id:
            subscribers = store.active_subscribers()
            if subscribers:
                chat_id = str(subscribers[0]["chat_id"])
            else:
                chat_id = self.discover_chat_id()

        if not self.enabled or not chat_id:
            return False

        ok = True
        for chunk in _split(text, MAX_MESSAGE_CHARS):
            result = self._call(
                "sendMessage",
                chat_id=chat_id,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
                disable_notification=silent,
            )
            ok = ok and result is not None
        return ok

    def broadcast(self, text: str, min_conviction: int = 0, silent: bool = False) -> int:
        """Send to every subscriber whose threshold this clears."""
        recipients = store.active_subscribers()
        if not recipients and self.default_chat_id:
            recipients = [{"chat_id": self.default_chat_id, "min_conviction": 0}]

        sent = 0
        for subscriber in recipients:
            if min_conviction and subscriber.get("min_conviction", 0) > min_conviction:
                continue
            if self.send(text, chat_id=subscriber["chat_id"], silent=silent):
                sent += 1
        return sent

    def push_opportunity(self, opportunity: dict[str, Any]) -> int:
        """Format and broadcast one scanner hit."""
        text = format_opportunity(opportunity)
        conviction = int(opportunity.get("conviction") or 0)
        sent = self.broadcast(text, min_conviction=conviction)

        store.log_notification(
            channel="telegram",
            kind="opportunity",
            title=f"{opportunity.get('symbol')} — {opportunity.get('verdict')}",
            body=text,
            payload={"symbol": opportunity.get("symbol"), "id": opportunity.get("id")},
            delivered=sent > 0,
        )
        if sent and opportunity.get("id"):
            store.mark_notified([opportunity["id"]])
        return sent

    # ---- receiving --------------------------------------------------------

    def _register_commands(self) -> None:
        self._commands = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "stop": self._cmd_stop,
            "signals": self._cmd_signals,
            "scan": self._cmd_scan,
            "price": self._cmd_price,
            "watch": self._cmd_watch,
            "unwatch": self._cmd_unwatch,
            "watchlist": self._cmd_watchlist,
            "portfolio": self._cmd_portfolio,
            "status": self._cmd_status,
        }

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = str(chat.get("id"))
        text = (message.get("text") or "").strip()
        if not text:
            return

        username = chat.get("username") or chat.get("first_name")
        log.info("telegram message from %s: %s", username, text[:80])

        if text.startswith("/"):
            parts = text[1:].split()
            command = parts[0].split("@")[0].lower()  # strip @BotName in groups
            args = parts[1:]
            handler = self._commands.get(command)
            reply = (
                handler(chat_id, args)
                if handler
                else f"Unknown command /{html.escape(command)}. Try /help."
            )
        else:
            reply = self._ask_brain(text, chat_id)

        self.send(reply, chat_id=chat_id)

    def _ask_brain(self, text: str, chat_id: str) -> str:
        """Free-form question — same engine as the web UI."""
        try:
            from ai.conversation import conversation

            result = conversation.respond(text, session_id=f"telegram:{chat_id}")
            return _to_telegram_html(result["response"])
        except Exception as exc:
            log.error("brain call from telegram failed: %s", exc)
            return "I couldn't reach my reasoning engine just now. Try again shortly."

    # ---- commands ---------------------------------------------------------

    def _cmd_start(self, chat_id: str, args: list[str]) -> str:
        store.upsert_subscriber(chat_id)
        return (
            f"<b>{html.escape(settings.assistant_name)} online.</b>\n\n"
            "You're subscribed to live opportunity alerts across Indian equities, "
            "ETFs, REITs, InvITs and bonds.\n\n"
            "Ask me anything in plain English, or use /help for commands."
        )

    def _cmd_help(self, chat_id: str, args: list[str]) -> str:
        return (
            "<b>Commands</b>\n"
            "/signals — latest opportunities found\n"
            "/scan — sweep the universe now\n"
            "/price SYMBOL — live quote\n"
            "/watch SYMBOL — add to watchlist\n"
            "/unwatch SYMBOL — remove\n"
            "/watchlist — what I'm watching\n"
            "/portfolio — your holdings\n"
            "/status — market and system state\n"
            "/stop — pause alerts\n\n"
            "Or just ask: <i>\"is Embassy REIT worth holding?\"</i>"
        )

    def _cmd_stop(self, chat_id: str, args: list[str]) -> str:
        store.unsubscribe(chat_id)
        return "Alerts paused. Send /start to resume."

    def _cmd_signals(self, chat_id: str, args: list[str]) -> str:
        opportunities = store.recent_opportunities(limit=5, since_hours=48)
        if not opportunities:
            return "Nothing on the board from the last 48 hours. Run /scan to sweep now."
        return "\n\n".join(format_opportunity(o, compact=True) for o in opportunities)

    def _cmd_scan(self, chat_id: str, args: list[str]) -> str:
        from realtime.scanner import scan

        self.send("Sweeping the universe — this takes a minute.", chat_id=chat_id)
        result = scan(limit=40, deep=True, trigger="telegram")
        if not result.opportunities:
            return (
                f"Scanned {len(result.signals)} instruments in {result.duration_seconds:.0f}s. "
                f"{len(result.shortlisted)} shortlisted, nothing convincing enough to flag."
            )
        header = (
            f"<b>{len(result.opportunities)} opportunities</b> "
            f"from {len(result.signals)} instruments\n"
        )
        return header + "\n\n".join(
            format_opportunity(o, compact=True) for o in result.opportunities
        )

    def _cmd_price(self, chat_id: str, args: list[str]) -> str:
        if not args:
            return "Usage: /price TCS"

        from ai.symbols import display_name, resolve_one
        from data.fetchers.stock_fetcher import stock_fetcher

        symbol = resolve_one(" ".join(args)) or args[0].upper()
        quote = stock_fetcher.get_live_price(symbol)
        if quote.get("error"):
            return f"No quote for {html.escape(symbol)}."

        change = quote.get("change_percent", 0)
        arrow = "▲" if change >= 0 else "▼"
        return (
            f"<b>{html.escape(display_name(symbol))}</b> ({html.escape(symbol)})\n"
            f"₹{quote.get('price')}  {arrow} {change:+.2f}%\n"
            f"Day ₹{quote.get('low')}–₹{quote.get('high')} · Vol {quote.get('volume', 0):,}"
        )

    def _cmd_watch(self, chat_id: str, args: list[str]) -> str:
        if not args:
            return "Usage: /watch RELIANCE"
        from ai.symbols import display_name, resolve_one

        symbol = resolve_one(" ".join(args)) or args[0].upper()
        store.add_to_watchlist(symbol)
        return f"Watching <b>{html.escape(display_name(symbol))}</b>. I'll flag anything material."

    def _cmd_unwatch(self, chat_id: str, args: list[str]) -> str:
        if not args:
            return "Usage: /unwatch RELIANCE"
        from ai.symbols import resolve_one

        symbol = resolve_one(" ".join(args)) or args[0].upper()
        removed = store.remove_from_watchlist(symbol)
        return f"Removed {html.escape(symbol)}." if removed else f"{html.escape(symbol)} wasn't on the list."

    def _cmd_watchlist(self, chat_id: str, args: list[str]) -> str:
        watched = store.get_watchlist()
        if not watched:
            return "Watchlist is empty. Add one with /watch TCS."
        from ai.symbols import display_name

        lines = [f"• <b>{html.escape(w['symbol'])}</b> — {html.escape(display_name(w['symbol']))}" for w in watched]
        return "<b>Watchlist</b>\n" + "\n".join(lines)

    def _cmd_portfolio(self, chat_id: str, args: list[str]) -> str:
        """The consolidated view — every broker and depository in one list."""
        from brokers import registry

        consolidated = registry.consolidated_holdings()
        holdings = consolidated["holdings"]
        if not holdings:
            return (
                "No holdings found. Add them manually, import a CDSL/NSDL statement, "
                "or connect Angel One in backend/.env."
            )

        lines: list[str] = []
        for row in holdings[:20]:
            arrow = "▲" if row["pnl_percent"] >= 0 else "▼"
            suffix = f" ×{row['held_across']}" if row["held_across"] > 1 else ""
            lines.append(
                f"• <b>{html.escape(row['symbol'])}</b>{suffix} {row['quantity']:g} @ ₹{row['avg_price']:,.2f}"
                f" → ₹{row['ltp']:,.2f}  {arrow} {row['pnl_percent']:+.1f}%"
            )

        totals = consolidated["totals"]
        footer = (
            f"\n\nInvested ₹{totals['invested']:,.0f} · Now ₹{totals['current_value']:,.0f} · "
            f"<b>{totals['pnl']:+,.0f} ({totals['pnl_percent']:+.1f}%)</b>\n"
            f"<i>{totals['instruments']} instruments across {totals['accounts']} account(s)</i>"
        )
        return "<b>Consolidated Portfolio</b>\n" + "\n".join(lines) + footer

    def _cmd_status(self, chat_id: str, args: list[str]) -> str:
        from ai.brain import brain
        from market.hours import status as market_status

        market = market_status()
        ai = brain.status()
        scans = store.last_scans(limit=1)

        lines = [
            f"<b>Market</b>: {market['phase']} ({market['session']})",
            f"<b>Brain</b>: {ai['mode']} · local {'up' if ai['local']['available'] else 'down'}"
            f" · cloud {'up' if ai['cloud']['available'] else 'not configured'}",
        ]
        if market.get("closes_in_minutes") is not None:
            lines.append(f"Closes in {market['closes_in_minutes']} min")
        elif market.get("opens_in_minutes") is not None:
            lines.append(f"Opens in {market['opens_in_minutes']} min")
        if scans:
            last = scans[0]
            lines.append(
                f"<b>Last scan</b>: {last['scanned']} scanned, "
                f"{last['published']} published ({last['trigger']})"
            )
        return "\n".join(lines)

    # ---- polling loop -----------------------------------------------------

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _poll_loop(self) -> None:
        log.info("telegram polling started")
        while not self._stop.is_set():
            try:
                updates = self._call(
                    "getUpdates",
                    http_timeout=POLL_TIMEOUT + 10,
                    offset=self._offset,
                    timeout=POLL_TIMEOUT,
                ) or []
                for update in updates:
                    self._offset = update["update_id"] + 1
                    try:
                        self._handle_update(update)
                    except Exception as exc:
                        log.error("failed handling update: %s", exc)
            except Exception as exc:
                log.error("telegram poll error: %s", exc)
                self._stop.wait(5)
        log.info("telegram polling stopped")

    def start_polling(self) -> bool:
        if not self.enabled:
            log.info("telegram disabled — no bot token")
            return False
        if self.is_running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="telegram-poll", daemon=True)
        self._thread.start()
        return True

    def stop_polling(self) -> None:
        self._stop.set()


# ---- formatting ------------------------------------------------------------


def _split(text: str, size: int) -> list[str]:
    """Split on line boundaries so HTML tags aren't cut in half."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _to_telegram_html(markdown: str) -> str:
    """
    Convert the brain's markdown to the small HTML subset Telegram accepts.

    Telegram rejects the whole message on a malformed tag, so escape first and
    only then introduce the handful of tags we actually want.
    """
    import re

    text = html.escape(markdown)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)
    return text


VERDICT_ICON = {
    "strong_buy": "🟢🟢",
    "buy": "🟢",
    "watch": "🟡",
    "avoid": "🔴",
    "exit": "🔴🔴",
}


def format_opportunity(opportunity: dict[str, Any], compact: bool = False) -> str:
    """Render one opportunity as a Telegram message."""
    symbol = html.escape(str(opportunity.get("symbol", "?")))
    verdict = str(opportunity.get("verdict") or "watch").lower()
    icon = VERDICT_ICON.get(verdict, "🟡")
    conviction = opportunity.get("conviction") or 0
    snapshot = opportunity.get("snapshot") or {}
    name = html.escape(str(snapshot.get("name") or symbol))
    asset_class = opportunity.get("asset_class") or snapshot.get("asset_class") or ""

    header = (
        f"{icon} <b>{symbol}</b> — {verdict.replace('_', ' ').upper()} "
        f"({conviction}% conviction)\n"
        f"<i>{name}</i>"
    )
    if asset_class and asset_class != "equity":
        header += f" · {html.escape(str(asset_class).upper())}"

    price = snapshot.get("price")
    change = snapshot.get("change_percent")
    if price:
        arrow = "▲" if (change or 0) >= 0 else "▼"
        header += f"\n₹{price:,.2f}"
        if change is not None:
            header += f"  {arrow} {change:+.2f}%"

    if compact:
        thesis = opportunity.get("thesis") or ""
        if thesis:
            header += f"\n{_to_telegram_html(str(thesis)[:200])}"
        return header

    parts = [header]

    if opportunity.get("thesis"):
        parts.append(_to_telegram_html(str(opportunity["thesis"])))

    reasons = opportunity.get("reasons") or []
    if reasons:
        parts.append("<b>Why</b>\n" + "\n".join(f"• {_to_telegram_html(str(r))}" for r in reasons[:4]))

    levels: list[str] = []
    if opportunity.get("entry_zone"):
        levels.append(f"Entry {html.escape(str(opportunity['entry_zone']))}")
    if opportunity.get("stop_loss"):
        levels.append(f"Stop ₹{opportunity['stop_loss']}")
    targets = opportunity.get("targets") or []
    if targets:
        levels.append("Targets " + ", ".join(f"₹{t}" for t in targets[:3]))
    if levels:
        parts.append("<b>Levels</b>\n" + " · ".join(levels))

    risks = opportunity.get("risks") or []
    if risks:
        parts.append("<b>Risks</b>\n" + "\n".join(f"• {_to_telegram_html(str(r))}" for r in risks[:3]))

    if opportunity.get("invalidation"):
        parts.append(f"<b>Wrong if</b>: {_to_telegram_html(str(opportunity['invalidation']))}")

    triggers = opportunity.get("triggers") or []
    if triggers:
        parts.append("<i>" + " · ".join(html.escape(str(t)) for t in triggers[:3]) + "</i>")

    parts.append("<i>Analysis, not investment advice.</i>")
    return "\n\n".join(parts)


bot = TelegramBot()
