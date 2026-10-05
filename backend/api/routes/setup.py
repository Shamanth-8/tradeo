"""
Setup routes — connect everything from the UI instead of editing .env.

Covers the integrations that take credentials: brokers, the optional cloud
model (OpenRouter / OpenAI / any OpenAI-compatible endpoint), and Telegram. Each has a matching test
endpoint so a connection can be verified the moment it's entered.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import credentials
from core.config import settings

router = APIRouter()


class CredentialUpdate(BaseModel):
    # Values are write-only: this API never returns a secret it was given.
    values: dict[str, Any] = Field(..., description="Field key -> value. Empty string clears.")


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Every configurable field and whether it's set. Never returns secrets."""
    return {
        **credentials.describe(),
        "runtime": settings.as_dict(),
    }


@router.post("/config")
async def set_config(update: CredentialUpdate) -> dict[str, Any]:
    """Save credentials and apply them live — no restart needed."""
    try:
        result = credentials.save(update.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**result, "runtime": settings.as_dict()}


# ---- Connection tests ------------------------------------------------------


@router.post("/test/broker/{broker}")
async def test_broker(broker: str) -> dict[str, Any]:
    """Attempt a real login and read back the account profile."""
    from brokers import registry

    adapter = registry.get(broker)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Unknown broker '{broker}'")

    if not adapter.is_configured():
        return {
            "broker": broker,
            "connected": False,
            "error": "Credentials incomplete",
            "required": ["API key", "client code", "MPIN", "TOTP secret"]
            if broker == "angelone"
            else [],
        }

    try:
        if not adapter.connect():
            return {
                "broker": broker,
                "connected": False,
                "error": "Login rejected. Check the client code, MPIN and TOTP secret.",
            }
        profile = adapter.profile()
        holdings = adapter.holdings()
        funds = adapter.funds()
        return {
            "broker": broker,
            "connected": True,
            "profile": profile,
            "holdings_found": len(holdings),
            "funds_available": funds.available,
            "can_trade": adapter.can_trade,
        }
    except Exception as exc:
        return {"broker": broker, "connected": False, "error": str(exc)}


@router.post("/test/ai")
async def test_ai() -> dict[str, Any]:
    """Check both the local model and the cloud reasoning model respond."""
    from ai.brain import brain
    from ai.providers import ProviderError

    results: dict[str, Any] = {"local": {}, "cloud": {}}

    for tier, provider in (("local", brain.local), ("cloud", brain.cloud)):
        if not provider.is_available():
            results[tier] = {
                "available": False,
                "model": provider.model,
                "error": "not configured or unreachable",
            }
            continue
        try:
            response = provider.complete(
                "Reply with exactly: ok", temperature=0, max_tokens=10
            )
            results[tier] = {
                "available": True,
                "model": provider.model,
                "reply": response[:60],
            }
        except ProviderError as exc:
            results[tier] = {"available": False, "model": provider.model, "error": str(exc)}

    return {"mode": brain.mode, **results}


@router.post("/test/telegram")
async def test_telegram() -> dict[str, Any]:
    from integrations.telegram import bot

    return bot.verify()


@router.get("/voice")
async def voice_status() -> dict[str, Any]:
    """Which speech path the HUD should use."""
    from voice.engine import status

    return status()


@router.get("/checklist")
async def checklist() -> dict[str, Any]:
    """
    What's working and what still needs attention.

    Ordered so the highest-value missing piece is first — the HUD shows this
    on the setup screen.
    """
    from ai.brain import brain
    from brokers import registry
    from integrations.telegram import bot
    from voice.engine import status as voice_state

    brain_state = brain.status()
    broker_state = registry.status()
    connected_brokers = [
        b for b in broker_state["brokers"] if b["configured"] and b["broker"] != "manual"
    ]
    voice = voice_state()

    items = [
        {
            "id": "local_brain",
            "label": "Local AI model",
            "status": "ok" if brain_state["local"]["available"] else "missing",
            "detail": brain_state["local"]["model"],
            "hint": "Install Ollama and pull a model" if not brain_state["local"]["available"] else None,
        },
        {
            "id": "cloud_brain",
            "label": "Cloud model (optional)",
            "status": "ok" if brain_state["cloud"]["available"] and settings.ai_mode != "local_only"
            else "optional",
            "detail": "off — fully local" if settings.ai_mode == "local_only"
            else brain_state["cloud"]["model"],
            "hint": None if settings.ai_mode == "local_only" or brain_state["cloud"]["available"]
            else "Add OPENROUTER_API_KEY, or set AI_MODE=local_only",
        },
        {
            "id": "broker",
            "label": "Broker connection",
            "status": "ok" if connected_brokers else "optional",
            "detail": ", ".join(b["display_name"] for b in connected_brokers) or "none — paper trading",
            "hint": "Optional: connect Zerodha, Dhan, Angel One or Kotak Neo, or import a CDSL/NSDL statement"
            if not connected_brokers else None,
        },
        {
            "id": "telegram",
            "label": "Telegram alerts",
            "status": "ok" if bot.enabled else "optional",
            "detail": "configured" if bot.enabled else "not set",
            "hint": "Create a bot with @BotFather for phone alerts" if not bot.enabled else None,
        },
        {
            "id": "voice",
            "label": "Voice",
            "status": "ok",
            "detail": f"{voice['stt']['active']} recognition, {voice['tts']['active']} speech",
            "hint": voice["install_hint"],
        },
    ]

    ready = sum(1 for i in items if i["status"] == "ok")
    return {
        "items": items,
        "ready": ready,
        "total": len(items),
        "blocking": [i for i in items if i["status"] == "missing"],
    }


