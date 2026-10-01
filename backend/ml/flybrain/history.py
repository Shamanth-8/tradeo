"""
History lab: replay the fly brain over 2018→today, retrain it, and chart it.

    venv/bin/python -m ml.flybrain.history

One run does three things:

1. The gate (rl.main): five seeds against the scorer, random wiring, raw
   features and buying everything, written to rl_report.json. It also
   retrains the live agent on all history by the same dopamine rule.
2. Live memory: whatever the brain learned from its own closed paper trades
   is re-applied on top, so retraining never erases the live record.
3. Charts: one seed replayed with every trade logged, turned into the series
   the Paper Trading page draws (rl_history.json).

The replay is causal, like the gate: at every date the brain has learned only
from trades that had already closed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd

from . import rl
from .experiment import CACHE_DIR, ROUND_TRIP
from .features import HORIZON

log = logging.getLogger("tradeo.flybrain.history")

HISTORY = CACHE_DIR / "rl_history.json"
ROLLING_TRADES = 100

_state: dict = {"running": False, "started_at": None, "finished_at": None, "error": None}
_lock = threading.Lock()


def _curve(returns: pd.Series, dates: np.ndarray) -> dict[str, float]:
    """Growth of ₹1, keyed by date."""
    growth = (1 + returns.sort_index()).cumprod()
    return {str(pd.Timestamp(dates[t]).date()): round(float(v), 4) for t, v in growth.items()}


def _by_year(returns: pd.Series, dates: np.ndarray) -> dict[int, float]:
    s = returns.copy()
    s.index = pd.to_datetime(dates[s.index])
    return {int(y): round(float(g.sum() * 100), 2) for y, g in s.groupby(s.index.year)}


def _reapply_live_memory() -> int:
    loaded = rl.load_agent()
    if loaded is None:
        return 0
    agent, payload = loaded
    memory = payload.get("live_memory") or []
    for item in memory:
        agent.learn(np.array(item["phi"]), float(item["reward"]))
    if memory:
        rl.save_agent(agent, {k: v for k, v in payload.items() if k != "agent"})
    return len(memory)


def run() -> dict:
    from . import connectome, reservoir

    started = time.time()
    report = rl.main()
    reapplied = _reapply_live_memory()

    data = pd.read_parquet(CACHE_DIR / "features.parquet")
    dates, symbols, tgt = rl._grid(data, ["target"])
    targets = tgt[..., 0]
    _, _, base = rl._grid(data, ["baseline_score"])

    frame = reservoir.run(connectome.mushroom_body(), data)
    cols = [c for c in frame.columns if c.startswith("n")]
    _, _, states = rl._grid(frame, cols)

    trade_log: list = []
    fly, _, counts = rl.simulate(states, targets, seed=rl.SEEDS[0], trade_log=trade_log)
    scorer = rl._fixed_rule(base[..., 0], targets, len(dates))
    random_picks = rl._fixed_rule(targets, targets, len(dates), np.random.default_rng(0))
    everything = pd.Series({t: float(np.nanmean(targets[t]) - ROUND_TRIP)
                            for t in range(rl.WARMUP_DAYS, len(dates), HORIZON)})

    # One row per rebalance date: the four equity curves side by side.
    curves = {name: _curve(series, dates) for name, series in
              (("fly_brain", fly), ("scorer", scorer), ("random", random_picks),
               ("buy_everything", everything))}
    equity = [{"date": d, **{name: curves[name].get(d) for name in curves}}
              for d in sorted(curves["fly_brain"])]

    # How the brain learned: rewards, punishments, recent win rate, and how
    # much it expected to earn when it chose.
    trades = pd.DataFrame(trade_log, columns=["t", "s", "reward", "predicted"])
    trades["date"] = [str(pd.Timestamp(dates[t]).date()) for t in trades["t"]]
    trades["win"] = trades["reward"] > 0
    trades["rewards"] = trades["win"].cumsum()
    trades["punishments"] = (~trades["win"]).cumsum()
    trades["win_rate"] = trades["win"].rolling(ROLLING_TRADES, min_periods=20).mean() * 100
    per_period = trades.groupby("date").agg(
        rewards=("rewards", "last"), punishments=("punishments", "last"),
        win_rate=("win_rate", "last"), predicted=("predicted", "mean"),
        slots=("s", "size")).reset_index()
    learning = [{"date": r.date, "rewards": int(r.rewards), "punishments": int(r.punishments),
                 "win_rate": None if pd.isna(r.win_rate) else round(float(r.win_rate), 1),
                 "expected_pct": round(float(r.predicted) * 100, 3),
                 "invested_pct": round(r.slots / rl.TOP_K * 100)}
                for r in per_period.itertuples()]

    by_symbol = (trades.assign(symbol=[str(symbols[s]) for s in trades["s"]])
                 .groupby("symbol")["reward"].agg(["count", "mean", lambda x: (x > 0).mean()]))
    by_symbol.columns = ["trades", "avg", "win_rate"]
    symbol_rows = [{"symbol": sym, "trades": int(r.trades), "avg_pct": round(float(r.avg) * 100, 2),
                    "win_rate": round(float(r.win_rate) * 100, 1)}
                   for sym, r in by_symbol.sort_values("trades", ascending=False).iterrows()]

    recent = [{"date": str(pd.Timestamp(dates[t]).date()), "symbol": str(symbols[s]),
               "net_pct": round(r * 100, 2), "expected_pct": round(p * 100, 3)}
              for t, s, r, p in trade_log[-40:]][::-1]

    years = sorted(set(_by_year(fly, dates)) | set(_by_year(scorer, dates)))
    fy, sy, ey = _by_year(fly, dates), _by_year(scorer, dates), _by_year(everything, dates)
    by_year = [{"year": y, "fly_brain": fy.get(y), "scorer": sy.get(y), "buy_everything": ey.get(y)}
               for y in years]

    results = report["results"]
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "data": {"from": str(pd.Timestamp(dates[0]).date()), "to": str(pd.Timestamp(dates[-1]).date()),
                 "symbols": len(symbols), "rebalance_days": HORIZON,
                 "round_trip_cost_pct": round(ROUND_TRIP * 100, 3)},
        "summary": {
            "passed": report["passed"],
            "checks": report["checks"],
            "test_years": f"{rl.FIRST_TEST_YEAR}–{pd.Timestamp(dates[-1]).year}",
            "net_per_year_pct": {
                "fly_brain": results["fly_rl"]["net_annualised_pct_mean"],
                "fly_brain_by_seed": results["fly_rl"]["net_annualised_pct_by_seed"],
                "random_wiring": results["random_wiring_rl"]["net_annualised_pct_mean"],
                "no_brain": results["features_rl"]["net_annualised_pct_mean"],
                "scorer": results["scorer_top5"]["net_annualised_pct"],
                "random_picks": results["random_top5"]["net_annualised_pct_mean"],
                "buy_everything": results["equal_weight_all"]["net_annualised_pct"],
            },
            "trades": counts["trades"],
            "wins": counts["wins"],
            "losses": counts["trades"] - counts["wins"],
            "win_rate": round(counts["wins"] / max(1, counts["trades"]) * 100, 1),
            "live_trades_reapplied": reapplied,
        },
        "equity": equity,
        "learning": learning,
        "by_year": by_year,
        "by_symbol": symbol_rows,
        "recent_trades": recent,
        # Every trade's net return and the value the brain predicted when it
        # chose, in order: the Monte Carlo lab resamples these.
        "trades": [[round(r, 5), round(p, 5)] for _, _, r, p in trade_log],
    }
    HISTORY.write_text(json.dumps(out))
    return out


def start() -> dict:
    """Run in the background; the page polls status()."""
    with _lock:
        if _state["running"]:
            return {"started": False, "error": "already running"}
        _state.update(running=True, started_at=datetime.now().isoformat(timespec="seconds"),
                      error=None)

    def work() -> None:
        try:
            run()
        except Exception as exc:
            log.error("history run failed: %s", exc, exc_info=True)
            _state["error"] = str(exc)
        finally:
            _state.update(running=False, finished_at=datetime.now().isoformat(timespec="seconds"))

    threading.Thread(target=work, name="fly-history", daemon=True).start()
    return {"started": True}


def status() -> dict:
    return dict(_state)


def load() -> dict | None:
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    print(json.dumps(result["summary"], indent=2))
