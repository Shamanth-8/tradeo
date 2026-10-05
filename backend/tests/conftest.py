"""
Tests run from backend/:  ./venv/bin/python -m pytest -q

They never write to the real database, credential store or agent switches:
anything stateful is pointed at a temporary directory first.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def agent_store(tmp_path, monkeypatch):
    """Agent switches in a throwaway file, starting from the shipped defaults."""
    from autopilot import agents

    monkeypatch.setattr(agents, "STORE", tmp_path / "agents.json")
    monkeypatch.setattr(agents, "apply_watchtower", lambda on: None)  # don't start real loops
    return agents


@pytest.fixture
def paper_db(tmp_path, monkeypatch, agent_store):
    """
    A fresh paper account in a throwaway database, with prices you set.

        paper_db.prices["INFY"] = 1500.0

    Nothing touches the network: quotes come from `prices`, and the market
    filter reports an uptrend unless a test says otherwise.
    """
    from types import SimpleNamespace

    from autopilot import costs, risk, store, triggers
    from data.fetchers.stock_fetcher import stock_fetcher
    from data.storage import database

    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "paper.db"))
    store._init()
    triggers._init()
    risk._init()

    prices: dict[str, float] = {}
    monkeypatch.setattr(triggers, "_price", lambda symbol: prices.get(symbol))
    monkeypatch.setattr(stock_fetcher, "get_live_price",
                        lambda symbol, exchange="NSE": {"price": prices.get(symbol, 0)})
    monkeypatch.setattr(costs, "daily_traded_value", lambda symbol: None)   # flat slippage
    trend = {"ok": True, "detail": "test uptrend"}
    monkeypatch.setattr(risk, "market_trend", lambda: dict(trend))
    return SimpleNamespace(prices=prices, trend=trend, store=store, triggers=triggers, risk=risk,
                           agents=agent_store)
