"""
Orchestration — everything the API layer needs, with none of the HTTP.

Resolving *which* strategy a request means is the fiddly part, and it is done
in one place so every endpoint agrees. A request can name a built-in key, a
saved strategy id, or carry raw source directly from the editor. The last of
those matters more than it looks: it is what lets the studio run unsaved code,
which is how anyone actually iterates.

The other job here is the live path. A deployed strategy is evaluated on the
most recent bars and, if it fires, its signal is turned into an autopilot
*proposal* — not an order. Everything a strategy emits still passes through
the same twelve guardrails as everything else, because the point of those
guardrails is that no source of conviction gets to skip them, including one
the user wrote themselves.
"""

from __future__ import annotations

import logging
from typing import Any

from . import data, library, optimize, store, walkforward
from .engine import BacktestConfig, run_backtest
from .sandbox import SandboxError, StrategyModule, compile_strategy

log = logging.getLogger("tradeo.strategies.runner")


class ResolutionError(Exception):
    """The request did not identify a strategy that exists."""


def resolve(
    *,
    source: str | None = None,
    builtin: str | None = None,
    strategy_id: int | None = None,
) -> tuple[StrategyModule, dict[str, Any]]:
    """
    Turn a request into a compiled strategy plus the metadata to attribute it.

    Precedence is source → saved → built-in, so an editor buffer always wins
    over what is on disk. Anything else would make the Run button lie about
    which code it just executed.
    """
    if source and source.strip():
        module = compile_strategy(source, name="custom")
        return module, {"origin": "editor", "strategy_id": strategy_id,
                        "strategy_key": builtin or "", "source": source}

    if strategy_id is not None:
        saved = store.get(strategy_id)
        if not saved:
            raise ResolutionError(f"no saved strategy with id {strategy_id}")
        module = compile_strategy(saved["source"], name=saved["name"])
        return module, {"origin": "saved", "strategy_id": strategy_id,
                        "strategy_key": "", "source": saved["source"]}

    if builtin:
        builtin_source = library.source_of(builtin)
        if not builtin_source:
            raise ResolutionError(
                f"no built-in strategy called '{builtin}' "
                f"(have: {', '.join(sorted(library.BUILTIN))})"
            )
        module = compile_strategy(builtin_source, name=builtin)
        return module, {"origin": "builtin", "strategy_id": None,
                        "strategy_key": builtin, "source": builtin_source}

    raise ResolutionError("provide one of: source, strategy_id, builtin")


def config_from(payload: dict[str, Any] | None) -> BacktestConfig:
    payload = payload or {}
    return BacktestConfig(
        initial_capital=float(payload.get("initial_capital", 100_000) or 100_000),
        default_size=float(payload.get("default_size", 0.95) or 0.95),
        risk_per_trade=float(payload.get("risk_per_trade", 0.02) or 0.02),
        max_position_pct=float(payload.get("max_position_pct", 1.0) or 1.0),
        allow_short=bool(payload.get("allow_short", False)),
        warmup_bars=int(payload.get("warmup_bars", 0) or 0),
    )


def frame_for(symbol: str, period: str, interval: str = "1d",
              exchange: str = "NSE", start: str | None = None,
              end: str | None = None):
    frame = data.history(symbol, period=period, interval=interval, exchange=exchange)
    if start or end:
        frame = data.slice_dates(frame, start, end)
        if len(frame) < data.MIN_BARS:
            raise data.DataError(
                f"only {len(frame)} bars between {start or 'start'} and {end or 'now'} "
                "— widen the window"
            )
    return frame


# ---------------------------------------------------------------------------
# The three things the studio does
# ---------------------------------------------------------------------------


