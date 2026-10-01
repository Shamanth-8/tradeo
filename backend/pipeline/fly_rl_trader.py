"""
Watchtower finds the openings; the fly brain decides which ones to trade.

    watchtower sweep ──► bullish openings ──► fly brain ──► approve / veto
                                                  │              │
                    dopamine on every close ◄─────┴── paper bracket (fly-rl)

1. Handoff. After every watchtower sweep (every 30 minutes while NSE is open),
   each bullish opening that survived the backtest and the local model is
   handed here, whether or not it cleared the Telegram alert floor.
2. Decision. The mushroom body sees the stock's features and values it. An
   opening is approved when the fly values it above the median of its
   66-stock universe today, a relative bar because after costs the brain
   expects every stock to lose a little (see rl_report.json). A vetoed
   opening is still taken EXPLORE of the time, so the brain can learn when
   its vetoes were wrong.
3. Trade. Approved openings fill free slots, up to LIVE_SLOTS open at once,
   through the same bracket engine as every other paper trade, tagged SOURCE.
4. Learning. The moment a fly-rl bracket closes, its net result is a reward
   or a punishment and the plastic layer learns from it (on_trade_closed,
   called from triggers.sweep). Telegram hears about every step.

Be clear about what this is. The same agent lost about 5% a year net in its
2020–26 walk-forward backtest, no better than random picks. This is a
forward test on paper, and nothing here can place a real order.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any

log = logging.getLogger("tradeo.fly_rl")

SOURCE = "fly-rl"
LIVE_SLOTS = 5          # fly-rl positions open at once
CONVICTION = 50         # sizes each at half the position cap (2.5% of equity)
HORIZON_DAYS = 7        # calendar days ≈ the 5 trading days it was trained on
VETO_PERCENTILE = 50.0  # approve only openings the fly ranks in the top half
EXPLORE = 0.10          # chance a vetoed opening is taken anyway, to learn from
SUMMARY_AT = (15, 35)   # IST, the end-of-day Telegram summary
VALUES_CACHE_SECONDS = 10 * 60

_lock = threading.RLock()
_values_cache: tuple[float, frozenset, tuple] | None = None
_last_handoff: dict[str, Any] | None = None


# ---- persistence --------------------------------------------------------------


def _events_path():
    from ml.flybrain.experiment import CACHE_DIR

    return CACHE_DIR / "fly_events.jsonl"


def _load():
    from ml.flybrain import rl

    loaded = rl.load_agent()
    if loaded is None:
        raise RuntimeError("fly brain not trained yet — run: ./scripts/setup.sh --flybrain")
    return loaded


def _save(agent, payload: dict[str, Any]) -> None:
    from ml.flybrain import rl

    rl.save_agent(agent, {k: v for k, v in payload.items() if k != "agent"})


def _universe() -> list[str]:
    import pandas as pd

    from ml.flybrain.experiment import CACHE_DIR

    return sorted(pd.read_parquet(CACHE_DIR / "prices.parquet", columns=["symbol"])["symbol"].unique())


def _event(kind: str, text: str, **data: Any) -> dict[str, Any]:
    """Append to the activity feed the Paper Trading page shows."""
    from market import hours

    record = {"at": hours.now_ist().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "text": text, **data}
    try:
        with _events_path().open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        log.warning("could not record fly event: %s", exc)
    log.info("fly-rl %s: %s", kind, text)
    return record


def events(limit: int = 40) -> list[dict[str, Any]]:
    path = _events_path()
    if not path.exists():
        return []
    lines = path.read_text().splitlines()[-limit:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _notify(text: str) -> None:
    try:
        from integrations.telegram import bot

        if bot.enabled:
            bot.send(text)
    except Exception as exc:
        log.warning("fly-rl telegram failed: %s", exc)


# ---- on / off -------------------------------------------------------------------


def enabled() -> bool:
    try:
        return bool(_load()[1].get("enabled", False))
    except RuntimeError:
        return False


def set_enabled(on: bool) -> dict[str, Any]:
    with _lock:
        agent, payload = _load()
        payload["enabled"] = bool(on)
        _save(agent, payload)
    _event("toggle", f"fly brain trading switched {'ON' if on else 'OFF'}")
    _notify(f"🧠 Fly brain paper trading switched <b>{'ON' if on else 'OFF'}</b>")
    return {"enabled": bool(on)}


# ---- the record ---------------------------------------------------------------


def _fly_trades(state_filter: str) -> list[dict[str, Any]]:
    from data.storage.database import get_db_connection

    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM autopilot_triggers WHERE source = ? AND {state_filter} "
            "ORDER BY id DESC", (SOURCE,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def scoreboard() -> dict[str, Any]:
    closed = [t for t in _fly_trades("state != 'open'") if t["state"] != "cancelled"]
    wins = [t for t in closed if float(t["realised_pnl"] or 0) > 0]
    pnl = sum(float(t["realised_pnl"] or 0) for t in closed)
    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "realised_pnl": round(pnl, 2),
        "open": len(_fly_trades("state = 'open'")),
    }


def _score_line(board: dict[str, Any] | None = None) -> str:
    b = board or scoreboard()
    rate = f"{b['win_rate']:.0f}%" if b["win_rate"] is not None else "–"
    return (f"Record: {b['trades']} closed · {b['wins']}W / {b['losses']}L ({rate}) · "
            f"P&L ₹{b['realised_pnl']:+,.0f} · {b['open']} open")


# ---- learning -----------------------------------------------------------------


def _process_closed(agent, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Dopamine for every closed fly-rl trade not yet learned from."""
    import numpy as np

    pending: dict[str, list[float]] = payload.setdefault("pending", {})
    learned: list[dict[str, Any]] = []
    for trade in _fly_trades("state != 'open'"):
        phi = pending.pop(str(trade["id"]), None)
        if phi is None:
            continue
        if trade["state"] == "cancelled":
            # A cancel is a human decision, not the market's verdict.
            _event("cancelled", f"{trade['symbol']} cancelled by hand — not learned from",
                   symbol=trade["symbol"])
            continue
        invested = float(trade["quantity"]) * float(trade["entry_price"])
        reward = float(trade["realised_pnl"] or 0) / invested if invested else 0.0
        before = float(np.array(phi) @ agent.w)
        delta = agent.learn(np.array(phi), reward)
        # Kept so a retrain on history can re-apply what was learned live.
        payload.setdefault("live_memory", []).append(
            {"id": trade["id"], "symbol": trade["symbol"], "phi": phi, "reward": reward})
        after = float(np.array(phi) @ agent.w)
        won = reward > 0
        payload["live_rewards" if won else "live_punishments"] = \
            payload.get("live_rewards" if won else "live_punishments", 0) + 1
        item = {"id": trade["id"], "symbol": trade["symbol"], "exit_reason": trade["state"],
                "pnl": round(float(trade["realised_pnl"] or 0), 2),
                "reward_pct": round(reward * 100, 2),
                "signal": "reward" if won else "punishment", "dopamine": round(delta, 4),
                "value_before_pct": round(before * 100, 3), "value_after_pct": round(after * 100, 3)}
        learned.append(item)
        _event("learned",
               f"{'WIN' if won else 'LOSS'} {trade['symbol']} {reward * 100:+.2f}% "
               f"(₹{item['pnl']:+,.0f}) → {item['signal']}, dopamine {delta:+.4f}",
               **item)
    return learned


