"""
Honest strategy evaluation on daily bars — one harness for every strategy.

    venv/bin/python -m pipeline.evaluate              # all strategies, prints a table
    venv/bin/python -m pipeline.evaluate --refresh    # re-download prices first

What makes these numbers more trustworthy than a single average per trade:

  portfolio, not trades   positions are sized and capped like the live agents
                          (risk per trade, max open, cash), marked to market daily
  real costs              delivery charges both legs (autopilot/costs.py) and
                          slippage that grows with the order's share of the
                          stock's daily traded value
  benchmarks              Nifty 50 buy-and-hold and random entries with the
                          same exits — a strategy that can't beat both has no edge
  standard measures       CAGR, Sharpe, maximum drawdown, exposure (share of
                          days with money in the market), trades, win rate
  a held-out period       rules are tuned on 2019–2024 only (IN_SAMPLE); 2025
                          onward (HOLDOUT) is reported separately and must not
                          be used to choose parameters

Known limits, stated wherever results are shown:

  survivorship bias       the universe is today's large caps (config/universe.json);
                          stocks that fell out of the indices are missing, so
                          absolute returns are flattered. Compare against the
                          random and buy-all columns, which share the bias.
  no intraday here        Yahoo only serves 60 days of 5-minute bars, too few to
                          evaluate intraday rules this way.

Results are written to data/cache/evaluation.json and shown on the Autopilot page.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from core.config import DATA_DIR

log = logging.getLogger("tradeo.evaluate")

START = "2019-01-01"           # one year of warm-up before the first trade
IN_SAMPLE = ("2020-01-01", "2024-12-31")
HOLDOUT = ("2025-01-01", None)
STARTING_CAPITAL = 1_000_000.0
RISK_FREE = 0.065              # Indian T-bill yield, roughly; used in the Sharpe ratio
SURVIVORSHIP_NOTE = ("Universe is today's large caps (survivorship bias): absolute returns are "
                     "flattered. Compare with the buy-all and random columns, which share the bias.")

PRICES_CACHE = DATA_DIR / "cache" / "evaluation_prices.parquet"
RESULTS = DATA_DIR / "cache" / "evaluation.json"


# ---- data -------------------------------------------------------------------------


def load_prices(refresh: bool = False) -> pd.DataFrame:
    """Daily OHLCV for the universe's equities since START (cached for a day)."""
    if not refresh and PRICES_CACHE.exists() and time.time() - PRICES_CACHE.stat().st_mtime < 86400:
        return pd.read_parquet(PRICES_CACHE)
    from ml.flybrain.prepare import download, universe_equities

    prices = download(universe_equities(), period="10y")
    prices["date"] = pd.to_datetime(prices["date"]).dt.tz_localize(None).dt.normalize()
    prices = prices[prices["date"] >= START].reset_index(drop=True)
    PRICES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(PRICES_CACHE)
    return prices


def load_nifty() -> pd.Series:
    from market import data

    bars = data.history("^NSEI", period="10y", interval="1d")
    closes = bars["Close"].dropna()
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    return closes[closes.index >= START]


def panel(prices: pd.DataFrame, column: str) -> pd.DataFrame:
    """date × symbol table of one column."""
    return prices.pivot_table(index="date", columns="symbol", values=column).sort_index()


def market_uptrend(nifty: pd.Series) -> pd.Series:
    """True on days the Nifty 50 closed above its 200-day average (known at that close)."""
    return nifty > nifty.rolling(200).mean()


# ---- metrics --------------------------------------------------------------------------


