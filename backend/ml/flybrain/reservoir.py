"""
Running a market through a fixed network.

Each stock gets its own copy of the network, and every trading day its
features are injected into the input neurons and the activity is allowed one
step to settle. The state therefore carries a fading memory of that stock's
recent history, shaped entirely by the wiring. A leaky-tanh rate model: the
standard echo-state formulation, nothing fly-specific beyond the matrix.

The parameters below are fixed, not tuned. Both the fly and the random
control use the same values, and tuning them on the test years would make
any difference between the two meaningless.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .connectome import Network
from .features import FEATURES

LEAK = 0.3          # how much of today's input replaces yesterday's state
GAIN = 0.9          # spectral radius after scaling: just inside stable
INPUT_SCALE = 0.5
SEED = 7


def _input_weights(net: Network) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    w_in = np.zeros((net.size, len(FEATURES)), dtype=np.float32)
    w_in[net.inputs] = rng.normal(0, INPUT_SCALE / np.sqrt(len(FEATURES)),
                                  size=(len(net.inputs), len(FEATURES)))
    return w_in


def run(net: Network, data: pd.DataFrame) -> pd.DataFrame:
    """
    Readout-neuron activity for every (date, symbol) row in `data`.

    Days a stock has no row (not yet listed, suspended) feed zeros — the
    cross-sectional average — so its state decays rather than jumping.
    """
    dates = np.sort(data["date"].unique())
    symbols = np.sort(data["symbol"].unique())
    grid = (data.set_index(["date", "symbol"])[FEATURES]
            .reindex(pd.MultiIndex.from_product([dates, symbols]))
            .fillna(0).to_numpy(np.float32)
            .reshape(len(dates), len(symbols), len(FEATURES)))

    w = (net.weights * GAIN).astype(np.float32)
    w_in = _input_weights(net)
    state = np.zeros((net.size, len(symbols)), dtype=np.float32)
    out = np.empty((len(dates), len(symbols), len(net.readout)), dtype=np.float32)

    for t in range(len(dates)):
        drive = w @ state + w_in @ grid[t].T
        state = (1 - LEAK) * state + LEAK * np.tanh(drive)
        out[t] = state[net.readout].T

    frame = pd.DataFrame(out.reshape(-1, len(net.readout)),
                         columns=[f"n{i}" for i in range(len(net.readout))])
    frame["date"] = np.repeat(dates, len(symbols))
    frame["symbol"] = np.tile(symbols, len(dates))
    return data[["date", "symbol"]].merge(frame, on=["date", "symbol"], how="left")
