"""
Intraday momentum: opening-range breakouts, closed the same day.

The rule (5-minute bars, NSE large caps):

  setup    after the first 15 minutes (the opening range), a bar closes
           ABOVE the opening-range high and ABOVE the day's VWAP, on volume
           at least VOLUME_SURGE x the stock's average bar so far today
  entry    next bar's open (never the close that produced the signal)
  stop     the larger of STOP_ATR x 5-min ATR and MIN_STOP_PCT below entry
  target   REWARD_R x the risk above entry
  exit     stop, target, or SQUARE_OFF (15:15), whichever comes first;
           a bar touching both counts as the stop (pessimistic)
  limits   one trade per stock per day, no new entries after LAST_ENTRY

Intraday charges and slippage (autopilot/costs.py, ~0.18% round trip) are
taken off every trade. `backtest()` replays the rule on Yahoo's 5-minute
history (the last ~60 days is all Yahoo serves); `scan()` applies it to
today's bars for the live agent.
"""

from __future__ import annotations

import json
import logging
from datetime import time as dtime
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("tradeo.intraday")

OPENING_RANGE_BARS = 3        # 09:15–09:30
VOLUME_SURGE = 2.0
STOP_ATR = 1.5
MIN_STOP_PCT = 0.004          # never tighter than 0.4%: noise would stop it out
REWARD_R = 2.0
LAST_ENTRY = dtime(14, 45)
SQUARE_OFF = dtime(15, 15)


def universe() -> list[str]:
    """Nifty 50 members from config/universe.json: the liquid end, where fills are realistic."""
    from core.config import PROJECT_ROOT

    inst = json.loads((PROJECT_ROOT / "config" / "universe.json").read_text())["instruments"]
    return sorted(k for k, v in inst.items()
                  if v.get("asset_class") == "equity" and "NIFTY50" in (v.get("index") or []))


def download(symbols: list[str], period: str = "60d") -> pd.DataFrame:
    from market import data

    raw = data.download([s + ".NS" for s in symbols], period=period, interval="5m", auto_adjust=False)
    by_ticker = data.split(raw, [s + ".NS" for s in symbols])
    frames = []
    for s in symbols:
        d = by_ticker.get(s + ".NS")
        if d is None:
            continue
        d = d.rename(columns=str.lower).reset_index()
        d = d.rename(columns={d.columns[0]: "ts"})
        d["ts"] = pd.to_datetime(d["ts"]).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        d["symbol"] = s
        frames.append(d[["symbol", "ts", "open", "high", "low", "close", "volume"]])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _day_features(day: pd.DataFrame) -> pd.DataFrame:
    day = day.sort_values("ts").reset_index(drop=True).copy()
    typical = (day.high + day.low + day.close) / 3
    vol = day.volume.replace(0, np.nan)
    day["vwap"] = (typical * vol).cumsum() / vol.cumsum()
    day["or_high"] = day.high.iloc[:OPENING_RANGE_BARS].max()
    day["avg_vol"] = day.volume.expanding().mean().shift(1)
    prev_close = day.close.shift()
    tr = pd.concat([day.high - day.low, (day.high - prev_close).abs(), (day.low - prev_close).abs()], axis=1).max(axis=1)
    day["atr"] = tr.rolling(14, min_periods=3).mean()
    return day


def _signal_rows(day: pd.DataFrame) -> pd.Series:
    t = day.ts.dt.time
    return ((day.index >= OPENING_RANGE_BARS) & (t <= LAST_ENTRY)
            & (day.close > day.or_high) & (day.close > day.vwap)
            & (day.volume >= VOLUME_SURGE * day.avg_vol))


def plan(entry: float, atr: float) -> tuple[float, float]:
    risk = max(STOP_ATR * (atr or 0), MIN_STOP_PCT * entry)
    return entry - risk, entry + REWARD_R * risk


