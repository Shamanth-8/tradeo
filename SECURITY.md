# Security

Tradeo is a paper-trading lab, but it handles broker credentials and contains
an unsupported real-order path that is off by default. Please report
vulnerabilities privately.

## Reporting

Use GitHub's **"Report a vulnerability"** button (Security → Advisories) on
this repository. Don't open a public issue for security problems. Include what
is affected, how to reproduce it, and the impact you expect. We aim to reply
within a week.

## In scope

- Secrets leaking: keys returned by the API, written to logs, or committed.
- Anything that could place a real-money order without all three switches
  (`LIVE_BROKER`, `<BROKER>_ALLOW_TRADING=true`, `AUTOPILOT_MODE=live`).
- Remote access: the backend binds to `127.0.0.1`; anything that exposes it.
- Broker or LLM plugin loading executing untrusted code without the user
  placing it in `backend/brokers/plugins/` or `backend/ai/providers/plugins/`
  themselves.

## For users

- Keys live only in `backend/.env` and `config/credentials.json` (mode 0600),
  both git-ignored. Never commit them, and check `git status` before pushing a
  fork.
- Leave every `*_ALLOW_TRADING` switch off. Tradeo is for paper trading only.
- Don't expose port 8000 to the internet; there is no authentication.
- Only install broker and LLM plugins you have read — they run as Python code.