def on_trade_closed() -> list[dict[str, Any]]:
    """Called when brackets close: learn at once, and say so on Telegram."""
    with _lock:
        agent, payload = _load()
        learned = _process_closed(agent, payload)
        if learned:
            _save(agent, payload)
    if learned:
        board = scoreboard()
        for item in learned:
            icon = "✅" if item["signal"] == "reward" else "❌"
            _notify(
                f"{icon} <b>Fly brain {'WIN' if item['signal'] == 'reward' else 'LOSS'}: "
                f"{item['symbol']}</b> ({item['exit_reason'].replace('_', ' ')})\n"
                f"{item['reward_pct']:+.2f}% · ₹{item['pnl']:+,.2f} after costs\n"
                f"🧠 {item['signal']} → dopamine {item['dopamine']:+.4f}; its value for that "
                f"setup moved {item['value_before_pct']:+.3f}% → {item['value_after_pct']:+.3f}%\n"
                f"{_score_line(board)}"
            )
    return learned


# ---- valuing stocks ------------------------------------------------------------


def _values(extra: list[str] | None = None) -> tuple[list[str], Any, Any, str]:
    """(symbols, phi, value, as_of) for the universe plus `extra`, cached briefly."""
    global _values_cache
    from ml.flybrain import rl

    wanted = frozenset(_universe()) | frozenset(extra or [])
    if _values_cache and time.time() - _values_cache[0] < VALUES_CACHE_SECONDS \
            and wanted <= _values_cache[1]:
        return _values_cache[2]

    agent, _ = _load()
    symbols, states, as_of = rl.live_states(sorted(wanted))
    phi = agent.phi(states)
    result = (symbols, phi, agent.value(phi), as_of)
    _values_cache = (time.time(), wanted, result)
    return result


