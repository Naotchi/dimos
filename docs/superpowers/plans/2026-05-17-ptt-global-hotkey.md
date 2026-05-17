# PTT Global Hotkey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the focus-requiring pygame `PttKeyboard` window with a `pynput` global hotkey listener (default F9, overridable via `DIMOS_PTT_KEY`) so PTT works regardless of which OS window has focus.

**Architecture:** In-place rewrite of `dimos/agents/realtime/ptt_keyboard.py`. The pygame window/loop/render code is deleted. A `pynput.keyboard.Listener` runs on a daemon thread and calls the existing pure `process_ptt_event` state machine. Public API (`PttKeyboard`, `mic_gate: Out[bool]`, `start`/`stop`, `blueprint()`) is unchanged so the `unitree_go2_agentic_voice_live` blueprint needs no edits.

**Tech Stack:** Python 3.12, pynput (new dep), pytest, existing dimos `Module` / `Out` / `@rpc` framework.

**Spec:** `docs/superpowers/specs/2026-05-17-ptt-global-hotkey-design.md`

---

## File map

- Modify: `dimos/agents/realtime/ptt_keyboard.py` — full rewrite of class body, keep `PttState` + `process_ptt_event`, drop pygame, add `_parse_key`, add `pynput.keyboard.Listener` integration
- Modify: `pyproject.toml` — add `"pynput>=1.7,<2"` to `dependencies`
- Modify: `tests/agents/realtime/test_ptt_keyboard.py` — switch trigger key from `space` to `f9`, add `_parse_key` and env tests

No other files change. `unitree_go2_agentic_voice_live.py` is untouched.

---

## Task 1: Add pynput dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependency line**

Open `pyproject.toml` and find the `dependencies = [...]` block. Add the line below alphabetically near other `p*` entries (or simply at the end of the list, before the closing `]`):

```toml
    "pynput>=1.7,<2",
```

- [ ] **Step 2: Install**

Run: `uv sync --extra all`
Expected: success (per fork CLAUDE.md, `--extra all` is the right form for dimos; `--all-extras` fails on cyclonedds).

- [ ] **Step 3: Verify importable**

Run: `python -c "import pynput.keyboard; print(pynput.keyboard.Key.f9)"`
Expected: `Key.f9`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(ptt): add pynput dep for global-hotkey PTT listener"
```

---

## Task 2: Update tests for new trigger key and parser (failing)

**Files:**
- Modify: `tests/agents/realtime/test_ptt_keyboard.py`

- [ ] **Step 1: Replace the test file contents**

Overwrite `tests/agents/realtime/test_ptt_keyboard.py` with:

```python
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
```

- [ ] **Step 2: Run and verify failures**

Run: `python -m pytest tests/agents/realtime/test_ptt_keyboard.py -v`
Expected: every test FAILs with `ImportError: cannot import name '_parse_key' ...` or `TypeError: process_ptt_event() got an unexpected keyword argument 'trigger'`. (We have not yet implemented the new signatures.)

- [ ] **Step 3: Do NOT commit yet** — tests are red.

---

## Task 3: Rewrite `ptt_keyboard.py` (drop pygame, add pynput + parser)

**Files:**
- Modify: `dimos/agents/realtime/ptt_keyboard.py`

- [ ] **Step 1: Overwrite the file**

Replace the entire contents of `dimos/agents/realtime/ptt_keyboard.py` with:

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Push-to-talk global hotkey module for AzureVoiceLiveAgent.

Uses ``pynput.keyboard.Listener`` so the PTT key works regardless of which
OS window has focus. While the trigger key is held, publishes True on the
``mic_gate`` Out port; on release, publishes False.

Default trigger is F9. Override with the ``DIMOS_PTT_KEY`` env var
(case-insensitive). Accepts pynput key names (``f1``–``f24``, ``space``,
``tab``, ``ctrl_l``/``ctrl_r``, ``shift_l``/``shift_r``, ``alt_l``/``alt_r``,
``esc``, …) or any single character (``a``, `` ` ``).

The listener does NOT suppress the key — it stays observable to other
applications. Requires X11 (Wayland is not supported by pynput).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from pynput.keyboard import Key, KeyCode, Listener

from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import Out
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_ENV_KEY = "DIMOS_PTT_KEY"
_DEFAULT_KEY = "f9"


@dataclass
class PttState:
    active: bool = False


def process_ptt_event(
    state: PttState,
    kind: str,
    key: str,
    trigger: str,
    emit: Callable[[bool], None],
) -> None:
    """Pure state transition for PTT key events.

    ``kind`` is "keydown" or "keyup"; ``key`` and ``trigger`` are lowercased
    key names. ``emit`` is called once per state change.
    """
    if key.lower() != trigger.lower():
        return
    if kind == "keydown" and not state.active:
        state.active = True
        emit(True)
    elif kind == "keyup" and state.active:
        state.active = False
        emit(False)


def _parse_key(name: str) -> Key | KeyCode:
    """Resolve a key name to a pynput ``Key`` or ``KeyCode``.

    Raises ``ValueError`` for unrecognised names so misconfiguration is
    surfaced at construction time, not at first keystroke.
    """
    lowered = name.strip().lower()
    if not lowered:
        raise ValueError("empty PTT key name")
    # Named special keys (f1–f24, space, ctrl_l, …) live as Key attributes.
    if hasattr(Key, lowered):
        return getattr(Key, lowered)
    if len(lowered) == 1:
        return KeyCode.from_char(lowered)
    raise ValueError(f"unrecognised PTT key name: {name!r}")


