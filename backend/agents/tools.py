"""
Deterministic tools — the arithmetic the agents reason *about*.

Nothing in this module calls a language model. That is the point: every number
an agent quotes has to come from a function you could re-run by hand and get
the same answer. When a verdict later says "the stop is inside one day's noise",
that claim traces to `atr()` and not to a model's impression.

Each tool returns `Evidence`, which carries the numbers *and* the chart that
explains them. Coupling those together is deliberate — a chart built later from
a bag of results is a chart that can quietly misrepresent what happened.

Data comes from yfinance, which is free, keyless, and covers NSE via the `.NS`
suffix: OHLCV, fundamentals, financial statements, earnings dates and
institutional holders. It is the widest free source available for Indian
equities, and it is what the rest of the app already uses.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from data.cache import cached

from .runtime import Chart, Evidence

log = logging.getLogger("tradeo.agents.tools")

# India-specific constants. Stated here rather than buried so they can be
# argued with.
RISK_FREE_RATE = 0.068          # ~10Y G-Sec
EQUITY_RISK_PREMIUM = 0.055     # long-run India equity premium
NIFTY = "^NSEI"


def _f(value: Any, default: float = 0.0) -> float:
    """Float coercion that also rejects NaN, which pandas produces constantly."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(result) or math.isinf(result) else result


def _yf(symbol: str) -> str:
    symbol = symbol.upper().strip()
    return symbol if symbol.startswith("^") or "." in symbol else f"{symbol}.NS"


# ---- data access -----------------------------------------------------------


@cached(ttl=900, prefix="agents.history", skip_if=lambda r: r is None or r.empty)
def history(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame | None:
    from market import data

    frame = data.history(_yf(symbol), period=period, interval=interval)
    if frame.empty:
        return None
    return frame


@cached(ttl=1800, prefix="agents.info", skip_if=lambda r: not r)
def info(symbol: str) -> dict[str, Any]:
    """
    The fundamentals blob.

    yfinance's `.info` is a single expensive call, so it is fetched once and
    every tool reads from the same cached copy rather than re-requesting.
    """
    from market import data

    return data.info(_yf(symbol))


@cached(ttl=86400, prefix="agents.financials", skip_if=lambda r: not r)
def financials(symbol: str) -> dict[str, Any]:
    """
    Income statement and balance sheet history.

    This is what makes a real valuation possible rather than a multiple guess —
    you need several years of earnings to see whether they are growing or just
    volatile.
    """
    from market import data

    out: dict[str, Any] = {}
    try:
        frames = data.statements(_yf(symbol))
        income = frames.get("income")
        balance = frames.get("balance")
        cashflow = frames.get("cashflow")

        def series(frame: Any, *names: str) -> list[dict[str, Any]]:
            if frame is None or getattr(frame, "empty", True):
                return []
            for name in names:
                if name in frame.index:
                    row = frame.loc[name]
                    return [
                        {"period": str(col.date()) if hasattr(col, "date") else str(col),
                         "value": _f(val)}
                        for col, val in row.items() if _f(val)
                    ]
            return []

        out["revenue"] = series(income, "Total Revenue", "Operating Revenue")
        out["net_income"] = series(income, "Net Income", "Net Income Common Stockholders")
        out["operating_income"] = series(income, "Operating Income", "EBIT")
        out["equity"] = series(balance, "Stockholders Equity", "Total Equity Gross Minority Interest")
        out["total_debt"] = series(balance, "Total Debt")
        out["free_cash_flow"] = series(cashflow, "Free Cash Flow")
    except Exception as exc:
        log.warning("financials failed for %s: %s", symbol, exc)
    return out


# ---- technical tools -------------------------------------------------------


def atr(frame: pd.DataFrame, period: int = 14) -> float:
    """Average true range — the volatility unit exits are priced in."""
    if frame is None or len(frame) < period + 1:
        return 0.0
    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    close = frame["Close"].to_numpy(dtype=float)
    prev = close[:-1]
    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev), np.abs(low[1:] - prev)),
    )
    return float(true_range[-period:].mean())


def rsi(series: pd.Series, period: int = 14) -> float:
    if series is None or len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(gain.iloc[-1]) / last_loss
    return float(100 - 100 / (1 + rs))