def _percentile(value: float, reference) -> float:
    import numpy as np

    return round(float((np.asarray(reference) < value).mean() * 100), 1)


def suggestions(refresh: bool = False) -> dict[str, Any]:
    """The fly brain's ranking of its universe today, best first."""
    global _values_cache
    if refresh:
        _values_cache = None
    agent, payload = _load()
    symbols, _, values, as_of = _values()
    universe = set(_universe())
    ref = [v for s, v in zip(symbols, values) if s in universe]
    rows = sorted(({"symbol": s, "expected_net_return_pct": round(float(v) * 100, 3),
                    "percentile": _percentile(v, ref),
                    "tradeable": _percentile(v, ref) >= VETO_PERCENTILE}
                   for s, v in zip(symbols, values) if s in universe),
                  key=lambda r: -r["expected_net_return_pct"])
    return {"as_of": as_of, "top": rows[:8], "all": rows, "agent": status(agent, payload)}


# ---- the handoff ----------------------------------------------------------------


def consider_openings(candidates: list, scanner_only: list | None = None) -> dict[str, Any]:
    """
    Watchtower's openings, judged by the fly brain. Called after every sweep.

    `candidates` are watchtower Candidate objects. An opening is a bullish one
    that survived every stage, or failed only the Telegram alert floor.
    `scanner_only` are bullish on the scanner but below the deep-analysis
    shortlist, so no model has looked at them; they are labelled as such.
    """
    global _last_handoff
    from autopilot import triggers
    from pipeline import pretrade
    from pipeline.daily_pick import plan_long

    openings = [c for c in candidates
                if c.direction == "bullish" and (c.alive or c.rejected_at == "alerted")]
    verified = {c.symbol for c in openings}
    openings += list(scanner_only or [])
    handoff: dict[str, Any] = {"at": time.strftime("%H:%M:%S"), "processed": len(candidates),
                               "scanner_only": len(scanner_only or []),
                               "openings": len(openings), "decisions": []}

    if not enabled():
        handoff["note"] = "fly brain trading is OFF"
        _last_handoff = handoff
        return handoff
    if not openings:
        handoff["note"] = "watchtower found no bullish openings this sweep"
        _event("sweep", f"watchtower checked {len(candidates)} stocks — no bullish openings")
        _last_handoff = handoff
        return handoff

    on_trade_closed()  # learn from anything that closed since the last sweep

    with _lock:
        agent, payload = _load()
        symbols, phi, values, as_of = _values([c.symbol for c in openings])
        index = {s: i for i, s in enumerate(symbols)}
        universe = set(_universe())
        ref = [v for s, v in zip(symbols, values) if s in universe]

        free = max(0, LIVE_SLOTS - len(_fly_trades("state = 'open'")))
        held = set(triggers.open_symbols())
        ranked = sorted(openings, key=lambda c: -float(values[index[c.symbol]])
                        if c.symbol in index else float("inf"))
        opened: list[dict[str, Any]] = []

        for c in ranked:
            row = {"symbol": c.symbol, "watchtower_score": round(c.score, 1),
                   "conviction": round(c.conviction, 1), "verdict": c.verdict,
                   "via": "watchtower" if c.symbol in verified else "scanner-only"}
            if c.symbol not in index:
                row.update(decision="veto", why="no price history the fly brain can read")
                handoff["decisions"].append(row)
                continue

            v = float(values[index[c.symbol]])
            pct = _percentile(v, ref)
            row.update(fly_value_pct=round(v * 100, 3), fly_percentile=pct)
            if pct >= VETO_PERCENTILE:
                decision = "approve"
            elif agent.rng.random() < EXPLORE:
                decision = "explore"
            else:
                row.update(decision="veto",
                           why=f"fly ranks it {pct:.0f}th percentile (needs {VETO_PERCENTILE:.0f}+)")
                # The technical plan it would have traded, for the record.
                plan = plan_long(SimpleNamespace(symbol=c.symbol, snapshot=c.snapshot))
                if plan["ok"]:
                    row["plan"] = {k: round(plan[k], 2) for k in ("entry", "stop", "target")}
                handoff["decisions"].append(row)
                continue

            if len(opened) >= free:
                row.update(decision="skip", why=f"all {LIVE_SLOTS} fly slots are full")
            elif c.symbol in held:
                row.update(decision="skip", why="already holding it")
            else:
                plan = plan_long(SimpleNamespace(symbol=c.symbol, snapshot=c.snapshot))
                guard = pretrade.check(c.symbol, c.score, plan) if plan["ok"] else None
                if not plan["ok"]:
                    row.update(decision="skip", why=plan["error"])
                elif not guard["ok"]:
                    row.update(decision="skip", why=guard["why"], news=guard["headlines"][:3])
                else:
                    row["pretrade"] = guard["why"]
                    origin = ("watchtower" if c.symbol in verified
                              else "scanner (no model review)")
                    reason = (f"{origin} {c.score:.0f}/{c.conviction:.0f}% → fly brain "
                              f"{'approved' if decision == 'approve' else 'exploring'} "
                              f"({pct:.0f}th pct, value {v * 100:+.2f}%)")
                    result = triggers.arm(
                        symbol=c.symbol, entry_price=plan["entry"], stop_loss=plan["stop"],
                        target=plan["target"], conviction=CONVICTION, source=SOURCE,
                        reason=reason, horizon_days=HORIZON_DAYS)
                    if result.get("ok"):
                        payload.setdefault("pending", {})[str(result["id"])] = \
                            phi[index[c.symbol]].tolist()
                        row.update(decision=decision, trade=result)
                        opened.append(result)
                        held.add(c.symbol)
                        _event("buy", f"BUY {c.symbol} x{result['quantity']} @ ₹{result['entry_price']:,.2f}"
                                      f" — {reason}", symbol=c.symbol, trade=result)
                    else:
                        row.update(decision="skip", why=result.get("error"))
            handoff["decisions"].append(row)

        _save(agent, payload)

    approved = [d for d in handoff["decisions"] if d.get("trade")]
    vetoed = [d for d in handoff["decisions"] if d["decision"] == "veto"]
    _event("sweep", f"watchtower passed {len(openings)} openings → fly brain bought "
                    f"{len(approved)}, vetoed {len(vetoed)}",
           decisions=handoff["decisions"])

    lines = [f"🧠 <b>Fly brain · watchtower handoff</b>",
             f"{len(openings)} openings → bought {len(approved)}, vetoed {len(vetoed)}"]
    for d in handoff["decisions"]:
        if d.get("trade"):
            t = d["trade"]
            lines.append(f"🟢 BUY {d['symbol']} {t['quantity']} @ ₹{t['entry_price']:,.2f} · "
                         f"stop ₹{t['stop_loss']:,.2f} · target ₹{t['target']:,.2f}"
                         f"{' (exploring)' if d['decision'] == 'explore' else ''}")
        else:
            lines.append(f"⚪ {d['symbol']}: {d['decision']} — {d.get('why', '')}")
    lines.append(_score_line())
    _notify("\n".join(lines))

    handoff["opened"] = len(opened)
    _last_handoff = handoff
    return handoff


