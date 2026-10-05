"""
Monthly momentum: hold the strongest stocks, rebalance once a month.

The rule (daily bars, the universe's equities):

  rank     return over the last 3 months, skipping the latest month (the most
           recent month tends to reverse), among stocks above their 200-day
           average with positive momentum
  hold     the top TOP, equal weight, CAPITAL_PCT of the paper account in all
  when     the first session of each month, at RUN_AT IST; names that stay in
           the list are kept (no churn), the rest are sold, new ones bought
  filter   while the Nifty 50 is below its 200-day average, hold cash

Why this one: of everything in Tradeo, cross-sectional momentum has the best
evidence — here and in decades of published research — and it trades about
once a month, so costs stay small. The 3-month lookback was chosen on
2020–2024 data only (63, 126 and 252 days all gave similar results, which is
reassuring); 2025 onward is held out. Numbers: `python -m pipeline.evaluate`.

Each holding is a paper bracket with a wide catastrophic stop (STOP_PCT) and
no practical target; the monthly rebalance is the real exit.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger("tradeo.momentum")

SOURCE = "momentum"
LOOKBACK = 63          # trading days (~3 months), chosen in-sample
SKIP = 21              # skip the latest month
TOP = 10
CAPITAL_PCT = 50.0     # share of the paper account this agent invests
STOP_PCT = 25.0        # catastrophic stop below entry
HORIZON_DAYS = 45      # a little over a month; extended at each rebalance
RUN_AT = (9, 30)


def ranking(prices: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    """The universe ranked by the rule, strongest first (only eligible stocks)."""
    from ml.flybrain.prepare import download, universe_equities

    if prices is None:
        prices = download(universe_equities(), period="2y")
    closes = prices.pivot_table(index="date", columns="symbol", values="close").sort_index()
    if len(closes) < LOOKBACK + SKIP + 1:
        return []
    momentum = closes.shift(SKIP).iloc[-1] / closes.shift(SKIP + LOOKBACK).iloc[-1] - 1
    above = closes.iloc[-1] > closes.rolling(200).mean().iloc[-1]
    eligible = momentum[above & (momentum > 0)].dropna().sort_values(ascending=False)
    return [{"symbol": s, "momentum_pct": round(float(m) * 100, 2), "close": round(float(closes[s].iloc[-1]), 2)}
            for s, m in eligible.items()]


def _settings() -> dict[str, Any]:
    from autopilot import agents

    return agents.momentum_settings()


def _held() -> dict[str, dict[str, Any]]:
    from autopilot import triggers

    return {t["symbol"]: t for t in triggers.active() if t.get("source") == SOURCE}


def rebalance() -> dict[str, Any]:
    """Bring the holdings in line with this month's list."""
    from autopilot import risk, store, triggers
    from data.fetchers.stock_fetcher import stock_fetcher

    rules = _settings()
    if not rules["enabled"]:
        return {"ok": False, "error": "momentum agent is switched off"}

    trend = risk.market_trend()
    ranked = ranking()
    top = int(rules["top"])
    target = [] if trend["ok"] is False else [r["symbol"] for r in ranked[:top]]
    held = _held()

    sold, kept, bought, skipped = [], [], [], []
    for symbol, t in held.items():
        if symbol in target:
            triggers.extend(t["id"], HORIZON_DAYS)
            kept.append(symbol)
        else:
            out = triggers.cancel(t["id"])
            sold.append({"symbol": symbol, "realised_pnl": out.get("realised_pnl")})

    equity = float(store.paper_account()["equity"])
    per_name = equity * float(rules["capital_pct"]) / 100 / max(1, top)
    by_symbol = {r["symbol"]: r for r in ranked}
    for symbol in target:
        if symbol in held:
            continue
        price = float(stock_fetcher.get_live_price(symbol).get("price") or 0)
        if price <= 0:
            skipped.append({"symbol": symbol, "why": "no live price"})
            continue
        result = triggers.arm(
            symbol=symbol, entry_price=price, stop_loss=price * (1 - STOP_PCT / 100),
            target=price * 3, quantity=int(per_name // price), conviction=60, source=SOURCE,
            horizon_days=HORIZON_DAYS,
            reason=(f"monthly momentum: #{target.index(symbol) + 1} by 3-month return "
                    f"(+{by_symbol[symbol]['momentum_pct']}%, latest month skipped), above its 200-day average"))
        if result.get("ok"):
            bought.append(result)
        else:
            skipped.append({"symbol": symbol, "why": result.get("error")})

    note = trend["detail"] + ("; holding cash" if trend["ok"] is False else "")
    _announce(bought, sold, note)
    return {"ok": True, "market": note, "target": target, "kept": kept, "sold": sold,
            "bought": bought, "skipped": skipped,
            "symbol": ", ".join(b["symbol"] for b in bought)}


def _announce(bought: list, sold: list, note: str) -> None:
    if not (bought or sold):
        return
    try:
        from integrations.telegram import bot

        if bot.enabled:
            lines = [f"BUY {b['symbol']} {b['quantity']} @ ₹{b['entry_price']:,.2f}" for b in bought]
            lines += [f"SELL {s['symbol']} (P&L ₹{s['realised_pnl'] or 0:,.0f})" for s in sold]
            bot.send("🔁 <b>Monthly momentum (paper)</b>\n" + note + "\n" + "\n".join(lines))
    except Exception as exc:  # alerts are best effort; the trade is already booked
        from core import failures

        failures.record("telegram.momentum", exc, log)


def tick() -> None:
    """Called every minute by the agent scheduler: rebalance once, on the month's first session."""
    from autopilot import agents
    from market import hours

    rules = _settings()
    now = hours.now_ist()
    month = now.strftime("%Y-%m")
    if (rules["enabled"] and hours.is_open() and (now.hour, now.minute) >= RUN_AT
            and rules.get("last_rebalance") != month):
        agents.set_momentum_state(last_rebalance=month)   # one attempt per month
        outcome = rebalance()
        log.info("momentum rebalance: %s", outcome.get("symbol") or outcome.get("error") or outcome.get("market"))
