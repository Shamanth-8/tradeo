"""
The built-in strategies.

Every one is stored as **source code**, not as a Python function. That is the
whole design: what ships as a built-in and what you write yourself are the
same kind of object, run by the same engine, under the same sandbox. "Fork
this and change it" is one click rather than a fork of the repository.

They are also teaching material. Each carries a comment explaining the way
that *kind* of strategy characteristically fails — trend systems bleeding in
ranges, mean-reversion systems catching falling knives — because a strategy
library that only shows the entry rule teaches half the subject.

Nothing here is a recommendation. They are starting points with published
failure modes, which is the honest thing a library can be.
"""

from __future__ import annotations

from typing import Any

CATEGORIES = ["trend", "mean-reversion", "breakout", "momentum", "volatility", "baseline"]


BUILTIN: dict[str, dict[str, Any]] = {}


def _register(key: str, category: str, source: str) -> None:
    BUILTIN[key] = {"key": key, "category": category, "source": source.strip() + "\n"}


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

_register("buy_and_hold", "baseline", '''
NAME = "Buy and hold"
DESCRIPTION = """
Buy on the first valid bar, never sell. The benchmark every other strategy in
this studio is measured against.

It is here as a strategy rather than only as a line on the chart for one
reason: most active strategies lose to it after costs, and seeing that stated
as a result rather than implied by a comparison is harder to argue with.
"""

PARAMS = {}


def on_bar(ctx):
    if not ctx.position:
        ctx.buy(size=0.99, reason="entered and staying")
''')


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------

_register("sma_cross", "trend", '''
NAME = "SMA crossover"
DESCRIPTION = """
Buy when the fast simple moving average crosses above the slow one, exit on
the reverse. The oldest trend-following rule there is.

How it fails: in a sideways market the averages cross back and forth, and
every crossing costs a round trip. The strategy does not lose because it is
wrong about direction — it loses because it is asked a direction question
during a period that has no direction. Check the exposure and cost figures
before the return.
"""

PARAMS = {
    "fast": {"default": 20, "low": 5, "high": 50, "step": 5},
    "slow": {"default": 50, "low": 30, "high": 200, "step": 10},
}


def on_bar(ctx):
    fast_n, slow_n = ctx.param("fast"), ctx.param("slow")
    if fast_n >= slow_n:
        return  # a "fast" average slower than the slow one has no meaning

    fast, slow = ctx.sma(fast_n), ctx.sma(slow_n)
    fast_prev, slow_prev = ctx.sma(fast_n, 1), ctx.sma(slow_n, 1)

    if not ctx.position and ctx.crossed_above(fast, slow, fast_prev, slow_prev):
        ctx.buy(reason=f"SMA{fast_n} crossed above SMA{slow_n}")
    elif ctx.position and ctx.crossed_below(fast, slow, fast_prev, slow_prev):
        ctx.sell("trend reversed")
''')


_register("golden_cross", "trend", '''
NAME = "Golden cross (50/200)"
DESCRIPTION = """
The 50-day above the 200-day, with an ATR stop underneath.

The unmodified golden cross trades perhaps twice a decade, which means its
sample size is never adequate and its statistics are decoration. The stop is
added here precisely so the strategy has a second way to exit — otherwise a
single bad regime is the entire result.
"""

PARAMS = {
    "stop_atr": {"default": 3.0, "low": 1.0, "high": 6.0, "step": 0.5},
}


def on_bar(ctx):
    fast, slow = ctx.sma(50), ctx.sma(200)
    fast_prev, slow_prev = ctx.sma(50, 1), ctx.sma(200, 1)

    if not ctx.position and ctx.crossed_above(fast, slow, fast_prev, slow_prev):
        stop = ctx.price - ctx.atr(14) * ctx.param("stop_atr")
        ctx.buy(stop=stop, reason="50 crossed above 200")
    elif ctx.position and ctx.crossed_below(fast, slow, fast_prev, slow_prev):
        ctx.sell("death cross")
''')


