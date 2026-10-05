"""
Fly brain test lab: Monte Carlo on its trades, and custom CSV data.

Monte Carlo
    Takes a list of per-trade net returns (from the history replay, the live
    paper record, or a CSV run), resamples them into thousands of alternative
    orderings, and reports the spread of outcomes: the chance of ending in
    profit, drawdowns, and a fan chart. Two sweeps help set up the trades:

      sizing     what fraction of equity per trade gives the best median
                 growth without a 95th-percentile drawdown over MAX_DD
      selectivity  keeping only the trades the brain valued most when it
                 chose them (causal: the value was known at entry)

    Resampling assumes trades are independent draws. It shows the range luck
    alone produces from the same edge; it cannot create an edge that isn't in
    the trades, and a selectivity or size picked from the same trades it is
    scored on is optimistic.

Custom CSV
    Daily OHLCV in, the same features, mushroom body and dopamine learning as
    the NSE replay out. The brain can start fresh or from a copy of the live
    brain, and the result can optionally be kept as the live brain (training).
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from . import rl
from .experiment import CACHE_DIR, ROUND_TRIP
from .features import FEATURES, HORIZON

log = logging.getLogger("tradeo.flybrain.lab")

RUNS_DIR = CACHE_DIR / "csv_runs"
MC_PATHS = 2000
MAX_DD = 0.20                      # drawdown ceiling for the sizing recommendation
SIZES = (0.01, 0.025, 0.05, 0.10, 0.20, 0.35)
SELECT = ((0, "all trades"), (25, "top 75% by value"), (50, "top 50%"), (75, "top 25%"))
MIN_ROWS = 320                     # a year of warm-up for the 200-day average + some trading
MAX_UPLOAD_MB = 25
PERIODS_PER_YEAR = 250 / HORIZON


# ---- Monte Carlo ------------------------------------------------------------------


def _paths(returns: np.ndarray, n_trades: int, fraction: float, rng) -> np.ndarray:
    """Equity paths, shape (MC_PATHS, n_trades + 1), each trade risking `fraction`."""
    draws = rng.choice(returns, size=(MC_PATHS, n_trades), replace=True)
    growth = np.cumprod(1 + fraction * draws, axis=1)
    return np.hstack([np.ones((MC_PATHS, 1)), growth])


def _drawdowns(paths: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(paths, axis=1)
    return (1 - paths / peak).max(axis=1)


def _summary(paths: np.ndarray) -> dict[str, float]:
    final = paths[:, -1]
    dd = _drawdowns(paths)
    q = np.percentile(final, [5, 25, 50, 75, 95])
    return {
        "p5_pct": round((q[0] - 1) * 100, 2), "p25_pct": round((q[1] - 1) * 100, 2),
        "median_pct": round((q[2] - 1) * 100, 2), "p75_pct": round((q[3] - 1) * 100, 2),
        "p95_pct": round((q[4] - 1) * 100, 2),
        "prob_profit": round(float((final > 1).mean()) * 100, 1),
        "prob_loss_20": round(float((final < 0.8).mean()) * 100, 1),
        "median_max_dd_pct": round(float(np.median(dd)) * 100, 2),
        "p95_max_dd_pct": round(float(np.percentile(dd, 95)) * 100, 2),
    }


def monte_carlo(trades: list[list[float]] | list[float], n_trades: int | None = None,
                fraction: float = 0.05, seed: int = 0) -> dict[str, Any]:
    """
    `trades`: net returns, or [net return, predicted value] pairs, in order.
    `n_trades`: path length; defaults to about a year of trading at the
    historical pace (capped at the sample size).
    """
    rows = np.array([t if isinstance(t, (list, tuple)) else [t, np.nan] for t in trades], float)
    if len(rows) < 10:
        return {"ok": False, "error": f"need at least 10 closed trades, have {len(rows)}"}
    returns, predicted = rows[:, 0], rows[:, 1]
    n = int(n_trades or min(len(returns), 500))
    rng = np.random.default_rng(seed)

    base = _paths(returns, n, fraction, rng)
    checkpoints = np.unique(np.linspace(0, n, min(n, 60) + 1).astype(int))
    bands = np.percentile(base[:, checkpoints], [5, 25, 50, 75, 95], axis=0)
    fan = [{"trade": int(c), "p5": round(float(bands[0, i]), 4), "p25": round(float(bands[1, i]), 4),
            "median": round(float(bands[2, i]), 4), "p75": round(float(bands[3, i]), 4),
            "p95": round(float(bands[4, i]), 4)} for i, c in enumerate(checkpoints)]
    samples = base[:15, checkpoints]
    for i, c in enumerate(checkpoints):
        for j in range(len(samples)):
            fan[i][f"s{j}"] = round(float(samples[j, i]), 4)

    finals = base[:, -1]
    hist_counts, edges = np.histogram((finals - 1) * 100, bins=30)
    histogram = [{"from_pct": round(float(edges[i]), 1), "to_pct": round(float(edges[i + 1]), 1),
                  "paths": int(hist_counts[i])} for i in range(len(hist_counts))]

    sizing = []
    for f in SIZES:
        s = _summary(_paths(returns, n, f, np.random.default_rng(seed + 1)))
        sizing.append({"fraction_pct": round(f * 100, 1), **s})
    safe = [row for row in sizing if row["p95_max_dd_pct"] <= MAX_DD * 100]
    best = max(safe, key=lambda r: r["median_pct"]) if safe else None

    selectivity = []
    if not np.isnan(predicted).all():
        for cut, label in SELECT:
            keep = returns[predicted >= np.nanpercentile(predicted, cut)] if cut else returns
            if len(keep) < 10:
                continue
            m = max(1, int(round(n * len(keep) / len(returns))))
            s = _summary(_paths(keep, m, fraction, np.random.default_rng(seed + 2)))
            selectivity.append({"keep": label, "trades": int(len(keep)),
                                "avg_trade_pct": round(float(keep.mean()) * 100, 3),
                                "win_rate": round(float((keep > 0).mean()) * 100, 1), **s})

    mean = float(returns.mean())
    if mean <= 0:
        advice = ("The average trade loses after costs, so every position size loses on median; "
                  "the smallest size loses least. Sizing cannot fix a negative edge.")
    elif best:
        advice = (f"{best['fraction_pct']}% of equity per trade gives the best median growth "
                  f"({best['median_pct']:+.1f}%) while keeping the 95th-percentile drawdown under "
                  f"{MAX_DD * 100:.0f}%.")
    else:
        advice = f"Even 1% per trade breaches a {MAX_DD * 100:.0f}% drawdown in bad paths; trade smaller."

    return {
        "ok": True,
        "paths": MC_PATHS, "trades_per_path": n, "fraction_pct": round(fraction * 100, 1),
        "sample": {"trades": int(len(returns)), "avg_trade_pct": round(mean * 100, 3),
                   "win_rate": round(float((returns > 0).mean()) * 100, 1),
                   "best_pct": round(float(returns.max()) * 100, 2),
                   "worst_pct": round(float(returns.min()) * 100, 2)},
        "summary": _summary(base),
        "fan": fan,
        "histogram": histogram,
        "sizing": sizing,
        "recommended_fraction_pct": best["fraction_pct"] if best and mean > 0 else None,
        "selectivity": selectivity,
        "advice": advice,
        "caveat": ("Trades are resampled as independent draws. This shows the spread luck gives "
                   "the same edge; it does not create one, and settings chosen on these trades "
                   "look better here than they will going forward."),
    }


def trades_for(source: str) -> list:
    """Trades for a Monte Carlo source: 'history', 'live', or 'csv:<run id>'."""
    if source == "history":
        path = CACHE_DIR / "rl_history.json"
        if not path.exists():
            raise ValueError("run the history replay first")
        trades = json.loads(path.read_text()).get("trades")
        if not trades:
            raise ValueError("this replay predates trade logging — press Replay & train again")
        return trades
    if source == "live":
        from pipeline import fly_rl_trader

        rows = [t for t in fly_rl_trader._fly_trades("state != 'open'") if t["state"] != "cancelled"]
        return [float(t["realised_pnl"] or 0) / (float(t["quantity"]) * float(t["entry_price"]))
                for t in rows if t["quantity"] and t["entry_price"]]
    if source.startswith("csv:"):
        run = load_run(source[4:])
        if not run:
            raise ValueError("no such CSV run")
        return run["trades"]
    raise ValueError(f"unknown source {source}")


# ---- custom CSV -------------------------------------------------------------------

ALIASES = {
    "date": ("date", "datetime", "timestamp", "time", "day"),
    "symbol": ("symbol", "ticker", "stock", "instrument", "name", "scrip"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c", "adj close", "adj_close", "adjclose", "ltp", "price"),
    "volume": ("volume", "vol", "v", "qty", "quantity"),
}


def parse_csv(raw: bytes, default_symbol: str = "CUSTOM") -> tuple[pd.DataFrame, list[str]]:
    """Read an OHLCV CSV into Tradeo's price shape, or explain exactly what is wrong."""
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"file is larger than {MAX_UPLOAD_MB} MB")
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"could not read it as CSV: {exc}") from exc

    lower = {c.strip().lower(): c for c in df.columns}
    mapping, notes = {}, []
    for want, names in ALIASES.items():
        found = next((lower[n] for n in names if n in lower), None)
        if found is not None:
            mapping[found] = want
    df = df.rename(columns=mapping)
    missing = [c for c in ("date", "open", "high", "low", "close", "volume") if c not in df.columns]
    if missing:
        raise ValueError(f"missing column(s): {', '.join(missing)}. Found: {', '.join(map(str, lower))}")
    if "symbol" not in df.columns:
        df["symbol"] = default_symbol
        notes.append(f"no symbol column — treated as one stock, '{default_symbol}'")

    df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=False, utc=True).dt.tz_localize(None)
    bad_dates = int(df["date"].isna().sum())
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")
    before = len(df)
    df = df.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    df = df[(df["close"] > 0) & (df["high"] >= df["low"])]
    if before - len(df):
        notes.append(f"dropped {before - len(df)} unusable rows"
                     + (f" ({bad_dates} with unreadable dates)" if bad_dates else ""))
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["date"] = df["date"].dt.normalize()
    df = (df.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last")
          .reset_index(drop=True))

    counts = df.groupby("symbol").size()
    short = counts[counts < MIN_ROWS]
    if len(short):
        notes.append(f"skipped {len(short)} symbol(s) with under {MIN_ROWS} daily rows: "
                     + ", ".join(short.index[:8]))
        df = df[~df["symbol"].isin(short.index)]
    if df.empty:
        raise ValueError(f"no symbol has {MIN_ROWS}+ daily rows; the indicators need about a year "
                         "of warm-up (200-day average, 52-week range) before the first trade")
    return df[["symbol", "date", "open", "high", "low", "close", "volume"]], notes


