"""
The mushroom body, learning by reward and punishment.

    venv/bin/python -m ml.flybrain.rl

This is how the real fly learns. Kenyon-cell activity reaches the output
neurons (MBONs); dopamine neurons fire on a *reward prediction error* — more
reward than expected, or less — and that dopamine strengthens or weakens the
synapses that were active when the choice was made (Aso & Rubin 2016;
Hige et al. 2015). Here the connectome stays fixed and only a plastic layer
on the MBON output learns, by exactly that rule:

    value   Q(s)   = w · φ(s)                 how good a trade on s looks
    dopamine δ     = reward − Q(s)            after the trade closes
    plasticity Δw  = η · δ · φ(s) / |φ(s)|²   (normalised delta rule)

A reward is the trade's net return after charges and slippage. A losing trade
is a negative reward, a punishment, and pushes w away from what chose it.
Learning happens only from trades actually taken: the agent is never told
what a stock it skipped would have done. That makes it reinforcement learning
(a contextual bandit), not the supervised readout of experiment.py.

It also learns when *not* to trade: a slot is only filled if the predicted
net value is above zero, so a brain that has learned to expect losses sits
in cash.

The backtest is causal by construction: the agent walks forward through the
dates, and at each decision it has learned only from trades that had already
closed. Every parameter below was fixed before the first run. None were
tuned on the results; tuning them to pass the gate would make the gate
meaningless.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .experiment import CACHE_DIR, ROUND_TRIP
from .features import FEATURES, HORIZON, build

log = logging.getLogger("tradeo.flybrain.rl")

RL_REPORT = CACHE_DIR / "rl_report.json"
RL_AGENT = CACHE_DIR / "rl_agent.json"

TOP_K = 5              # slots per rebalance
LEARNING_RATE = 0.02   # η in the plasticity rule
EPSILON = 0.10         # chance a slot explores a random stock instead
MIN_EDGE = 0.0         # fill a slot only if predicted net return > this
WARMUP_DAYS = 60       # reservoir settles before the first decision
FIRST_TEST_YEAR = 2020
SEEDS = (1, 2, 3, 4, 5)


@dataclass
class DopamineAgent:
    """A plastic readout trained by reward prediction error."""

    dim: int
    learning_rate: float = LEARNING_RATE
    epsilon: float = EPSILON
    seed: int = 0
    w: np.ndarray = field(default=None)          # type: ignore[assignment]
    mean: np.ndarray = field(default=None)       # type: ignore[assignment]
    var: np.ndarray = field(default=None)        # type: ignore[assignment]
    seen: int = 0
    rewards: int = 0
    punishments: int = 0
    dopamine_sum: float = 0.0

    def __post_init__(self) -> None:
        if self.w is None:
            self.w = np.zeros(self.dim + 1)
        if self.mean is None:
            self.mean = np.zeros(self.dim)
            self.var = np.ones(self.dim)
        self.rng = np.random.default_rng(self.seed)

    # -- perception ---------------------------------------------------------

    def observe(self, states: np.ndarray) -> None:
        """Update the running normaliser with today's states (causal)."""
        for row in states:
            self.seen += 1
            delta = row - self.mean
            self.mean += delta / self.seen
            self.var += (delta * (row - self.mean) - self.var) / self.seen

    def phi(self, states: np.ndarray) -> np.ndarray:
        z = (states - self.mean) / np.sqrt(self.var + 1e-8)
        return np.hstack([np.clip(z, -4, 4), np.ones((len(z), 1))])

    def value(self, phi: np.ndarray) -> np.ndarray:
        return phi @ self.w

    # -- action -------------------------------------------------------------

    def choose(self, phi: np.ndarray, k: int = TOP_K, explore: bool = True) -> list[int]:
        """Indices to buy: the best predicted, some slots exploring, none below MIN_EDGE."""
        q = self.value(phi)
        order = [int(i) for i in np.argsort(-q) if q[i] > MIN_EDGE][:k]
        if explore:
            pool = [i for i in range(len(q)) if i not in order]
            for slot in range(k):
                if pool and self.rng.random() < self.epsilon:
                    pick = int(self.rng.choice(pool))
                    pool.remove(pick)
                    if slot < len(order):
                        order[slot] = pick
                    else:
                        order.append(pick)
        return order

    # -- learning -----------------------------------------------------------

    def learn(self, phi: np.ndarray, reward: float) -> float:
        """One dopamine pulse. Returns δ, the prediction error."""
        delta = float(reward - phi @ self.w)
        self.w += self.learning_rate * delta * phi / (phi @ phi + 1e-8)
        self.dopamine_sum += delta
        if reward > 0:
            self.rewards += 1
        else:
            self.punishments += 1
        return delta

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict:
        return {"dim": self.dim, "learning_rate": self.learning_rate, "epsilon": self.epsilon,
                "seed": self.seed, "w": self.w.tolist(), "mean": self.mean.tolist(),
                "var": self.var.tolist(), "seen": self.seen, "rewards": self.rewards,
                "punishments": self.punishments, "dopamine_sum": self.dopamine_sum}

    @classmethod
    def from_dict(cls, d: dict) -> "DopamineAgent":
        agent = cls(dim=d["dim"], learning_rate=d["learning_rate"], epsilon=d["epsilon"],
                    seed=d["seed"])
        agent.w, agent.mean, agent.var = (np.array(d[k]) for k in ("w", "mean", "var"))
        for k in ("seen", "rewards", "punishments", "dopamine_sum"):
            setattr(agent, k, d[k])
        return agent


