"""
Tradeo — unified multi-asset investing intelligence for Indian retail investors.
FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
import uvicorn

from core.config import DATA_DIR, settings

from api.routes import (
    stocks,
    portfolio,
    paper_trading,
    technicals,
    backtest,
    chat,
    alerts,
    assistant,
    signals,
    wealth,
    setup,
    discover,
    autopilot,
    strategies,
)
from api.routes import fundamental_analysis
from api.routes.novel import trade_clone, regret_analyzer, mood_ring, dna_matching
from api.routes.novel import future_you, margin_safety, exit_architect

# Aliased because these route modules share a name with the packages they
# drive (`lowlatency`, `pipeline`). The aliases keep it obvious at every use
# site which one is meant.
from api.routes import agents as agent_routes
from api.routes import lowlatency as lowlatency_routes
from api.routes import options as options_routes
from api.routes import bridge as bridge_routes
from api.routes import pipeline as pipeline_routes
from api.routes import learning as learning_routes
from api.routes import research as research_routes
from api.routes import flybrain as flybrain_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("tradeo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bring subsystems up before the first request, and shut them down cleanly."""
    from ai.brain import brain
    from core import credentials
    from data.storage.database import init_db
    from integrations.telegram import bot
    from realtime.engine import watchtower

    # Runtime credentials layer over the environment, so anything entered in
    # the UI survives a restart without ever touching .env.
    applied = credentials.apply_to_settings()
    if applied:
        log.info("applied %d stored credential(s)", len(applied))

    init_db()
    # Nothing heavy is preloaded: the local model loads on the first question
    # and unloads OLLAMA_KEEP_ALIVE after the last one; the voice models load
    # on the first mic click and unload after VOICE_IDLE_SECONDS idle.
    from voice import engine as voice_engine

    voice_engine.start_idle_unloader()

    # Telegram is both a sink (push) and a client (pull), if configured.
    if bot.enabled:
        watchtower.add_sink(bot.push_opportunity)
        if settings.telegram_polling:
            bot.start_polling()

    # ---- the low-latency stack ------------------------------------------
    # Order matters: the store subscribes to the bus, so it must be listening
    # before anything can publish. Each subsystem is guarded independently —
    # a missing optional dependency must degrade one feature, not the app.
    try:
        from lowlatency.bus import bus as event_bus
        from lowlatency.store import store

        if settings.wal_enabled:
            event_bus.enable_wal(str(DATA_DIR / "wal" / "events.jsonl"))
        store.start()
    except Exception as exc:
        log.warning("low-latency store unavailable: %s", exc)

    try:
        # Importing the library is what registers the agents — without this the
        # executor's registry is empty and /api/agents returns nothing.
        from agents import library as agent_library

        log.info("registered %d analysis agents", len(agent_library.ALL_AGENTS))
    except Exception as exc:
        log.warning("could not register agents: %s", exc)

    try:
        from autopilot import triggers

        # Watches open paper brackets and closes them at their stop or target.
        triggers.monitor.start(settings.trigger_monitor_seconds)
    except Exception as exc:
        log.warning("trigger monitor unavailable: %s", exc)

    # Nothing scans or trades in the background until you switch it on
    # (Autopilot page). Watchtower covers every scanning loop: the realtime
    # scanner, the pipeline that feeds the fly brain, the simulation and the
    # research candidate cache. They are the main CPU load.
    from autopilot import agents as automation

    if automation.switch("watchtower"):
        automation.apply_watchtower(True)
    else:
        log.info("watchtower off — no background scanning (switch it on in Autopilot)")

    # Idles until Watchtower is on; then keeps the research candidates fresh.
    bridge_routes.keep_warm()

    # The research engine (a second Python server, ~1 GB RAM) is not started
    # here: the Research page's Start button launches it when you need it.

    # The pick of the day: by rule, paper only, once per trading day.
    from pipeline import daily_pick

    daily_pick.start()

    # Intraday (every 5 min) and swing (09:25) agents: idle until switched on.
    from pipeline import intraday, swing

    intraday.start()
    swing.start()

    # The fly brain: learns from its closed paper trades, then trades on paper.
    from pipeline import fly_rl_trader

    fly_rl_trader.start()

    if settings.autopilot_mode == "live" or settings.live_broker:
        # Tradeo is a paper-trading lab; the order path is unsupported.
        log.warning(
            "UNSUPPORTED: real-money settings detected (AUTOPILOT_MODE=%s, LIVE_BROKER=%s). "
            "Tradeo is for paper trading only — see DISCLAIMER.md. You are responsible "
            "for any real order sent.",
            settings.autopilot_mode, settings.live_broker or "-",
        )

    log.info(
        "%s online — local=%s cloud=%s mode=%s telegram=%s",
        settings.assistant_name,
        settings.ollama_model,
        settings.cloud_model_name if settings.cloud_enabled else "not configured",
        settings.ai_mode,
        "on" if bot.enabled else "off",
    )

    yield

    # First: the engine is a separate process, and it is the one thing that
    # outlives Tradeo if a slow shutdown below gets Tradeo force-killed.
    from integrations.research_engine import engine as research_engine

    research_engine.stop()
    bot.stop_polling()
    watchtower.stop()
    try:
        from pipeline.watchtower import watchtower as pipeline_watchtower

        pipeline_watchtower.stop()
    except Exception:
        pass
    log.info("%s shutting down", settings.assistant_name)