def _features(prices: pd.DataFrame) -> pd.DataFrame:
    """Same features as the NSE replay. With few stocks, standardise over time instead."""
    from .features import _per_symbol

    data = pd.concat([_per_symbol(g) for _, g in prices.groupby("symbol")], ignore_index=True)
    data = data.dropna(subset=FEATURES).copy()
    if data["symbol"].nunique() >= 5:
        g = data.groupby("date")[FEATURES]
        z = (data[FEATURES] - g.transform("mean")) / g.transform("std").replace(0, np.nan)
    else:
        # Cross-sectional z-scores are undefined for one stock. An expanding
        # per-stock z-score uses only the past, so it stays causal.
        g = data.groupby("symbol")[FEATURES]
        mean = g.transform(lambda s: s.expanding(20).mean().shift(1))
        std = g.transform(lambda s: s.expanding(20).std().shift(1)).replace(0, np.nan)
        z = (data[FEATURES] - mean) / std
    data[FEATURES] = z.clip(-3, 3).fillna(0)
    return data.sort_values(["date", "symbol"]).reset_index(drop=True)


def run_csv(raw: bytes, filename: str, start_from: str = "live", train_live: bool = False,
            seed: int = 1) -> dict[str, Any]:
    from . import connectome, reservoir

    started = time.time()
    default_symbol = (filename.rsplit(".", 1)[0] or "CUSTOM").upper()[:20]
    prices, notes = parse_csv(raw, default_symbol)
    data = _features(prices)
    dates, symbols, tgt = rl._grid(data, ["target"])
    targets = tgt[..., 0]
    if len(dates) < rl.WARMUP_DAYS + 4 * HORIZON:
        raise ValueError("too few usable days after the indicator warm-up to trade anything")

    frame = reservoir.run(connectome.mushroom_body(), data)
    cols = [c for c in frame.columns if c.startswith("n")]
    _, _, states = rl._grid(frame, cols)

    loaded = rl.load_agent()
    if start_from == "live" and loaded:
        agent = rl.DopamineAgent.from_dict(loaded[0].to_dict())
        agent.rng = np.random.default_rng(seed)
    else:
        agent = rl.DopamineAgent(dim=states.shape[2], seed=seed)
        start_from = "fresh"
    rewards0, punish0 = agent.rewards, agent.punishments

    top_k = min(rl.TOP_K, len(symbols))
    log_rows: list = []
    fly, agent, counts = rl.simulate(states, targets, seed=seed, agent=agent,
                                     trade_log=log_rows, top_k=top_k)

    everything = pd.Series({t: float(np.nanmean(targets[t]) - ROUND_TRIP)
                            for t in range(rl.WARMUP_DAYS, len(dates), HORIZON)
                            if not np.isnan(targets[t]).all()})
    first, last = rl.WARMUP_DAYS, len(dates) - 1
    close = prices.pivot(index="date", columns="symbol", values="close").reindex(pd.to_datetime(dates))
    hold = close.iloc[first:].ffill()
    hold_curve = (hold / hold.iloc[0]).mean(axis=1)

    def curve(series: pd.Series) -> dict[str, float]:
        growth = (1 + series.sort_index()).cumprod()
        return {str(pd.Timestamp(dates[t]).date()): float(v) for t, v in growth.items()}

    fly_c, ew_c = curve(fly), curve(everything)
    equity = [{"date": d, "fly_brain": round(fly_c[d], 4), "rotate_everything": round(ew_c.get(d, np.nan), 4)
               if d in ew_c else None,
               "buy_and_hold": round(float(hold_curve.get(pd.Timestamp(d), np.nan)), 4)}
              for d in sorted(fly_c)]

    net = [r for _, _, r, _ in log_rows]
    years = max(1e-9, (pd.Timestamp(dates[last]) - pd.Timestamp(dates[first])).days / 365.25)
    total = float(np.prod([1 + r for r in fly.values]) - 1) if len(fly) else 0.0
    hold_total = float(hold_curve.iloc[-1] - 1) if len(hold_curve) else 0.0
    by_symbol = (pd.DataFrame([(str(symbols[s]), r) for _, s, r, _ in log_rows], columns=["symbol", "r"])
                 .groupby("symbol")["r"].agg(["count", "mean", lambda x: (x > 0).mean()]))
    by_symbol.columns = ["trades", "avg", "win"]

    run_id = uuid.uuid4().hex[:10]
    result = {
        "id": run_id,
        "filename": filename,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "notes": notes,
        "data": {"symbols": [str(s) for s in symbols], "rows": int(len(prices)),
                 "from": str(pd.Timestamp(dates[0]).date()), "to": str(pd.Timestamp(dates[-1]).date()),
                 "trading_from": str(pd.Timestamp(dates[first]).date()),
                 "standardised": "across stocks" if len(symbols) >= 5 else "over time (few stocks)",
                 "slots": top_k},
        "brain": {"start_from": start_from, "learned_rewards": agent.rewards - rewards0,
                  "learned_punishments": agent.punishments - punish0,
                  "kept_as_live_brain": False},
        "summary": {
            "trades": counts["trades"], "wins": counts["wins"], "losses": counts["trades"] - counts["wins"],
            "win_rate": round(counts["wins"] / max(1, counts["trades"]) * 100, 1),
            "avg_trade_pct": round(float(np.mean(net)) * 100, 3) if net else None,
            "total_return_pct": round(total * 100, 2),
            "per_year_pct": round(((1 + total) ** (1 / years) - 1) * 100, 2) if total > -1 else -100.0,
            "buy_and_hold_pct": round(hold_total * 100, 2),
            "rotate_everything_per_year_pct": round(float(everything.mean()) * PERIODS_PER_YEAR * 100, 2),
            "invested_pct": round(counts["trades"] / max(1, len(fly) * top_k) * 100, 1),
            "round_trip_cost_pct": round(ROUND_TRIP * 100, 3),
        },
        "equity": equity,
        "by_symbol": [{"symbol": s, "trades": int(r.trades), "avg_pct": round(float(r.avg) * 100, 2),
                       "win_rate": round(float(r.win) * 100, 1)}
                      for s, r in by_symbol.sort_values("trades", ascending=False).iterrows()],
        "trades": [[round(r, 5), round(p, 5)] for _, _, r, p in log_rows],
        "recent_trades": [{"date": str(pd.Timestamp(dates[t]).date()), "symbol": str(symbols[s]),
                           "net_pct": round(r * 100, 2), "expected_pct": round(p * 100, 3)}
                          for t, s, r, p in log_rows[-25:]][::-1],
    }
    result["monte_carlo"] = monte_carlo(result["trades"]) if len(net) >= 10 else None

    if train_live and loaded:
        _, payload = loaded
        rl.save_agent(agent, {k: v for k, v in payload.items() if k != "agent"}
                      | {"trained_on_csv": [*payload.get("trained_on_csv", []), filename]})
        result["brain"]["kept_as_live_brain"] = True
        try:
            from pipeline import fly_rl_trader

            fly_rl_trader._event("trained", f"live brain trained on {filename}: "
                                            f"{counts['trades']} trades, win rate "
                                            f"{result['summary']['win_rate']}%")
        except (ImportError, KeyError) as exc:
            log.debug("could not log the training event: %s", exc)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{run_id}.json").write_text(json.dumps(result))
    return result


