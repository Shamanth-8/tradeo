"""
Regression tests for bugs found by hand, and for rules the app must keep.
Each test names what it protects.
"""

from __future__ import annotations

import numpy as np
import pytest


# ---- defaults: nothing runs or trades until switched on -----------------------

def test_everything_starts_off(agent_store):
    assert agent_store.switch("watchtower") is False
    assert agent_store.daily_pick_settings()["enabled"] is False


def test_daily_pick_refuses_while_off(agent_store):
    from pipeline import daily_pick

    result = daily_pick.run()
    assert result["ok"] is False and "switched off" in result["error"]


def test_daily_pick_rules_are_validated(agent_store):
    with pytest.raises(ValueError):
        agent_store.update("daily-pick", {"min_score": 200})
    with pytest.raises(ValueError):
        agent_store.update("daily-pick", {"run_at": "18:00"})


def test_watchtower_switch_persists(agent_store):
    agent_store.set_switch("watchtower", True)
    assert agent_store.switch("watchtower") is True
    agent_store.set_switch("watchtower", False)
    assert agent_store.switch("watchtower") is False


def test_watchtower_never_trades_itself():
    from pipeline.watchtower import Watchtower

    assert not hasattr(Watchtower, "_arm_trigger")


def test_fly_brain_off_means_no_trades(monkeypatch):
    from pipeline import fly_rl_trader
    from pipeline.watchtower import Candidate

    monkeypatch.setattr(fly_rl_trader, "enabled", lambda: False)
    handoff = fly_rl_trader.consider_openings([Candidate(symbol="INFY", direction="bullish")])
    assert handoff["note"] == "fly brain trading is OFF" and not handoff["decisions"]


def test_research_agent_cannot_trade():
    source = open("mcp_server.py").read()
    assert "def tradeo_paper_pick" not in source


# ---- news and sentiment ------------------------------------------------------------

def test_symbol_match_is_whole_word(monkeypatch):
    """'LT' used to match 'resuLTs', 'STL' and 'muLTibagger'."""
    from data.fetchers import news_fetcher as nf

    articles = [
        {"title": "Q2 results beat estimates", "summary": ""},
        {"title": "STL Networks rallies", "summary": ""},
        {"title": "Multibagger defence stock", "summary": ""},
        {"title": "L&T: LT bags ₹5,000 crore order", "summary": ""},
    ]
    monkeypatch.setattr(nf.news_fetcher, "fetch_all", lambda: articles)
    hits = nf.news_fetcher.fetch_latest_news(keywords=["LT"])
    assert [h["title"] for h in hits] == ["L&T: LT bags ₹5,000 crore order"]


def _no_verifier(monkeypatch):
    from ai.providers import verifier as v

    monkeypatch.setattr(v.verifier, "local_only", True)


def test_pretrade_passes_without_news(monkeypatch):
    from data.fetchers import news_fetcher as nf
    from pipeline import pretrade

    _no_verifier(monkeypatch)
    monkeypatch.setattr(nf.news_fetcher, "fetch_latest_news", lambda keywords=[]: [])
    assert pretrade.check("ICICIBANK", 64)["ok"] is True


def test_pretrade_blocks_bad_news(monkeypatch):
    from data.fetchers import news_fetcher as nf
    from pipeline import pretrade

    _no_verifier(monkeypatch)
    bad = [{"title": "Fraud probe widens, shares crash on terrible loss and default fears"}]
    monkeypatch.setattr(nf.news_fetcher, "fetch_latest_news", lambda keywords=[]: bad)
    result = pretrade.check("WIPRO", 64)
    assert result["ok"] is False and result["why"].startswith("red-flag news")


def test_one_gloomy_headline_does_not_block(monkeypatch):
    """The news check is a minor filter: a single negative-sounding headline is too noisy to veto a trade."""
    from data.fetchers import news_fetcher as nf
    from pipeline import pretrade

    _no_verifier(monkeypatch)
    gloomy = [{"title": "Shares slip as weak quarter disappoints, terrible outlook"}]
    monkeypatch.setattr(nf.news_fetcher, "fetch_latest_news", lambda keywords=[]: gloomy)
    assert pretrade.check("WIPRO", 64)["ok"] is True


# ---- voice ----------------------------------------------------------------------------