_register("macd_trend", "trend", '''
NAME = "MACD trend"
DESCRIPTION = """
Enter when the MACD line crosses above its signal *and* the histogram is
expanding; exit on the opposite cross.

The histogram condition is the part that matters. A bare MACD cross fires at
the moment momentum stops falling, which in a downtrend is simply a pause.
Requiring the histogram to widen asks for evidence the move is being pushed,
not just that it stopped being pushed the other way.
"""

PARAMS = {
    "fast": {"default": 12, "low": 5, "high": 20, "step": 1},
    "slow": {"default": 26, "low": 15, "high": 40, "step": 2},
    "signal": {"default": 9, "low": 5, "high": 15, "step": 1},
}


def on_bar(ctx):
    fast, slow, signal = ctx.param("fast"), ctx.param("slow"), ctx.param("signal")
    if fast >= slow:
        return

    line, sig, hist = ctx.macd(fast, slow, signal)
    line_p, sig_p, hist_p = ctx.macd(fast, slow, signal, back=1)

    if not ctx.position:
        if ctx.crossed_above(line, sig, line_p, sig_p) and hist > hist_p:
            ctx.buy(stop=ctx.price - 2 * ctx.atr(14), reason="MACD cross, histogram expanding")
    elif ctx.crossed_below(line, sig, line_p, sig_p):
        ctx.sell("MACD rolled over")
''')


_register("adx_trend", "trend", '''
NAME = "Regime-filtered trend"
DESCRIPTION = """
The same moving-average trend rule, but only allowed to trade when ADX says a
trend actually exists.

This is the single highest-value filter in the library, and it is worth
running the plain SMA crossover beside it to see why. Most of what a trend
system loses is not lost on wrong calls in a trend; it is bled away on
crossings during a range. ADX is a direction-agnostic measure of whether
there is a trend at all, so it can veto without needing to be right about
which way.
"""

PARAMS = {
    "fast": {"default": 20, "low": 5, "high": 50, "step": 5},
    "slow": {"default": 50, "low": 30, "high": 150, "step": 10},
    "adx_floor": {"default": 22, "low": 10, "high": 40, "step": 2},
}


def on_bar(ctx):
    fast_n, slow_n, floor = ctx.param("fast"), ctx.param("slow"), ctx.param("adx_floor")
    if fast_n >= slow_n:
        return

    fast, slow = ctx.sma(fast_n), ctx.sma(slow_n)
    fast_prev, slow_prev = ctx.sma(fast_n, 1), ctx.sma(slow_n, 1)
    trending = ctx.adx(14) >= floor

    if not ctx.position:
        if trending and ctx.crossed_above(fast, slow, fast_prev, slow_prev):
            ctx.buy(stop=ctx.price - 2.5 * ctx.atr(14),
                    reason=f"trend confirmed, ADX {ctx.adx(14):.0f}")
    elif ctx.crossed_below(fast, slow, fast_prev, slow_prev):
        ctx.sell("trend reversed")
''')


_register("chandelier", "trend", '''
NAME = "Chandelier trend rider"
DESCRIPTION = """
Enter on strength, then trail a stop hung from the highest high since entry,
at a multiple of ATR.

Trend systems make their money from a small number of very large moves, which
means the exit rule matters far more than the entry. A fixed target caps
precisely the trades the whole approach depends on. A trailing stop gives the
outlier room to become an outlier, and pays for that with a worse win rate —
a trade most people find uncomfortable and should look at honestly before
running this.
"""

PARAMS = {
    "entry_lookback": {"default": 20, "low": 10, "high": 60, "step": 5},
    "trail_atr": {"default": 3.0, "low": 1.5, "high": 6.0, "step": 0.5},
}


def on_bar(ctx):
    lookback = ctx.param("entry_lookback")
    atr_now = ctx.atr(14)

    if not ctx.position:
        if ctx.price > ctx.highest(lookback, 1):
            ctx.buy(stop=ctx.price - atr_now * ctx.param("trail_atr"),
                    trail=atr_now * ctx.param("trail_atr"),
                    reason=f"{lookback}-bar high")
    else:
        # Re-hang the stop as volatility changes, never loosening it.
        ctx.set_trail(atr_now * ctx.param("trail_atr"))
''')