def load_run(run_id: str) -> dict | None:
    path = RUNS_DIR / f"{''.join(c for c in run_id if c.isalnum())}.json"
    return json.loads(path.read_text()) if path.exists() else None


def list_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    out = []
    for path in sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        try:
            r = json.loads(path.read_text())
            out.append({"id": r["id"], "filename": r["filename"], "created_at": r["created_at"],
                        "symbols": len(r["data"]["symbols"]), "win_rate": r["summary"]["win_rate"],
                        "trades": r["summary"]["trades"], "per_year_pct": r["summary"]["per_year_pct"],
                        "kept_as_live_brain": r["brain"]["kept_as_live_brain"]})
        except (ValueError, KeyError):
            continue
    return out


def sample_csv(symbols: tuple[str, ...] = ("RELIANCE", "TCS"), years: int = 5) -> str:
    """A ready-to-edit example in the expected format, cut from Tradeo's own NSE data."""
    prices = pd.read_parquet(CACHE_DIR / "prices.parquet")
    prices = prices[prices["symbol"].isin(symbols)].copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.tz_localize(None)
    prices = prices[prices["date"] >= prices["date"].max() - pd.DateOffset(years=years)]
    prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close"):
        prices[c] = prices[c].round(2)
    prices["volume"] = prices["volume"].astype("int64")
    return prices[["date", "symbol", "open", "high", "low", "close", "volume"]].to_csv(index=False)


# ---- background jobs ----------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def start_csv(raw: bytes, filename: str, start_from: str, train_live: bool) -> dict:
    # Validate synchronously so a bad file is rejected with its reason at once.
    parse_csv(raw, (filename.rsplit(".", 1)[0] or "CUSTOM").upper()[:20])
    job = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _jobs[job] = {"running": True, "error": None, "run_id": None, "filename": filename}

    def work() -> None:
        try:
            result = run_csv(raw, filename, start_from, train_live)
            _jobs[job].update(run_id=result["id"])
        except Exception as exc:
            log.error("csv run failed: %s", exc, exc_info=True)
            _jobs[job].update(error=str(exc))
        finally:
            _jobs[job]["running"] = False

    threading.Thread(target=work, name="fly-csv", daemon=True).start()
    return {"job": job}


def job(job_id: str) -> dict | None:
    return _jobs.get(job_id)
