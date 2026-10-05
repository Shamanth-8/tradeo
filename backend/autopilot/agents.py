"""
The trading agents, and their switches.

The agents that may open paper trades on their own:

  momentum    monthly: holds the top stocks by 3-month momentum
  swing       daily: volume breakouts, held up to two weeks
  daily-pick  once a day, the top bullish stock(s) by Tradeo's scorer
  intraday    every 5 minutes: opening-range breakouts, closed the same day
  fly-rl      the fly brain: judges Watchtower's openings and learns from
              every closed trade

All trade the same automated paper account, and every buy passes the
account-level risk checks in risk.py; results are split by agent on the
Paper Trading page. Watchtower itself never trades: it only scans and
hands its openings to the fly brain.

Everything here starts OFF on a fresh install — Watchtower (all background
scanning), the fly brain and the daily pick — and is switched on from the
Autopilot page. Settings live in data/agents.json (git-ignored).
"""

from __future__ import annotations

import json
import threading
from typing import Any

from core.config import DATA_DIR

STORE = DATA_DIR / "agents.json"

DAILY_PICK_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "min_score": 60.0,      # the scorer's bullish line; lower = more picks
    "picks_per_day": 1,
    "position_pct": 10.0,   # ceiling per pick, % of equity; size is set by risk (autopilot/risk.py)
    "run_at": "09:30",      # IST
}
LIMITS = {"min_score": (50.0, 90.0), "picks_per_day": (1, 5), "position_pct": (0.5, 10.0)}

INTRADAY_DEFAULTS: dict[str, Any] = {"enabled": False, "max_open": 3, "max_trades_per_day": 10,
                                     "position_pct": 10.0}
SWING_DEFAULTS: dict[str, Any] = {"enabled": False, "max_open": 5, "position_pct": 10.0}
AGENT_LIMITS = {"max_open": (1, 10), "max_trades_per_day": (1, 30), "position_pct": (0.5, 10.0)}

MOMENTUM_DEFAULTS: dict[str, Any] = {"enabled": False, "top": 10, "capital_pct": 50.0}
MOMENTUM_LIMITS = {"top": (5, 15), "capital_pct": (10.0, 100.0)}

# Background switches that are not agents. All off by default.
SWITCH_DEFAULTS = {"watchtower": False}

_lock = threading.Lock()


def _read() -> dict[str, Any]:
    try:
        return json.loads(STORE.read_text()) if STORE.exists() else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STORE)


def switch(name: str) -> bool:
    return bool(_read().get("switches", {}).get(name, SWITCH_DEFAULTS.get(name, False)))


def apply_watchtower(on: bool) -> None:
    """
    Start or stop every background scanning loop together: the realtime
    scanner (Live signals, Telegram alerts), the pipeline Watchtower (feeds the
    fly brain), the background simulation, and the research candidate cache.
    These are what keep the CPU busy between your questions.
    """
    import logging

    from core import failures
    from core.config import get_settings

    log = logging.getLogger("tradeo.agents")
    settings = get_settings()
    parts = []
    try:
        from realtime.engine import watchtower as scanner
        (scanner.start() if on and settings.scanner_enabled else scanner.stop())
        parts.append("scanner")
    except Exception as exc:  # one loop failing to start must not block the others
        failures.record("watchtower.scanner", exc, log)
    try:
        from pipeline.watchtower import watchtower as pipeline
        (pipeline.start() if on else pipeline.stop())
        parts.append("pipeline")
    except Exception as exc:  # one loop failing to start must not block the others
        failures.record("watchtower.watchtower", exc, log)
    try:
        from pipeline.simulation import simulation
        (simulation.start(interval_seconds=180) if on else simulation.stop())
        parts.append("simulation")
    except Exception as exc:  # one loop failing to start must not block the others
        failures.record("watchtower.simulation", exc, log)
    log.info("watchtower %s (%s)", "ON" if on else "OFF", ", ".join(parts))


def set_switch(name: str, on: bool) -> None:
    with _lock:
        data = _read()
        data.setdefault("switches", {})[name] = bool(on)
        _write(data)
    if name == "watchtower":
        apply_watchtower(bool(on))


def intraday_settings() -> dict[str, Any]:
    return {**INTRADAY_DEFAULTS, **_read().get("intraday", {})}


def swing_settings() -> dict[str, Any]:
    return {**SWING_DEFAULTS, **_read().get("swing", {})}


def _clean_simple(patch: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "enabled" in patch:
        out["enabled"] = bool(patch["enabled"])
    for key in allowed:
        if key in patch and patch[key] is not None:
            low, high = AGENT_LIMITS[key]
            value = float(patch[key])
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low:g} and {high:g}")
            out[key] = value if key == "position_pct" else int(value)
    return out


