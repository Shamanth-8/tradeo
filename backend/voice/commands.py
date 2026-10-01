"""
Voice command routing.

The requirement is that everything is controllable by voice, with a manual
fallback for everything. That second half is what makes the design work: every
command here is a plain function that the UI can also call from a button, so
voice is an *alternative* input to the same actions, never a separate code path
that can drift.

Matching is rule-based first and only falls back to the model when no rule
fires. That ordering matters more than it looks:

  * "start the feed" must work in 5 ms, offline, every single time. Routing it
    through a 20-second CPU inference would make voice control useless.
  * Speech recognisers mangle finance vocabulary constantly, so the patterns
    are written against what recognisers actually produce, not against correct
    spelling — "watch tower", "d han", "margin of safety" as three words.

Anything that isn't a recognised command falls through to the conversational
brain, so the assistant still answers questions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("tradeo.voice.commands")


@dataclass
class CommandResult:
    """What a command did, and what the assistant should say about it."""

    handled: bool
    action: str = ""
    speech: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    navigate: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "action": self.action,
            "speech": self.speech,
            "data": self.data,
            "navigate": self.navigate,
            "error": self.error,
        }


# Recogniser output is unreliable on tickers, so a symbol is only accepted if
# it is a real instrument. This avoids "start the feed" being read as a request
# about a stock called FEED.
#
# `ai.symbols.resolve` is used rather than a ticker scan because people speak
# company names, not tickers — "margin of safety on Infosys" has to reach INFY,
# and a bare ticker match never gets there.
def _resolve_symbol(text: str) -> str | None:
    from ai.symbols import resolve

    hits = resolve(text, limit=1)
    if hits:
        return hits[0]

    # Fall back to a literal ticker, for when someone does say "T C S".
    from market.universe import UNIVERSE

    joined = "".join(re.sub(r"[^A-Z& ]", " ", text.upper()).split())
    for symbol in sorted(UNIVERSE, key=len, reverse=True):
        if len(symbol) >= 3 and symbol in joined:
            return symbol
    return None


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.0f} percent"
    except (TypeError, ValueError):
        return "unknown"


# ---- the commands ----------------------------------------------------------


def cmd_feed_start(text: str, symbol: str | None) -> CommandResult:
    from lowlatency.ingest import feed, synthetic
    from market.universe import scan_list

    synthetic_mode = bool(re.search(r"\b(synthetic|simulate[d]?|test|demo|fake)\b", text))
    symbols = [symbol] if symbol else scan_list(limit=25)

    if synthetic_mode:
        result = synthetic.start(symbols, rate_hz=5.0)
        speech = (f"Synthetic feed running on {result.get('instruments', 0)} instruments."
                  if result.get("started") else
                  f"Could not start: {result.get('reason')}")
    else:
        result = feed.start(symbols)
        if result.get("started"):
            speech = f"Live feed starting on {result.get('instruments', 0)} instruments."
        else:
            speech = (f"Could not start the live feed: {result.get('reason')}. "
                      "Say 'start synthetic feed' to test the pipeline without Dhan.")

    return CommandResult(True, "feed.start", speech, result, navigate="/signals")


def cmd_feed_stop(text: str, symbol: str | None) -> CommandResult:
    from lowlatency.ingest import feed, synthetic

    feed.stop()
    synthetic.stop()
    return CommandResult(True, "feed.stop", "Feed stopped.", {"stopped": True})


def cmd_feed_status(text: str, symbol: str | None) -> CommandResult:
    from lowlatency import ingest

    status = ingest.status()
    live, syn = status["live"], status["synthetic"]
    active = live if live["connected"] else syn

    if not (live["running"] or syn["running"]):
        speech = "No feed is running. Say 'start the feed' to begin."
    else:
        speech = (f"{'Live' if live['connected'] else 'Synthetic'} feed, "
                  f"{active['ticks']} ticks at {active['ticks_per_second']} per second, "
                  f"{active['errors']} errors.")
    return CommandResult(True, "feed.status", speech, status)


def cmd_breaks(text: str, symbol: str | None) -> CommandResult:
    from lowlatency.recon import engine

    rows = engine.open_breaks()
    if not rows:
        speech = "No reconciliation breaks. Everything matches."
    else:
        high = [b for b in rows if b["severity"] == "high"]
        speech = f"{len(rows)} open break{'s' if len(rows) != 1 else ''}"
        if high:
            speech += f", {len(high)} high severity. Worst: {high[0]['detail']}"
        else:
            speech += f". {rows[0]['detail']}"
    return CommandResult(True, "recon.breaks", speech,
                         {"breaks": rows[:10], "stats": engine.summary()})


def cmd_decision(text: str, symbol: str | None) -> CommandResult:
    """
    The one decision — margin of safety and exit plan together.

    Spoken as one answer deliberately, because they come from one computation.
    """
    if not symbol:
        return CommandResult(False, error="which symbol?")

    from pipeline.simulation import simulation

    decision = simulation.get(symbol)
    payload = decision.as_dict()
    mos, plan = payload["margin_of_safety"], payload["exit_plan"]

    parts = [f"{symbol}: {payload['stance']}, conviction {_pct(payload['conviction'])}."]
    if mos:
        parts.append(
            f"Fair value {mos['fair_value']:,.0f} rupees against a price of "
            f"{mos['price']:,.0f}, a margin of safety of {mos['margin_pct']:.0f} percent — "
            f"{mos['verdict']}."
        )
    if plan:
        parts.append(
            f"Stop at {plan['stop_loss']:,.0f}, first target {plan['targets'][0]:,.0f}, "
            f"risk to reward {plan['risk_reward']}."
        )
    parts.append(f"Suggested size {payload['position_size_pct']} percent of the portfolio.")

    return CommandResult(True, "decision", " ".join(parts), payload,
                         navigate=f"/signals?symbol={symbol}")


def cmd_margin_of_safety(text: str, symbol: str | None) -> CommandResult:
    if not symbol:
        return CommandResult(False, error="which symbol?")

    from pipeline.simulation import simulation

    payload = simulation.get(symbol).as_dict()
    mos = payload["margin_of_safety"]
    if not mos:
        return CommandResult(True, "margin_of_safety",
                             f"No valuation available for {symbol}.", payload)

    speech = (f"{symbol} is worth about {mos['fair_value']:,.0f} rupees against a price of "
              f"{mos['price']:,.0f}. That is a margin of safety of {mos['margin_pct']:.0f} "
              f"percent — {mos['verdict']}. Method: {mos['method']}, "
              f"{mos['confidence']} confidence.")
    return CommandResult(True, "margin_of_safety", speech, payload)


def cmd_exit_plan(text: str, symbol: str | None) -> CommandResult:
    if not symbol:
        return CommandResult(False, error="which symbol?")

    from pipeline.simulation import simulation

    payload = simulation.get(symbol).as_dict()
    plan = payload["exit_plan"]
    if not plan:
        return CommandResult(True, "exit_plan", f"No exit plan for {symbol}.", payload)

    targets = ", ".join(f"{t:,.0f}" for t in plan["targets"])
    speech = (f"For {symbol}: stop loss {plan['stop_loss']:,.0f}, targets {targets}. "
              f"Risk {plan['risk_per_share']:,.0f} rupees a share for a reward of "
              f"{plan['reward_per_share']:,.0f}, so {plan['risk_reward']} to one. "
              f"{plan['invalidation']}")
    return CommandResult(True, "exit_plan", speech, payload)


def cmd_watchtower_run(text: str, symbol: str | None) -> CommandResult:
    import threading

    from pipeline.watchtower import watchtower

    if symbol:
        # A full chain on one symbol takes 40-60 seconds — the local model is
        # most of it. Blocking the voice response for that long makes the
        # assistant feel broken, so it is dispatched to a thread and the result
        # is collected from the history endpoint (or arrives on Telegram).
        threading.Thread(
            target=watchtower.run_one,
            args=(symbol,),
            name=f"watchtower-{symbol}",
            daemon=True,
        ).start()
        return CommandResult(
            True, "watchtower.symbol",
            f"Running {symbol} through the full chain — backtest, local model, "
            "then the verifier. It takes about a minute; I will alert you if it "
            "clears every stage.",
            {"symbol": symbol, "dispatched": True},
            navigate="/signals",
        )

    # A full sweep takes minutes, so it is started rather than awaited.
    result = watchtower.start()
    speech = ("Watchtower is already running." if not result.get("started")
              else f"Watchtower started, sweeping every {result['interval_minutes']} minutes.")
    return CommandResult(True, "watchtower.start", speech, result, navigate="/signals")


def cmd_watchtower_status(text: str, symbol: str | None) -> CommandResult:
    from pipeline.watchtower import watchtower

    status = watchtower.status()
    last = status.get("last_run") or {}
    if not last:
        speech = ("Watchtower has not run a sweep yet."
                  if not status["running"] else "Watchtower is running but has not finished a sweep.")
    else:
        speech = (f"Last sweep found {last.get('discovered', 0)} candidates and alerted on "
                  f"{last.get('alerted', 0)}, in {last.get('duration_seconds', 0)} seconds.")
    return CommandResult(True, "watchtower.status", speech, status)


def cmd_portfolio(text: str, symbol: str | None) -> CommandResult:
    from brokers.registry import registry

    consolidated = registry.consolidated_holdings()
    totals = consolidated["totals"]
    if not totals["instruments"]:
        return CommandResult(True, "portfolio",
                             "No holdings recorded yet. Import a statement or connect a broker.",
                             consolidated, navigate="/wealth")

    speech = (f"{totals['instruments']} instruments across {totals['accounts']} account"
              f"{'s' if totals['accounts'] != 1 else ''}. "
              f"Invested {totals['invested']:,.0f} rupees, now worth "
              f"{totals['current_value']:,.0f}, a profit of {totals['pnl']:,.0f} "
              f"or {totals['pnl_percent']:.1f} percent.")
    return CommandResult(True, "portfolio", speech, consolidated, navigate="/wealth")


def cmd_best_picks(text: str, symbol: str | None) -> CommandResult:
    from pipeline import tradeclone

    result = tradeclone.best_picks(limit=3, deep=False)
    picks = result["picks"]
    if not picks:
        return CommandResult(True, "tradeclone.best",
                             "Nothing to rank — no holdings are connected yet.", result)

    named = ", ".join(
        f"{p['symbol']} at {_pct(p['conviction'])} conviction" for p in picks[:3]
    )
    return CommandResult(True, "tradeclone.best",
                         f"Your best positions right now: {named}.", result,
                         navigate="/wealth")


def cmd_postmortem(text: str, symbol: str | None) -> CommandResult:
    if not symbol:
        return CommandResult(False, error="which symbol?")

    from pipeline import tradeclone

    report = tradeclone.post_mortem(symbol, use_verifier=True)
    if report.get("error"):
        return CommandResult(True, "tradeclone.postmortem",
                             f"Cannot review {symbol}: {report['error']}", report)

    review = report.get("verifier_view") or {}
    if review and not review.get("error"):
        speech = f"{symbol}: {review.get('summary') or review.get('what_happened')}. Lesson: {review.get('lesson')}"
    else:
        local = report.get("local_view") or {}
        speech = local.get("text") or f"Reconstructed {report.get('fills', 0)} fills for {symbol}."
    return CommandResult(True, "tradeclone.postmortem", speech[:600], report)


def cmd_whats_new(text: str, symbol: str | None) -> CommandResult:
    from learning import feed

    result = feed.whats_new(limit=5)
    items = result["items"]
    if not items:
        return CommandResult(True, "learning", "Nothing new in the feed right now.", result)

    affecting = result.get("affecting_your_holdings") or []
    speech = f"Top item: {items[0]['title']}."
    if affecting:
        speech += (f" And {len(affecting)} item{'s' if len(affecting) != 1 else ''} "
                   f"affecting what you hold, starting with {affecting[0]['title']}.")
    return CommandResult(True, "learning", speech, result, navigate="/learn")


def cmd_market_status(text: str, symbol: str | None) -> CommandResult:
    from market import hours

    status = hours.status()
    if hours.is_open():
        speech = (f"The market is open, closing in "
                  f"{status.get('closes_in_minutes', 0)} minutes.")
    else:
        phase = str(status.get("phase") or "closed")
        detail = "" if phase == "closed" else f" It is currently {phase}."
        opens = status.get("opens_at")
        if opens:
            detail += f" Next open {str(opens)[11:16]}."
        speech = f"The market is closed.{detail}"
    return CommandResult(True, "market.status", speech, status)


def cmd_pick_of_day(text: str, symbol: str | None) -> CommandResult:
    """The latest pick of the day (rule or the research engine), from Tradeo's own trigger table."""
    from data.storage.database import get_db_connection
    from market import hours
    from ml.flybrain import ranker

    conn = get_db_connection()
    try:
        # opened_at is UTC; the question is about the Indian trading day.
        row = conn.execute(
            "SELECT *, DATE(opened_at, '+330 minutes') AS day FROM autopilot_triggers "
            "WHERE source IN ('daily-pick', 'vibe-trading') ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    model = "the fly brain" if ranker.active() else "Tradeo's scorer"
    if not row:
        return CommandResult(True, "pick_of_day",
                             f"No pick of the day has been made yet. Ranking is by {model}.",
                             {"ranker": model})

    pick = dict(row)
    today = pick["day"] == hours.now_ist().date().isoformat()
    lead = "Today's pick is" if today else f"No pick today. The last one, on {pick['day']}, was"
    speech = (f"{lead} {pick['symbol']}, "
              f"{pick['quantity']} shares at {pick['entry_price']:,.2f} rupees, "
              f"stop {pick['stop_loss']:,.2f}, target {pick['target']:,.2f}. ")
    if pick["state"] == "open":
        speech += "Still open."
    else:
        speech += f"Closed at {pick['exit_price']:,.2f}, {pick['realised_pnl']:+,.0f} rupees after costs."
    speech += f" Ranked by {model}."
    return CommandResult(True, "pick_of_day", " ".join(speech.split()), pick, navigate="/autopilot")


def cmd_fly_brain(text: str, symbol: str | None) -> CommandResult:
    """The fly brain's current suggestions and its paper record."""
    from pipeline import fly_rl_trader

    try:
        picks = fly_rl_trader.suggestions()
    except RuntimeError as exc:
        return CommandResult(True, "fly_brain", str(exc), {})
    agent = picks["agent"]
    top = picks["top"][:3]
    names = ", ".join(r["symbol"] for r in top)
    if any(r["tradeable"] for r in top):
        lead = f"The fly brain's top picks are {names}."
    else:
        lead = (f"The fly brain expects every stock to lose after costs right now, so it is "
                f"mostly staying in cash. Its least bad are {names}.")
    record = (f" On paper it has closed {agent['live_closed_trades']} trades, "
              f"{agent['live_wins']} winners, {agent['live_realised_pnl']:+,.0f} rupees.")
    data = {k: v for k, v in picks.items() if k not in ("states", "symbols", "all")}
    return CommandResult(True, "fly_brain", lead + record, data, navigate="/autopilot")


def cmd_paper_trade(text: str, symbol: str | None) -> CommandResult:
    """Scan (Yahoo Finance), plan entry/stop/target, and let the fly brain paper trade."""
    from pipeline import fly_rl_trader

    try:
        result = fly_rl_trader.trade_now()
    except RuntimeError as exc:
        return CommandResult(True, "paper_trade", str(exc), {})
    if not result.get("ok"):
        return CommandResult(True, "paper_trade", result["error"], result)

    lines = [f"Scanned {result['scanned']} stocks ({result['source']}, Yahoo Finance data): "
             f"{result['bullish']} bullish."]
    if not result["bullish"]:
        best = ", ".join(f"{t['symbol']} {t['score']}" for t in result["top_scores"][:3])
        lines.append(f"Nothing scored 60+ right now. Highest: {best}. No trade.")
    for d in result["handoff"].get("decisions", []):
        if d.get("trade"):
            t = d["trade"]
            lines.append(f"BOUGHT {d['symbol']} x{t['quantity']} at {t['entry_price']:,.2f}, "
                         f"stop {t['stop_loss']:,.2f}, target {t['target']:,.2f} "
                         f"(fly {d.get('fly_percentile')}th percentile).")
        else:
            plan = d.get("plan")
            levels = (f" Plan was entry {plan['entry']:,.2f}, stop {plan['stop']:,.2f}, "
                      f"target {plan['target']:,.2f}.") if plan else ""
            lines.append(f"{d['decision'].upper()} {d['symbol']} (score {d['watchtower_score']}): "
                         f"{d.get('why', '')}.{levels}")
    lines.append(fly_rl_trader._score_line())
    return CommandResult(True, "paper_trade", " ".join(lines), result, navigate=None)


def cmd_help(text: str, symbol: str | None) -> CommandResult:
    speech = (
        "You can say: start the feed, feed status, show breaks, "
        "what is the margin of safety on Infosys, exit plan for TCS, "
        "run watchtower on Reliance, what's new, my portfolio, today's pick, fly brain picks, best picks, "
        "or why did HDFC Bank lose money."
    )
    return CommandResult(True, "help", speech, {"commands": [p[0] for p in PATTERNS]})


# ---- routing table ---------------------------------------------------------
#
# Order matters: the first pattern that matches wins, so specific phrasings
# come before general ones. Patterns are matched against lowercased text with
# recogniser artefacts already normalised.

PATTERNS: list[tuple[str, str, Callable[[str, str | None], CommandResult]]] = [
    ("feed.stop",        r"\b(stop|kill|halt|pause)\b.*\b(feed|ticks?|stream|data)\b", cmd_feed_stop),
    ("feed.start",       r"\b(start|begin|launch|run|resume)\b.*\b(feed|ticks?|stream|market data)\b", cmd_feed_start),
    ("feed.status",      r"\b(feed|tick|latency|pipeline)\b.*\b(status|health|running|alive|how)\b", cmd_feed_status),
    ("feed.status",      r"\b(is|are)\b.*\b(feed|ticks?)\b.*\b(working|running|live|on)\b", cmd_feed_status),

    ("recon.breaks",     r"\b(break|breaks|reconcil\w*|mismatch\w*|unmatched)\b", cmd_breaks),

    ("margin_of_safety", r"\b(margin of safety|margin safety|fair value|undervalued|overvalued|what.{0,10}worth)\b", cmd_margin_of_safety),
    ("exit_plan",        r"\b(exit|stop loss|stoploss|target|where.{0,15}(sell|get out)|invalidat\w*)\b", cmd_exit_plan),
    ("decision",         r"\b(decision|verdict|what should i do|should i (buy|sell|hold)|call on)\b", cmd_decision),

    ("watchtower.status", r"\b(watch ?tower|scanner|sweep)\b.*\b(status|found|result|last)\b", cmd_watchtower_status),
    ("watchtower.run",   r"\b(watch ?tower|scan|sweep|analys[ei]|analyz[ei]|check)\b", cmd_watchtower_run),

    ("tradeclone.postmortem", r"\b(why|what went wrong|post ?mortem|lost money|losing|mistake)\b", cmd_postmortem),
    ("paper_trade",      r"\b(paper ?trad\w*|trade (the |today.?s |these |some )?(picks?|stocks)|picked stocks|place (paper )?trades?|start trading)\b", cmd_paper_trade),
    ("fly_brain",        r"\b(fly|flight|fruit ?fly)\b.*\b(brain|picks?|suggest\w*|trad\w*)\b", cmd_fly_brain),
    ("pick_of_day",      r"\b(pick of the day|today.?s pick|daily pick|vibe ?(trading)? pick)\b", cmd_pick_of_day),
    ("tradeclone.best",  r"\b(best|top)\b.*\b(pick|holding|position|stock)s?\b", cmd_best_picks),
    ("portfolio",        r"\b(portfolio|holdings?|my stocks|net worth|how am i doing|p ?and ?l|pnl)\b", cmd_portfolio),

    ("learning",         r"\b(what.?s new|new (thing|instrument|listing)|learn|circular|sebi|ipo|upcoming)\b", cmd_whats_new),
    ("market.status",    r"\b(market)\b.*\b(open|closed|status|hours)\b", cmd_market_status),
    ("help",             r"\b(help|what can (you|i) (do|say)|commands|options)\b", cmd_help),
]

# Recogniser artefacts, applied before matching. These are what Web Speech
# actually produces for Indian finance vocabulary.
NORMALISE = [
    (r"\bwatch\s+tower\b", "watchtower"),
    (r"\bd\s*han\b", "dhan"),
    (r"\bdee\s*mat\b", "demat"),
    (r"\bin\s*vit\b", "invit"),
    (r"\bree\s*it\b", "reit"),
    (r"\bg\s*sec\b", "gsec"),
    (r"\bstop\s+loss\b", "stoploss"),
    (r"\bp\s*&\s*l\b", "pnl"),
]


def route(text: str) -> CommandResult:
    """
    Map an utterance to an action.

    Returns `handled=False` when nothing matched, which is the caller's cue to
    fall through to the conversational brain rather than apologising.
    """
    if not text or not text.strip():
        return CommandResult(False, error="empty")

    lowered = text.lower().strip()
    for pattern, replacement in NORMALISE:
        lowered = re.sub(pattern, replacement, lowered)

    symbol = _resolve_symbol(text)

    for action, pattern, handler in PATTERNS:
        if not re.search(pattern, lowered):
            continue
        try:
            result = handler(lowered, symbol)
        except Exception as exc:
            log.warning("voice command %s failed: %s", action, exc, exc_info=True)
            return CommandResult(
                True, action,
                f"That failed: {str(exc)[:160]}",
                {}, error=str(exc)[:300],
            )

        if result.handled:
            log.info("voice: '%s' -> %s%s", text[:60], action,
                     f" [{symbol}]" if symbol else "")
            return result

        # A handler can decline — usually because it needs a symbol it did not
        # get. Ask for the missing piece rather than falling through silently.
        if result.error == "which symbol?":
            return CommandResult(
                True, action,
                "Which stock did you mean? Say it with the name, for example "
                "'margin of safety on Infosys'.",
                {}, error="symbol_required",
            )

    return CommandResult(False, error="no_match")


def describe() -> dict[str, Any]:
    """
    Every voice command, for the UI to render as buttons.

    This is the manual fallback: the same actions, same code path, no
    microphone required.
    """
    examples = {
        "feed.start": "Start the feed",
        "feed.stop": "Stop the feed",
        "feed.status": "Feed status",
        "recon.breaks": "Show breaks",
        "margin_of_safety": "Margin of safety on TCS",
        "exit_plan": "Exit plan for TCS",
        "decision": "What should I do about TCS",
        "watchtower.run": "Run watchtower",
        "watchtower.status": "Watchtower status",
        "paper_trade": "Paper trade the picks",
        "fly_brain": "What does the fly brain pick",
        "pick_of_day": "What's today's pick",
        "tradeclone.best": "Best picks",
        "tradeclone.postmortem": "Why did TCS lose money",
        "portfolio": "My portfolio",
        "learning": "What's new",
        "market.status": "Is the market open",
        "help": "Help",
    }
    seen: list[dict[str, str]] = []
    for action, _pattern, _handler in PATTERNS:
        if any(entry["action"] == action for entry in seen):
            continue
        seen.append({
            "action": action,
            "example": examples.get(action, action),
            "needs_symbol": action in {
                "margin_of_safety", "exit_plan", "decision", "tradeclone.postmortem",
            },
        })
    return {"commands": seen}
