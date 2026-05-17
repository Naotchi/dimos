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
(case-insensitive). Accepts pynput key names (``f1``-``f24``, ``space``,
``tab``, ``ctrl_l``/``ctrl_r``, ``shift_l``/``shift_r``, ``alt_l``/``alt_r``,
``esc``, ...) or any single character (``a``, `` ` ``).

The listener does NOT suppress the key - it stays observable to other
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
    # Named special keys (f1-f24, space, ctrl_l, ...) live as Key attributes.
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