def backtest(request: dict[str, Any]) -> dict[str, Any]:
    module, meta = resolve(
        source=request.get("source"),
        builtin=request.get("builtin"),
        strategy_id=request.get("strategy_id"),
    )
    symbol = str(request.get("symbol", "")).upper()
    period = str(request.get("period", "5y"))
    frame = frame_for(symbol, period, str(request.get("interval", "1d")),
                      str(request.get("exchange", "NSE")),
                      request.get("start"), request.get("end"))

    result = run_backtest(
        module, frame, symbol=symbol,
        params=request.get("params") or {},
        config=config_from(request.get("config")),
    )

    payload = result.as_dict()
    payload["data"] = data.describe(frame)
    payload["strategy_meta"] = {**module.as_dict(), **{k: v for k, v in meta.items()
                                                       if k != "source"}}

    if not result.error:
        store.record_run(
            kind="backtest", symbol=symbol, period=period,
            strategy_id=meta.get("strategy_id"), strategy_key=meta.get("strategy_key", ""),
            strategy_name=module.name, source=meta.get("source", ""),
            params=result.params,
            summary={
                "total_return_pct": result.metrics.total_return_pct,
                "benchmark_return_pct": result.metrics.benchmark_return_pct,
                "sharpe": result.metrics.sharpe,
                "max_drawdown_pct": result.metrics.max_drawdown_pct,
                "total_trades": result.metrics.total_trades,
                "win_rate_pct": result.metrics.win_rate_pct,
                "concerns": len(result.concerns),
            },
        )
    return payload


def sweep(request: dict[str, Any]) -> dict[str, Any]:
    module, meta = resolve(
        source=request.get("source"),
        builtin=request.get("builtin"),
        strategy_id=request.get("strategy_id"),
    )
    symbol = str(request.get("symbol", "")).upper()
    period = str(request.get("period", "5y"))
    frame = frame_for(symbol, period, str(request.get("interval", "1d")),
                      str(request.get("exchange", "NSE")),
                      request.get("start"), request.get("end"))

    result = optimize.sweep(
        module, frame, symbol=symbol,
        objective=str(request.get("objective", "robust")),
        config=config_from(request.get("config")),
        only=request.get("only"),
        max_combinations=int(request.get("max_combinations", optimize.MAX_COMBINATIONS)),
    )

    payload = result.as_dict()
    payload["data"] = data.describe(frame)
    if not result.error and result.best:
        store.record_run(
            kind="sweep", symbol=symbol, period=period,
            strategy_id=meta.get("strategy_id"), strategy_key=meta.get("strategy_key", ""),
            strategy_name=module.name, source=meta.get("source", ""),
            params=result.best.params,
            summary={"objective": result.objective, "combinations": result.combinations,
                     "best_score": result.best.score,
                     "stability": result.best.stability},
        )
    return payload


def walk_forward(request: dict[str, Any]) -> dict[str, Any]:
    module, meta = resolve(
        source=request.get("source"),
        builtin=request.get("builtin"),
        strategy_id=request.get("strategy_id"),
    )
    symbol = str(request.get("symbol", "")).upper()
    period = str(request.get("period", "5y"))
    frame = frame_for(symbol, period, str(request.get("interval", "1d")),
                      str(request.get("exchange", "NSE")),
                      request.get("start"), request.get("end"))

    result = walkforward.run(
        module, frame, symbol=symbol,
        folds=int(request.get("folds", 5)),
        oos_fraction=float(request.get("oos_fraction", 0.25)),
        mode=str(request.get("mode", "anchored")),
        objective=str(request.get("objective", "robust")),
        config=config_from(request.get("config")),
        optimise=bool(request.get("optimise", True)),
    )

    payload = result.as_dict()
    payload["data"] = data.describe(frame)
    if not result.error:
        store.record_run(
            kind="walkforward", symbol=symbol, period=period,
            strategy_id=meta.get("strategy_id"), strategy_key=meta.get("strategy_key", ""),
            strategy_name=module.name, source=meta.get("source", ""),
            params={},
            summary={
                "oos_return_pct": result.metrics.total_return_pct,
                "benchmark_return_pct": result.metrics.benchmark_return_pct,
                "efficiency": result.efficiency,
                "consistency_pct": result.consistency_pct,
                "oos_trades": result.metrics.total_trades,
            },
        )
    return payload