SIGNALS_FRESH_SECONDS = 35 * 60


def trade_now() -> dict[str, Any]:
    """
    "Paper trade the picks", on demand.

    Market data is Yahoo Finance through the scanner (no broker needed). Uses
    watchtower's last sweep if it is fresh, otherwise scores the universe now.
    Every bullish stock gets an ATR entry/stop/target, and the fly brain
    decides which to paper trade. Returns the handoff plus the scan it used.
    """
    from datetime import datetime

    from core.config import get_settings
    from market import hours
    from pipeline.watchtower import Candidate, watchtower
    from realtime.scanner import scan

    if not hours.is_open():
        return {"ok": False, "error": f"NSE is {hours.phase()} — paper trades would fill at stale prices"}
    if not enabled():
        return {"ok": False, "error": "fly brain trading is switched OFF on the Paper Trading page"}

    last_at = (watchtower.last_run or {}).get("at")
    fresh = bool(last_at) and (datetime.now() - datetime.fromisoformat(last_at)).total_seconds() \
        < SIGNALS_FRESH_SECONDS
    signals = getattr(watchtower, "last_signals", None) if fresh else None
    source = f"watchtower sweep at {last_at[11:16]}" if signals else "fresh scan"
    if not signals:
        signals = scan(limit=get_settings().scan_universe_limit, deep=False, trigger="fly-trade-now").signals

    bullish = sorted((s for s in signals if s.direction == "bullish"), key=lambda s: -s.score)
    candidates = []
    for s in bullish:
        c = Candidate(symbol=s.symbol, score=s.score, direction=s.direction, conviction=s.score,
                      verdict="scanner-only", triggers=list(s.triggers), snapshot=dict(s.snapshot))
        candidates.append(c)

    handoff = consider_openings([], candidates) if candidates else {"decisions": []}
    top = sorted(signals, key=lambda s: -s.score)[:5]
    return {"ok": True, "source": source, "scanned": len(signals), "bullish": len(bullish),
            "top_scores": [{"symbol": s.symbol, "score": round(s.score, 1), "direction": s.direction}
                           for s in top],
            "handoff": handoff}