@router.get("/failures")
async def failure_counts() -> dict[str, Any]:
    """
    Every part that failed since the backend started: data sources, news,
    the cloud verifier, each agent's scheduled run. A part that recovered
    shows as "degraded"; one whose latest attempt failed as "failing".
    """
    from core import failures

    counts = failures.snapshot()
    return {"components": counts,
            "failing": [name for name, c in counts.items() if c["state"] == "failing"]}


@router.get("/diagnostics")
async def diagnostics() -> dict[str, Any]:
    """
    What is actually connected, and what to do about what isn't.

    The distinction that matters — and that a boolean "configured" flag cannot
    express — is between three states:

      missing   no credential at all
      blocked   credential is valid, but something outside the app stops it
                working (no credits, no subscription, nobody has messaged the
                bot yet). Nothing in Tradeo can fix these.
      live      working right now

    Reporting "not connected" for all three is what makes a user re-enter a key
    that was never the problem.
    """
    from concurrent.futures import ThreadPoolExecutor

    from core.config import get_settings

    settings = get_settings()

    def cloud_reasoning() -> dict[str, Any]:
        """The optional cloud model. Probed for real: a key string alone proves nothing."""
        from ai.brain import brain

        if settings.ai_mode == "local_only" and not (settings.verify_with_cloud and settings.cloud_enabled):
            return {"state": "off", "detail": "AI_MODE=local_only — every answer comes from the local model"}
        if settings.ai_mode == "local_only":
            models = brain.cloud.list_models()
            return {"state": "live" if models else "blocked",
                    "detail": f"verifier only — {settings.cloud_provider} · {settings.cloud_model_name} "
                              "double-checks trades; every answer still comes from the local model",
                    "action": None if models else f"Check the {settings.cloud_provider} key"}
        if not settings.cloud_enabled:
            return {"state": "missing", "detail": f"No {settings.cloud_provider} key",
                    "action": "Add OPENROUTER_API_KEY (free at openrouter.ai), or set AI_MODE=local_only"}
        models = brain.cloud.list_models()
        return {"state": "live" if models else "blocked",
                "detail": f"{settings.cloud_provider} · {settings.cloud_model_name}",
                "action": None if models else f"Check the {settings.cloud_provider} key"}

    def telegram() -> dict[str, Any]:
        from integrations.telegram import bot

        result = bot.verify()
        if not result.get("configured"):
            return {"state": "missing", "detail": "No bot token",
                    "action": "Create a bot with @BotFather"}
        if not result.get("valid"):
            return {"state": "blocked", "detail": str(result.get("error"))[:180],
                    "action": "Check the token"}
        subscribers = result.get("subscribers") or 0
        if subscribers or settings.telegram_chat_id:
            return {"state": "live",
                    "detail": f"@{result.get('username')} · {subscribers} subscriber(s)"}
        # A bot cannot open a conversation on Telegram. This is the platform's
        # rule, not a misconfiguration, and saying so saves a lot of confusion.
        return {
            "state": "blocked",
            "detail": f"@{result.get('username')} is live but has never been messaged",
            "action": f"Send any message to @{result.get('username')} — a bot "
                      "cannot start the conversation, so it has no chat to reply to",
        }

    def brokers() -> dict[str, Any]:
        """Live brokers (optional — paper trading needs none)."""
        from brokers import registry

        live = [a for a in registry.all if a.name not in ("manual", "depository") and a.is_configured()]
        if not live:
            return {"state": "off", "detail": "No broker connected — paper trading on Yahoo Finance prices",
                    "action": "Optional: connect one in the Brokers section below"}
        states = [a.status() for a in live]
        up = [s["display_name"] for s in states if s.get("connected")]
        down = [f"{s['display_name']}: {s.get('error') or 'login failed'}" for s in states
                if not s.get("connected")]
        if up and not down:
            return {"state": "live", "detail": ", ".join(up)}
        return {"state": "blocked", "detail": "; ".join(down)[:180],
                "action": "Re-check the credentials on this screen (Zerodha: log in again daily)"}

    def finnhub() -> dict[str, Any]:
        if not settings.finnhub_api_key:
            return {"state": "missing", "detail": "No Finnhub key",
                    "action": "Free key at finnhub.io"}
        from learning import feed

        rows = feed.ipo_calendar()
        return {"state": "live", "detail": f"IPO and earnings calendars · {len(rows)} upcoming"}

    def local_model() -> dict[str, Any]:
        from ai.brain import brain

        status = brain.status()
        return ({"state": "live", "detail": settings.ollama_model} if status.get("local")
                else {"state": "blocked", "detail": "Ollama not reachable",
                      "action": "Start Ollama, then pull the model"})

    def holdings() -> dict[str, Any]:
        from brokers.registry import registry

        active = [a.display_name for a in registry.active]
        if not active:
            return {"state": "missing", "detail": "No holdings source",
                    "action": "Import a CDSL/NSDL statement, or add holdings manually"}
        total = registry.consolidated_holdings()["totals"]
        if not total["instruments"]:
            return {"state": "off", "detail": "No real holdings (no broker, statement or manual entries)",
                    "action": "Connect a broker or import a CDSL/NSDL statement on the Wealth page"}
        return {"state": "live",
                "detail": f"{', '.join(active)} · {total['instruments']} instruments"}

    def voice() -> dict[str, Any]:
        from voice.engine import status as voice_status

        # `stt` and `tts` are nested objects, not strings — reading them as
        # strings stringified the whole dict into the UI.
        info = voice_status()
        stt = (info.get("stt") or {}).get("active", "browser")
        tts = (info.get("tts") or {}).get("active", "browser")
        return {"state": "live",
                "detail": f"{stt.replace('_', ' ')} recognition, "
                          f"{tts.replace('_', ' ')} speech"}

    checks = {
        "local_model": ("Local model", local_model),
        "reasoning": ("Cloud model (optional)", cloud_reasoning),
        "telegram": ("Telegram alerts", telegram),
        "brokers": ("Brokers (optional)", brokers),
        "holdings": ("Holdings", holdings),
        "finnhub": ("Learning feed", finnhub),
        "voice": ("Voice", voice),
    }

    # In parallel: several of these make network calls, and running them
    # serially makes the connections screen feel broken.
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {key: pool.submit(fn) for key, (_label, fn) in checks.items()}
        for key, future in futures.items():
            label = checks[key][0]
            try:
                results[key] = {"label": label, **future.result(timeout=30)}
            except Exception as exc:
                results[key] = {"label": label, "state": "blocked",
                                "detail": str(exc)[:180], "action": None}

    states = [r["state"] for r in results.values()]
    return {
        "checks": results,
        "summary": {
            "live": states.count("live"),
            "blocked": states.count("blocked"),
            "missing": states.count("missing"),
            "total": len(states),
        },
    }


