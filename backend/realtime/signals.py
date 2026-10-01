"""
Deterministic signal scoring.

The scanner sweeps the whole universe on a schedule, so the first pass has to
be cheap: pure arithmetic over data already fetched, no LLM calls. Only names
that clear a threshold here are worth spending inference on.

Every component returns 0-100 where 50 is neutral, so a symbol with nothing
interesting happening scores ~50 and gets ignored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("tradeo.signals")

# How much each component contributes to the composite score.
WEIGHTS: dict[str, float] = {
    "momentum": 0.25,
    "trend": 0.25,
    "position": 0.15,
    "volume": 0.10,
    "sentiment": 0.15,
    "quality": 0.10,
}


@dataclass
class Signal:
    symbol: str
    score: float
    direction: str  # bullish | bearish | neutral
    components: dict[str, float] = field(default_factory=dict)
    triggers: list[str] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.score >= 65 or self.score <= 35

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 1),
            "direction": self.direction,
            "components": {k: round(v, 1) for k, v in self.components.items()},
            "triggers": self.triggers,
            "snapshot": self.snapshot,
            "actionable": self.is_actionable,
        }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if result != result else result  # NaN guard


def _momentum(tech: dict[str, Any], triggers: list[str]) -> float:
    """RSI, MACD and ADX — is something moving, and with conviction?"""
    if not tech:
        return 50.0

    score = 50.0
    rsi = _num(tech.get("rsi"), 50)

    # Oversold and overbought are both signals; the direction differs.
    if rsi < 30:
        score += 20
        triggers.append(f"RSI oversold at {rsi:.0f}")
    elif rsi < 45:
        score += 8
    elif rsi > 70:
        score -= 20
        triggers.append(f"RSI overbought at {rsi:.0f}")
    elif rsi > 55:
        score += 5

    macd = _num(tech.get("macd"))
    macd_signal = _num(tech.get("macd_signal"))
    histogram = _num(tech.get("macd_histogram"))
    if macd > macd_signal:
        score += 10
        if histogram > 0 and abs(histogram) > abs(macd) * 0.1:
            triggers.append("MACD bullish crossover with expanding histogram")
    else:
        score -= 10

    # ADX measures conviction, not direction — it amplifies whatever we found.
    adx = _num(tech.get("adx"))
    if adx > 25:
        score = 50 + (score - 50) * 1.2
        if adx > 40:
            triggers.append(f"strong trend (ADX {adx:.0f})")

    return max(0.0, min(100.0, score))


def _trend(tech: dict[str, Any], price: float, triggers: list[str]) -> float:
    """Where price sits against its moving averages."""
    if not tech or not price:
        return 50.0

    score = 50.0
    sma20 = _num(tech.get("sma_20"))
    sma50 = _num(tech.get("sma_50"))
    sma200 = _num(tech.get("sma_200"))

    for sma, weight, label in ((sma20, 8, "20DMA"), (sma50, 10, "50DMA"), (sma200, 14, "200DMA")):
        if not sma:
            continue
        score += weight if price > sma else -weight
        # Sitting within 1% of a major average is where breakouts and rejections
        # happen — worth surfacing even though the score barely moves.
        if label == "200DMA" and abs(price - sma) / sma < 0.01:
            triggers.append("price testing its 200DMA")

    # Golden/death cross: the 50 crossing the 200 is the classic regime marker.
    if sma50 and sma200:
        if sma50 > sma200 * 1.01:
            triggers.append("50DMA above 200DMA (bullish regime)")
        elif sma50 < sma200 * 0.99:
            triggers.append("50DMA below 200DMA (bearish regime)")

    return max(0.0, min(100.0, score))


def _position(info: dict[str, Any], tech: dict[str, Any], price: float, triggers: list[str]) -> float:
    """Position in the 52-week range and against the Bollinger band."""
    score = 50.0

    high = _num(info.get("fifty_two_week_high"))
    low = _num(info.get("fifty_two_week_low"))
    if high and low and high > low and price:
        pct = (price - low) / (high - low) * 100
        if pct > 95:
            score -= 5
            triggers.append("at 52-week high")
        elif pct > 80:
            score += 10  # strength near highs is usually continuation
        elif pct < 10:
            score += 15
            triggers.append(f"near 52-week low ({pct:.0f}% up the range)")
        elif pct < 30:
            score += 8

    bb_upper = _num(tech.get("bb_upper"))
    bb_lower = _num(tech.get("bb_lower"))
    if price and bb_lower and price < bb_lower:
        score += 12
        triggers.append("price below lower Bollinger band")
    elif price and bb_upper and price > bb_upper:
        score -= 12
        triggers.append("price above upper Bollinger band")

    return max(0.0, min(100.0, score))


def _volume(tech: dict[str, Any], info: dict[str, Any], live: dict[str, Any], triggers: list[str]) -> float:
    """Unusual participation is what separates a real move from drift."""
    score = 50.0

    volume = _num(live.get("volume")) or _num(info.get("volume"))
    average = _num(info.get("avg_volume"))
    if volume and average:
        ratio = volume / average
        if ratio > 2.5:
            score += 20
            triggers.append(f"volume {ratio:.1f}x average")
        elif ratio > 1.5:
            score += 10
        elif ratio < 0.5:
            score -= 8

    cmf = _num(tech.get("cmf"))
    if cmf > 0.1:
        score += 8
    elif cmf < -0.1:
        score -= 8

    return max(0.0, min(100.0, score))


def _sentiment(sent: dict[str, Any], triggers: list[str]) -> float:
    if not sent:
        return 50.0
    raw = _num(sent.get("score"))
    confidence = _num(sent.get("confidence"), 30) / 100.0
    # A -1..+1 score becomes 0..100, scaled by how sure we are about it.
    score = 50 + (raw * 50 * max(0.3, confidence))
    if abs(raw) > 0.4:
        triggers.append(f"sentiment {sent.get('label')} ({raw:+.2f})")
    return max(0.0, min(100.0, score))


def _quality(quality: dict[str, Any], fundamentals: dict[str, Any], triggers: list[str]) -> float:
    if quality and quality.get("total_score") is not None:
        score = _num(quality.get("total_score"), 50)
        if score >= 75:
            triggers.append(f"quality grade {quality.get('grade')}")
        return max(0.0, min(100.0, score))

    if not fundamentals:
        return 50.0

    score = 50.0
    pe = _num(fundamentals.get("pe_ratio"))
    if 0 < pe < 15:
        score += 12
    elif pe > 60:
        score -= 12

    roe = _num(fundamentals.get("roe"))
    if roe > 0.18:
        score += 12
    elif 0 < roe < 0.08:
        score -= 8

    return max(0.0, min(100.0, score))


def score_context(symbol: str, ctx: dict[str, Any]) -> Signal:
    """Score a symbol from an already-built analyst context."""
    tech = ctx.get("technicals") or {}
    info = ctx.get("info") or {}
    live = ctx.get("price") or {}
    fundamentals = ctx.get("fundamentals") or {}
    quality = ctx.get("quality") or {}
    sent = ctx.get("sentiment") or {}

    price = _num(live.get("price")) or _num(info.get("current_price"))
    triggers: list[str] = []

    components = {
        "momentum": _momentum(tech, triggers),
        "trend": _trend(tech, price, triggers),
        "position": _position(info, tech, price, triggers),
        "volume": _volume(tech, info, live, triggers),
        "sentiment": _sentiment(sent, triggers),
        "quality": _quality(quality, fundamentals, triggers),
    }

    composite = sum(components[k] * WEIGHTS[k] for k in components)

    # Reversal detection is a separate model; let a strong reading nudge the score.
    reversal = ctx.get("reversal") or {}
    probability = _num(reversal.get("reversal_probability"))
    if probability >= 60:
        recommendation = str(reversal.get("recommendation", "")).upper()
        nudge = 6 if "BUY" in recommendation else (-6 if "SELL" in recommendation else 0)
        composite += nudge
        if nudge:
            triggers.append(f"reversal model: {recommendation} at {probability:.0f}%")

    composite = max(0.0, min(100.0, composite))
    direction = "bullish" if composite >= 60 else ("bearish" if composite <= 40 else "neutral")

    return Signal(
        symbol=symbol.upper(),
        score=composite,
        direction=direction,
        components=components,
        triggers=triggers[:6],
        snapshot={
            "price": price or None,
            "change_percent": _num(live.get("change_percent")) or None,
            "rsi": _num(tech.get("rsi")) or None,
            # Carried so a downstream consumer can size a stop from volatility
            # without refetching the whole context.
            "atr": _num(tech.get("atr")) or None,
            "sector": info.get("sector"),
            "name": info.get("name"),
        },
    )