@pytest.mark.parametrize("heard, command", [
    ("Hey Tradio, do paper trading.", "do paper trading."),
    ("Tradeo show my portfolio", "show my portfolio"),
    ("so anyway the trade was fine", None),
])
def test_wake_word_variants(heard, command):
    """Whisper writes 'Tradeo' as 'Tradio'; the wake word used to be stripped before the check."""
    from voice.engine import addressed

    assert addressed(heard) == command


def test_invit_correction():
    from voice.engine import clean_transcript

    assert clean_transcript("what is an invitee") == "what is an InvIT"


def test_typed_paper_trading_routes_to_command():
    """Typed 'do paper trading' used to reach the LLM, which asked for prices."""
    from voice import commands

    assert commands.route("if you have picked stocks work on it and do paper trading").action == "paper_trade"


# ---- brokers --------------------------------------------------------------------------

def test_no_live_broker_by_default():
    from brokers import registry

    adapter, why = registry.live_broker()
    assert adapter is None and why


def test_plugin_broker_loads_and_guards_orders(tmp_path, monkeypatch):
    import importlib

    from brokers import base
    from brokers.registry import registry

    reg_module = importlib.import_module("brokers.registry")  # the module, not the object

    template = open("brokers/plugins/_example_broker.py").read()
    (tmp_path / "testbroker.py").write_text(template)
    monkeypatch.setattr(reg_module, "PLUGIN_DIR", tmp_path)
    try:
        registry.reload()
        adapter = registry.get("example")
        assert adapter is not None and adapter.is_configured() is False
        with pytest.raises(base.BrokerError, match="Live orders are off"):
            adapter.place_order(base.OrderRequest(symbol="INFY", side="BUY", quantity=1))
    finally:
        monkeypatch.undo()
        registry.reload()


# ---- LLM plugins ------------------------------------------------------------------------

def test_llm_plugin_is_picked_by_name(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ai.providers import registry
    from ai.providers.openai_compat import build_cloud_provider

    (tmp_path / "echo.py").write_text(
        "from ai.providers.base import LLMProvider\n"
        "class Echo(LLMProvider):\n"
        "    name = 'echo'\n"
        "    def __init__(self, settings): self._model = settings.cloud_model\n"
        "    model = property(lambda self: self._model)\n"
        "    def is_available(self): return True\n"
        "    def complete(self, prompt, system=None, temperature=0.3, max_tokens=1200, json_mode=False):\n"
        "        return prompt\n"
    )
    (tmp_path / "broken.py").write_text("raise ImportError('nope')\n")   # must not sink the rest
    monkeypatch.setattr(registry, "PLUGIN_DIR", tmp_path)

    provider = build_cloud_provider(SimpleNamespace(cloud_provider="echo", cloud_model="m1"))
    assert provider.name == "echo" and provider.model == "m1" and provider.complete("hi") == "hi"


def test_shipped_llm_template_is_not_loaded():
    from ai.providers import registry

    assert "example" not in registry.plugin_providers()


# ---- fly brain and test lab -------------------------------------------------------------

def test_dopamine_learns_in_the_right_direction():
    from ml.flybrain.rl import DopamineAgent

    agent = DopamineAgent(dim=4, seed=0)
    phi = np.array([1.0, 0.5, -0.5, 0.2, 1.0])
    agent.learn(phi, 0.05)
    assert phi @ agent.w > 0          # a reward raises the value of that state
    before = phi @ agent.w
    agent.learn(phi, -0.05)
    assert phi @ agent.w < before     # a punishment lowers it


def test_csv_rejects_missing_columns():
    from ml.flybrain import lab

    with pytest.raises(ValueError, match="missing column"):
        lab.parse_csv(b"date,close\n2024-01-01,5\n")


def test_csv_single_stock_without_symbol_column():
    from ml.flybrain import lab

    dates = np.arange("2023-01-01", "2024-06-01", dtype="datetime64[D]")[:400]
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, len(dates)))
    rows = "\n".join(f"{d},{c:.2f},{c + 1:.2f},{c - 1:.2f},{c:.2f},1000" for d, c in zip(dates, close))
    prices, notes = lab.parse_csv(f"Date,Open,High,Low,Close,Volume\n{rows}\n".encode(), "INFY")
    assert prices["symbol"].unique().tolist() == ["INFY"] and len(prices) == 400
    assert any("no symbol column" in n for n in notes)