def _simulate_day(day: pd.DataFrame, i: int) -> dict[str, Any] | None:
    """Enter at bar i+1's open, walk forward to stop/target/square-off."""
    if i + 1 >= len(day):
        return None
    entry = float(day.open.iloc[i + 1])
    stop, target = plan(entry, float(day.atr.iloc[i]))
    for j in range(i + 1, len(day)):
        bar = day.iloc[j]
        if bar.ts.time() >= SQUARE_OFF:
            return {"exit": float(bar.open), "how": "square-off", "bars": j - i}
        if bar.low <= stop:
            return {"exit": min(stop, float(bar.open)), "how": "stop", "bars": j - i}
        if bar.high >= target:
            return {"exit": max(target, float(bar.open)), "how": "target", "bars": j - i}
    return {"exit": float(day.close.iloc[-1]), "how": "close", "bars": len(day) - 1 - i}


def backtest(bars: pd.DataFrame, random_entries: bool = False, seed: int = 0) -> dict[str, Any]:
    """Replay the rule (or random entries with the same exits, as a control)."""
    from autopilot import costs

    rt_cost = (costs.charges(1.0, "BUY", "INTRADAY") + costs.charges(1.0, "SELL", "INTRADAY")
               + 2 * costs.INTRADAY_SLIPPAGE)
    rng = np.random.default_rng(seed)
    trades = []
    for (symbol, date), day in bars.groupby([bars.symbol, bars.ts.dt.date]):
        if len(day) < 20:
            continue
        day = _day_features(day)
        if random_entries:
            eligible = np.flatnonzero((day.index >= OPENING_RANGE_BARS) & (day.ts.dt.time <= LAST_ENTRY))
            hits = [int(rng.choice(eligible))] if len(eligible) and rng.random() < 0.3 else []
        else:
            hits = np.flatnonzero(_signal_rows(day))[:1]   # first signal only: one trade per stock per day
        for i in hits:
            out = _simulate_day(day, int(i))
            if out:
                entry = float(day.open.iloc[int(i) + 1])
                trades.append({"symbol": symbol, "date": str(date), "entry": entry, **out,
                               "net": out["exit"] / entry - 1 - rt_cost})
    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades": 0}
    days = t.date.nunique()
    return {
        "trades": int(len(t)), "trading_days": int(days),
        "trades_per_day": round(len(t) / max(1, bars.ts.dt.date.nunique()), 1),
        "win_rate": round(float((t.net > 0).mean()) * 100, 1),
        "avg_net_pct": round(float(t.net.mean()) * 100, 3),
        "median_minutes": int(t.bars.median() * 5),
        "exits": t.how.value_counts().to_dict(),
        "round_trip_cost_pct": round(rt_cost * 100, 3),
        # At 2.5% of the account per trade:
        "account_pct_total": round(float(t.net.sum()) * 2.5, 2),
    }


def scan(bars: pd.DataFrame) -> list[dict[str, Any]]:
    """Today's live setups, strongest volume surge first."""
    out = []
    today = bars.ts.dt.date.max()
    for symbol, day in bars[bars.ts.dt.date == today].groupby("symbol"):
        if len(day) <= OPENING_RANGE_BARS:
            continue
        day = _day_features(day)
        last = len(day) - 1
        if _signal_rows(day).iloc[last]:
            row = day.iloc[last]
            out.append({"symbol": symbol, "price": float(row.close), "atr": float(row.atr or 0),
                        "surge": round(float(row.volume / row.avg_vol), 1),
                        "above_vwap_pct": round(float(row.close / row.vwap - 1) * 100, 2),
                        "bar": row.ts.strftime("%H:%M")})
    return sorted(out, key=lambda r: -r["surge"])


# ---- the live agent ---------------------------------------------------------------

SOURCE = "intraday"
SCAN_EVERY_SECONDS = 300


