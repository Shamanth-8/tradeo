"""
The analyst — turns raw market data into something the brain can reason over.

Everything expensive (yfinance calls, indicator maths) happens here once and is
handed to the LLM as a compact, human-readable context block. Two consumers use
it: the chat/voice interface and the realtime scanner.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from data.fetchers.stock_fetcher import stock_fetcher
from data.processors.technical_analyzer import technical_analyzer

from .brain import brain
from .prompts import ANALYSIS_PROMPT, OPPORTUNITY_PROMPT, PORTFOLIO_REVIEW_PROMPT
from .providers import ProviderError
from .sentiment import analyze_symbol as sentiment_for

log = logging.getLogger("tradeo.analyst")


def _fmt_money(value: Any) -> str:
    """Render a rupee amount the way an Indian investor reads it."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if n == 0:
        return "n/a"
    for divisor, unit in ((1e7, "cr"), (1e5, "lakh")):
        if abs(n) >= divisor:
            return f"₹{n / divisor:,.2f} {unit}"
    return f"₹{n:,.2f}"


def _fmt_pct(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "n/a"
    # yfinance returns ratios for margins/growth and already-scaled numbers elsewhere.
    return f"{n * 100:.2f}%" if -1.5 < n < 1.5 else f"{n:.2f}%"


def build_context(
    symbol: str,
    exchange: str = "NSE",
    include_sentiment: bool = True,
    include_fundamentals: bool = True,
    llm_sentiment: bool = True,
) -> dict[str, Any]:
    """Collect every signal Tradeo has on one symbol."""
    symbol = symbol.upper()
    ctx: dict[str, Any] = {"symbol": symbol, "exchange": exchange}

    info = stock_fetcher.get_stock_info(symbol, exchange)
    if "error" not in info:
        ctx["info"] = info

    ctx["price"] = stock_fetcher.get_live_price(symbol, exchange)

    try:
        df = stock_fetcher.get_historical_data(symbol, exchange=exchange, period="1y")
        if df is not None and not df.empty:
            ctx["technicals"] = technical_analyzer.get_latest_indicators(df)
            ctx["reversal"] = technical_analyzer.detect_reversal_signals(df)
    except Exception as exc:
        log.warning("technicals failed for %s: %s", symbol, exc)

    if include_fundamentals:
        fundamentals = stock_fetcher.get_fundamentals(symbol, exchange)
        if "error" not in fundamentals:
            ctx["fundamentals"] = fundamentals
            try:
                from ml.quality_scorer import quality_scorer

                ctx["quality"] = quality_scorer.score(fundamentals)
            except Exception as exc:
                log.warning("quality score failed for %s: %s", symbol, exc)

    if include_sentiment:
        try:
            # The scanner sweeps the whole universe, so it takes the VADER-only
            # reading; inference is reserved for names that already look
            # interesting.
            ctx["sentiment"] = sentiment_for(
                symbol, (ctx.get("info") or {}).get("name"), use_llm=llm_sentiment
            )
        except Exception as exc:
            log.warning("sentiment failed for %s: %s", symbol, exc)

    return ctx


def render_context(ctx: dict[str, Any]) -> str:
    """Flatten a context dict into the prose block the LLM sees."""
    info = ctx.get("info") or {}
    price = ctx.get("price") or {}
    tech = ctx.get("technicals") or {}
    rev = ctx.get("reversal") or {}
    fun = ctx.get("fundamentals") or {}
    qual = ctx.get("quality") or {}
    sent = ctx.get("sentiment") or {}

    lines: list[str] = [
        f"SYMBOL: {ctx.get('symbol')} ({info.get('name', 'unknown')}) on {ctx.get('exchange', 'NSE')}",
    ]

    if info:
        lines.append(
            f"Sector: {info.get('sector', 'n/a')} / {info.get('industry', 'n/a')} | "
            f"Market cap: {_fmt_money(info.get('market_cap'))}"
        )

    if price and "error" not in price:
        lines.append(
            f"PRICE: ₹{price.get('price', 'n/a')} "
            f"({price.get('change_percent', 0):+.2f}% today) | "
            f"day range ₹{price.get('low', 'n/a')}–₹{price.get('high', 'n/a')}"
        )
    if info.get("fifty_two_week_high"):
        high = info["fifty_two_week_high"]
        low = info.get("fifty_two_week_low", 0)
        current = price.get("price") or info.get("current_price") or 0
        position = ((current - low) / (high - low) * 100) if high > low else 0
        lines.append(
            f"52-week range: ₹{low:,.2f}–₹{high:,.2f} "
            f"(currently {position:.0f}% up the range)"
        )

    if tech:
        lines.append(
            "TECHNICALS: "
            f"RSI {tech.get('rsi')} ({tech.get('rsi_signal')}), "
            f"MACD {tech.get('macd_signal_type')}, "
            f"ADX {tech.get('adx')}, trend {tech.get('trend')}, "
            f"SMA20 ₹{tech.get('sma_20')} / SMA50 ₹{tech.get('sma_50')} / SMA200 ₹{tech.get('sma_200')}, "
            f"ATR ₹{tech.get('atr')}, Bollinger ₹{tech.get('bb_lower')}–₹{tech.get('bb_upper')}"
        )

    if rev:
        signals = rev.get("signals") or []
        lines.append(
            f"REVERSAL: probability {rev.get('reversal_probability', 0)}%, "
            f"call {rev.get('recommendation', 'n/a')}"
            + (f", signals: {'; '.join(str(s) for s in signals[:4])}" if signals else "")
        )

    if fun:
        lines.append(
            "FUNDAMENTALS: "
            f"P/E {fun.get('pe_ratio') or 'n/a'}, P/B {fun.get('pb_ratio') or 'n/a'}, "
            f"ROE {_fmt_pct(fun.get('roe'))}, "
            f"profit margin {_fmt_pct(fun.get('profit_margin'))}, "
            f"revenue growth {_fmt_pct(fun.get('revenue_growth'))}, "
            f"D/E {fun.get('debt_to_equity') or 'n/a'}, "
            f"dividend yield {_fmt_pct(fun.get('dividend_yield'))}, "
            f"beta {fun.get('beta') or 'n/a'}"
        )

    if qual:
        cats = qual.get("category_scores", {})
        lines.append(
            f"QUALITY SCORE: {qual.get('total_score')}/100 (grade {qual.get('grade')}) — "
            + ", ".join(f"{k} {v}" for k, v in cats.items())
        )

    if sent:
        drivers = sent.get("drivers") or []
        lines.append(
            f"SENTIMENT: {sent.get('label')} ({sent.get('score'):+.2f}, "
            f"confidence {sent.get('confidence')}%, {sent.get('evidence_count', 0)} sources) — "
            f"{sent.get('summary', '')}"
            + (f" Drivers: {'; '.join(drivers[:4])}." if drivers else "")
        )
        for headline in (sent.get("headlines") or [])[:5]:
            lines.append(f"  • {headline.get('title')} [{headline.get('source')}]")

    return "\n".join(lines)


VERDICTS = ("strong_buy", "buy", "watch", "avoid", "exit")
HORIZONS = ("intraday", "swing", "positional", "long_term")


def _pick_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """
    Coerce a model's answer to one of `allowed`.

    Small models routinely echo the schema back — "strong_buy|buy" or the whole
    option list — instead of choosing. Take the first allowed token that
    appears, which is the most confident choice in the model's own ordering.
    """
    if not value:
        return default

    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text in allowed:
        return text

    for token in re.split(r"[|,/]+", text):
        token = token.strip()
        if token in allowed:
            return token

    # Longest-first so "strong_buy" wins over the "buy" inside it.
    for candidate in sorted(allowed, key=len, reverse=True):
        if candidate in text:
            return candidate
    return default


def _coerce_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace("₹", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalise_verdict(data: dict[str, Any]) -> dict[str, Any]:
    """Make a raw model response safe for downstream logic to trust."""
    data["verdict"] = _pick_enum(data.get("verdict"), VERDICTS, "watch")
    data["horizon"] = _pick_enum(data.get("horizon"), HORIZONS, "swing")

    try:
        data["conviction"] = max(0, min(100, int(float(data.get("conviction") or 0))))
    except (TypeError, ValueError):
        data["conviction"] = 0

    data["stop_loss"] = _coerce_number(data.get("stop_loss"))

    targets = data.get("targets") or []
    if not isinstance(targets, list):
        targets = [targets]
    data["targets"] = [t for t in (_coerce_number(t) for t in targets) if t is not None][:4]

    for key in ("reasons", "risks"):
        value = data.get(key) or []
        if isinstance(value, str):
            value = [value]
        data[key] = [str(v).strip() for v in value if str(v).strip()][:5]

    for key in ("thesis", "invalidation", "entry_zone"):
        if data.get(key) is not None:
            data[key] = str(data[key]).strip() or None

    # A buy call with no stop is not actionable — say so rather than imply safety.
    if data["verdict"] in ("strong_buy", "buy") and data["stop_loss"] is None:
        data["stop_loss_missing"] = True

    return data


def evaluate_opportunity(
    symbol: str,
    exchange: str = "NSE",
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the brain whether this name is worth acting on right now."""
    ctx = ctx or build_context(symbol, exchange)
    prompt = OPPORTUNITY_PROMPT.format(context=render_context(ctx))

    try:
        data, response = brain.think_json(prompt, task="opportunity", max_tokens=900)
    except (ProviderError, ValueError) as exc:
        log.warning("opportunity evaluation failed for %s: %s", symbol, exc)
        return {
            "symbol": symbol.upper(),
            "verdict": "watch",
            "conviction": 0,
            "error": str(exc),
            "engine": "unavailable",
        }

    if not isinstance(data, dict):
        return {
            "symbol": symbol.upper(),
            "verdict": "watch",
            "conviction": 0,
            "error": "model returned a non-object",
            "engine": response.provider,
        }

    data = _normalise_verdict(data)
    data["symbol"] = symbol.upper()
    data["engine"] = response.provider
    data["model"] = response.model
    data["latency_ms"] = response.latency_ms
    data["snapshot"] = {
        "price": (ctx.get("price") or {}).get("price"),
        "change_percent": (ctx.get("price") or {}).get("change_percent"),
        "rsi": (ctx.get("technicals") or {}).get("rsi"),
        "quality": (ctx.get("quality") or {}).get("total_score"),
        "sentiment": (ctx.get("sentiment") or {}).get("score"),
    }
    return data


def brief(
    symbol: str,
    horizon: str = "swing",
    exchange: str = "NSE",
    register: str = "screen",
) -> dict[str, Any]:
    """A prose briefing on one symbol, for the chat panel or the voice channel."""
    from core.config import settings

    ctx = build_context(symbol, exchange)
    prompt = ANALYSIS_PROMPT.format(
        symbol=symbol.upper(),
        horizon=horizon,
        context=render_context(ctx),
        user_title=settings.user_title,
    )
    response = brain.think(prompt, task="deep_analysis", register=register, max_tokens=1000)
    return {"symbol": symbol.upper(), "horizon": horizon, "briefing": response.text, **response.as_dict()}


def review_portfolio(context_block: str, register: str = "screen") -> dict[str, Any]:
    """Have the brain review an already-rendered portfolio summary."""
    prompt = PORTFOLIO_REVIEW_PROMPT.format(context=context_block)
    response = brain.think(
        prompt, task="portfolio_review", register=register, max_tokens=1200
    )
    return response.as_dict()