app = FastAPI(
    title="Tradeo API",
    description=(
        "Unified multi-asset investing and market intelligence for Indian "
        "retail investors (NSE/BSE equities, ETFs, REITs, InvITs, bonds, G-Secs)."
    ),
    version="3.0.0",
    lifespan=lifespan,
)

# CORS for Electron frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "message": f"{settings.assistant_name} API is running",
        "version": app.version,
        "market": "India (NSE/BSE)",
    }


@app.get("/health")
async def health_check():
    """Liveness plus a quick read on which subsystems are actually up."""
    from ai.brain import brain
    from market.universe import summary

    from integrations.research_engine import engine as research_engine

    brain_status = brain.status()
    research = await run_in_threadpool(research_engine.status)
    return {
        "status": "healthy",
        "ai": {
            "online": brain_status["online"],
            "mode": brain_status["mode"],
            "local": brain_status["local"]["available"],
            "cloud": brain_status["cloud"]["available"],
        },
        "universe": summary(),
        "research": {"online": research["online"], "llm": research["llm"]},
    }


# ===== Core Routes =====
app.include_router(stocks.router, prefix="/api/stocks", tags=["Stocks"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(
    paper_trading.router, prefix="/api/paper-trading", tags=["Paper Trading"]
)
app.include_router(
    technicals.router, prefix="/api/technicals", tags=["Technical Analysis"]
)
app.include_router(backtest.router, prefix="/api/backtest", tags=["Backtesting"])
app.include_router(chat.router, prefix="/api/chat", tags=["AI Chatbot"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])

# ===== Intelligence Layer (local Ollama; optional cloud model) =====
app.include_router(assistant.router, prefix="/api/ai", tags=["AI Brain"])

# ===== Realtime watchtower (scanner, watchlist, Telegram) =====
app.include_router(signals.router, prefix="/api/signals", tags=["Realtime Signals"])

# ===== Unified wealth (brokers, depositories, consolidated analytics) =====
app.include_router(wealth.router, prefix="/api/wealth", tags=["Unified Wealth"])

# ===== Setup & connections (credentials, connection tests, voice) =====
app.include_router(setup.router, prefix="/api/setup", tags=["Setup"])

# ===== Discovery, education, risk profiling, suitability =====
app.include_router(discover.router, prefix="/api/discover", tags=["Discover & Learn"])

# ===== Autopilot (paper by default; live needs two switches) =====
app.include_router(autopilot.router, prefix="/api/autopilot", tags=["Autopilot"])

# ===== Strategy Studio (sandboxed custom code, walk-forward, sweeps) =====
app.include_router(
    strategies.router, prefix="/api/strategies", tags=["Strategy Studio"]
)

# ===== Enhanced Fundamentals =====
app.include_router(
    fundamental_analysis.router,
    prefix="/api/fundamentals",
    tags=["Fundamental Analysis"],
)

# ===== Novel AI Features =====
app.include_router(
    trade_clone.router, prefix="/api/novel/trade-clone", tags=["Trade Clone"]
)
app.include_router(
    regret_analyzer.router, prefix="/api/novel/regret", tags=["Regret Analyzer"]
)
app.include_router(
    mood_ring.router, prefix="/api/novel/mood", tags=["Market Mood Ring"]
)
app.include_router(
    dna_matching.router, prefix="/api/novel/dna", tags=["Stock DNA Matching"]
)
app.include_router(
    future_you.router, prefix="/api/novel/future", tags=["Future You Simulator"]
)
app.include_router(
    margin_safety.router, prefix="/api/novel/margin", tags=["Margin of Safety"]
)
app.include_router(
    exit_architect.router, prefix="/api/novel/exit", tags=["Exit Strategy Architect"]
)

# ---- the low-latency stack ------------------------------------------------
app.include_router(
    lowlatency_routes.router, prefix="/api/lowlatency", tags=["Low Latency"]
)
app.include_router(
    pipeline_routes.router, prefix="/api/pipeline", tags=["Watchtower Pipeline"]
)
app.include_router(
    learning_routes.router, prefix="/api/learning", tags=["Learning Feed"]
)
app.include_router(
    agent_routes.router, prefix="/api/agents", tags=["Analysis Agents"]
)
app.include_router(
    options_routes.router, prefix="/api/options", tags=["Options"]
)

# ---- the research engine sends its picks here (via backend/mcp_server.py) --
app.include_router(
    bridge_routes.router, prefix="/api/bridge", tags=["Research Bridge"]
)

# ---- the research engine itself: agent, backtest runs, factors -----------
app.include_router(
    research_routes.router, prefix="/api/research", tags=["Research Engine"]
)

# ---- the fly brain: RL stock suggestions and its own paper trader ---------
app.include_router(
    flybrain_routes.router, prefix="/api/flybrain", tags=["Fly Brain RL"]
)


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
