"""
Research engine bridge.

The research engine picks; Tradeo is its Indian execution layer.
The research engine reaches these routes through `backend/mcp_server.py`, never
directly, and everything it can do here is paper.

A pick from the research engine goes through the same gates as one from the
watchtower: the entry is Tradeo's own live price, the stop comes from
`derive_stop`, and `arm_from_signal` applies the conviction bar and the
position caps. The research engine supplies the idea, not the numbers that decide
what a losing trade costs.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter()
log = logging.getLogger("tradeo.bridge")

def _score(symbol: str):
    from market.universe import UNIVERSE
    from realtime.scanner import _score_one

    # An unknown ticker is not a fast failure: the fetchers retry for minutes
    # before giving up. The research engine will pass US tickers, so refuse up front.
    symbol = symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")
    if symbol not in UNIVERSE:
        raise HTTPException(
            status_code=404,
            detail=f"{symbol} is not in Tradeo's Indian universe (config/universe.json)",
        )

    _, signal, error = _score_one(symbol)
    if not signal:
        raise HTTPException(status_code=404, detail=error or f"no data for {symbol}")
    return signal


# A full-universe scan takes minutes; an agent's tool call gets seconds. The
# ranking is kept warm here and served from memory, with its age attached so
# the caller can see how fresh it is.
CANDIDATES_TTL = 15 * 60
_ranking: dict = {}
_ranking_lock = threading.Lock()


def refresh_candidates() -> dict:
    """Scan the universe and rank it. Serialised: concurrent callers wait for one scan."""
    from ml.flybrain import ranker
    from realtime.scanner import scan

    with _ranking_lock:
        if _ranking and time.time() - _ranking["at"] < CANDIDATES_TTL:
            return _ranking

        result = scan(deep=False, trigger="vibe-trading")
        fly = ranker.status()
        rows: list[dict] = []

        if fly["active"]:
            try:
                scores = ranker.rank([s.symbol for s in result.signals])
                ranked = sorted((s for s in result.signals if s.symbol in scores),
                                key=lambda s: scores[s.symbol], reverse=True)
                rows = [{**s.as_dict(), "fly_score": scores[s.symbol]} for s in ranked]
            except Exception as exc:
                fly = {"active": False, "reason": f"fly ranker failed: {str(exc)[:160]}"}
        if not fly["active"]:
            # Every scored stock, not just the shortlist: a quiet day has an
            # empty shortlist, and "nothing cleared the gate" is for
            # the research engine to conclude from the scores, not for the bridge to hide.
            ranked = sorted(result.signals, key=lambda s: s.score, reverse=True)
            rows = [s.as_dict() for s in ranked]

        _ranking.clear()
        _ranking.update({
            "at": time.time(),
            "ranker": "flybrain" if fly["active"] else "tradeo-scorer",
            "ranker_status": fly,
            "scanned": len(result.signals),
            "rows": rows,
            "errors": result.errors[:10],
        })
        return _ranking


def keep_warm() -> None:
    """Background refresher, started with the app."""
    def loop() -> None:
        from autopilot import agents

        while True:
            if not agents.switch("watchtower"):
                time.sleep(60)  # idle until Watchtower is switched on
                continue
            try:
                refresh_candidates()
            except Exception as exc:
                log.warning("candidate refresh failed: %s", exc)
            time.sleep(CANDIDATES_TTL)

    threading.Thread(target=loop, name="bridge-candidates", daemon=True).start()


@router.get("/candidates")
def candidates(limit: int = Query(25, ge=1, le=200)):
    """
    Indian stocks ranked strongest first, and which model ranked them.

    The fly-connectome ranker orders the list only once its backtest gate has
    passed (ml/flybrain/experiment.py); until then Tradeo's scorer does, and
    `ranker_status.reason` says why.
    """
    ranking = refresh_candidates()
    return {
        "ranker": ranking["ranker"],
        "ranker_status": ranking["ranker_status"],
        "as_of_minutes_ago": round((time.time() - ranking["at"]) / 60, 1),
        "scanned": ranking["scanned"],
        "candidates": ranking["rows"][:limit],
        "errors": ranking["errors"],
    }


@router.get("/quote/{symbol}")
def quote(symbol: str):
    """Tradeo's score and snapshot (price, ATR, indicators) for one NSE symbol."""
    return _score(symbol).as_dict()


@router.get("/daily-pick")
def daily_pick_today():
    """Today's rule-based pick, if one has been made."""
    from pipeline import daily_pick

    from autopilot import agents

    picks = daily_pick._picked_today()
    rules = agents.daily_pick_settings()
    return {"picked": bool(picks), "pick": picks[-1] if picks else None, "picks": picks,
            "enabled": rules["enabled"], "rule": (
                f"up to {rules['picks_per_day']} highest-ranked bullish stock(s) scoring "
                f"{rules['min_score']:g}+ that pass every check, at {rules['run_at']} IST "
                f"({'on' if rules['enabled'] else 'switched off'})")}


@router.post("/daily-pick/run")
def daily_pick_run():
    """Run the rule now instead of waiting for the schedule (only when switched on)."""
    from pipeline import daily_pick

    return daily_pick.run()


@router.get("/brokers")
def brokers():
    """Which Indian brokers are connected. All trading here stays paper regardless."""
    from brokers import quotes
    from brokers.registry import registry

    rows = [registry.get(name).status() for name in quotes.PRICE_BROKERS]
    return {
        "brokers": rows,
        "price_source": (quotes.connected() or ["free-quote"])[0],
        "orders": "paper only — no bridge route reaches a broker's order API",
    }


@router.get("/positions")
def positions():
    """Open paper brackets and the paper account."""
    from autopilot import store, triggers

    return {"account": store.paper_account(), "open": triggers.active()}


@router.get("/performance")
def performance():
    """The paper record — what the research engine's picks have actually done."""
    from autopilot import triggers

    return triggers.performance()