# ---------------------------------------------------------------------------
# Mean reversion
# ---------------------------------------------------------------------------

_register("rsi_reversion", "mean-reversion", '''
NAME = "RSI mean reversion"
DESCRIPTION = """
Buy when RSI falls below the oversold line, sell when it recovers past the
overbought one.

How it fails: "oversold" and "collapsing" look identical to RSI. In a genuine
decline the indicator pins below 30 for weeks while the price keeps falling,
and the strategy holds all the way down. That is why this version carries a
hard stop — mean reversion without one is a bet that nothing ever breaks.
"""

PARAMS = {
    "period": {"default": 14, "low": 5, "high": 30, "step": 1},
    "oversold": {"default": 30, "low": 15, "high": 40, "step": 5},
    "overbought": {"default": 70, "low": 55, "high": 85, "step": 5},
    "stop_atr": {"default": 2.5, "low": 1.0, "high": 5.0, "step": 0.5},
}


def on_bar(ctx):
    period = ctx.param("period")
    value = ctx.rsi(period)

    if not ctx.position:
        if value < ctx.param("oversold"):
            ctx.buy(stop=ctx.price - ctx.atr(14) * ctx.param("stop_atr"),
                    reason=f"RSI {value:.0f} oversold")
    elif value > ctx.param("overbought"):
        ctx.sell(f"RSI {value:.0f} recovered")
''')


_register("bollinger_reversion", "mean-reversion", '''
NAME = "Bollinger band reversion"
DESCRIPTION = """
Buy a close below the lower band, exit at the middle band.

Exiting at the middle rather than the upper band is deliberate. The lower
band is where the edge is; the journey from the middle to the upper band is
ordinary drift, and waiting for it converts a high-probability small win into
a coin flip. Most of the damage this strategy does is caused by greed in the
exit rule, not by the entry.
"""

PARAMS = {
    "period": {"default": 20, "low": 10, "high": 40, "step": 2},
    "deviations": {"default": 2.0, "low": 1.0, "high": 3.5, "step": 0.25},
    "stop_atr": {"default": 2.0, "low": 1.0, "high": 4.0, "step": 0.5},
}


def on_bar(ctx):
    lower, middle, _upper = ctx.bollinger(ctx.param("period"), ctx.param("deviations"))
    if lower != lower:
        return  # still warming up

    if not ctx.position:
        if ctx.price < lower:
            ctx.buy(stop=ctx.price - ctx.atr(14) * ctx.param("stop_atr"),
                    target=middle,
                    reason="closed below the lower band")
    elif ctx.price >= middle:
        ctx.sell("reverted to the mean")
''')


_register("zscore_reversion", "mean-reversion", '''
NAME = "Z-score reversion"
DESCRIPTION = """
Buy when price is more than N standard deviations below its own rolling mean,
exit when it returns to the mean.

Nearly the same idea as the Bollinger version, expressed in units that let
you compare a stretch across instruments. Two standard deviations means the
same thing on a bond ETF and on a small cap; two rupees does not.
"""

PARAMS = {
    "period": {"default": 20, "low": 10, "high": 60, "step": 5},
    "entry_z": {"default": -2.0, "low": -3.5, "high": -1.0, "step": 0.25},
    "exit_z": {"default": 0.0, "low": -1.0, "high": 1.5, "step": 0.25},
}


def on_bar(ctx):
    z = ctx.zscore(ctx.param("period"))
    if z != z:
        return

    if not ctx.position:
        if z <= ctx.param("entry_z"):
            ctx.buy(stop=ctx.price - 2 * ctx.atr(14), reason=f"z-score {z:.2f}")
    elif z >= ctx.param("exit_z"):
        ctx.sell(f"reverted, z-score {z:.2f}")
''')