def _key_to_name(key: Any) -> str:
    """Return a lowercase canonical name for a pynput event key.

    pynput passes either a ``Key`` (special) or ``KeyCode`` (char) to
    callbacks. We normalise to the same string form used by ``_parse_key``.
    """
    if isinstance(key, Key):
        return key.name
    if isinstance(key, KeyCode) and key.char is not None:
        return key.char.lower()
    return ""


class PttKeyboard(Module):
    """Global hotkey that drives a boolean ``mic_gate`` while a key is held."""

    mic_gate: Out[bool]

    _state: PttState
    _trigger_key: Key | KeyCode
    _trigger_key_name: str
    _listener: Listener | None = None

    def __init__(self, trigger_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._state = PttState(active=False)

        name = trigger_key if trigger_key is not None else os.environ.get(_ENV_KEY, _DEFAULT_KEY)
        self._trigger_key_name = name.strip().lower()
        self._trigger_key = _parse_key(self._trigger_key_name)

        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            logger.warning(
                "PttKeyboard: XDG_SESSION_TYPE=wayland detected; pynput global "
                "hotkeys do not work reliably on Wayland. Consider switching to "
                "an X11 session if PTT does not respond."
            )

    @rpc
    def start(self) -> None:
        super().start()
        self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        logger.info("PttKeyboard listening for %s (set DIMOS_PTT_KEY to override)", self._trigger_key_name)

    @rpc
    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._state.active:
            self.mic_gate.publish(False)
            self._state.active = False
        super().stop()

    def _emit(self, value: bool) -> None:
        logger.info("PTT mic_gate=%s", value)
        self.mic_gate.publish(value)

    def _on_press(self, key: Any) -> None:
        process_ptt_event(
            self._state,
            kind="keydown",
            key=_key_to_name(key),
            trigger=self._trigger_key_name,
            emit=self._emit,
        )

    def _on_release(self, key: Any) -> None:
        process_ptt_event(
            self._state,
            kind="keyup",
            key=_key_to_name(key),
            trigger=self._trigger_key_name,
            emit=self._emit,
        )
```

- [ ] **Step 2: Run the test file**

Run: `python -m pytest tests/agents/realtime/test_ptt_keyboard.py -v`
Expected: all 12 tests PASS.

- [ ] **Step 3: Quick smoke check (no listener needed)**

Run:
```bash
DIMOS_PTT_KEY=f8 python -c "from dimos.agents.realtime.ptt_keyboard import PttKeyboard; p = PttKeyboard(); print(p._trigger_key, p._trigger_key_name)"
```
Expected: `Key.f8 f8`

Run:
```bash
python -c "from dimos.agents.realtime.ptt_keyboard import PttKeyboard; p = PttKeyboard(); print(p._trigger_key, p._trigger_key_name)"
```
Expected: `Key.f9 f9`

- [ ] **Step 4: Verify pygame is gone**

Run: `grep -n "pygame\|SDL_VIDEODRIVER\|_screen\|_render" dimos/agents/realtime/ptt_keyboard.py`
Expected: no matches (exit code 1).

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/realtime/ptt_keyboard.py tests/agents/realtime/test_ptt_keyboard.py
git commit -m "$(cat <<'EOF'
refactor(ptt): replace pygame PTT window with pynput global hotkey

Drops the focus-requiring pygame window. PttKeyboard now starts a
pynput.keyboard.Listener on a daemon thread, defaulting to F9 hold and
configurable via DIMOS_PTT_KEY. Public Module API (mic_gate, start/stop,
blueprint()) is unchanged, so the voice-live blueprint needs no edits.
EOF
)"
```

---

## Task 4: Manual end-to-end verification

These steps require a real X11 session and a working voice-live setup. The implementer cannot fake them with unit tests; record the outcome in the PR description.

**Files:** none

- [ ] **Step 1: Launch voice-live**

Run (in a terminal — the launch command differs per setup; use the same one normally used to start `unitree-go2-agentic-voice-live`):
```bash
python scripts/replay_agentic_voice_live.py
```
(or whichever launcher the operator uses for this blueprint)

- [ ] **Step 2: Confirm no pygame window appears**

Expected: no "Voice Live PTT" window is created. The rerun viewer / terminal are the only windows.

- [ ] **Step 3: Confirm listener log line**

Expected log line shortly after startup: `PttKeyboard listening for f9 (set DIMOS_PTT_KEY to override)`

- [ ] **Step 4: Verify focus-less PTT**

Click into another window (e.g. the terminal, a browser). Hold **F9**.
Expected log lines:
```
PTT mic_gate=True
```
Release F9. Expected:
```
PTT mic_gate=False
```
Repeat with the rerun viewer focused — same result.

- [ ] **Step 5: Verify env override**

Stop, then relaunch with `DIMOS_PTT_KEY=f8 …`. Confirm startup log shows `listening for f8` and that F9 no longer triggers, F8 does.

- [ ] **Step 6: Verify graceful shutdown**

Ctrl-C the process while NOT holding the PTT key. Expected: no traceback from the listener thread; process exits cleanly.

Press the PTT key just before Ctrl-C (so `_state.active` is True). Expected: a final `PTT mic_gate=False` log line from `stop()`.

- [ ] **Step 7: (No commit)** — manual checks only. If anything fails, debug and amend Task 3.

---

## Notes for the implementer

- `unitree_go2_agentic_voice_live.py` is intentionally untouched. The blueprint still calls `PttKeyboard.blueprint()` with no args; the env var or a future kwarg controls the trigger.
- `pynput` on Wayland is best-effort only. We warn but do not refuse to start, because some compositors route through XWayland and it does work.
- The listener is **not** in suppress mode. The trigger key reaches other apps too. That is acceptable here — F9 has no typing collision and any cross-app fire just toggles `mic_gate` for the held duration.
- Do not introduce a status UI in this plan. The spec explicitly leaves rerun integration / viewer floating panel out of scope.
