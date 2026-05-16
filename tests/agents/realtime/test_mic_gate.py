"""Tests for AzureVoiceLiveAgent._on_mic_gate / mic_gate-aware startup."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure the worktree's dimos package is used instead of the editable install
# pointing at the main repo (the editable .pth points to /home/naoki/dimos).
_WORKTREE_ROOT = Path(__file__).resolve().parents[3]
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))

from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent  # noqa: E402


def _make_agent_minimal() -> AzureVoiceLiveAgent:
    """Instantiate without calling start(); we just exercise _on_mic_gate."""
    agent = AzureVoiceLiveAgent.__new__(AzureVoiceLiveAgent)
    agent._mic_active = threading.Event()
    return agent


def test_on_mic_gate_true_sets_active():
    agent = _make_agent_minimal()
    assert not agent._mic_active.is_set()

    agent._on_mic_gate(True)

    assert agent._mic_active.is_set()


def test_on_mic_gate_false_clears_active():
    agent = _make_agent_minimal()
    agent._mic_active.set()

    agent._on_mic_gate(False)

    assert not agent._mic_active.is_set()


def test_session_updated_auto_set_when_gate_unwired():
    agent = _make_agent_minimal()
    agent._mic_gate_connected = False
    agent.agent_idle = MagicMock()

    agent._maybe_activate_mic_on_session_ready()

    assert agent._mic_active.is_set()
    agent.agent_idle.publish.assert_called_once_with(True)


def test_session_updated_skips_auto_set_when_gate_wired():
    agent = _make_agent_minimal()
    agent._mic_gate_connected = True
    agent.agent_idle = MagicMock()

    agent._maybe_activate_mic_on_session_ready()

    assert not agent._mic_active.is_set()
    agent.agent_idle.publish.assert_called_once_with(True)