def compare(request: dict[str, Any]) -> dict[str, Any]:
    """
    Every built-in against one symbol, ranked.

    The point is not to pick a winner. It is that seeing fourteen approaches
    on the same instrument, with buy-and-hold among them, is the fastest way
    to learn that most of them lose to holding after costs.
    """
    symbol = str(request.get("symbol", "")).upper()
    period = str(request.get("period", "5y"))
    frame = frame_for(symbol, period, str(request.get("interval", "1d")),
                      str(request.get("exchange", "NSE")))
    config = config_from(request.get("config"))

    rows: list[dict[str, Any]] = []
    for key, entry in library.BUILTIN.items():
        try:
            module = compile_strategy(entry["source"], name=key)
            result = run_backtest(module, frame, symbol=symbol, config=config)
        except SandboxError as exc:
            rows.append({"key": key, "name": key, "error": str(exc)})
            continue

        if result.error:
            rows.append({"key": key, "name": module.name, "error": result.error})
            continue

        m = result.metrics
        rows.append({
            "key": key,
            "name": module.name,
            "category": entry["category"],
            "total_return_pct": round(m.total_return_pct, 2),
            "cagr_pct": round(m.cagr_pct, 2),
            "sharpe": round(m.sharpe, 2),
            "sortino": round(m.sortino, 2),
            "max_drawdown_pct": round(m.max_drawdown_pct, 2),
            "total_trades": m.total_trades,
            "win_rate_pct": round(m.win_rate_pct, 1),
            "profit_factor": round(m.profit_factor, 2) if m.profit_factor else None,
            "exposure_pct": round(m.exposure_pct, 1),
            "cost_drag_pct": round(m.cost_drag_pct, 2),
            "beats_benchmark": m.beats_benchmark,
            "concerns": len(result.concerns),
        })

    scored = [r for r in rows if "error" not in r]
    scored.sort(key=lambda r: r["sharpe"], reverse=True)
    benchmark = next((r for r in scored if r["key"] == "buy_and_hold"), None)
    beat = sum(1 for r in scored if r.get("beats_benchmark"))

    return {
        "symbol": symbol,
        "period": period,
        "data": data.describe(frame),
        "results": scored + [r for r in rows if "error" in r],
        "benchmark": benchmark,
        "verdict": (
            f"{beat} of {len(scored)} strategies beat buy-and-hold on {symbol} "
            f"after costs over this period."
            if scored else "Nothing ran successfully."
        ),
    }


# ---------------------------------------------------------------------------
# Live signals
# ---------------------------------------------------------------------------


def evaluate_live(module: StrategyModule, symbol: str, *,
                  period: str = "2y", params: dict[str, Any] | None = None,
                  config: BacktestConfig | None = None) -> dict[str, Any]:
    """
    Run a strategy up to the most recent bar and report what it wants to do now.

    Implemented by replaying history rather than by evaluating the last bar in
    isolation, because a strategy's decision depends on whether it is already
    in a position — and that is only knowable by having walked the whole way
    there. It is the same code path as the backtest, which is the point: a
    signal you see live is one the backtest would also have taken.
    """
    frame = data.history(symbol, period=period)
    result = run_backtest(module, frame, symbol=symbol, params=params, config=config)

    if result.error:
        return {"symbol": symbol.upper(), "error": result.error, "signal": None}

    last_date = result.dates[-1] if result.dates else ""
    # A marker on the final bar means the strategy acted *today*; anything
    # earlier is history and must not be presented as a fresh signal.
    fresh = [m for m in result.markers if m.get("date") == last_date]
    open_trade = result.trades[-1] if result.trades else None
    holding = bool(open_trade and open_trade.exit_reason.startswith("marked to market"))

    signal = None
    if fresh:
        marker = fresh[-1]
        signal = {
            "action": "BUY" if marker["type"] == "entry" else "SELL",
            "price": marker["price"],
            "reason": marker.get("reason", ""),
            "date": marker["date"],
        }

    return {
        "symbol": symbol.upper(),
        "strategy": module.name,
        "as_of": last_date,
        "signal": signal,
        "holding": holding,
        "position": {
            "entry_date": open_trade.entry_date,
            "entry_price": open_trade.entry_price,
            "quantity": open_trade.quantity,
            "return_pct": round(open_trade.return_pct, 2),
        } if holding and open_trade else None,
        # The track record is attached deliberately: a signal without the
        # history of the rule that produced it is just an opinion.
        "backing": {
            "total_trades": result.metrics.total_trades,
            "win_rate_pct": round(result.metrics.win_rate_pct, 1),
            "total_return_pct": round(result.metrics.total_return_pct, 2),
            "benchmark_return_pct": round(result.metrics.benchmark_return_pct, 2),
            "max_drawdown_pct": round(result.metrics.max_drawdown_pct, 2),
            "sample_adequate": result.metrics.sample_adequate,
        },
        "concerns": result.concerns,
    }