# ---- broker login redirects (Zerodha, and any plugin with a login flow) -----


@router.get("/brokers/{broker}/login")
async def broker_login(broker: str):
    """Send the browser to the broker's login page (daily for Zerodha)."""
    from fastapi.responses import RedirectResponse

    from brokers import registry

    adapter = registry.get(broker)
    if adapter is None or not hasattr(adapter, "login_url"):
        raise HTTPException(status_code=404, detail=f"{broker} has no browser login flow")
    try:
        return RedirectResponse(adapter.login_url())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/brokers/{broker}/callback")
async def broker_callback(broker: str, request_token: str | None = None, status: str | None = None):
    """Where the broker redirects after login. Stores the session token."""
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import HTMLResponse

    from brokers import registry

    adapter = registry.get(broker)
    if adapter is None or not hasattr(adapter, "complete_login"):
        raise HTTPException(status_code=404, detail=f"{broker} has no browser login flow")
    if not request_token or (status and status != "success"):
        raise HTTPException(status_code=400, detail=f"login was not completed (status={status})")
    try:
        await run_in_threadpool(adapter.complete_login, request_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"login failed: {exc}") from exc
    return HTMLResponse(f"<h3>{adapter.display_name} connected for today.</h3>"
                        "<p>You can close this tab and return to Tradeo.</p>")