def metrics(equity: pd.Series, invested: pd.Series | None = None) -> dict[str, Any]:
    """CAGR, Sharpe, max drawdown and exposure of a daily equity curve."""
    equity = equity.dropna()
    if len(equity) < 20:
        return {"days": len(equity)}
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    total = equity.iloc[-1] / equity.iloc[0]
    daily = equity.pct_change().dropna()
    excess = daily - RISK_FREE / 252
    sharpe = float(excess.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0
    drawdown = equity / equity.cummax() - 1
    out = {
        "from": str(equity.index[0].date()),
        "to": str(equity.index[-1].date()),
        "cagr_pct": round((total ** (1 / years) - 1) * 100, 2) if years > 0 else None,
        "total_return_pct": round((total - 1) * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
        "volatility_pct": round(float(daily.std() * math.sqrt(252)) * 100, 2),
    }
    if invested is not None:
        invested = invested.reindex(equity.index).fillna(0)
        out["exposure_pct"] = round(float((invested > 0).mean()) * 100, 1)
        out["avg_invested_pct"] = round(float((invested / equity).mean()) * 100, 1)
    return out


def trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnl = np.array([t["pnl"] for t in trades])
    ret = np.array([t["return_pct"] for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    return {
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "avg_trade_pct": round(float(ret.mean()), 3),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
    }


def window(series: pd.Series, span: tuple[str, str | None]) -> pd.Series:
    start, end = span
    s = series[series.index >= start]
    return s[s.index <= end] if end else s


# ---- the portfolio simulator ------------------------------------------------------------


@dataclass
class Position:
    symbol: str
    qty: int
    entry_date: pd.Timestamp
    entry_price: float          # after slippage
    cost: float                 # cash paid incl. charges
    stop: float
    target: float
    exit_by: int                # index into dates


@dataclass
class Book:
    cash: float = STARTING_CAPITAL
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)


def _fill(price: float, side: str, notional: float, adv: float | None) -> float:
    from autopilot import costs

    return costs.fill_price(price, side, order_value=notional, daily_value=adv)


def _buy(book: Book, symbol: str, price: float, qty: int, date, adv, stop, target, exit_by) -> bool:
    from autopilot import costs

    if qty <= 0:
        return False
    fill = _fill(price, "BUY", qty * price, adv)
    value = qty * fill
    total = value + costs.charges(value, "BUY")
    if total > book.cash:
        qty = int(book.cash / (fill * 1.003))
        if qty <= 0:
            return False
        value = qty * fill
        total = value + costs.charges(value, "BUY")
    book.cash -= total
    book.positions[symbol] = Position(symbol, qty, date, fill, total, stop, target, exit_by)
    return True


def _sell(book: Book, symbol: str, price: float, date, adv, reason: str) -> None:
    from autopilot import costs

    p = book.positions.pop(symbol)
    fill = _fill(price, "SELL", p.qty * price, adv)
    value = p.qty * fill
    proceeds = value - costs.charges(value, "SELL")
    book.cash += proceeds
    book.trades.append({
        "symbol": symbol, "entry_date": str(p.entry_date.date()), "exit_date": str(date.date()),
        "pnl": proceeds - p.cost, "return_pct": (proceeds / p.cost - 1) * 100, "reason": reason,
    })


def _mark(book: Book, closes: pd.Series) -> tuple[float, float]:
    invested = sum(p.qty * float(closes.get(s, p.entry_price)) for s, p in book.positions.items()
                   if not math.isnan(float(closes.get(s, p.entry_price))))
    return book.cash + invested, invested


def simulate_brackets(
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    hold_days: int,
    max_open: int,
    risk_pct: float = 0.25,
    max_position_pct: float = 10.0,
    allowed: pd.Series | None = None,
) -> dict[str, Any]:
    """
    Bracket strategies: `signals` has one row per setup known at a day's close
    (date, symbol, stop_distance, rank). Entry is the next day's open; the stop
    and a 2R target are checked each day (stop first; a gap past a level fills
    at the open); out after `hold_days` sessions at the close. `allowed` is an
    optional date → bool series (e.g. the market filter) gating new entries.
    """
    opens, highs, lows, closes = (panel(prices, c) for c in ("open", "high", "low", "close"))
    adv = (panel(prices, "close") * panel(prices, "volume")).rolling(20).mean()
    dates = closes.index
    by_date = {d: g.sort_values("rank") for d, g in signals.groupby("date")}

    book = Book()
    curve, invested_curve = [], []
    for i, day in enumerate(dates):
        # 1. exits
        for symbol in list(book.positions):
            p = book.positions[symbol]
            o, h, lo, c = (float(f.at[day, symbol]) for f in (opens, highs, lows, closes))
            if any(math.isnan(v) for v in (o, h, lo, c)):
                continue
            a = adv.at[day, symbol]
            if lo <= p.stop:
                _sell(book, symbol, min(o, p.stop), day, a, "stop")
            elif h >= p.target:
                _sell(book, symbol, max(o, p.target), day, a, "target")
            elif i >= p.exit_by:
                _sell(book, symbol, c, day, a, "time")

        # 2. entries from yesterday's signals, at today's open
        if i > 0 and dates[i - 1] in by_date and (allowed is None or bool(allowed.get(dates[i - 1], False))):
            equity, _ = _mark(book, closes.loc[dates[i - 1]])
            for row in by_date[dates[i - 1]].itertuples():
                if len(book.positions) >= max_open:
                    break
                if row.symbol in book.positions:
                    continue
                o = float(opens.at[day, row.symbol])
                if math.isnan(o) or o <= 0:
                    continue
                stop = o - row.stop_distance
                if stop <= 0:
                    continue
                qty = int(min(equity * risk_pct / 100 / row.stop_distance,
                              equity * max_position_pct / 100 / o))
                _buy(book, row.symbol, o, qty, day, adv.at[day, row.symbol], stop,
                     o + 2 * row.stop_distance, i + hold_days)

        equity, invested = _mark(book, closes.loc[day])
        curve.append(equity)
        invested_curve.append(invested)

    return {"equity": pd.Series(curve, index=dates), "invested": pd.Series(invested_curve, index=dates),
            "trades": book.trades}


def simulate_rebalance(
    prices: pd.DataFrame,
    choose: Callable[[pd.Timestamp], list[str]],
    rebalance_dates: list[pd.Timestamp],
) -> dict[str, Any]:
    """
    Equal-weight portfolio of `choose(date)` (decided at that close), traded at
    the next open; names that stay are kept (no churn), the rest are sold.
    """
    opens, closes = panel(prices, "open"), panel(prices, "close")
    adv = (closes * panel(prices, "volume")).rolling(20).mean()
    dates = closes.index
    pending: list[str] | None = None
    rebalance = set(rebalance_dates)

    book = Book()
    curve, invested_curve = [], []
    for day in dates:
        if pending is not None:
            target = pending
            pending = None
            for symbol in [s for s in book.positions if s not in target]:
                o = float(opens.at[day, symbol])
                _sell(book, symbol, o if not math.isnan(o) else float(closes.at[day, symbol]),
                      day, adv.at[day, symbol], "rebalance")
            new = [s for s in target if s not in book.positions]
            if new:
                equity, _ = _mark(book, closes.loc[day])
                per = equity / max(1, len(target))
                for symbol in new:
                    o = float(opens.at[day, symbol])
                    if math.isnan(o) or o <= 0:
                        continue
                    _buy(book, symbol, o, int(per / o), day, adv.at[day, symbol], 0.0, math.inf, 10**9)
        if day in rebalance:
            pending = choose(day)
        equity, invested = _mark(book, closes.loc[day])
        curve.append(equity)
        invested_curve.append(invested)

    # Close what's left so the last trades count in the statistics.
    for symbol in list(book.positions):
        _sell(book, symbol, float(closes[symbol].dropna().iloc[-1]), dates[-1], None, "end")
    return {"equity": pd.Series(curve, index=dates), "invested": pd.Series(invested_curve, index=dates),
            "trades": book.trades}


# ---- strategies -------------------------------------------------------------------------


def swing_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """The live swing rule (pipeline/swing.py): 20-day-high breakout on 2x volume above the 50-day average."""
    rows = []
    for symbol, d in prices.groupby("symbol"):
        d = d.sort_values("date").reset_index(drop=True)
        prev = d.close.shift()
        tr = pd.concat([d.high - d.low, (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        hi20 = d.high.rolling(20).max().shift(1)
        vol20 = d.volume.rolling(20).mean().shift(1)
        sma50 = d.close.rolling(50).mean()
        hit = (d.close > hi20) & (d.volume > 2 * vol20) & (d.close > sma50)
        for i in np.flatnonzero(hit.to_numpy()):
            rows.append({"date": d.date[i], "symbol": symbol, "stop_distance": 2 * float(atr[i]),
                         "rank": -float(d.volume[i] / vol20[i])})
    return pd.DataFrame(rows)


def random_like(signals: pd.DataFrame, prices: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Same dates and stop distances (as a % of price), random stocks: the no-skill control."""
    rng = np.random.default_rng(seed)
    closes = panel(prices, "close")
    out = []
    for row in signals.itertuples():
        available = closes.loc[row.date].dropna()
        symbol = rng.choice(available.index)
        pct = row.stop_distance / float(closes.at[row.date, row.symbol])
        out.append({"date": row.date, "symbol": symbol,
                    "stop_distance": pct * float(available[symbol]), "rank": rng.random()})
    return pd.DataFrame(out)


def month_ends(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(dates, index=dates)
    return list(s.groupby([dates.year, dates.month]).max())


def momentum_chooser(prices: pd.DataFrame, lookback: int, top: int, skip: int = 21,
                     uptrend: pd.Series | None = None) -> Callable[[pd.Timestamp], list[str]]:
    """Top `top` by return over `lookback` days ending `skip` days ago, above their 200-day average."""
    closes = panel(prices, "close")
    momentum = closes.shift(skip) / closes.shift(skip + lookback) - 1
    above = closes > closes.rolling(200).mean()

    def choose(day: pd.Timestamp) -> list[str]:
        if uptrend is not None and not bool(uptrend.get(day, False)):
            return []                                   # risk-off: all cash
        m = momentum.loc[day][above.loc[day]].dropna()
        m = m[m > 0]
        return list(m.sort_values(ascending=False).index[:top])

    return choose


def buy_all_chooser(prices: pd.DataFrame) -> Callable[[pd.Timestamp], list[str]]:
    closes = panel(prices, "close")
    return lambda day: list(closes.loc[day].dropna().index)


# ---- run everything ---------------------------------------------------------------------


def _report(name: str, sim: dict[str, Any], nifty: pd.Series, note: str = "") -> dict[str, Any]:
    out = {"name": name, "note": note}
    for label, span in (("in_sample", IN_SAMPLE), ("holdout", HOLDOUT)):
        eq = window(sim["equity"], span)
        inv = window(sim["invested"], span)
        trades = [t for t in sim["trades"]
                  if t["entry_date"] >= span[0] and (span[1] is None or t["entry_date"] <= span[1])]
        bench = window(nifty, span)
        out[label] = {**metrics(eq / eq.iloc[0] * STARTING_CAPITAL, inv), **trade_stats(trades),
                      "nifty_cagr_pct": metrics(bench).get("cagr_pct"),
                      "nifty_max_drawdown_pct": metrics(bench).get("max_drawdown_pct")}
    return out


def run(refresh: bool = False) -> dict[str, Any]:
    started = time.time()
    prices = load_prices(refresh)
    nifty = load_nifty()
    uptrend = market_uptrend(nifty)
    results: list[dict[str, Any]] = []

    # Swing — as live, with the market filter, and the random control.
    from autopilot.agents import SWING_DEFAULTS

    signals = swing_signals(prices)
    for name, sigs, gate, note in (
        ("Swing breakout", signals, None, "no market filter"),
        ("Swing breakout + market filter", signals, uptrend, "new buys only while Nifty > 200-day average"),
        ("Random entries (swing exits)", random_like(signals, prices), None, "no-skill control"),
    ):
        sim = simulate_brackets(prices, sigs, hold_days=10, max_open=int(SWING_DEFAULTS["max_open"]), allowed=gate)
        results.append(_report(name, sim, nifty, note))

    # Monthly momentum — lookback chosen on IN_SAMPLE only (see momentum.py).
    from pipeline import momentum

    rebal = month_ends(panel(prices, "close").index)
    for name, chooser, note in (
        (f"Momentum top {momentum.TOP} (monthly)",
         momentum_chooser(prices, momentum.LOOKBACK, momentum.TOP), "always invested"),
        (f"Momentum top {momentum.TOP} + market filter",
         momentum_chooser(prices, momentum.LOOKBACK, momentum.TOP, uptrend=uptrend),
         "cash while Nifty < 200-day average"),
        ("Buy all (equal weight, monthly)", buy_all_chooser(prices), "no-skill control"),
    ):
        results.append(_report(name, simulate_rebalance(prices, chooser, rebal), nifty, note))

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "seconds": round(time.time() - started, 1),
        "in_sample": f"{IN_SAMPLE[0]} → {IN_SAMPLE[1]}",
        "holdout": f"{HOLDOUT[0]} → today",
        "symbols": int(prices["symbol"].nunique()),
        "costs": "delivery charges both legs + size/liquidity-scaled slippage",
        "risk_free_pct": RISK_FREE * 100,
        "caveats": [SURVIVORSHIP_NOTE,
                    "Intraday is not evaluated here: Yahoo serves only 60 days of 5-minute bars.",
                    "Hold-out results are for checking, not tuning. Changing a rule after looking at "
                    "them turns them into in-sample results."],
        "results": results,
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(report, indent=2, default=str))
    return report


def latest() -> dict[str, Any] | None:
    try:
        return json.loads(RESULTS.read_text())
    except (OSError, ValueError):
        return None


def table(report: dict[str, Any]) -> str:
    lines = []
    for period in ("in_sample", "holdout"):
        lines.append(f"\n{period.upper()} ({report[period]})")
        lines.append(f"{'strategy':44} {'CAGR':>7} {'Nifty':>7} {'Sharpe':>7} {'MaxDD':>7} "
                     f"{'Expo':>6} {'Trades':>7} {'Win%':>6} {'Avg%':>7}")
        for r in report["results"]:
            m = r[period]
            lines.append(f"{r['name'][:44]:44} {m.get('cagr_pct', 0):>7.1f} {m.get('nifty_cagr_pct') or 0:>7.1f} "
                         f"{m.get('sharpe', 0):>7.2f} {m.get('max_drawdown_pct', 0):>7.1f} "
                         f"{m.get('exposure_pct', 0):>6.0f} {m.get('trades', 0):>7} "
                         f"{m.get('win_rate_pct', 0):>6.1f} {m.get('avg_trade_pct', 0):>7.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rep = run(refresh="--refresh" in sys.argv)
    print(table(rep))
    for c in rep["caveats"]:
        print("•", c)