_register("gap_fade", "mean-reversion", '''
NAME = "Gap fade"
DESCRIPTION = """
When a stock opens sharply lower without follow-through, buy the close and
exit within a few bars.

Read the average hold and the modelled fill before trusting anything this
produces. On daily bars the engine can only transact at the *next* open, so a
strategy reasoning about a gap is already a day late. It is included because
short-horizon ideas are the ones most often ruined by that detail, and seeing
it is more useful than being warned about it.
"""

PARAMS = {
    "gap_pct": {"default": -2.0, "low": -6.0, "high": -0.5, "step": 0.5},
    "max_hold": {"default": 5, "low": 1, "high": 15, "step": 1},
}


def on_bar(ctx):
    if ctx.position:
        if ctx.position.bars_held >= ctx.param("max_hold"):
            ctx.sell("held long enough")
        return

    previous_close = ctx.close(1)
    if previous_close != previous_close or previous_close <= 0:
        return

    gap = (ctx.open() - previous_close) / previous_close * 100
    recovered = ctx.price > ctx.open()

    if gap <= ctx.param("gap_pct") and recovered:
        ctx.buy(stop=ctx.low() * 0.99, reason=f"faded a {gap:.1f}% gap")
''')


# ---------------------------------------------------------------------------
# Breakout
# ---------------------------------------------------------------------------

_register("donchian_breakout", "breakout", '''
NAME = "Donchian breakout"
DESCRIPTION = """
Buy a close above the highest high of the last N bars, exit below the lowest
low of the last M. The Turtle rule, essentially.

How it fails: most breakouts do not continue. This is a strategy with a low
win rate that survives only because the winners are much larger than the
losers — so judge it on profit factor and expectancy, and expect the win rate
to look bad. If you cannot sit through six losses in a row, the backtest
being profitable will not help you.
"""

PARAMS = {
    "entry": {"default": 20, "low": 10, "high": 100, "step": 5},
    "exit": {"default": 10, "low": 5, "high": 50, "step": 5},
}


def on_bar(ctx):
    if not ctx.position:
        if ctx.price > ctx.highest(ctx.param("entry"), 1):
            ctx.buy(reason=f"{ctx.param('entry')}-bar breakout")
    elif ctx.price < ctx.lowest(ctx.param("exit"), 1):
        ctx.sell("broke the exit channel")
''')


_register("volume_breakout", "breakout", '''
NAME = "Volume-confirmed breakout"
DESCRIPTION = """
A breakout to a new high, but only when volume is well above its own average.

Volume is the cheapest lie detector available on a breakout. A move to a new
high on thin trade is usually a few orders finding no resistance, and it
retraces; the same move on twice the usual volume means someone with size
needed to be in. This filter removes most trades, which is the point.
"""

PARAMS = {
    "lookback": {"default": 30, "low": 10, "high": 90, "step": 5},
    "volume_multiple": {"default": 1.8, "low": 1.0, "high": 4.0, "step": 0.2},
    "stop_atr": {"default": 2.0, "low": 1.0, "high": 4.0, "step": 0.5},
}


def on_bar(ctx):
    lookback = ctx.param("lookback")

    if not ctx.position:
        broke_out = ctx.price > ctx.highest(lookback, 1)
        confirmed = ctx.volume_ratio(20) >= ctx.param("volume_multiple")
        if broke_out and confirmed:
            ctx.buy(stop=ctx.price - ctx.atr(14) * ctx.param("stop_atr"),
                    trail=ctx.atr(14) * ctx.param("stop_atr"),
                    reason=f"breakout on {ctx.volume_ratio(20):.1f}x volume")
    elif ctx.price < ctx.sma(20):
        ctx.sell("lost the 20-day")
''')