# ---- walk-forward simulation ------------------------------------------------


def _grid(frame: pd.DataFrame, columns: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(dates, symbols, values[date, symbol, col]) with NaN where a stock has no row."""
    dates = np.sort(frame["date"].unique())
    symbols = np.sort(frame["symbol"].unique())
    values = (frame.set_index(["date", "symbol"])[columns]
              .reindex(pd.MultiIndex.from_product([dates, symbols]))
              .to_numpy(np.float64).reshape(len(dates), len(symbols), len(columns)))
    return dates, symbols, values


def simulate(states: np.ndarray, targets: np.ndarray, seed: int,
             agent: DopamineAgent | None = None,
             trade_log: list | None = None,
             top_k: int = TOP_K) -> tuple[pd.Series, DopamineAgent, dict]:
    """
    Walk forward through `states[date, symbol, dim]`, trading every HORIZON days.

    Returns the net return of each rebalance period (cash slots earn 0), the
    trained agent, and trade counts. `trade_log`, if given, receives
    (date index, symbol index, net reward, predicted value) per trade.
    """
    n_dates, n_symbols, dim = states.shape
    agent = agent or DopamineAgent(dim=dim, seed=seed)
    pending: list[tuple[int, np.ndarray, float]] = []
    period: dict[int, float] = {}
    trades = wins = 0

    for t in range(WARMUP_DAYS, n_dates, HORIZON):
        # Dopamine for every trade that has closed by today's close.
        still = []
        for closes_at, phi, reward in pending:
            if closes_at <= t:
                agent.learn(phi, reward)
            else:
                still.append((closes_at, phi, reward))
        pending = still

        live = ~np.isnan(states[t]).any(axis=1) & ~np.isnan(targets[t])
        idx = np.flatnonzero(live)
        if len(idx) < top_k:
            continue
        agent.observe(states[t, idx])
        phi = agent.phi(states[t, idx])
        chosen = agent.choose(phi, k=top_k)

        net = [float(targets[t, idx[c]] - ROUND_TRIP) for c in chosen]
        for c, r in zip(chosen, net):
            pending.append((t + HORIZON, phi[c], r))
            if trade_log is not None:
                trade_log.append((t, int(idx[c]), r, float(phi[c] @ agent.w)))
        trades += len(net)
        wins += sum(r > 0 for r in net)
        period[t] = sum(net) / top_k          # empty slots sit in cash

    return pd.Series(period), agent, {"trades": trades, "wins": wins}


def _stats(returns: pd.Series, dates: np.ndarray, counts: dict | None = None) -> dict:
    s = returns.copy()
    s.index = pd.to_datetime(dates[s.index])
    test = s[s.index.year >= FIRST_TEST_YEAR]
    per_year = 250 / HORIZON
    out = {
        "net_annualised_pct": round(float(test.mean() * per_year * 100), 2),
        "period_win_rate": round(float((test > 0).mean()), 3),
        "by_year_pct": {int(y): round(float(g.sum() * 100), 2) for y, g in test.groupby(test.index.year)},
        "periods": int(len(test)),
    }
    if counts:
        out["trades_all_years"] = counts["trades"]
        out["trade_win_rate_all_years"] = round(counts["wins"] / max(1, counts["trades"]), 3)
    return out


def _fixed_rule(scores: np.ndarray, targets: np.ndarray, n_dates: int, rng=None) -> pd.Series:
    """Always fully invested in the top-K by `scores` (or random if rng), for comparison."""
    period = {}
    for t in range(WARMUP_DAYS, n_dates, HORIZON):
        live = np.flatnonzero(~np.isnan(scores[t]) & ~np.isnan(targets[t]))
        if len(live) < TOP_K:
            continue
        top = rng.choice(live, TOP_K, replace=False) if rng is not None \
            else live[np.argsort(-scores[t, live])[:TOP_K]]
        period[t] = float(np.mean(targets[t, top]) - ROUND_TRIP)
    return pd.Series(period)


def _agent_curve(name, states, targets, dates):
    runs = [simulate(states, targets, seed) for seed in SEEDS]
    stats = [_stats(r, dates, c) for r, _, c in runs]
    mean = float(np.mean([s["net_annualised_pct"] for s in stats]))
    log.info("%-12s net %.2f%%/yr over %d seeds", name, mean, len(SEEDS))
    return {"net_annualised_pct_mean": round(mean, 2),
            "net_annualised_pct_by_seed": [s["net_annualised_pct"] for s in stats],
            "seed_1": stats[0]}


def main() -> dict:
    from . import connectome, reservoir

    data = pd.read_parquet(CACHE_DIR / "features.parquet")
    fly = connectome.mushroom_body()
    control = connectome.random_control(fly)

    dates, symbols, tgt = _grid(data, ["target"])
    targets = tgt[..., 0]
    _, _, base = _grid(data, ["baseline_score"])
    _, _, feats = _grid(data, FEATURES)

    results = {}
    for name, net in (("fly_rl", fly), ("random_wiring_rl", control)):
        frame = reservoir.run(net, data)
        cols = [c for c in frame.columns if c.startswith("n")]
        _, _, states = _grid(frame, cols)
        results[name] = _agent_curve(name, states, targets, dates)
        if name == "fly_rl":
            fly_states = states
    results["features_rl"] = _agent_curve("features_rl", feats, targets, dates)

    results["scorer_top5"] = _stats(_fixed_rule(base[..., 0], targets, len(dates)), dates)
    rng = np.random.default_rng(0)
    rand = [_stats(_fixed_rule(targets, targets, len(dates), rng), dates)["net_annualised_pct"]
            for _ in SEEDS]
    results["random_top5"] = {"net_annualised_pct_mean": round(float(np.mean(rand)), 2)}
    universe = {t: float(np.nanmean(targets[t]) - ROUND_TRIP)
                for t in range(WARMUP_DAYS, len(dates), HORIZON)}
    results["equal_weight_all"] = _stats(pd.Series(universe), dates)

    fly_net = results["fly_rl"]["net_annualised_pct_mean"]
    checks = {
        "profitable_after_costs": fly_net > 0,
        "beats_scorer": fly_net > results["scorer_top5"]["net_annualised_pct"],
        "beats_random_wiring": fly_net > results["random_wiring_rl"]["net_annualised_pct_mean"],
        "beats_no_network": fly_net > results["features_rl"]["net_annualised_pct_mean"],
        "beats_buying_everything": fly_net > results["equal_weight_all"]["net_annualised_pct"],
        "positive_on_every_seed": min(results["fly_rl"]["net_annualised_pct_by_seed"]) > 0,
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "passed": all(checks.values()),
        "checks": checks,
        "setup": {"symbols": len(symbols), "first_test_year": FIRST_TEST_YEAR,
                  "horizon_days": HORIZON, "top_k": TOP_K, "learning_rate": LEARNING_RATE,
                  "epsilon": EPSILON, "seeds": list(SEEDS),
                  "round_trip_cost_pct": round(ROUND_TRIP * 100, 3),
                  "learning": "online, causal: dopamine only from trades already closed",
                  "caveat": "today's universe members only — survivorship bias, equal for all"},
        "results": results,
    }
    RL_REPORT.write_text(json.dumps(report, indent=2))

    # The live agent: trained on all history by the same rule, then it keeps
    # learning from its own paper trades (pipeline/fly_rl_trader.py).
    _, agent, _ = simulate(fly_states, targets, seed=SEEDS[0])
    save_agent(agent, {"trained_through": str(pd.Timestamp(dates[-1]).date())})
    return report


# ---- live use -----------------------------------------------------------------


def save_agent(agent: DopamineAgent, extra: dict | None = None, path: Path = RL_AGENT) -> None:
    payload = {"agent": agent.to_dict(), **(extra or {})}
    if path.exists():
        old = json.loads(path.read_text())
        payload = {**{k: v for k, v in old.items() if k != "agent"}, **payload}
    path.write_text(json.dumps(payload))


def load_agent(path: Path = RL_AGENT) -> tuple[DopamineAgent, dict] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return DopamineAgent.from_dict(payload["agent"]), payload


def live_states(symbols: list[str]) -> tuple[list[str], np.ndarray, str]:
    """Mushroom-body output for each symbol at the latest close."""
    from . import connectome, reservoir
    from .ranker import _prices

    data = build(_prices(symbols))
    frame = reservoir.run(connectome.mushroom_body(), data)
    latest = frame["date"] == frame["date"].max()
    cols = [c for c in frame.columns if c.startswith("n")]
    rows = frame.loc[latest]
    return rows["symbol"].tolist(), rows[cols].to_numpy(np.float64), str(frame["date"].max().date())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(main(), indent=2))
