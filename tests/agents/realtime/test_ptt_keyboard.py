"""Tests for PttKeyboard event processing and key parsing (pynput-independent)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pynput.keyboard import Key, KeyCode

_WORKTREE_ROOT = Path(__file__).resolve().parents[3]
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))

from dimos.agents.realtime.ptt_keyboard import (  # noqa: E402
    PttKeyboard,
    PttState,
    _parse_key,
    process_ptt_event,
)


# ---- process_ptt_event ----


def test_trigger_keydown_emits_true():
    state = PttState(active=False)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keydown", key="f9", trigger="f9", emit=emitted.append)

    assert state.active is True
    assert emitted == [True]


def test_trigger_keyup_emits_false():
    state = PttState(active=True)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keyup", key="f9", trigger="f9", emit=emitted.append)

    assert state.active is False
    assert emitted == [False]


def test_repeated_keydown_no_duplicate_emit():
    state = PttState(active=True)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keydown", key="f9", trigger="f9", emit=emitted.append)

    assert state.active is True
    assert emitted == []


def test_non_trigger_key_ignored():
    state = PttState(active=False)
    emitted: list[bool] = []

    process_ptt_event(state, kind="keydown", key="a", trigger="f9", emit=emitted.append)
    process_ptt_event(state, kind="keyup", key="a", trigger="f9", emit=emitted.append)

    assert state.active is False
    assert emitted == []


# ---- _parse_key ----


def test_parse_function_key():
    assert _parse_key("f9") is Key.f9


def test_parse_function_key_uppercase():
    assert _parse_key("F9") is Key.f9


def test_parse_named_key():
    assert _parse_key("space") is Key.space


def test_parse_named_key_with_side():
    assert _parse_key("ctrl_r") is Key.ctrl_r


def test_parse_char_key():
    assert _parse_key("a") == KeyCode.from_char("a")


def test_parse_unknown_key_raises():
    with pytest.raises(ValueError):
        _parse_key("not_a_key")


# ---- env / trigger_key resolution ----


def test_trigger_key_defaults_to_f9(monkeypatch):
    monkeypatch.delenv("DIMOS_PTT_KEY", raising=False)
    ptt = PttKeyboard()
    assert ptt._trigger_key is Key.f9
    assert ptt._trigger_key_name == "f9"


def test_trigger_key_env_override(monkeypatch):
    monkeypatch.setenv("DIMOS_PTT_KEY", "f8")
    ptt = PttKeyboard()
    assert ptt._trigger_key is Key.f8
    assert ptt._trigger_key_name == "f8"


def test_trigger_key_kwarg_beats_env(monkeypatch):
    monkeypatch.setenv("DIMOS_PTT_KEY", "f8")
    ptt = PttKeyboard(trigger_key="space")
    assert ptt._trigger_key is Key.space
    assert ptt._trigger_key_name == "space"


def test_invalid_trigger_key_raises_at_init(monkeypatch):
    monkeypatch.setenv("DIMOS_PTT_KEY", "not_a_key")
    with pytest.raises(ValueError):
        PttKeyboard()