def scan_deployed(period: str = "2y") -> dict[str, Any]:
    """
    Every deployed strategy against every symbol it watches.

    Anything that fires becomes an autopilot proposal, which means it meets
    the guardrails, the suitability check and the two-switch live rule like
    any other trade. A strategy the user wrote does not get a private door.
    """
    findings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for saved in store.deployed():
        symbols = saved.get("symbols") or []
        if not symbols:
            errors.append({"strategy": saved["name"],
                           "error": "deployed but watching no symbols"})
            continue

        try:
            module = compile_strategy(saved["source"], name=saved["name"])
        except SandboxError as exc:
            errors.append({"strategy": saved["name"], "error": str(exc)})
            continue

        for symbol in symbols[:25]:
            try:
                evaluation = evaluate_live(module, str(symbol), period=period,
                                           params=saved.get("params") or None)
            except Exception as exc:
                errors.append({"strategy": saved["name"], "symbol": symbol,
                               "error": str(exc)})
                continue

            if evaluation.get("signal"):
                evaluation["strategy_id"] = saved["id"]
                findings.append(evaluation)

    return {"findings": findings, "errors": errors,
            "strategies": len(store.deployed())}


def propose(evaluation: dict[str, Any]) -> dict[str, Any]:
    """
    Hand a live strategy signal to the autopilot as a proposal.

    Shaped as an opportunity because that is the autopilot's only input
    vocabulary — which is exactly why it is done this way rather than by
    reaching past it into the order path.
    """
    from autopilot.agent import Autopilot

    signal = evaluation.get("signal") or {}
    if not signal:
        return {"error": "no live signal to propose"}

    backing = evaluation.get("backing", {})
    # Conviction from the rule's own out-of-sample-ish record, not from a
    # number the strategy asserted about itself. Capped below the level a
    # discretionary call can reach, and capped hard when the sample is thin.
    win_rate = float(backing.get("win_rate_pct", 0) or 0)
    conviction = min(85.0, max(40.0, win_rate))
    if not backing.get("sample_adequate"):
        conviction = min(conviction, 60.0)

    opportunity = {
        "symbol": evaluation["symbol"],
        "verdict": "buy" if signal.get("action") == "BUY" else "exit",
        "conviction": conviction,
        "thesis": (
            f"{evaluation.get('strategy', 'Custom strategy')}: {signal.get('reason') or 'signal fired'}"
            f" — {backing.get('total_trades', 0)} historical trades, "
            f"{win_rate:.0f}% win rate"
        ),
        "source": "custom_strategy",
        "snapshot": {"price": signal.get("price")},
        "entry": signal.get("price"),
        "concerns": evaluation.get("concerns", []),
    }

    proposal = Autopilot().propose(opportunity)
    if proposal is None:
        return {"error": "the autopilot declined to build a proposal from this signal",
                "opportunity": opportunity}
    return proposal
