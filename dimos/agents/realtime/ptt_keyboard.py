# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Push-to-talk keyboard module for AzureVoiceLiveAgent.

Opens a small pygame window. While SPACE is held, publishes True on the
``mic_gate`` Out port; on release, publishes False. The Voice Live agent
gates microphone forwarding based on these events.

Mirrors the dependency / window pattern of dimos.robot.unitree.keyboard_teleop
so the PTT window sits naturally next to rerun and other tooling windows.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

import pygame

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import Out
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

os.environ.setdefault("SDL_VIDEODRIVER", "x11")

_WINDOW_WIDTH = 400
_WINDOW_HEIGHT = 150
_FONT_SIZE = 24
_LOOP_RATE_HZ = 60
_BG_COLOR = (30, 30, 30)
_IDLE_COLOR = (120, 120, 120)
_REC_COLOR = (220, 60, 60)
_TEXT_COLOR = (220, 220, 220)
_HINT_COLOR = (150, 150, 150)
_INDICATOR_RADIUS = 18


@dataclass
class PttState:
    active: bool = False


def process_ptt_event(
    state: PttState,
    kind: str,
    key: str,
    emit: Callable[[bool], None],
) -> None:
    """Pure state transition for PTT key events.

    ``kind`` is "keydown" or "keyup"; ``key`` is the lowercased key name
    (only "space" is acted on). ``emit`` is called once per state change.
    """
    if key != "space":
        return
    if kind == "keydown" and not state.active:
        state.active = True
        emit(True)
    elif kind == "keyup" and state.active:
        state.active = False
        emit(False)


class PttKeyboard(Module):
    """Pygame window that drives a boolean ``mic_gate`` while SPACE is held."""

    mic_gate: Out[bool]

    _state: PttState
    _stop_event: threading.Event
    _thread: threading.Thread | None = None
    _screen: Any = None
    _font: Any = None
    _clock: Any = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._state = PttState(active=False)
        self._stop_event = threading.Event()

    @rpc
    def start(self) -> None:
        super().start()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._pygame_loop, daemon=True)
        self._thread.start()

    @rpc
    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(DEFAULT_THREAD_JOIN_TIMEOUT)
        if self._state.active:
            self.mic_gate.publish(False)
            self._state.active = False
        super().stop()

    def _emit(self, value: bool) -> None:
        logger.info("PTT mic_gate=%s", value)
        self.mic_gate.publish(value)

    def _pygame_loop(self) -> None:
        pygame.init()
        pygame.key.set_repeat()
        self._screen = pygame.display.set_mode(
            (_WINDOW_WIDTH, _WINDOW_HEIGHT), pygame.SWSURFACE
        )
        pygame.display.set_caption("Voice Live PTT")
        self._clock = pygame.time.Clock()
        self._font = pygame.font.Font(None, _FONT_SIZE)

        while not self._stop_event.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._stop_event.set()
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                    process_ptt_event(
                        self._state, kind="keydown", key="space", emit=self._emit
                    )
                elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
                    process_ptt_event(
                        self._state, kind="keyup", key="space", emit=self._emit
                    )
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self._stop_event.set()

            self._render()
            self._clock.tick(_LOOP_RATE_HZ)

        pygame.quit()

    def _render(self) -> None:
        self._screen.fill(_BG_COLOR)
        color = _REC_COLOR if self._state.active else _IDLE_COLOR
        pygame.draw.circle(self._screen, color, (40, 75), _INDICATOR_RADIUS)
        status = "Recording..." if self._state.active else "Idle"
        text_surf = self._font.render(status, True, _TEXT_COLOR)
        self._screen.blit(text_surf, (75, 65))
        hint = self._font.render("Hold SPACE to talk", True, _HINT_COLOR)
        self._screen.blit(hint, (20, 20))
        pygame.display.flip()