# ---------------------------------------------------------------------------
# Momentum & volatility
# ---------------------------------------------------------------------------

_register("momentum", "momentum", '''
NAME = "Rate-of-change momentum"
DESCRIPTION = """
Hold while the N-period rate of change is positive and above a floor; step
aside when it is not.

Momentum is the most robust anomaly in the published literature and the most
uncomfortable to run, because it asks you to buy what has already gone up.
The floor exists so the strategy is not in the market during a drift of
nearly nothing, where it collects costs and no edge.
"""

PARAMS = {
    "period": {"default": 60, "low": 20, "high": 180, "step": 10},
    "floor_pct": {"default": 5.0, "low": 0.0, "high": 20.0, "step": 1.0},
    "exit_pct": {"default": 0.0, "low": -10.0, "high": 5.0, "step": 1.0},
}


def on_bar(ctx):
    change = ctx.roc(ctx.param("period"))
    if change != change:
        return

    if not ctx.position:
        if change >= ctx.param("floor_pct"):
            ctx.buy(stop=ctx.price - 3 * ctx.atr(14), reason=f"{change:.0f}% momentum")
    elif change < ctx.param("exit_pct"):
        ctx.sell("momentum faded")
''')


_register("squeeze", "volatility", '''
NAME = "Volatility squeeze"
DESCRIPTION = """
Wait for the Bollinger bands to compress to an unusually narrow width, then
take the direction of the break out of it.

Volatility is the one market property that genuinely reverts — quiet periods
are reliably followed by loud ones. What the squeeze does *not* tell you is
which way, so this is a strategy that is right about timing and guessing
about direction, and the stop is doing most of the work.
"""

PARAMS = {
    "period": {"default": 20, "low": 10, "high": 40, "step": 2},
    "window": {"default": 120, "low": 40, "high": 250, "step": 20},
    "percentile": {"default": 25, "low": 5, "high": 50, "step": 5},
    "stop_atr": {"default": 2.0, "low": 1.0, "high": 4.0, "step": 0.5},
}


# The class form, used here because "narrow" only means something relative to
# this instrument's own recent history, and that history has to be carried
# from bar to bar. State lives on `self`; there is one instance per run.
class Strategy:
    def __init__(self):
        self.widths = []

    def on_bar(self, ctx):
        period = ctx.param("period")
        width = ctx.bb_width(period)
        if width != width:
            return  # warming up

        window = ctx.param("window")
        self.widths.append(width)
        if len(self.widths) > window:
            self.widths.pop(0)
        if len(self.widths) < window // 2:
            return  # not enough history to say what "normal" width is here

        # Squeezed means narrow against its own distribution — an absolute
        # threshold would mean something different on every instrument.
        ranked = sorted(self.widths)
        cutoff = ranked[int(len(ranked) * ctx.param("percentile") / 100)]
        _lower, middle, upper_prev = ctx.bollinger(period, back=1)

        if not ctx.position:
            if width <= cutoff and ctx.price > upper_prev:
                stop_distance = ctx.atr(14) * ctx.param("stop_atr")
                ctx.buy(stop=ctx.price - stop_distance, trail=stop_distance,
                        reason=f"squeeze released at {width:.1f}% width")
        elif ctx.price < ctx.sma(period):
            ctx.sell("fell back into the range")
''')