def momentum_settings() -> dict[str, Any]:
    return {**MOMENTUM_DEFAULTS, **_read().get("momentum", {})}


def set_momentum_state(**state: Any) -> None:
    with _lock:
        data = _read()
        data["momentum"] = {**data.get("momentum", {}), **state}
        _write(data)


def _clean_momentum(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "enabled" in patch:
        out["enabled"] = bool(patch["enabled"])
    for key, (low, high) in MOMENTUM_LIMITS.items():
        if key in patch and patch[key] is not None:
            value = float(patch[key])
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low:g} and {high:g}")
            out[key] = int(value) if key == "top" else value
    return out


def daily_pick_settings() -> dict[str, Any]:
    return {**DAILY_PICK_DEFAULTS, **_read().get("daily-pick", {})}


def _clean_daily_pick(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "enabled" in patch:
        out["enabled"] = bool(patch["enabled"])
    for key, (low, high) in LIMITS.items():
        if key in patch and patch[key] is not None:
            value = float(patch[key])
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low:g} and {high:g}")
            out[key] = int(value) if key == "picks_per_day" else value
    if "run_at" in patch:
        hh, mm = str(patch["run_at"]).split(":")
        hour, minute = int(hh), int(mm)
        if not ((9, 15) <= (hour, minute) <= (15, 15)):
            raise ValueError("run_at must be within market hours, 09:15–15:15 IST")
        out["run_at"] = f"{hour:02d}:{minute:02d}"
    return out


def _record(source: str) -> dict[str, Any]:
    """This agent's trades on the paper account."""
    from data.storage.database import get_db_connection

    conn = get_db_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT state, realised_pnl FROM autopilot_triggers WHERE source = ?", (source,))]
    finally:
        conn.close()
    closed = [r for r in rows if r["state"] not in ("open", "cancelled")]
    wins = sum(float(r["realised_pnl"] or 0) > 0 for r in closed)
    return {"trades": len(rows), "open": sum(r["state"] == "open" for r in rows),
            "closed": len(closed), "wins": wins, "losses": len(closed) - wins,
            "realised_pnl": round(sum(float(r["realised_pnl"] or 0) for r in closed), 2)}


