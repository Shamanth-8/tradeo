# Adding APIs, and using Tradeo's own API

> Tradeo is for **paper trading only** — testing strategies with virtual money.
> See [DISCLAIMER.md](../DISCLAIMER.md).

This page covers:

1. [Adding your API keys](#1-adding-your-api-keys) — services Tradeo already supports
2. [Adding a new data API](#2-adding-a-new-data-api) — prices, news, fundamentals, anything
3. [Adding a broker API](#3-adding-a-broker-api)
4. [Adding an LLM API](#4-adding-an-llm-api)
5. [Using Tradeo's REST API](#5-using-tradeos-rest-api) — from your own scripts
6. [Using Tradeo from an AI agent (MCP)](#6-using-tradeo-from-an-ai-agent-mcp)

---

## 1. Adding your API keys

Tradeo works with **no keys at all**. Keys only add optional features:

| Key | Adds | Get one |
|---|---|---|
| `OPENROUTER_API_KEY` | cloud model for the trade double-check and the research agent (`:free` models cost nothing) | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `OPENAI_API_KEY` | same, with OpenAI | [platform.openai.com](https://platform.openai.com) |
| `CLOUD_API_KEY` + `CLOUD_BASE_URL` + `CLOUD_MODEL` | same, with any OpenAI-compatible service | see [the LLM guide](../backend/ai/providers/plugins/README.md) |
| `FINNHUB_API_KEY` | IPO and earnings calendars | [finnhub.io](https://finnhub.io) (free) |
| `TELEGRAM_BOT_TOKEN` | phone alerts and chat | [@BotFather](https://t.me/BotFather) |
| Broker keys | your real holdings (read-only) | see the README's *Brokers* section |

Two ways to enter them:

- **In the app:** **Connections** screen. No restart needed. Saved to
  `config/credentials.json` (file mode 0600).
- **In a file:** `backend/.env` (copy `backend/.env.example`, which documents
  every option). Restart the backend after editing.

Both files are git-ignored. **Never commit them**, and run `git status`
before every push.

---

## 2. Adding a new data API

Say you want a new source — NSE bulk deals, Google News, FII/DII flows, a paid
data vendor. Three steps.

### a) Declare the key (if it needs one)

`backend/core/config.py`, in `Settings.__init__`:

```python
self.mydata_api_key: str | None = _env("MYDATA_API_KEY")
```

`backend/core/credentials.py`, in `FIELDS` — this makes it appear on the
Connections screen:

```python
"MYDATA_API_KEY": {"attr": "mydata_api_key", "group": "data", "secret": True,
                   "label": "MyData API key (bulk deals)"},
```

And add an empty `MYDATA_API_KEY=` with a comment to `backend/.env.example`.

### b) Write the fetcher

Put it in `backend/data/fetchers/`. Follow the pattern of
`_finnhub()` in `backend/learning/feed.py`:

```python
import logging
import requests

from core.config import get_settings
from data.cache import cached

log = logging.getLogger("tradeo.data")


# Cache for 10 min so a scan of 50 stocks doesn't repeat calls; don't cache failures.
@cached(ttl=600, prefix="mydata.bulk_deals", skip_if=lambda r: not r)
def bulk_deals(symbol: str) -> list[dict]:
    settings = get_settings()
    if not settings.mydata_api_key:   # no key → feature quietly off
        return []
    try:
        resp = requests.get(
            "https://api.mydata.example/v1/bulk-deals",
            params={"symbol": symbol},
            headers={"Authorization": f"Bearer {settings.mydata_api_key}"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("deals", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("mydata bulk deals for %s failed: %s", symbol, exc)
        return []                     # a dead source degrades, never crashes
```

Rules every fetcher follows:

- **Optional.** Without a key, return an empty result. Tradeo must keep working.
- **Never raise into the caller.** Log a warning and return empty.
- **Cache.** Use `data.cache.cached` so a scan of 50 stocks doesn't make 50 identical calls.
- **Timeouts** on every request.
- **Never log the key.** Log key *names* only.
- **Respect the source's terms.** Many free sources allow personal use only.

### c) Use it

Call it where it helps: a strategy rule in `backend/pipeline/`, sentiment in
`backend/ai/sentiment.py` (`analyze_symbol()`), or a new route in
`backend/api/routes/`. To show it in the UI, add the route to
`frontend/src/services/api.js` and render it on a page.

If it feeds a strategy, include a backtest showing it helps after costs
(see [CONTRIBUTING.md](../CONTRIBUTING.md)).

---

## 3. Adding a broker API

One Python file, no other changes. Guide:
[`backend/brokers/plugins/README.md`](../backend/brokers/plugins/README.md).
Brokers are used to **read** holdings and prices; Tradeo's agents trade on paper.

---

## 4. Adding an LLM API

Any Ollama model is one line; any OpenAI-compatible API is three keys and no
code; anything else is one plugin file. Guide:
[`backend/ai/providers/plugins/README.md`](../backend/ai/providers/plugins/README.md).

---

## 5. Using Tradeo's REST API

When the backend is running, Tradeo is a normal HTTP API on
`http://127.0.0.1:8000`. **Interactive docs for every endpoint:**
[http://localhost:8000/docs](http://localhost:8000/docs).

Some useful ones:

```bash
# Market data and analysis
curl -s localhost:8000/api/stocks/INFY/price
curl -s "localhost:8000/api/stocks/INFY/historical?period=1y"
curl -s localhost:8000/api/stocks/INFY/technicals
curl -s localhost:8000/api/ai/sentiment/INFY

# The manual paper account
curl -s localhost:8000/api/paper-trading/account
curl -s -X POST localhost:8000/api/paper-trading/buy \
     -H 'Content-Type: application/json' \
     -d '{"symbol": "INFY", "quantity": 5}'

# The agents' automated paper account
curl -s localhost:8000/api/autopilot/agents          # switches, rules, backtests
curl -s localhost:8000/api/autopilot/account
curl -s localhost:8000/api/autopilot/trades

# Health
curl -s localhost:8000/api/ai/status                  # which LLMs are online
curl -s localhost:8000/api/setup/diagnostics          # every connection
curl -s localhost:8000/api/autopilot/safety           # shows that orders stay on paper, and why
```

From Python:

```python
import requests

API = "http://127.0.0.1:8000/api"
print(requests.get(f"{API}/stocks/TCS/price").json())
print(requests.get(f"{API}/autopilot/trades").json())
```

The API has **no authentication** and listens on `127.0.0.1` only. Don't
expose it to a network.

---

## 6. Using Tradeo from an AI agent (MCP)

`backend/mcp_server.py` exposes Tradeo as read-only [MCP](https://modelcontextprotocol.io)
tools (`tradeo_candidates`, `tradeo_quote`, `tradeo_stock_analysis`,
`tradeo_news`, `tradeo_positions`, …) for any MCP client — Claude Desktop,
Claude Code, Cursor, or the bundled research engine. None of them can place a
trade.

Add it to your client's MCP config (example in
`config/research-engine-mcp.json`):

```json
{
  "mcpServers": {
    "tradeo": {
      "command": "/path/to/tradeo/backend/venv/bin/python",
      "args": ["/path/to/tradeo/backend/mcp_server.py"],
      "env": { "TRADEO_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

The Tradeo backend must be running (`./scripts/start.sh`).
