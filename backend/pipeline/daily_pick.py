"""
The pick of the day, chosen by rule rather than by a model.

A 3B model on this machine could read Tradeo's candidates but not reliably
act on them (twice, live, it wandered off into unrelated web searches before
choosing), so the choice is arithmetic: the highest-ranked bullish stock that
passes every check below. The reason recorded is the scorer's own signals;
see `_reason` for why the local model does not write it.

Be clear about what this is. The ranking is Tradeo's scorer — or the fly
brain, if it ever passes its gate — and the scorer measured no out-of-sample
edge (ml/flybrain report). Trading its top pick on paper is how that gets
settled on live data. It is a phase-1 test, not a strategy that has earned money.

`plan_long` is shared with the Vibe-Trading bridge, so a pick from either
source meets the same checks.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("tradeo.daily_pick")

SOURCE = "daily-pick"
# Switch, minimum score, picks per day, size and time are set on the
# Autopilot page and stored by autopilot/agents.py. Off by default.

# Target distance when none is supplied, in multiples of the risk taken.
DEFAULT_REWARD_MULTIPLE = 2.0


def plan_long(signal, stop_hint: float | None = None,
              target_hint: float | None = None) -> dict[str, Any]:
    """Entry, stop and target for a long in `signal`, or why there can't be one."""
    from autopilot import costs
    from brokers.quotes import broker_ltp
    from pipeline.watchtower import derive_stop

    snapshot = signal.snapshot or {}

    # A connected broker's own price beats the free quote the score was built on.
    quote = broker_ltp(signal.symbol)
    entry, price_source = quote if quote else (float(snapshot.get("price") or 0), "free-quote")
    if entry <= 0:
        return {"ok": False, "error": f"no live price for {signal.symbol}"}

    if costs.at_upper_circuit(snapshot.get("change_percent")):
        return {"ok": False, "error": (
            f"{signal.symbol} is up {snapshot['change_percent']:.1f}% — at its upper "
            "circuit, a buy would not fill")}

    atr = float(snapshot.get("atr") or 0) or None
    stop, stop_source, problem = derive_stop(entry, atr, stop_hint)
    notes = [problem] if problem else []
    if stop is None or stop >= entry:
        return {"ok": False, "error": "no usable stop below entry", "notes": notes}

    target = target_hint
    if not target or target <= entry:
        if target:
            notes.append(f"supplied target ₹{target:,.2f} is not above entry — replaced")
        target = entry + DEFAULT_REWARD_MULTIPLE * (entry - stop)

    return {"ok": True, "entry": entry, "price_source": price_source, "stop": stop,
            "stop_source": stop_source, "target": target, "notes": notes}