def describe() -> list[dict[str, Any]]:
    from pipeline import fly_rl_trader

    from pipeline.watchtower import watchtower as pipeline

    from pipeline import evaluate

    dp = daily_pick_settings()
    intra, sw, mom = intraday_settings(), swing_settings(), momentum_settings()
    tested = {r["name"]: r for r in (evaluate.latest() or {}).get("results", [])}
    fly = fly_rl_trader.status()
    wt = pipeline.status()
    watch_on = switch("watchtower")
    return [
        {
            "id": "watchtower",
            "kind": "scanner",
            "name": "Watchtower (market scanner)",
            "enabled": watch_on,
            "how": ("Scans the market every 30 minutes during market hours: scores the stocks, "
                    "backtests the promising ones and has the local AI review them. It never trades — "
                    "it feeds the fly brain, Live signals and Telegram alerts. This is the main CPU "
                    "load, so it's off until you need it."),
            "status": {"sweeping": wt.get("sweep_in_progress"), "runs": wt.get("runs"),
                       "last_run_at": (wt.get("last_run") or {}).get("at")},
        },
        {
            "id": "momentum",
            "name": "Monthly momentum",
            "enabled": mom["enabled"],
            "how": (f"On the first trading day of each month (09:30): holds the top {mom['top']} stocks by "
                    f"3-month return (latest month skipped) that are above their 200-day average, equal "
                    f"weight, {mom['capital_pct']:g}% of the account in all. Cash while the Nifty 50 is below "
                    f"its 200-day average. Names that stay in the list are kept."),
            "settings": {k: mom[k] for k in ("top", "capital_pct")},
            "limits": MOMENTUM_LIMITS,
            "record": _record("momentum"),
            "last_rebalance": mom.get("last_rebalance"),
            "backtest": _tested_text(tested.get("Momentum top 10 + market filter"),
                                     "Run `python -m pipeline.evaluate` to measure it."),
            "evaluation": tested.get("Momentum top 10 + market filter"),
        },
        {
            "id": "intraday",
            "name": "Intraday (same-day)",
            "enabled": intra["enabled"],
            "how": (f"Every 5 minutes, 09:20–14:45: buys Nifty 50 stocks breaking above their first-15-minute "
                    f"high and VWAP on a volume surge. Stop 0.4%+ (ATR), target 2:1, everything closed by 15:15. "
                    f"Up to {intra['max_open']} open, {intra['max_trades_per_day']} a day, "
                    f"{intra['position_pct']:g}% each, intraday charges."),
            "settings": {k: intra[k] for k in ("max_open", "max_trades_per_day", "position_pct")},
            "limits": AGENT_LIMITS,
            "record": _record("intraday"),
            "backtest": ("Last 60 days of 5-min bars: −0.25% avg per trade after 0.21% costs, 29% wins, "
                         "~21 trades/day — no better than random entries. Losing in all 5 variants tested."),
        },
        {
            "id": "swing",
            "name": "Swing (up to 2 weeks)",
            "enabled": sw["enabled"],
            "how": (f"At 09:25 each session: buys yesterday's volume breakouts (close above the 20-day high on "
                    f"2x volume, above the 50-day average). Stop 2×ATR, target 2:1, out within 14 days. "
                    f"Up to {sw['max_open']} open, {sw['position_pct']:g}% each."),
            "settings": {k: sw[k] for k in ("max_open", "position_pct")},
            "limits": AGENT_LIMITS,
            "record": _record("swing"),
            "backtest": _tested_text(tested.get("Swing breakout + market filter"),
                                     "No better than random entries. Run `python -m pipeline.evaluate` for numbers."),
            "evaluation": tested.get("Swing breakout + market filter"),
        },
        {
            "id": "daily-pick",
            "name": "Daily pick",
            "enabled": dp["enabled"],
            "how": (f"At {dp['run_at']} IST on trading days, buys up to {dp['picks_per_day']} of the "
                    f"highest-scoring bullish stocks (Tradeo scorer ≥ {dp['min_score']:g}), "
                    f"{dp['position_pct']:g}% of the account each, with a 2×ATR stop and 2:1 target."),
            "settings": {k: dp[k] for k in ("min_score", "picks_per_day", "position_pct", "run_at")},
            "limits": LIMITS,
            "record": _record("daily-pick"),
            "backtest": "2020–26 replay: +0.54% avg per trade, 45.6% wins — no better than random picks.",
        },
        {
            "id": "fly-rl",
            "name": "Fly brain",
            "enabled": bool(fly.get("enabled")) if fly.get("ready") else False,
            "ready": bool(fly.get("ready")),
            "how": ("Every Watchtower scan (30 min) hands over its bullish stocks; the fly brain buys "
                    "the ones it ranks in the top half of its universe (up to 5 open, 2.5% each) and "
                    "learns from every close."),
            "settings": fly.get("rules", {}),
            "record": _record("fly-rl"),
            "backtest": "2020–26 walk-forward: about −5%/yr after costs — no edge shown yet.",
            "error": fly.get("error"),
            "needs": None if watch_on else "Watchtower is off — the fly brain gets no stocks to judge until you switch it on.",
        },
    ]


def _tested_text(result: dict[str, Any] | None, fallback: str) -> str:
    """One line from pipeline/evaluate.py's latest run: in-sample, then the held-out period."""
    if not result:
        return fallback

    def part(label: str, m: dict[str, Any]) -> str:
        return (f"{label} {m.get('cagr_pct', 0):+.1f}%/yr vs Nifty {m.get('nifty_cagr_pct') or 0:+.1f}%, "
                f"Sharpe {m.get('sharpe', 0):.2f}, worst drop {m.get('max_drawdown_pct', 0):.0f}%")

    return (f"{part('2020–24:', result['in_sample'])}; held out {part('2025+:', result['holdout'])}. "
            "Survivorship-biased universe — compare with the controls on the Autopilot page.")


def update(agent_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if agent_id == "watchtower":
        if "enabled" in patch:
            set_switch("watchtower", bool(patch["enabled"]))
    elif agent_id in ("intraday", "swing"):
        allowed = ("max_open", "max_trades_per_day", "position_pct") if agent_id == "intraday" \
            else ("max_open", "position_pct")
        clean = _clean_simple(patch, allowed)
        with _lock:
            data = _read()
            data[agent_id] = {**data.get(agent_id, {}), **clean}
            _write(data)
    elif agent_id == "momentum":
        clean = _clean_momentum(patch)
        with _lock:
            data = _read()
            data["momentum"] = {**data.get("momentum", {}), **clean}
            _write(data)
    elif agent_id == "daily-pick":
        clean = _clean_daily_pick(patch)
        with _lock:
            data = _read()
            data["daily-pick"] = {**data.get("daily-pick", {}), **clean}
            _write(data)
    elif agent_id == "fly-rl":
        if "enabled" in patch:
            from pipeline import fly_rl_trader

            fly_rl_trader.set_enabled(bool(patch["enabled"]))
    else:
        raise ValueError(f"unknown agent '{agent_id}'")
    return next(a for a in describe() if a["id"] == agent_id)
