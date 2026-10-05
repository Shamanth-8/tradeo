"""
Check every symbol in config/universe.json actually resolves on Yahoo Finance.

Ticker symbols drift — companies rename, ETFs get delisted, InvITs change
their trading symbol. Run this after editing the universe:

    cd backend && venv/bin/python -m market.validate_universe
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from market import data

from market.universe import UNIVERSE


def check(symbol: str) -> tuple[str, bool, str]:
    entry = UNIVERSE[symbol]
    suffix = ".BO" if entry.get("exchange") == "BSE" else ".NS"
    ticker = f"{symbol}{suffix}"
    try:
        hist = data.history(ticker, period="5d")
        if hist.empty:
            return symbol, False, "no price history"
        return symbol, True, f"₹{hist['Close'].iloc[-1]:,.2f}"
    except Exception as exc:
        return symbol, False, str(exc)[:80]


def main() -> int:
    symbols = sorted(UNIVERSE)
    print(f"Validating {len(symbols)} instruments against Yahoo Finance...\n")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, symbols))

    bad = [(s, msg) for s, ok, msg in results if not ok]
    for symbol, ok, msg in results:
        entry = UNIVERSE[symbol]
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {symbol:<14} {entry['asset_class']:<11} {msg}")

    print(f"\n{len(results) - len(bad)}/{len(results)} resolved.")
    if bad:
        print("\nFix or remove these in config/universe.json:")
        for symbol, msg in bad:
            print(f"  - {symbol}: {msg}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
