"""
Build the fly brain's training data from scratch (free, Yahoo Finance).

    venv/bin/python -m ml.flybrain.prepare            # ~10 years, all universe equities
    venv/bin/python -m ml.flybrain.history            # then replay + train the brain

Writes data/cache/flybrain/prices.parquet (daily OHLCV) and features.parquet
(the indicators the brain sees). Stocks with under MIN_ROWS days of history
are skipped: the indicators need about a year of warm-up.
"""

from __future__ import annotations

import json
import logging
import time

import pandas as pd

from core.config import PROJECT_ROOT

from .experiment import CACHE_DIR
from .features import build

log = logging.getLogger("tradeo.flybrain.prepare")

PERIOD = "10y"
MIN_ROWS = 320


def universe_equities() -> list[str]:
    instruments = json.loads((PROJECT_ROOT / "config" / "universe.json").read_text())["instruments"]
    return sorted(k for k, v in instruments.items() if v.get("asset_class") == "equity")


def download(symbols: list[str], period: str = PERIOD) -> pd.DataFrame:
    from market import data

    raw = data.download([s + ".NS" for s in symbols], period=period, interval="1d")
    by_ticker = data.split(raw, [s + ".NS" for s in symbols])
    frames = []
    for s in symbols:
        d = by_ticker.get(s + ".NS")
        if d is None:
            log.warning("no data for %s", s)
            continue
        if len(d) < MIN_ROWS:
            log.info("skipping %s: only %d days of history", s, len(d))
            continue
        d = d.rename(columns=str.lower).reset_index().rename(columns={"Date": "date"})
        d["symbol"] = s
        frames.append(d[["symbol", "date", "open", "high", "low", "close", "volume"]])
    if not frames:
        raise RuntimeError("Yahoo Finance returned no usable data — check the internet connection")
    return pd.concat(frames, ignore_index=True)


def main() -> dict:
    started = time.time()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    symbols = universe_equities()
    log.info("downloading %d stocks (%s daily) from Yahoo Finance…", len(symbols), PERIOD)
    prices = download(symbols)
    prices.to_parquet(CACHE_DIR / "prices.parquet")
    log.info("building features (a few minutes)…")
    features = build(prices)
    features.to_parquet(CACHE_DIR / "features.parquet")
    summary = {"symbols": int(prices["symbol"].nunique()), "rows": int(len(prices)),
               "from": str(pd.Timestamp(prices["date"].min()).date()),
               "to": str(pd.Timestamp(prices["date"].max()).date()),
               "seconds": round(time.time() - started, 1)}
    log.info("ready: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(main())