def scan_now() -> dict[str, Any]:
    """Run a watchtower sweep now, in the background; it hands off here when done."""
    from market import hours
    from pipeline.watchtower import watchtower

    synthetic = False
    try:
        from lowlatency.ingest import synthetic as feed

        synthetic = feed.status().get("running", False)
    except Exception:
        pass
    if not (hours.is_open() or synthetic):
        return {"started": False, "error": f"NSE is {hours.phase()} — trades would fill at stale prices"}
    if watchtower.status()["sweep_in_progress"]:
        return {"started": False, "error": "a watchtower sweep is already running"}

    from core.config import get_settings

    limit = get_settings().scan_universe_limit
    threading.Thread(target=lambda: watchtower.run(limit=limit), name="fly-scan", daemon=True).start()
    _event("scan", "manual watchtower sweep started")
    return {"started": True}


# ---- reads ---------------------------------------------------------------------


def status(agent=None, payload=None) -> dict[str, Any]:
    from ml.flybrain import rl

    if agent is None:
        try:
            agent, payload = _load()
        except RuntimeError as exc:
            return {"ready": False, "error": str(exc)}
    report = json.loads(rl.RL_REPORT.read_text()) if rl.RL_REPORT.exists() else {}
    board = scoreboard()
    return {
        "ready": True,
        "enabled": bool(payload.get("enabled", False)),
        "source": SOURCE,
        "trained_through": payload.get("trained_through"),
        "backtest_rewards": agent.rewards - payload.get("live_rewards", 0),
        "backtest_punishments": agent.punishments - payload.get("live_punishments", 0),
        "live_rewards": payload.get("live_rewards", 0),
        "live_punishments": payload.get("live_punishments", 0),
        "live_closed_trades": board["trades"],
        "live_wins": board["wins"],
        "live_realised_pnl": board["realised_pnl"],
        "scoreboard": board,
        "rules": {"slots": LIVE_SLOTS, "position_pct": 2.5, "approve_percentile": VETO_PERCENTILE,
                  "explore": EXPLORE, "hold_days": HORIZON_DAYS},
        "backtest": {"passed": report.get("passed"),
                     "fly_rl_net_annualised_pct":
                         report.get("results", {}).get("fly_rl", {}).get("net_annualised_pct_mean"),
                     "scorer_net_annualised_pct":
                         report.get("results", {}).get("scorer_top5", {}).get("net_annualised_pct")},
    }


def dashboard() -> dict[str, Any]:
    """Everything the Paper Trading page's fly brain panel shows."""
    from autopilot import triggers
    from market import hours
    from pipeline.watchtower import watchtower

    wt = watchtower.status()
    return {
        "status": status(),
        "market": {"open": hours.is_open(), "phase": hours.phase(),
                   "now_ist": hours.now_ist().strftime("%H:%M")},
        "watchtower": {"running": wt["running"], "sweeping": wt["sweep_in_progress"],
                       "runs": wt["runs"], "last_run_at": (wt.get("last_run") or {}).get("at")},
        "last_handoff": _last_handoff,
        "open_positions": [t for t in triggers.active() if t.get("source") == SOURCE],
        "closed_trades": [t for t in triggers.history(limit=200) if t.get("source") == SOURCE][:25],
        "events": events(40),
    }


# ---- background -------------------------------------------------------------------


_summarised: set = set()


def _daily_summary() -> None:
    board = scoreboard()
    today = [e for e in events(200) if e["at"].startswith(time.strftime("%Y-%m-%d"))]
    buys = sum(e["kind"] == "buy" for e in today)
    learned = [e for e in today if e["kind"] == "learned"]
    wins = sum(e.get("signal") == "reward" for e in learned)
    _notify(
        f"🧠 <b>Fly brain · end of day</b>\n"
        f"Today: {buys} bought · {len(learned)} closed ({wins}W / {len(learned) - wins}L)\n"
        f"{_score_line(board)}"
    )


def start() -> None:
    """Backstop learning every minute, and the end-of-day summary."""
    from market import hours

    def loop() -> None:
        while True:
            try:
                on_trade_closed()
                now = hours.now_ist()
                if (now.weekday() < 5 and (now.hour, now.minute) >= SUMMARY_AT
                        and now.date() not in _summarised and enabled()):
                    _summarised.add(now.date())
                    _daily_summary()
            except Exception as exc:
                log.warning("fly-rl loop: %s", exc)
            time.sleep(60)

    threading.Thread(target=loop, name="fly-rl", daemon=True).start()
