"""
The money path: charges, sizing, fills, expiry and account-level risk.

Everything runs on a throwaway paper database (the `paper_db` fixture), with
prices set by the test — no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ---- charges -------------------------------------------------------------------------

def test_delivery_round_trip_costs_about_042_percent():
    from autopilot import costs

    notional = 100_000.0
    buy = costs.fill_price(1000, "BUY")
    sell = costs.fill_price(1000, "SELL")
    slippage = (buy - sell) / 1000 * notional
    total = slippage + costs.charges(notional, "BUY") + costs.charges(notional, "SELL")
    assert total / notional * 100 == pytest.approx(0.42, abs=0.02)


def test_intraday_brokerage_is_capped_at_20_rupees_an_order():
    from autopilot import costs

    big = costs.charges(10_000_000, "BUY", "INTRADAY")
    # ₹1 crore: 0.03% would be ₹3,000; the cap keeps brokerage at ₹20.
    assert big < 10_000_000 * 0.0003


def test_intraday_round_trip_is_cheaper_than_delivery():
    from autopilot import costs

    def round_trip(product):
        return costs.charges(100_000, "BUY", product) + costs.charges(100_000, "SELL", product)

    assert round_trip("INTRADAY") < round_trip("DELIVERY") / 2


def test_slippage_scales_with_order_size_against_volume():
    from autopilot import costs

    small = costs.fill_price(1000, "BUY", order_value=1e5, daily_value=1e9)
    large = costs.fill_price(1000, "BUY", order_value=1e8, daily_value=1e9)
    assert 1000 < small < large


# ---- sizing --------------------------------------------------------------------------

def test_size_by_risk_loses_the_set_share_at_the_stop(paper_db):
    # ₹10L equity, 0.25% risk = ₹2,500; stop ₹50 away → 50 shares (₹50,000, under the 10% cap).
    assert paper_db.risk.size(1_000_000, 1000, 950, max_position_pct=10) == 50


def test_size_is_capped_by_max_position(paper_db):
    # A 0.5% stop would allow ₹5L by risk; the 10% cap holds it to ₹1L.
    assert paper_db.risk.size(1_000_000, 1000, 995, max_position_pct=10) == 100


def test_wider_stop_means_fewer_shares(paper_db):
    tight = paper_db.risk.size(1_000_000, 1000, 960, max_position_pct=100)
    wide = paper_db.risk.size(1_000_000, 1000, 900, max_position_pct=100)
    assert wide < tight


# ---- fills and exits --------------------------------------------------------------------

def test_gap_through_the_stop_fills_at_the_real_price():
    from autopilot.triggers import check_one

    out = check_one({"stop_loss": 950, "target": 1100}, price=900)
    assert out["reason"] == "stopped_out" and out["exit_price"] == 900 and out["gapped"]


def test_stop_is_checked_before_target():
    from autopilot.triggers import check_one

    # A degenerate bracket where both levels are crossed: assume the loss.
    out = check_one({"stop_loss": 1000, "target": 990}, price=995)
    assert out["reason"] == "stopped_out"


def test_end_to_end_paper_trade(paper_db):
    paper_db.prices["INFY"] = 1000.0
    opened = paper_db.triggers.arm("INFY", 1000, 950, 1100, source="test", max_position_pct=10)
    assert opened["ok"], opened
    assert opened["quantity"] == 50

    paper_db.prices["INFY"] = 1105.0
    swept = paper_db.triggers.sweep()
    assert [c["reason"] for c in swept["closed"]] == ["target_hit"]
    pnl = swept["closed"][0]["realised_pnl"]
    # 50 × ~₹100 gross, minus slippage and charges on both legs.
    assert 4_000 < pnl < 5_250

    account = paper_db.store.paper_account()
    assert account["positions"] == []
    assert account["cash"] == pytest.approx(1_000_000 + pnl, abs=1)


def test_expired_bracket_is_closed_at_market(paper_db):
    paper_db.prices["TCS"] = 3000.0
    past_utc = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    opened = paper_db.triggers.arm("TCS", 3000, 2900, 3200, source="test",
                                   max_position_pct=10, expires_at_utc=past_utc)
    assert opened["ok"], opened
    swept = paper_db.triggers.sweep()
    assert [c["reason"] for c in swept["closed"]] == ["expired"]


def test_intraday_square_off_is_1515_ist_in_utc():
    from pipeline import intraday

    stamp = datetime.strptime(intraday._expires_utc(), "%Y-%m-%d %H:%M:%S")
    assert (stamp.hour, stamp.minute) == (9, 45)   # 15:15 IST = 09:45 UTC


# ---- account-level risk --------------------------------------------------------------------

def test_daily_loss_limit_blocks_new_buys(paper_db):
    paper_db.risk.mark(1_000_000)            # today's open
    account = {"equity": 975_000, "positions": []}   # −2.5% today, limit 2%
    ok, why = paper_db.risk.check_entry("INFY", 10_000, account)
    assert not ok and "daily loss limit" in why


def test_drawdown_switch_latches_until_resumed(paper_db):
    from datetime import date

    from data.storage.database import get_db_connection

    # A peak on an earlier day, so today's opening mark doesn't count as a daily loss.
    conn = get_db_connection()
    conn.execute("INSERT INTO autopilot_equity_marks VALUES (?, ?, ?, ?)",
                 ((date.today() - timedelta(days=30)).isoformat(), 1_200_000, 1_200_000, 1_200_000))
    conn.commit()
    conn.close()

    ok, why = paper_db.risk.check_entry("INFY", 10_000, {"equity": 1_000_000, "positions": []})
    assert not ok and "halted" in why                     # −16.7% from the peak, limit 10%
    # Still halted even if equity recovers: it latches.
    ok, _ = paper_db.risk.check_entry("INFY", 10_000, {"equity": 1_190_000, "positions": []})
    assert not ok

    paper_db.risk.resume()
    ok, why = paper_db.risk.check_entry("INFY", 10_000, paper_db.store.paper_account())
    assert ok, why


def test_sector_cap(paper_db):
    # HDFCBANK and ICICIBANK are both Banking in config/universe.json.
    account = {"equity": 1_000_000, "positions": [{"symbol": "HDFCBANK", "value": 200_000}]}
    ok, why = paper_db.risk.check_entry("ICICIBANK", 100_000, account)   # 30% > 25%
    assert not ok and "sector cap" in why
    ok, _ = paper_db.risk.check_entry("TCS", 100_000, account)           # IT: fine
    assert ok


def test_market_filter_pauses_buys_in_a_downtrend(paper_db):
    paper_db.trend.update(ok=False, detail="Nifty 50 below its 200-day average")
    ok, why = paper_db.risk.check_entry("INFY", 10_000, {"equity": 1_000_000, "positions": []})
    assert not ok and "market filter" in why


def test_market_filter_fails_safe_without_data(paper_db):
    paper_db.trend.update(ok=None, detail="Nifty 50 history unavailable")
    ok, why = paper_db.risk.check_entry("INFY", 10_000, {"equity": 1_000_000, "positions": []})
    assert not ok and "not buying blind" in why


def test_market_filter_can_be_switched_off(paper_db):
    paper_db.risk.update({"market_filter": False})
    paper_db.trend.update(ok=False, detail="down")
    ok, _ = paper_db.risk.check_entry("INFY", 10_000, {"equity": 1_000_000, "positions": []})
    assert ok


def test_risk_limits_are_validated(paper_db):
    with pytest.raises(ValueError):
        paper_db.risk.update({"risk_per_trade_pct": 5})


# ---- momentum agent ---------------------------------------------------------------------

def test_momentum_holds_cash_in_a_downtrend(paper_db, monkeypatch):
    from pipeline import momentum

    paper_db.risk.update({"market_filter": False})          # let the buy through first
    paper_db.prices["TCS"] = 3000.0
    assert paper_db.triggers.arm("TCS", 3000, 2250, 9000, quantity=10, source="momentum")["ok"]

    paper_db.agents.update("momentum", {"enabled": True})
    paper_db.trend.update(ok=False, detail="Nifty 50 below its 200-day average")
    monkeypatch.setattr(momentum, "ranking", lambda prices=None: [{"symbol": "TCS", "momentum_pct": 20, "close": 3000}])
    out = momentum.rebalance()
    assert out["target"] == [] and [s["symbol"] for s in out["sold"]] == ["TCS"]


def test_momentum_keeps_names_that_stay_and_buys_new_ones(paper_db, monkeypatch):
    from pipeline import momentum

    paper_db.agents.update("momentum", {"enabled": True, "top": 5})
    paper_db.prices.update({"TCS": 3000.0, "INFY": 1500.0})
    monkeypatch.setattr(momentum, "ranking", lambda prices=None: [
        {"symbol": "TCS", "momentum_pct": 20, "close": 3000}, {"symbol": "INFY", "momentum_pct": 10, "close": 1500}])
    first = momentum.rebalance()
    assert sorted(b["symbol"] for b in first["bought"]) == ["INFY", "TCS"]
    second = momentum.rebalance()
    assert sorted(second["kept"]) == ["INFY", "TCS"] and not second["bought"] and not second["sold"]


# ---- scheduler and evaluation ---------------------------------------------------------------

def test_a_failing_agent_tick_is_counted_not_raised():
    from autopilot import scheduler
    from core import failures

    def boom():
        raise RuntimeError("data source down")

    scheduler._wrap("test-agent", boom)()          # must not raise
    assert failures.snapshot()["agent.test-agent"]["state"] == "failing"


def test_evaluation_metrics_on_a_known_curve():
    import pandas as pd

    from pipeline import evaluate

    days = pd.bdate_range("2021-01-01", periods=505)
    # Doubles over two years, with one 20% drop on the way.
    values = [100 * 2 ** (i / 504) for i in range(505)]
    values[300:320] = [v * 0.8 for v in values[300:320]]
    m = evaluate.metrics(pd.Series(values, index=days))
    assert m["total_return_pct"] == pytest.approx(100, abs=0.5)
    years = (days[-1] - days[0]).days / 365.25
    assert m["cagr_pct"] == pytest.approx((2 ** (1 / years) - 1) * 100, abs=0.1)
    assert m["max_drawdown_pct"] == pytest.approx(-20, abs=0.5)