def test_monte_carlo_negative_edge_is_reported_honestly():
    from ml.flybrain import lab

    trades = list(np.random.default_rng(1).normal(-0.002, 0.03, 300))
    mc = lab.monte_carlo(trades, fraction=0.05)
    assert mc["ok"] and 0 <= mc["summary"]["prob_profit"] <= 100
    assert "Sizing cannot fix a negative edge" in mc["advice"]


# ---- API smoke (no lifespan: nothing starts in the background) ---------------------------

def test_api_agents_and_validation():
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    agents = client.get("/api/autopilot/agents").json()
    assert {a["id"] for a in agents} == {"watchtower", "momentum", "intraday", "swing", "daily-pick", "fly-rl"}
    bad = client.post("/api/autopilot/agents/daily-pick", json={"min_score": 500})
    assert bad.status_code == 400


# ---- intraday / swing / long-term -------------------------------------------------------

def test_new_agents_start_off(agent_store):
    assert agent_store.intraday_settings()["enabled"] is False
    assert agent_store.swing_settings()["enabled"] is False
    with pytest.raises(ValueError):
        agent_store.update("intraday", {"max_trades_per_day": 99})
    assert agent_store.momentum_settings()["enabled"] is False
    with pytest.raises(ValueError):
        agent_store.update("momentum", {"capital_pct": 500})


def test_intraday_costs_are_lower_than_delivery():
    from autopilot import costs

    delivery = costs.charges(1e5, "BUY") + costs.charges(1e5, "SELL")
    intraday = costs.charges(1e5, "BUY", "INTRADAY") + costs.charges(1e5, "SELL", "INTRADAY")
    assert intraday < delivery / 2   # ~0.08% vs ~0.22% in charges (slippage widens the gap further)


def test_expiry_compares_utc(paper_db):
    """Expiry used to compare a UTC timestamp with IST local time: brackets died 5.5 h early."""
    from datetime import datetime, timedelta, timezone

    paper_db.prices["INFY"] = 1000.0
    in_an_hour_utc = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert paper_db.triggers.arm("INFY", 1000, 950, 1100, source="test", max_position_pct=10,
                                 expires_at_utc=in_an_hour_utc)["ok"]
    assert paper_db.triggers.sweep()["closed"] == []


def test_intraday_stop_and_target():
    from pipeline import intraday

    stop, target = intraday.plan(1000.0, atr=1.0)        # tiny ATR → 0.4% floor
    assert stop == pytest.approx(996.0) and target == pytest.approx(1008.0)


def test_intraday_scan_finds_a_breakout():
    import pandas as pd

    from pipeline import intraday

    ts = pd.date_range("2026-10-01 09:15", periods=12, freq="5min")
    base = [100, 100.2, 100.1, 100.0, 100.1, 100.2, 100.1, 100.0, 100.1, 100.2, 100.1, 101.5]
    vol = [1000] * 11 + [5000]
    bars = pd.DataFrame({"symbol": "TEST", "ts": ts, "open": base, "high": [b + 0.3 for b in base],
                         "low": [b - 0.3 for b in base], "close": base, "volume": vol})
    setups = intraday.scan(bars)
    assert [s["symbol"] for s in setups] == ["TEST"] and setups[0]["surge"] >= 2


# ---- security ----------------------------------------------------------------------------

def test_other_websites_cannot_call_the_api():
    """CORS used to allow "*": any site open in the browser could read holdings or change keys via localhost."""
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    evil = client.get("/api/autopilot/agents", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    ui = client.get("/api/autopilot/agents", headers={"Origin": "http://localhost:5173"})
    assert ui.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_strategy_stuck_inside_one_bar_is_stopped():
    """The time budget used to be checked only between bars; a long loop inside on_bar hung the API."""
    import time as clock

    from strategies.sandbox import TimeBudgetExceeded, compile_strategy, time_limit

    strategy = compile_strategy("def on_bar(ctx):\n    n = 0\n    while n >= 0:\n        n += 1\n")
    started = clock.perf_counter()
    with pytest.raises(TimeBudgetExceeded):
        with time_limit(clock.perf_counter() + 0.5):
            strategy.on_bar(None)
    assert clock.perf_counter() - started < 3
