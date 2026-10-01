"""
Does the fly brain pick better stocks than Tradeo already does?

    venv/bin/python -m ml.flybrain.experiment

Walk-forward by calendar year: every test year is predicted by a readout fitted
only on the years before it, with a HORIZON-day purge so no training target
overlaps a test day. Four contenders see identical rows:

  fly       ridge readout on the mushroom body's output neurons
  random    same readout on a random network of the same size and density
  ridge     same readout straight on the features, no network at all
  baseline  Tradeo's current scorer (price components; no fitting)

The fly is allowed to rank live stocks only if it beats the baseline on
out-of-sample IC and on net pick returns, and beats the random control on IC.
Otherwise the fly wiring adds nothing the rest of the system doesn't already
do, and the report says so.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from autopilot import costs
from core.config import PROJECT_ROOT

from . import connectome, reservoir
from .features import FEATURES, HORIZON, build

log = logging.getLogger("tradeo.flybrain")

CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "flybrain"
REPORT = CACHE_DIR / "report.json"
MODEL = CACHE_DIR / "readout.joblib"

FIRST_TEST_YEAR = 2020
RIDGE_ALPHA = 10.0
TOP_N = 5

# One round trip on the paper engine's own cost model: charges both legs,
# slippage both legs.
ROUND_TRIP = costs.charges(1.0, "BUY") + costs.charges(1.0, "SELL") + 2 * costs.SLIPPAGE


def _rank_target(data: pd.DataFrame) -> pd.Series:
    """Cross-sectional rank: the question is which stock, not whether the market rises."""
    return data.groupby("date")["target"].rank(pct=True) - 0.5


def _walk_forward(x: np.ndarray, data: pd.DataFrame) -> np.ndarray:
    prediction = np.full(len(data), np.nan)
    y = _rank_target(data).to_numpy()
    dates = data["date"].to_numpy()
    all_dates = np.sort(np.unique(dates))

    for year in range(FIRST_TEST_YEAR, pd.Timestamp(all_dates[-1]).year + 1):
        test = (dates >= np.datetime64(f"{year}-01-01")) & (dates < np.datetime64(f"{year + 1}-01-01"))
        if not test.any():
            continue
        first_test = dates[test].min()
        cutoff = all_dates[max(0, np.searchsorted(all_dates, first_test) - HORIZON)]
        train = (dates < cutoff) & ~np.isnan(y)
        scaler = StandardScaler().fit(x[train])
        model = Ridge(alpha=RIDGE_ALPHA).fit(scaler.transform(x[train]), y[train])
        prediction[test] = model.predict(scaler.transform(x[test]))
    return prediction


def _evaluate(data: pd.DataFrame, score: str) -> dict:
    frame = data.dropna(subset=[score, "target"])
    frame = frame[frame["date"].dt.year >= FIRST_TEST_YEAR]

    daily_ic = frame.groupby("date").apply(
        lambda g: g[score].rank().corr(g["target"].rank()), include_groups=False).dropna()
    # Daily ICs overlap by HORIZON days, which inflates a naive t-stat. Every
    # HORIZON-th day is independent.
    spaced = daily_ic.iloc[::HORIZON]
    t_stat = spaced.mean() / (spaced.std() / np.sqrt(len(spaced))) if len(spaced) > 2 else 0.0

    # Trade it: every HORIZON days buy the TOP_N highest-ranked, equal weight.
    rebalance = sorted(frame["date"].unique())[::HORIZON]
    picks = frame[frame["date"].isin(rebalance)]
    top = picks.sort_values(score, ascending=False).groupby("date").head(TOP_N)
    pick_return = top.groupby("date")["target"].mean() - ROUND_TRIP
    universe = picks.groupby("date")["target"].mean() - ROUND_TRIP
    periods_per_year = 250 / HORIZON

    by_year = {}
    for year, g in daily_ic.groupby(daily_ic.index.year):
        year_picks = pick_return[pick_return.index.year == year]
        by_year[int(year)] = {"ic": round(float(g.mean()), 4),
                              "net_pick_return_pct": round(float(year_picks.sum() * 100), 2)}

    return {
        "ic_mean": round(float(daily_ic.mean()), 4),
        "ic_t_stat": round(float(t_stat), 2),
        "ic_hit_rate": round(float((daily_ic > 0).mean()), 3),
        f"top{TOP_N}_net_return_per_trade_pct": round(float(pick_return.mean() * 100), 3),
        f"top{TOP_N}_net_annualised_pct": round(float(pick_return.mean() * periods_per_year * 100), 2),
        "universe_net_annualised_pct": round(float(universe.mean() * periods_per_year * 100), 2),
        f"top{TOP_N}_win_rate": round(float((pick_return > 0).mean()), 3),
        "trades": int(len(pick_return)),
        "by_year": by_year,
    }


def main() -> dict:
    features_path = CACHE_DIR / "features.parquet"
    if features_path.exists():
        data = pd.read_parquet(features_path)
    else:
        data = build(pd.read_parquet(CACHE_DIR / "prices.parquet"))
        data.to_parquet(features_path)

    fly = connectome.mushroom_body()
    control = connectome.random_control(fly)

    contenders = {"baseline": data["baseline_score"].to_numpy()}
    contenders["ridge"] = _walk_forward(data[FEATURES].to_numpy(), data)
    for name, net in (("fly", fly), ("random", control)):
        states = reservoir.run(net, data)
        contenders[name] = _walk_forward(states.filter(regex=r"^n\d+$").to_numpy(), data)

    for name, values in contenders.items():
        data[name] = values
    results = {name: _evaluate(data, name) for name in contenders}

    fly_r, base_r, rand_r = results["fly"], results["baseline"], results["random"]
    net_key = f"top{TOP_N}_net_annualised_pct"
    checks = {
        "beats_baseline_ic": fly_r["ic_mean"] > base_r["ic_mean"],
        "beats_baseline_net_return": fly_r[net_key] > base_r[net_key],
        "beats_random_wiring_ic": fly_r["ic_mean"] > rand_r["ic_mean"],
        "ic_significant": fly_r["ic_t_stat"] > 2.0,
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "passed": all(checks.values()),
        "checks": checks,
        "network": {"neurons": fly.size, "connections": int(fly.weights.nnz),
                    "inputs": len(fly.inputs), "readout": len(fly.readout)},
        "setup": {"symbols": int(data["symbol"].nunique()),
                  "first_test_year": FIRST_TEST_YEAR, "horizon_days": HORIZON,
                  "round_trip_cost_pct": round(ROUND_TRIP * 100, 3),
                  "caveat": "today's universe members only — survivorship bias, equal for all"},
        "results": results,
    }
    REPORT.write_text(json.dumps(report, indent=2))

    # The readout the live ranker uses, fitted on every labelled row. Saved
    # whether or not the gate passed; ranker.py refuses to use it unless the
    # report says it did.
    import joblib

    states = reservoir.run(fly, data).filter(regex=r"^n\d+$").to_numpy()
    labelled = ~np.isnan(_rank_target(data).to_numpy())
    scaler = StandardScaler().fit(states[labelled])
    model = Ridge(alpha=RIDGE_ALPHA).fit(scaler.transform(states[labelled]),
                                         _rank_target(data).to_numpy()[labelled])
    joblib.dump({"scaler": scaler, "model": model, "report": REPORT.name}, MODEL)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(main(), indent=2))