def technical_evidence(symbol: str) -> Evidence:
    """Trend, momentum and volatility, with the price series to show it on."""
    frame = history(symbol, period="1y")
    if frame is None:
        return Evidence(label="Technicals", confidence=0,
                        caveats=["no price history available"], source="yfinance")

    close = frame["Close"]
    price = _f(close.iloc[-1])
    sma20 = _f(close.rolling(20).mean().iloc[-1])
    sma50 = _f(close.rolling(50).mean().iloc[-1])
    sma200 = _f(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else 0.0
    current_atr = atr(frame)
    current_rsi = rsi(close)

    high_52 = _f(frame["High"].max())
    low_52 = _f(frame["Low"].min())
    position_in_range = ((price - low_52) / (high_52 - low_52) * 100) if high_52 > low_52 else 50.0

    # 60 sessions is enough to see the shape without making the chart unreadable.
    tail = frame.tail(60)
    series_data = [
        {
            "date": str(idx.date()),
            "close": round(_f(row["Close"]), 2),
            "sma20": round(_f(close.rolling(20).mean().loc[idx]), 2),
            "upper": round(_f(row["Close"]) + 2 * current_atr, 2),
            "lower": round(_f(row["Close"]) - 2 * current_atr, 2),
        }
        for idx, row in tail.iterrows()
    ]

    trend = "up" if price > sma50 > sma200 and sma200 else ("down" if price < sma50 else "sideways")

    return Evidence(
        label="Technicals",
        source="yfinance daily bars",
        confidence=80.0 if len(frame) > 200 else 55.0,
        values={
            "price": round(price, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "rsi": round(current_rsi, 1),
            "atr": round(current_atr, 2),
            "atr_percent": round(current_atr / price * 100, 2) if price else 0,
            "high_52w": round(high_52, 2),
            "low_52w": round(low_52, 2),
            "position_in_52w_range": round(position_in_range, 1),
            "trend": trend,
        },
        caveats=[] if len(frame) >= 200 else ["under a year of history — 200-day trend unavailable"],
        charts=[
            Chart(
                kind="series",
                title="Price with 2×ATR envelope",
                data=series_data,
                config={
                    "x": "date",
                    "lines": [
                        {"key": "close", "label": "Close", "emphasis": True},
                        {"key": "sma20", "label": "20-day average"},
                    ],
                    "band": {"upper": "upper", "lower": "lower", "label": "±2 ATR"},
                },
                caption=(
                    f"One ATR is ₹{current_atr:,.2f} ({current_atr / price * 100:.1f}% of price). "
                    "Any stop inside this band is inside normal daily noise."
                ),
            )
        ],
    )


# ---- valuation tools -------------------------------------------------------


def valuation_evidence(symbol: str) -> Evidence:
    """
    Fair value by every method the data supports, shown side by side.

    Deliberately *not* collapsed into one number. Three methods disagreeing by
    40% is the single most useful thing a valuation can tell you, and averaging
    them into a tidy figure destroys exactly that information.
    """
    meta = info(symbol)
    frame = history(symbol, period="1y")
    price = _f(meta.get("currentPrice")) or (_f(frame["Close"].iloc[-1]) if frame is not None else 0.0)

    if price <= 0:
        return Evidence(label="Valuation", confidence=0,
                        caveats=["no price available"], source="yfinance")

    eps = _f(meta.get("trailingEps"))
    forward_eps = _f(meta.get("forwardEps"))
    book = _f(meta.get("bookValue"))
    roe = _f(meta.get("returnOnEquity"))
    growth = _f(meta.get("earningsGrowth")) or _f(meta.get("revenueGrowth"))
    trailing_pe = _f(meta.get("trailingPE"))
    sector_pe = 22.0  # broad NSE median; a sector table would refine this
    fcf = _f(meta.get("freeCashflow"))
    shares = _f(meta.get("sharesOutstanding"))

    methods: list[dict[str, Any]] = []

    # 1. Earnings power at a justified multiple.
    if eps > 0:
        growth_pct = growth * 100 if abs(growth) < 1 else growth
        justified = sector_pe * (1 + max(-0.4, min(0.5, growth_pct / 100)))
        methods.append({
            "method": "Earnings × justified P/E",
            "value": round(eps * justified, 2),
            "detail": f"EPS ₹{eps:.2f} × {justified:.1f}",
            "weight": 0.4,
        })

    # 2. Forward earnings, which price in guidance rather than history.
    if forward_eps > 0:
        methods.append({
            "method": "Forward earnings",
            "value": round(forward_eps * sector_pe, 2),
            "detail": f"Forward EPS ₹{forward_eps:.2f} × {sector_pe:.0f}",
            "weight": 0.25,
        })

    # 3. Book value at an ROE-justified multiple — the honest method for
    #    financials and anything with lumpy earnings.
    if book > 0:
        roe_pct = roe * 100 if abs(roe) < 1 else roe
        multiple = max(0.6, min(4.0, roe_pct / 12.0)) if roe_pct else 1.5
        methods.append({
            "method": "Book × ROE multiple",
            "value": round(book * multiple, 2),
            "detail": f"Book ₹{book:.2f} × {multiple:.2f} (ROE {roe_pct:.1f}%)",
            "weight": 0.2,
        })

    # 4. A perpetuity on free cash flow. Crude, but it is the only method here
    #    anchored to cash rather than to what the market pays for peers.
    if fcf > 0 and shares > 0:
        discount = RISK_FREE_RATE + EQUITY_RISK_PREMIUM
        growth_assumption = 0.04
        per_share = (fcf / shares) * (1 + growth_assumption) / (discount - growth_assumption)
        methods.append({
            "method": "FCF perpetuity",
            "value": round(per_share, 2),
            "detail": f"FCF/share ₹{fcf / shares:.2f}, {discount * 100:.1f}% discount, 4% growth",
            "weight": 0.15,
        })

    if not methods:
        return Evidence(
            label="Valuation", source="yfinance fundamentals", confidence=0,
            values={"price": round(price, 2), "fair_value": round(price, 2), "margin_pct": 0.0},
            caveats=["no usable valuation inputs — treated as fairly priced"],
        )

    total_weight = sum(m["weight"] for m in methods)
    blended = sum(m["value"] * m["weight"] for m in methods) / total_weight
    margin_pct = (blended - price) / price * 100

    values = [m["value"] for m in methods]
    spread_pct = ((max(values) - min(values)) / blended * 100) if blended else 0.0

    caveats: list[str] = []
    # Wide disagreement between methods is the finding, not a nuisance.
    if spread_pct > 50:
        caveats.append(
            f"Methods disagree by {spread_pct:.0f}% — treat the blended figure as a range, "
            "not a target"
        )
    if len(methods) < 2:
        caveats.append("Only one valuation method had inputs — low confidence")
    if trailing_pe and trailing_pe > 60:
        caveats.append(f"Trailing P/E of {trailing_pe:.0f} leaves no room for disappointment")

    confidence = min(85.0, 30.0 + len(methods) * 15.0 - max(0.0, spread_pct - 30) * 0.5)

    chart_rows = [
        {"name": "Market price", "value": round(price, 2), "type": "price"},
        *[{"name": m["method"], "value": m["value"], "type": "method",
           "detail": m["detail"]} for m in methods],
        {"name": "Blended fair value", "value": round(blended, 2), "type": "result"},
    ]

    return Evidence(
        label="Valuation",
        source="yfinance fundamentals",
        confidence=max(10.0, confidence),
        values={
            "price": round(price, 2),
            "fair_value": round(blended, 2),
            "margin_pct": round(margin_pct, 1),
            "method_spread_pct": round(spread_pct, 1),
            "methods": methods,
            "trailing_pe": round(trailing_pe, 1) if trailing_pe else None,
        },
        caveats=caveats,
        charts=[
            Chart(
                kind="bridge",
                title="What it costs versus what each method says it is worth",
                data=chart_rows,
                config={"x": "name", "y": "value", "highlight": "type", "unit": "₹"},
                caption=(
                    f"Blended fair value ₹{blended:,.0f} against a price of ₹{price:,.0f} — "
                    f"a margin of safety of {margin_pct:+.0f}%. "
                    f"Methods span {spread_pct:.0f}%."
                ),
            )
        ],
    )


# ---- quality and growth ----------------------------------------------------


def fundamentals_evidence(symbol: str) -> Evidence:
    """Multi-year revenue and profit, so growth claims are checkable."""
    statements = financials(symbol)
    meta = info(symbol)

    revenue = list(reversed(statements.get("revenue") or []))
    net_income = list(reversed(statements.get("net_income") or []))

    if not revenue:
        return Evidence(
            label="Fundamentals", source="yfinance statements", confidence=20,
            values={
                "roe": round(_f(meta.get("returnOnEquity")) * 100, 1),
                "margin": round(_f(meta.get("profitMargins")) * 100, 1),
                "debt_to_equity_pct": round(_f(meta.get("debtToEquity")), 1),
            },
            caveats=["no financial statement history available"],
        )

    by_period = {r["period"]: {"period": r["period"][:7], "revenue": r["value"] / 1e7}
                 for r in revenue}
    for row in net_income:
        if row["period"] in by_period:
            by_period[row["period"]]["net_income"] = row["value"] / 1e7

    rows = list(by_period.values())

    def cagr(series: list[dict[str, Any]], key: str) -> float:
        points = [r[key] for r in series if r.get(key) and r[key] > 0]
        if len(points) < 2:
            return 0.0
        years = len(points) - 1
        return ((points[-1] / points[0]) ** (1 / years) - 1) * 100

    revenue_cagr = cagr(rows, "revenue")
    profit_cagr = cagr(rows, "net_income")

    # yfinance reports debtToEquity already multiplied by 100, so 10.2 means
    # 10.2%, not 10.2x. Passing the bare number downstream got a model to call
    # a near-debt-free company "highly levered" — the units have to travel with
    # the value.
    debt_to_equity_pct = _f(meta.get("debtToEquity"))

    caveats: list[str] = []
    # Profit growing much faster than revenue is usually margin expansion,
    # which cannot continue forever — worth saying out loud.
    if profit_cagr > revenue_cagr + 15 and revenue_cagr > 0:
        caveats.append(
            f"Profit is compounding at {profit_cagr:.0f}% against revenue at "
            f"{revenue_cagr:.0f}% — margin expansion, which has a ceiling"
        )
    if revenue_cagr < 0:
        caveats.append(f"Revenue is shrinking at {abs(revenue_cagr):.0f}% a year")
    if debt_to_equity_pct > 150:
        caveats.append(f"Debt is {debt_to_equity_pct:.0f}% of equity — leverage cuts both ways")

    return Evidence(
        label="Fundamentals",
        source="yfinance statements",
        confidence=70.0 if len(rows) >= 3 else 45.0,
        values={
            "revenue_cagr": round(revenue_cagr, 1),
            "profit_cagr": round(profit_cagr, 1),
            "years": len(rows),
            "roe": round(_f(meta.get("returnOnEquity")) * 100, 1),
            "margin": round(_f(meta.get("profitMargins")) * 100, 1),
            "debt_to_equity_pct": round(debt_to_equity_pct, 1),
        },
        caveats=caveats,
        charts=[
            Chart(
                kind="bar",
                title="Revenue and net profit by year (₹ crore)",
                data=rows,
                config={
                    "x": "period",
                    "bars": [
                        {"key": "revenue", "label": "Revenue"},
                        {"key": "net_income", "label": "Net profit"},
                    ],
                    "unit": "₹ cr",
                },
                caption=(
                    f"Revenue compounding at {revenue_cagr:.1f}% a year, profit at "
                    f"{profit_cagr:.1f}%, over {len(rows)} reported years."
                ),
            )
        ],
    )


# ---- risk ------------------------------------------------------------------


def beta_evidence(symbol: str) -> Evidence:
    """
    Beta against the Nifty 50, computed here rather than taken from a provider.

    Data providers quote beta against the S&P 500, which for an Indian stock is
    close to meaningless — it is the difference between TCS at 0.85 and TCS at
    0.16, and the second number would size a position wrongly.
    """
    stock = history(symbol, period="1y")
    index = history(NIFTY, period="1y")

    if stock is None or index is None or len(stock) < 60 or len(index) < 60:
        return Evidence(label="Beta", confidence=0, source="computed vs ^NSEI",
                        caveats=["not enough overlapping history"])

    stock_returns = stock["Close"].pct_change().dropna()
    index_returns = index["Close"].pct_change().dropna()
    joined = pd.concat([stock_returns, index_returns], axis=1, join="inner").dropna()
    joined.columns = ["stock", "index"]

    coverage = len(joined) / max(len(index_returns), 1) * 100
    if len(joined) < 50:
        return Evidence(label="Beta", confidence=0, source="computed vs ^NSEI",
                        caveats=[f"only {len(joined)} overlapping sessions"])

    variance = float(joined["index"].var())
    beta = float(joined.cov().iloc[0, 1] / variance) if variance else 0.0
    volatility = float(joined["stock"].std() * math.sqrt(252) * 100)
    index_volatility = float(joined["index"].std() * math.sqrt(252) * 100)

    # Max drawdown over the window — the number that actually predicts whether
    # someone will abandon a position.
    curve = (1 + joined["stock"]).cumprod()
    drawdown = float(((curve - curve.cummax()) / curve.cummax()).min() * 100)

    reading = ("moves more than the market" if beta > 1.15
               else "moves less than the market" if beta < 0.85
               else "moves with the market")

    return Evidence(
        label="Beta and volatility",
        source="computed against ^NSEI",
        confidence=min(90.0, coverage),
        values={
            "beta": round(beta, 2),
            "annual_volatility_pct": round(volatility, 1),
            "nifty_volatility_pct": round(index_volatility, 1),
            "max_drawdown_pct": round(drawdown, 1),
            "sessions": len(joined),
            "reading": reading,
        },
        caveats=[] if coverage > 80 else [f"only {coverage:.0f}% overlap with the index series"],
        charts=[
            Chart(
                kind="gauge",
                title="Beta against the Nifty 50",
                data=[{"name": "Beta", "value": round(beta, 2)}],
                config={
                    "min": 0, "max": 2,
                    "bands": [
                        {"to": 0.85, "label": "Defensive", "tone": "good"},
                        {"to": 1.15, "label": "Market-like", "tone": "neutral"},
                        {"to": 2.0, "label": "Amplified", "tone": "warn"},
                    ],
                },
                caption=(
                    f"Beta {beta:.2f} — {reading}. Annualised volatility {volatility:.0f}% "
                    f"against the index at {index_volatility:.0f}%. "
                    f"Worst drawdown in the window: {drawdown:.0f}%."
                ),
            )
        ],
    )


def concentration_evidence() -> Evidence:
    """
    Portfolio concentration, by Herfindahl index and effective holdings.

    "Effective holdings" is the number that lands: someone with twelve stocks
    and 46% in one of them does not have a twelve-stock portfolio.
    """
    from brokers.registry import registry

    consolidated = registry.consolidated_holdings()
    rows = consolidated["holdings"]
    total = consolidated["totals"]["current_value"]

    if not rows or total <= 0:
        return Evidence(label="Concentration", confidence=0,
                        caveats=["no holdings recorded"], source="broker registry")

    weights = [(r["symbol"], r["current_value"] / total * 100) for r in rows]
    weights.sort(key=lambda w: w[1], reverse=True)

    hhi = sum(w ** 2 for _, w in weights)
    effective = 10000 / hhi if hhi else 0

    level = ("concentrated" if hhi > 2500 else
             "moderately concentrated" if hhi > 1500 else "diversified")

    caveats = [f"{s} is {w:.0f}% of the portfolio" for s, w in weights if w > 25]

    return Evidence(
        label="Concentration",
        source="broker registry",
        confidence=95.0,
        values={
            "hhi": round(hhi, 1),
            "effective_holdings": round(effective, 1),
            "actual_holdings": len(weights),
            "level": level,
            "top_weight_pct": round(weights[0][1], 1),
        },
        caveats=caveats,
        charts=[
            Chart(
                kind="bar",
                title="Portfolio weight by holding",
                data=[{"name": s, "weight": round(w, 2)} for s, w in weights[:12]],
                config={"x": "name", "bars": [{"key": "weight", "label": "Weight %"}],
                        "unit": "%", "threshold": 25},
                caption=(
                    f"HHI {hhi:,.0f} — {level}. {len(weights)} holdings behaving like "
                    f"{effective:.1f}."
                ),
            )
        ],
    )


# ---- scoring ---------------------------------------------------------------


def conviction_waterfall(components: dict[str, float], base: float = 50.0) -> Chart:
    """
    How a conviction number was built, contribution by contribution.

    This is the single most useful chart in the system: it turns "78%
    conviction" from an assertion into an arithmetic statement you can dispute
    one term at a time.
    """
    rows: list[dict[str, Any]] = [{"name": "Base", "value": round(base, 1),
                                   "cumulative": round(base, 1), "type": "base"}]
    running = base
    for name, delta in components.items():
        running += delta
        rows.append({
            "name": name.replace("_", " ").title(),
            "value": round(delta, 1),
            "cumulative": round(running, 1),
            "type": "positive" if delta >= 0 else "negative",
        })
    rows.append({"name": "Conviction", "value": round(running, 1),
                 "cumulative": round(running, 1), "type": "total"})

    return Chart(
        kind="waterfall",
        title="How this conviction was reached",
        data=rows,
        config={"x": "name", "y": "value", "cumulative": "cumulative", "unit": "%"},
        caption=(
            "Every term is arithmetic from the evidence above. Disagree with one and "
            "you can see exactly how much the answer moves."
        ),
    )


def factor_radar(scores: dict[str, float]) -> Chart:
    """The scoring profile across factors — shape matters more than any one axis."""
    return Chart(
        kind="radar",
        title="Factor profile",
        data=[{"factor": k.replace("_", " ").title(), "score": round(v, 1)}
              for k, v in scores.items()],
        config={"angle": "factor", "radius": "score", "max": 100},
        caption="A balanced shape beats a spike: one strong factor rarely survives contact.",
    )


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
