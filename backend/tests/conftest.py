"""
Tests run from backend/:  ./venv/bin/python -m pytest -q

They never write to the real database, credential store or agent switches:
anything stateful is pointed at a temporary directory first.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def agent_store(tmp_path, monkeypatch):
    """Agent switches in a throwaway file, starting from the shipped defaults."""
    from autopilot import agents

    monkeypatch.setattr(agents, "STORE", tmp_path / "agents.json")
    monkeypatch.setattr(agents, "apply_watchtower", lambda on: None)  # don't start real loops
    return agents