_register("dual_momentum", "momentum", '''
NAME = "Trend + momentum agreement"
DESCRIPTION = """
Two independent conditions must agree: price above its long moving average
(trend), and positive rate of change over a shorter window (momentum).

Combining signals feels like it should improve things and usually does not —
two indicators computed from the same price series are not independent
evidence, however different their formulas look. This is in the library so
you can measure that rather than assume it: run it against `momentum` alone
and see whether the filter earned its reduction in trades.
"""

PARAMS = {
    "trend_period": {"default": 200, "low": 50, "high": 250, "step": 25},
    "momentum_period": {"default": 40, "low": 10, "high": 120, "step": 10},
}


def on_bar(ctx):
    above_trend = ctx.price > ctx.sma(ctx.param("trend_period"))
    change = ctx.roc(ctx.param("momentum_period"))
    if change != change:
        return

    if not ctx.position:
        if above_trend and change > 0:
            ctx.buy(stop=ctx.price - 3 * ctx.atr(14), reason="trend and momentum agree")
    elif not above_trend or change < 0:
        ctx.sell("one of the two conditions broke")
''')


# ---------------------------------------------------------------------------
# The blank page
# ---------------------------------------------------------------------------

TEMPLATE = '''
NAME = "My strategy"
DESCRIPTION = """What this does, and how you expect it to fail."""

# Anything declared here becomes a slider in the studio and a dimension the
# optimiser can sweep. Omit low/high to keep a value fixed.
PARAMS = {
    "period": {"default": 14, "low": 5, "high": 40, "step": 1},
}


def on_bar(ctx):
    """
    Called once per bar, on the close.

    Anything you order fills at the NEXT bar's open — you cannot trade the
    close you are looking at.

    Reading the bar          ctx.price, ctx.open(), ctx.high(), ctx.low(),
                             ctx.volume(), ctx.close(1) for the previous bar
    Indicators               ctx.sma(n) ctx.ema(n) ctx.rsi(n) ctx.atr(n)
                             ctx.adx(n) ctx.macd() ctx.bollinger(n, k)
                             ctx.bb_width(n) ctx.stochastic() ctx.vwap(n)
                             ctx.zscore(n) ctx.highest(n) ctx.lowest(n)
                             ctx.roc(n) ctx.stdev(n) ctx.volume_ratio(n)
                             — every one takes back=1 for the previous bar
    Crossings                ctx.crossed_above(fast, slow, fast_prev, slow_prev)
                             ctx.crossed_below(...) — an event, not a state
    Where you stand          ctx.position (None when flat), ctx.equity, ctx.cash
                             ctx.position.bars_held, .unrealised_pct, .entry_price
    Acting                   ctx.buy(size=, stop=, target=, trail=, reason=)
                             ctx.sell(reason=) / ctx.close_position(reason=)
                             ctx.set_stop(price) ctx.set_trail(distance)
    Debugging                ctx.log("...")

    Need state across bars? Use the class form instead:

        class Strategy:
            def __init__(self):
                self.count = 0
            def on_bar(self, ctx):
                self.count += 1
    """
    period = ctx.param("period")

    if not ctx.position:
        if ctx.rsi(period) < 30:
            ctx.buy(stop=ctx.price - 2 * ctx.atr(14), reason="oversold")
    elif ctx.rsi(period) > 70:
        ctx.sell("recovered")
'''.strip() + "\n"


def catalogue() -> list[dict[str, Any]]:
    """Every built-in, with its metadata read out of its own source."""
    from .sandbox import compile_strategy

    out: list[dict[str, Any]] = []
    for key, entry in BUILTIN.items():
        try:
            module = compile_strategy(entry["source"], name=key)
            out.append({
                "key": key,
                "category": entry["category"],
                "name": module.name,
                "description": module.description,
                "params": [p.as_dict() for p in module.params],
                "source": entry["source"],
                "builtin": True,
            })
        except Exception as exc:  # pragma: no cover - a broken built-in is a bug
            out.append({
                "key": key, "category": entry["category"], "name": key,
                "description": f"failed to load: {exc}", "params": [],
                "source": entry["source"], "builtin": True, "broken": True,
            })
    return out


def source_of(key: str) -> str | None:
    entry = BUILTIN.get(key)
    return entry["source"] if entry else None
