# Disclaimer: paper trading only

**Tradeo is for paper trading: testing trading strategies with virtual money.
It is not built, tested or supported for trading real money in real markets.**

## What Tradeo is for

- Testing a strategy idea on a **virtual ₹10 lakh account** with realistic
  Indian charges and slippage, before you decide anything.
- Backtesting rules on history and comparing them against random picks.
- Learning how markets, instruments and strategies behave, with no money at
  risk.

## What Tradeo is not

- **Not a live trading system.** Its signals, agents and AI are experiments.
  None of the built-in strategies has shown a proven edge after costs (see
  the backtest table in the README). Most lose money or match random picks.
- **Not investment advice.** Nothing it says or does is a recommendation to
  buy or sell any security. The authors and contributors are not registered
  with SEBI as investment advisers or research analysts.
- **Not reliable enough for money.** Prices come from free, delayed and
  sometimes wrong public sources (Yahoo Finance, RSS, Screener.in). Language
  models make things up. Code has bugs. The broker connectors are mostly
  untested with real accounts.

## About broker connections

Connecting a broker is supported only to **read** your holdings and live
prices. The code contains an order path behind three switches
(`LIVE_BROKER`, `<BROKER>_ALLOW_TRADING`, `AUTOPILOT_MODE=live`), all off by
default. It is **unsupported**: it is not part of what this project offers,
it has not been verified, and bug reports about real-money losses will not be
treated as the project's responsibility. If you turn it on, you alone are
responsible for every order it sends.

## Past results

Backtests and paper results are hypothetical. They don't include every real
cost or market condition (liquidity, gaps, rejected orders, outages, your own
behaviour), use today's large-cap stocks (survivorship bias), and do not
predict future returns.

## No warranty

Tradeo is provided "as is" under the [MIT License](LICENSE), without warranty
of any kind. The authors and contributors are not liable for any loss arising
from its use.

Trading in securities involves risk of loss. Before investing real money,
talk to a SEBI-registered investment adviser.
