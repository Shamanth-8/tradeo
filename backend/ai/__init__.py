"""
Tradeo's intelligence layer.

`brain` is the single entry point: it routes between the local Ollama model and
an optional cloud model (off by default: AI_MODE=local_only); every AI feature in the
app goes through it.
"""

from .brain import Brain, BrainResponse, brain
from .providers import ProviderError, extract_json

__all__ = ["Brain", "BrainResponse", "brain", "ProviderError", "extract_json"]
