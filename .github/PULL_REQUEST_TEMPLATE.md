## What changed

## How I tested it

- [ ] `cd backend && ./venv/bin/python -m pytest -q` passes
- [ ] `cd frontend && npx vite build` succeeds
- [ ] No secrets, personal data or local paths added (`git status` checked)
- [ ] New trading agents start OFF and trade on paper only; nothing makes real-money trading easier
- [ ] Strategy PRs: backtest after costs, with a random-entry control
