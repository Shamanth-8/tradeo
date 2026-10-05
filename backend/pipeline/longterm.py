"""
Long-term picks by holding period: 1 month, 6 months, 1 year, more than a year.

Lists, not orders: each horizon ranks the universe's equities by the rule that
was tested for that horizon, and says how that rule did.

  1 month     3-month momentum, skipping the latest month, above the 50-day average
  6 months    6-month momentum, skipping the latest month, above the 200-day average
  1 year      12-month momentum, skipping the latest month, above the 200-day average
  1 year +    quality: return on equity, low debt, earnings growth, above the
              200-day average. No backtest: there is no free history of
              fundamentals, so this is a screen, not a tested strategy.

"Skipping the latest month" is the standard momentum construction: the most
recent month tends to reverse, so it is left out of the ranking.

Backtests (2020–26, top 10 vs buying all 66, after costs) use today's large
caps, which flatters absolute returns (survivorship); the gap over buying
everything is the meaningful part.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import pandas as pd

from core.config import DATA_DIR

log = logging.getLogger("tradeo.longterm")

CACHE = DATA_DIR / "cache" / "longterm.json"
FUND_CACHE = DATA_DIR / "cache" / "fundamentals.json"
TTL = 6 * 3600
FUND_TTL = 24 * 3600
TOP = 10

HORIZONS = {
    "1m": {"label": "1 month", "lookback": 63, "trend": 50,
           "rule": "Strongest 3-month momentum (skipping the latest month), above the 50-day average.",
           "evidence": "Backtest 2020–26, rebalanced monthly: 20.0%/yr vs 17.1% buying all; beat it in 56% of months (80)."},
    "6m": {"label": "6 months", "lookback": 126, "trend": 200,
           "rule": "Strongest 6-month momentum (skipping the latest month), above the 200-day average.",
           "evidence": "Backtest 2020–26: 41.7%/yr vs 30.2% buying all; beat it in 8 of 12 half-years. Few periods."},
    "1y": {"label": "1 year", "lookback": 252, "trend": 200,
           "rule": "Strongest 12-month momentum (skipping the latest month), above the 200-day average.",
           "evidence": "Backtest 2020–26: 43.2%/yr vs 32.5% buying all; beat it in 3 of 5 years. Very few periods."},
    "1y+": {"label": "More than 1 year", "lookback": None, "trend": 200,
            "rule": "Quality: high return on equity, low debt, growing earnings, above the 200-day average.",
            "evidence": "Not backtested — no free history of company fundamentals. A screen, not a tested strategy."},
}
SKIP = 21  # trading days in the skipped latest month

_lock = threading.Lock()


def _fundamentals(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """ROE, debt/equity and growth from Yahoo, cached for a day (it is slow: ~1 s a stock)."""
    try:
        cached = json.loads(FUND_CACHE.read_text())
        if time.time() - cached.get("at", 0) < FUND_TTL and set(symbols) <= set(cached["data"]):
            return cached["data"]
    except (OSError, ValueError, KeyError):
        pass
    from market import data as market_data

    data: dict[str, dict[str, Any]] = {}
    for s in symbols:
        info = market_data.info(s + ".NS")  # {} when Yahoo has nothing
        data[s] = {"roe": info.get("returnOnEquity"), "debt_to_equity": info.get("debtToEquity"),
                   "earnings_growth": info.get("earningsGrowth"), "revenue_growth": info.get("revenueGrowth"),
                   "pe": info.get("trailingPE"), "name": info.get("shortName"), "roe_source": "yahoo"}
        if data[s]["roe"] is None:
            # Yahoo has ROE for only ~1 in 5 NSE stocks; Screener.in has it for nearly all.
            try:
                from data.fetchers.screener_scraper import screener_scraper

                roe = (screener_scraper.get_company_data(s).get("top_ratios") or {}).get("ROE")
                if roe is not None:
                    data[s].update(roe=float(roe) / 100, roe_source="screener.in")
                time.sleep(0.5)  # be polite to Screener
            except Exception as exc:
                log.info("screener ROE unavailable for %s: %s", s, exc)
    FUND_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FUND_CACHE.write_text(json.dumps({"at": time.time(), "data": data}))
    return data


def build() -> dict[str, Any]:
    from ai.symbols import display_name
    from ml.flybrain.prepare import download, universe_equities

    prices = download(universe_equities(), period="2y")
    rows = []
    for symbol, d in prices.groupby("symbol"):
        c = d.sort_values("date").close.reset_index(drop=True)
        if len(c) < 252 + SKIP:
            continue
        last = float(c.iloc[-1])
        row = {"symbol": symbol, "name": display_name(symbol) or symbol, "price": round(last, 2),
               "above_50dma": bool(last > c.tail(50).mean()), "above_200dma": bool(last > c.tail(200).mean()),
               "ret_1m_pct": round((last / c.iloc[-1 - SKIP] - 1) * 100, 1)}
        for key, h in HORIZONS.items():
            if h["lookback"]:
                row[f"mom_{key}"] = round((c.iloc[-1 - SKIP] / c.iloc[-1 - SKIP - h["lookback"]] - 1) * 100, 1)
        rows.append(row)
    table = pd.DataFrame(rows)

    lists: dict[str, Any] = {}
    for key, h in HORIZONS.items():
        trend = table["above_50dma"] if h["trend"] == 50 else table["above_200dma"]
        if h["lookback"]:
            col = f"mom_{key}"
            picks = table[trend & (table[col] > 0)].sort_values(col, ascending=False).head(TOP)
            items = [{"symbol": r.symbol, "name": r.name, "price": r.price,
                      "metric": f"{getattr(r, f'mom_{key}'):+.1f}% momentum",
                      "detail": f"last month {r.ret_1m_pct:+.1f}%"} for r in picks.itertuples()]
        else:
            fund = _fundamentals(table.symbol.tolist())
            q = table[trend].copy()
            for col in ("roe", "debt_to_equity", "earnings_growth"):
                q[col] = q.symbol.map(lambda s: (fund.get(s) or {}).get(col))
            q = q.dropna(subset=["roe"])
            q = q[q.roe > 0.12]                       # 12%+ return on equity
            score = (q.roe.rank(pct=True)
                     + (-q.debt_to_equity.fillna(q.debt_to_equity.median())).rank(pct=True)
                     + q.earnings_growth.fillna(0).rank(pct=True))
            picks = q.assign(score=score).sort_values("score", ascending=False).head(TOP)
            items = [{"symbol": r.symbol, "name": r.name, "price": r.price,
                      "metric": f"ROE {r.roe * 100:.0f}%",
                      "detail": (f"debt/equity {r.debt_to_equity:.0f}" if pd.notna(r.debt_to_equity) else "debt n/a")
                                + (f" · earnings {r.earnings_growth * 100:+.0f}%" if pd.notna(r.earnings_growth) else "")}
                     for r in picks.itertuples()]
        lists[key] = {**{k: h[k] for k in ("label", "rule", "evidence")}, "picks": items}

    return {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "universe": int(len(table)),
            "caveat": ("Lists, not advice. Momentum backtests use today's large caps, which inflates "
                       "absolute returns; compare against buying everything."),
            "horizons": lists}


def picks(refresh: bool = False) -> dict[str, Any]:
    with _lock:
        if not refresh:
            try:
                cached = json.loads(CACHE.read_text())
                if time.time() - cached.get("_at", 0) < TTL:
                    return cached
            except (OSError, ValueError):
                pass
        result = build()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({**result, "_at": time.time()}))
        return result