def _picked_today() -> list[dict[str, Any]]:
    from data.storage.database import get_db_connection
    from market import hours

    conn = get_db_connection()
    try:
        # opened_at is UTC; the day that matters is the Indian trading day.
        rows = conn.execute(
            "SELECT * FROM autopilot_triggers WHERE source = ? "
            "AND DATE(opened_at, '+330 minutes') = ? ORDER BY id",
            (SOURCE, hours.now_ist().date().isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _reason(signal) -> str:
    """
    Why the rule chose it, from the scorer's own signals.

    Not model-written: on the first live pick the local 3B model described a
    bullish, rule-chosen stock as "in a bearish trading range" and
    "undervalued" — prose that contradicts the facts it was handed, and it
    would have been stored as the trade's reason.
    """
    signals = "; ".join(list(signal.triggers)[:4]) or "no individual signal fired"
    return f"rule: top-ranked bullish, score {signal.score:.1f} — {signals}"[:300]


def run(force: bool = False) -> dict[str, Any]:
    """Choose and paper-trade today's picks, by the rules set on the Autopilot page."""
    from api.routes.bridge import refresh_candidates
    from autopilot import agents, store, triggers
    from market import hours
    from realtime.scanner import _score_one

    rules = agents.daily_pick_settings()
    min_score, wanted = float(rules["min_score"]), int(rules["picks_per_day"])
    if not rules["enabled"] and not force:
        return {"ok": False, "error": "the Daily pick agent is switched off (Autopilot page)"}
    if not hours.is_open():
        return {"ok": False, "error": f"NSE is {hours.phase()} — no pick outside the session"}
    existing = _picked_today()
    if len(existing) >= wanted and not force:
        return {"ok": False, "skipped": True,
                "error": f"already picked today: {', '.join(e['symbol'] for e in existing)}",
                "pick": existing[-1]}

    ranking = refresh_candidates()
    held = set(triggers.open_symbols())
    considered: list[dict[str, Any]] = []
    opened: list[dict[str, Any]] = []
    equity = float(store.paper_account()["equity"])

    for row in ranking["rows"]:
        if len(existing) + len(opened) >= wanted:
            break
        symbol = row["symbol"]
        if row.get("direction") != "bullish" or row.get("score", 0) < min_score:
            continue
        if symbol in held:
            considered.append({"symbol": symbol, "skipped": "already holding"})
            continue

        # Rescore now: the ranking may be up to 15 minutes old, the fill is not.
        _, signal, error = _score_one(symbol)
        if not signal or signal.direction != "bullish":
            considered.append({"symbol": symbol, "skipped": error or "no longer bullish"})
            continue
        plan = plan_long(signal)
        if not plan["ok"]:
            considered.append({"symbol": symbol, "skipped": plan["error"]})
            continue
        from pipeline import pretrade

        guard = pretrade.check(symbol, signal.score, plan)
        if not guard["ok"]:
            considered.append({"symbol": symbol, "skipped": guard["why"]})
            continue

        reason = f"{_reason(signal)} | news: {guard['why']}"[:400]
        quantity = int(equity * float(rules["position_pct"]) / 100 // plan["entry"])
        # `arm`, not `arm_from_signal`: the conviction bar there is for the
        # model-scored watchtower. This pick's bar is min_score, applied above.
        result = triggers.arm(
            symbol=symbol, entry_price=plan["entry"], stop_loss=plan["stop"],
            target=plan["target"], quantity=quantity or None, conviction=signal.score,
            source=SOURCE, reason=reason,
        )
        result.update({"ranker": ranking["ranker"], "score": round(signal.score, 1),
                       "price_source": plan["price_source"], "stop_source": plan["stop_source"],
                       "reason": reason, "notes": plan["notes"]})
        if result.get("ok"):
            _announce(result)
            opened.append(result)
            held.add(symbol)
        else:
            considered.append({"symbol": symbol, "skipped": result.get("error")})

    if opened:
        return {"ok": True, "opened": opened, "symbol": ", ".join(o["symbol"] for o in opened),
                "considered": considered}
    return {"ok": False, "error": f"no bullish stock scored {min_score:g}+ and passed the checks",
            "ranker": ranking["ranker"], "considered": considered}


def _announce(result: dict[str, Any]) -> None:
    try:
        from integrations.telegram import bot

        if bot.enabled:
            bot.send(
                f"<b>Pick of the day: {result['symbol']}</b> (paper)\n"
                f"{result['quantity']} @ ₹{result['entry_price']:,.2f} · stop ₹{result['stop_loss']:,.2f}"
                f" · target ₹{result['target']:,.2f}\n{result['reason']}"
            )
    except Exception as exc:
        log.warning("could not announce the pick: %s", exc)


# Days already tried, so a refused day is not retried every minute.
_attempted: dict = {}


def start() -> None:
    """Fire once each trading day at the set time IST, when switched on."""
    from autopilot import agents
    from market import hours

    def loop() -> None:
        while True:
            rules = agents.daily_pick_settings()
            now = hours.now_ist()
            run_at = tuple(int(x) for x in rules["run_at"].split(":"))
            due = (rules["enabled"] and hours.is_open() and (now.hour, now.minute) >= run_at
                   and now.date() not in _attempted)
            if due and len(_picked_today()) < int(rules["picks_per_day"]):
                try:
                    outcome = run()
                    log.info("pick of the day: %s", outcome.get("symbol") or outcome.get("error"))
                except Exception as exc:
                    log.warning("pick of the day failed: %s", exc)
                # One attempt per day: a refused day stays refused rather than
                # retrying into worse prices all afternoon.
                _attempted[now.date()] = True
            time.sleep(60)

    threading.Thread(target=loop, name="daily-pick", daemon=True).start()
