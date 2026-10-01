"""
The fly brain as a live ranker — only if it earned it.

`active()` is False unless `experiment.py` has run and every check in its
report passed. While it is False, the bridge ranks with Tradeo's scorer and
says so, so Vibe-Trading always knows which model chose the order.
"""

from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

from . import connectome, reservoir
from .experiment import MODEL, REPORT
from .features import build

log = logging.getLogger("tradeo.flybrain")

# Two years: one to warm up the 200-day average and 52-week range, one for
# the reservoir to settle — its memory fades within weeks at LEAK 0.3.
HISTORY = "2y"
CACHE_SECONDS = 30 * 60

_cache: dict[str, tuple[float, dict[str, float]]] = {}


def status() -> dict:
    if not REPORT.exists():
        return {"active": False, "reason": "experiment not run (python -m ml.flybrain.experiment)"}
    report = json.loads(REPORT.read_text())
    if not report.get("passed"):
        failed = [name for name, ok in report.get("checks", {}).items() if not ok]
        return {"active": False, "reason": "failed its backtest gate: " + ", ".join(failed),
                "generated_at": report.get("generated_at")}
    if not MODEL.exists():
        return {"active": False, "reason": "passed, but no saved readout — rerun the experiment"}
    return {"active": True, "generated_at": report.get("generated_at")}


def active() -> bool:
    return status()["active"]


def _prices(symbols: list[str]) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download([s + ".NS" for s in symbols], period=HISTORY, interval="1d",
                      auto_adjust=True, group_by="ticker", threads=True, progress=False)
    frames = []
    for s in symbols:
        try:
            d = raw[s + ".NS"].dropna(how="all")
        except KeyError:
            continue
        d = d.rename(columns=str.lower).reset_index().rename(columns={"Date": "date"})
        d["symbol"] = s
        frames.append(d[["symbol", "date", "open", "high", "low", "close", "volume"]])
    return pd.concat(frames, ignore_index=True)


def rank(symbols: list[str]) -> dict[str, float]:
    """Fly score per symbol for the latest session; higher is better. Empty if inactive."""
    if not active():
        return {}
    key = ",".join(sorted(symbols))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    import joblib

    saved = joblib.load(MODEL)
    data = build(_prices(symbols))
    states = reservoir.run(connectome.mushroom_body(), data)
    latest = data["date"] == data["date"].max()
    x = states.loc[latest.to_numpy()].filter(regex=r"^n\d+$").to_numpy()
    scores = saved["model"].predict(saved["scaler"].transform(x))
    result = dict(zip(data.loc[latest, "symbol"], np.round(scores, 5).tolist()))
    _cache[key] = (time.time(), result)
    return result
