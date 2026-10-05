"""
Swing: volume-momentum breakouts, held up to two weeks.

The rule (daily bars, the universe's equities):

  setup    yesterday's close above its prior 20-day high, on volume at least
           2x its 20-day average, while above its 50-day average
  entry    at the market the next session (RUN_AT IST)
  stop     2 x ATR(14) below entry; target 2:1; out after HOLD_DAYS calendar
           days (about 10 trading days), delivery charges

Tested with pipeline/evaluate.py (portfolio, real costs): no better than
random entries with the same exits, in-sample (2020–24) or held out (2025+);
the market filter doesn't rescue it. Kept as a reference and for the live
paper record.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger("tradeo.swing")

SOURCE = "swing"
RUN_AT = (9, 25)
HOLD_DAYS = 14


def scan() -> list[dict[str, Any]]:
    """Stocks whose last completed daily bar is a volume breakout, strongest first."""
    from ml.flybrain.prepare import download, universe_equities

    prices = download(universe_equities(), period="2y")  # the helper skips stocks under 320 days
    out = []
    for symbol, d in prices.groupby("symbol"):
        d = d.sort_values("date").reset_index(drop=True)
        if len(d) < 60:
            continue
        prev = d.close.shift()
        tr = pd.concat([d.high - d.low, (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        hi20 = d.high.rolling(20).max().shift(1)
        vol20 = d.volume.rolling(20).mean().shift(1)
        sma50 = d.close.rolling(50).mean()
        i = len(d) - 1
        # Today's bar is still forming before the close: judge yesterday's.
        if pd.Timestamp(d.date.iloc[i]).date() == pd.Timestamp.now(tz="Asia/Kolkata").date():
            i -= 1
        if d.close[i] > hi20[i] and d.volume[i] > 2 * vol20[i] and d.close[i] > sma50[i]:
            out.append({"symbol": symbol, "close": float(d.close[i]), "atr": float(atr[i]),
                        "volume_x": round(float(d.volume[i] / vol20[i]), 1),
                        "breakout_pct": round(float(d.close[i] / hi20[i] - 1) * 100, 2),
                        "date": str(pd.Timestamp(d.date[i]).date())})
    return sorted(out, key=lambda r: -r["volume_x"])


def run_once() -> dict[str, Any]:
    from autopilot import agents, triggers
    from brokers.quotes import broker_ltp
    from market import hours
    from pipeline import pretrade

    rules = agents.swing_settings()
    if not hours.is_open():
        return {"ok": False, "error": f"NSE is {hours.phase()}"}
    open_now = len([t for t in triggers.active() if t.get("source") == SOURCE])
    room = int(rules["max_open"]) - open_now
    if room <= 0:
        return {"ok": False, "error": f"{open_now} swing positions already open"}

    from data.fetchers.stock_fetcher import stock_fetcher

    setups = scan()
    held = set(triggers.open_symbols())
    opened, skipped = [], []
    for s in setups:
        if len(opened) >= room:
            break
        if s["symbol"] in held:
            continue
        quote = broker_ltp(s["symbol"])
        price = quote[0] if quote else float(stock_fetcher.get_live_price(s["symbol"]).get("price") or 0)
        if price <= 0:
            continue
        stop = price - 2 * s["atr"]
        guard = pretrade.check(s["symbol"], None, {"entry": price, "stop": stop, "target": price + 2 * (price - stop)})
        if not guard["ok"]:
            skipped.append({"symbol": s["symbol"], "why": guard["why"]})
            continue
        result = triggers.arm(
            symbol=s["symbol"], entry_price=price, stop_loss=stop, target=price + 2 * (price - stop),
            max_position_pct=float(rules["position_pct"]), conviction=60,
            source=SOURCE, horizon_days=HOLD_DAYS,
            reason=(f"swing breakout {s['date']}: closed {s['breakout_pct']}% above its 20-day high "
                    f"on {s['volume_x']}x volume, above the 50-day average | news: {guard['why']}"))
        if result.get("ok"):
            opened.append(result)
            held.add(s["symbol"])
        else:
            skipped.append({"symbol": s["symbol"], "why": result.get("error")})
    if opened:
        try:
            from integrations.telegram import bot

            if bot.enabled:
                bot.send("📈 <b>Swing (paper)</b>\n" + "\n".join(
                    f"BUY {o['symbol']} {o['quantity']} @ ₹{o['entry_price']:,.2f} · stop ₹{o['stop_loss']:,.2f}"
                    f" · target ₹{o['target']:,.2f} · up to {HOLD_DAYS} days" for o in opened))
        except Exception as exc:  # alerts are best effort; the trade is already booked
            from core import failures

            failures.record("telegram.swing", exc, log)
    return {"ok": bool(opened), "setups": len(setups), "opened": opened, "skipped": skipped,
            "symbol": ", ".join(o["symbol"] for o in opened)}


_attempted: set = set()


def tick() -> None:
    """Called every minute by autopilot/scheduler.py: runs once a day at RUN_AT, while switched on."""
    from autopilot import agents
    from market import hours

    now = hours.now_ist()
    if (agents.swing_settings()["enabled"] and hours.is_open()
            and (now.hour, now.minute) >= RUN_AT and now.date() not in _attempted):
        _attempted.add(now.date())
        outcome = run_once()
        log.info("swing: %s", outcome.get("symbol") or outcome.get("error") or "no setups")
