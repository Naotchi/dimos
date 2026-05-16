"""Tests for PttKeyboard event processing (pygame-independent)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKTREE_ROOT = Path(__file__).resolve().parents[3]
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))

from dimos.agents.realtime.ptt_keyboard import process_ptt_event, PttState  # noqa: E402


def test_space_keydown_emits_true():
    state = PttState(active=False)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keydown", key="space", emit=emitted.append)

    assert state.active is True
    assert emitted == [True]


def test_space_keyup_emits_false():
    state = PttState(active=True)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keyup", key="space", emit=emitted.append)

    assert state.active is False
    assert emitted == [False]


def test_repeated_keydown_no_duplicate_emit():
    state = PttState(active=True)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keydown", key="space", emit=emitted.append)

    assert state.active is True
    assert emitted == []


def test_non_space_key_ignored():
    state = PttState(active=False)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keydown", key="a", emit=emitted.append)
    process_ptt_event(state, kind="keyup", key="a", emit=emitted.append)

    assert state.active is False
    assert emitted == []
