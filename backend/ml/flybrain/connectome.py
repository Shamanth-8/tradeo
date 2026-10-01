"""
The fruit fly's mushroom body, as a fixed recurrent network.

Source: FlyWire FAFB v783 (Dorkenwald et al. 2024; Schlegel et al. 2024),
CC-BY-4.0 — `proofread_connections_783.feather` from zenodo.org/records/10676866
and the neuron annotations from github.com/flyconnectome/flywire_annotations.
Both are fetched into `data/connectome/` by `scripts/fetch_connectome.sh`.

Why the mushroom body: it is where the fly learns which stimuli predict
reward or punishment. Projection neurons carry odour identity in, ~5,000
Kenyon cells expand it into a sparse code, dopamine neurons carry the
reinforcement signal, and ~100 output neurons (MBONs) report a valence. The
reservoir keeps that shape: market features go in through the projection
neurons and the readout sees only the MBONs.

Nothing here is trained. The wiring is the fly's; only the linear readout on
top of it learns (see reservoir.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import sparse

from core.config import PROJECT_ROOT

log = logging.getLogger("tradeo.flybrain")

CONNECTOME_DIR = PROJECT_ROOT / "data" / "connectome"
CONNECTIONS = CONNECTOME_DIR / "proofread_connections_783.feather"
ANNOTATIONS = CONNECTOME_DIR / "Supplemental_file1_neuron_annotations.tsv"
CACHE = PROJECT_ROOT / "data" / "cache" / "flybrain" / "mushroom_body.npz"

INPUT_CLASS = "ALPN"
READOUT_CLASS = "MBON"
MB_CLASSES = ("ALPN", "Kenyon_Cell", "DAN", "MBON", "MBIN")
MB_CELL_TYPES = ("APL", "DPM")  # the feedback inhibitor and the modulatory partner

# FlyWire's transmitter classifier calls Kenyon cells dopaminergic; they are
# cholinergic (Barnstedt et al. 2016), and the FlyWire papers flag this.
# Signing them inhibitory-or-modulatory would invert the whole expansion layer.
NT_OVERRIDES = {"Kenyon_Cell": "acetylcholine"}

# Glutamate acts through GluCl in the fly central brain, so it is inhibitory
# here, as in Shiu et al. 2024's whole-brain model.
INHIBITORY = {"gaba", "glutamate"}

# Connections under this many synapses are dropped — the FlyWire convention
# for separating real connections from reconstruction noise.
MIN_SYNAPSES = 5


@dataclass
class Network:
    weights: sparse.csr_matrix   # post x pre, signed, spectral radius 1
    inputs: np.ndarray           # indices that receive the features
    readout: np.ndarray          # indices the readout sees
    label: str

    @property
    def size(self) -> int:
        return self.weights.shape[0]


def _neurons() -> pd.DataFrame:
    a = pd.read_csv(ANNOTATIONS, sep="\t", low_memory=False,
                    usecols=["root_id", "cell_class", "cell_type", "top_nt", "known_nt"])
    keep = a.cell_class.isin(MB_CLASSES) | a.cell_type.isin(MB_CELL_TYPES)
    mb = a[keep].copy()
    nt = mb.known_nt.fillna(mb.top_nt).astype(str).str.lower()
    for cell_class, transmitter in NT_OVERRIDES.items():
        nt[mb.cell_class == cell_class] = transmitter
    mb["sign"] = np.where(nt.str.contains("|".join(INHIBITORY)), -1.0, 1.0)
    return mb.reset_index(drop=True)


def _normalise(w: sparse.csr_matrix) -> sparse.csr_matrix:
    """Scale to spectral radius 1 so the reservoir's gain is set in one place."""
    from scipy.sparse.linalg import eigs

    radius = abs(eigs(w.astype(np.float64), k=1, which="LM",
                      return_eigenvectors=False, maxiter=5000)[0])
    return (w / radius).tocsr().astype(np.float32)


def mushroom_body() -> Network:
    """The fly's wiring. Built once from the FlyWire files, then cached."""
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=False)
        w = sparse.csr_matrix((z["data"], z["indices"], z["indptr"]), shape=tuple(z["shape"]))
        return Network(w, z["inputs"], z["readout"], "fly mushroom body")

    if not CONNECTIONS.exists():
        raise FileNotFoundError(f"{CONNECTIONS} missing — run scripts/fetch_connectome.sh")

    mb = _neurons()
    index = pd.Series(np.arange(len(mb)), index=mb.root_id.values)

    edges = pd.read_feather(CONNECTIONS, columns=["pre_pt_root_id", "post_pt_root_id", "syn_count"])
    edges = edges[edges.pre_pt_root_id.isin(index.index) & edges.post_pt_root_id.isin(index.index)]
    # One row per neuropil in the source table; a connection is the sum.
    edges = edges.groupby(["pre_pt_root_id", "post_pt_root_id"], as_index=False).syn_count.sum()
    edges = edges[edges.syn_count >= MIN_SYNAPSES]

    pre = index[edges.pre_pt_root_id.values].values
    post = index[edges.post_pt_root_id.values].values
    # Synapse counts span three orders of magnitude; log keeps the handful of
    # huge connections from being the whole network.
    strength = np.log1p(edges.syn_count.values) * mb.sign.values[pre]
    w = sparse.csr_matrix((strength, (post, pre)), shape=(len(mb), len(mb)))
    w = _normalise(w)

    inputs = np.flatnonzero(mb.cell_class.values == INPUT_CLASS)
    readout = np.flatnonzero(mb.cell_class.values == READOUT_CLASS)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CACHE, data=w.data, indices=w.indices, indptr=w.indptr,
                        shape=np.array(w.shape), inputs=inputs, readout=readout)
    log.info("mushroom body: %d neurons, %d connections, %d inputs, %d readout",
             w.shape[0], w.nnz, len(inputs), len(readout))
    return Network(w, inputs, readout, "fly mushroom body")


def random_control(fly: Network, seed: int = 0) -> Network:
    """
    Same neuron count, edge count, sign balance and in/out sizes — random wiring.

    This is the control that decides whether the fly's structure is doing
    anything. If a random network of the same size scores as well, the result
    is "a reservoir helps", not "a fly brain helps".
    """
    rng = np.random.default_rng(seed)
    n, nnz = fly.size, fly.weights.nnz
    flat = rng.choice(n * n, size=nnz, replace=False)
    post, pre = np.divmod(flat, n)
    # Keep the fly's weight distribution, including its signs, just not where they go.
    values = rng.permutation(fly.weights.data)
    w = _normalise(sparse.csr_matrix((values, (post, pre)), shape=(n, n)))
    picks = rng.permutation(n)
    return Network(w, picks[: len(fly.inputs)],
                   picks[len(fly.inputs): len(fly.inputs) + len(fly.readout)],
                   "random wiring (control)")