def _expires_utc() -> str:
    """Today's square-off time, in UTC as the bracket table stores it."""
    from datetime import datetime, timedelta

    from market import hours

    today = hours.now_ist().date()
    square_ist = datetime.combine(today, SQUARE_OFF)
    return (square_ist - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")


def _today_count(conn_rows: str) -> int:
    from data.storage.database import get_db_connection
    from market import hours

    conn = get_db_connection()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM autopilot_triggers WHERE source = ? {conn_rows}"
            " AND DATE(opened_at, '+330 minutes') = ?",
            (SOURCE, hours.now_ist().date().isoformat())).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def run_once() -> dict[str, Any]:
    """One scan: buy today's fresh breakouts within the agent's limits."""
    from autopilot import agents, triggers
    from market import hours
    from pipeline import pretrade

    rules = agents.intraday_settings()
    now = hours.now_ist().time()
    if not hours.is_open():
        return {"ok": False, "error": f"NSE is {hours.phase()}"}
    if now > LAST_ENTRY:
        return {"ok": False, "error": f"no new intraday entries after {LAST_ENTRY:%H:%M}"}

    open_now = len([t for t in triggers.active() if t.get("source") == SOURCE])
    done_today = _today_count("")
    room = min(int(rules["max_open"]) - open_now, int(rules["max_trades_per_day"]) - done_today)
    if room <= 0:
        return {"ok": False, "error": f"limit reached ({open_now} open, {done_today} today)"}

    bars = download(universe(), period="1d")
    setups = scan(bars)
    held = set(triggers.open_symbols())
    traded_today = set()
    opened, skipped = [], []
    for s in setups:
        if len(opened) >= room:
            break
        if s["symbol"] in held or s["symbol"] in traded_today:
            continue
        # Fast check: bad news blocks; the cloud verifier is skipped at this pace.
        guard = pretrade.check(s["symbol"], None, None, use_verifier=False)
        if not guard["ok"]:
            skipped.append({"symbol": s["symbol"], "why": guard["why"]})
            continue
        stop, target = plan(s["price"], s["atr"])
        result = triggers.arm(
            symbol=s["symbol"], entry_price=s["price"], stop_loss=stop, target=target,
            max_position_pct=float(rules["position_pct"]), conviction=60, source=SOURCE, product="INTRADAY",
            expires_at_utc=_expires_utc(),
            reason=(f"intraday breakout {s['bar']}: above opening range and VWAP "
                    f"(+{s['above_vwap_pct']}%), volume {s['surge']}x; square-off {SQUARE_OFF:%H:%M}"))
        if result.get("ok"):
            opened.append(result)
            traded_today.add(s["symbol"])
        else:
            skipped.append({"symbol": s["symbol"], "why": result.get("error")})
    if opened:
        _announce(opened)
    return {"ok": bool(opened), "setups": len(setups), "opened": opened, "skipped": skipped,
            "symbol": ", ".join(o["symbol"] for o in opened)}


def _announce(opened: list[dict[str, Any]]) -> None:
    try:
        from integrations.telegram import bot

        if bot.enabled:
            bot.send("⚡ <b>Intraday (paper)</b>\n" + "\n".join(
                f"BUY {o['symbol']} {o['quantity']} @ ₹{o['entry_price']:,.2f} · stop ₹{o['stop_loss']:,.2f}"
                f" · target ₹{o['target']:,.2f} · out by {SQUARE_OFF:%H:%M}" for o in opened))
    except Exception as exc:  # alerts are best effort; the trade is already booked
        from core import failures

        failures.record("telegram.intraday", exc, log)


def tick() -> None:
    """Called every SCAN_EVERY_SECONDS by autopilot/scheduler.py: scans during the session, while on."""
    from autopilot import agents
    from market import hours

    if agents.intraday_settings()["enabled"] and hours.is_open():
        outcome = run_once()
        if outcome.get("opened"):
            log.info("intraday opened %s", outcome["symbol"])
