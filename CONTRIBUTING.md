# Contributing to Tradeo

Thanks for helping. Tradeo is a local-first **paper-trading lab** for Indian
investors: a place to test trading strategies with virtual money, not to trade
real money (see [DISCLAIMER.md](DISCLAIMER.md)). The most valuable
contributions are the ones that make it more **honest** and more **useful**:
strategies that survive a fair backtest, data sources, LLM and broker plugins,
and fixes.

## Ground rules

1. **Never commit secrets.** `backend/.env` and `config/credentials.json` are
   git-ignored; keep it that way. Run `git status` before every commit.
2. **Paper trading only.** Agents trade the paper account. PRs that make
   real-money trading easier, on by default, or part of a strategy's normal
   flow won't be merged. The existing unsupported order path must stay behind
   its three switches (`LIVE_BROKER`, `<BROKER>_ALLOW_TRADING`,
   `AUTOPILOT_MODE=live`), all off by default. New agents start **OFF**.
3. **Local first.** Features must work without a cloud key. A cloud model may
   only be optional (see `VERIFY_WITH_CLOUD`).
4. **Report results honestly.** A strategy PR includes its backtest after
   costs *and* a random-entry control with the same exits. Results that don't
   beat random are still welcome — say so on the agent's card.
5. **Numbers that decide money are computed in code**, never by a language
   model (stops, targets, sizes).
6. **Market data goes through `market/data.py`** — never call yfinance
   directly. One place for caching, retries and failure counts.
7. **Don't hide failures.** If you catch a broad exception to keep running,
   record it with `core.failures.record(...)` so it shows on the Connections
   screen. A silent `except Exception: pass` won't be merged.

## Setting up

```bash
git clone https://github.com/<you>/tradeo.git && cd tradeo
./scripts/setup.sh                      # backend, UI, local model, voice
cd backend && ./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest -q          # should pass before and after your change
cd ../frontend && npx vite build        # the UI must build
```

Run the app with `./scripts/start.sh` (UI at http://localhost:5173, API docs
at http://localhost:8000/docs).

## Good first contributions

- **A broker plugin** — copy `backend/brokers/plugins/_example_broker.py`
  (guide: `backend/brokers/plugins/README.md`). Upstox, Groww, Fyers, ICICI
  Direct and 5paisa are all missing. Read-only (holdings, prices) is enough.
- **An LLM plugin** — for an API that isn't OpenAI-compatible, copy
  `backend/ai/providers/plugins/_example_provider.py`
  (guide: `backend/ai/providers/plugins/README.md`). OpenAI-compatible APIs
  need no code — a docs PR adding a tested `CLOUD_BASE_URL` is welcome too.
- **A strategy** — see below.
- **A data source** — e.g. per-stock Google News for sentiment, NSE bulk/block
  deals, delivery %, FII/DII flows (guide: [`docs/ADD_AN_API.md`](docs/ADD_AN_API.md)).
- **Tests** for any bug you fix (`backend/tests/test_regressions.py`).
- **UI polish** — keep `prefers-reduced-motion` working.

## Adding a strategy (agent)

An agent is a small module in `backend/pipeline/` plus a switch:

1. **Rule + evaluation.** Write the rule as plain functions, then add it to
   `pipeline/evaluate.py`: a signal function for bracket strategies
   (`simulate_brackets`) or a chooser for rebalancing ones
   (`simulate_rebalance`), plus its no-skill control. That gives CAGR, Sharpe,
   drawdown and exposure against the Nifty, with real costs. **Choose
   parameters on the in-sample period (2020–24) only**; report the held-out
   period (2025+) once, after the rule is fixed. `pipeline/momentum.py` is a
   complete example.
2. **Live agent.** A `run_once()` that finds setups, calls
   `pipeline.pretrade.check()` and opens brackets with
   `autopilot.triggers.arm(..., source="<your-agent>", max_position_pct=...)`
   (sized by risk; account limits apply automatically), and a `tick()` that
   does nothing unless the switch is on. Register the tick in
   `autopilot/scheduler.py`.
3. **Switch.** Add defaults and a card in `backend/autopilot/agents.py`
   (`describe()` / `update()`) with your backtest text, and a label in
   `api/routes/wealth.py` `AGENTS`. The Autopilot page and the per-agent
   results on Paper Trading pick it up automatically.
4. **Tests.** At least: starts OFF, and the rule's core maths.

## Pull requests

- One topic per PR; describe what changed and how you tested it.
- `pytest -q` passes and `npx vite build` succeeds (CI runs both).
- Match the surrounding code style: explain *why* in comments, keep functions
  small, prefer plain Python over new dependencies.
- New dependencies go in `backend/requirements.txt` (or
  `requirements-brokers.txt` if only one broker needs it) and the README's
  *Libraries used* table.

## Reporting bugs and ideas

Open an issue with the bug or feature template. For security problems, see
[SECURITY.md](SECURITY.md) — please don't open a public issue.

By contributing you agree that your work is released under the project's MIT
licence and that you'll follow the [Code of Conduct](CODE_OF_CONDUCT.md).
