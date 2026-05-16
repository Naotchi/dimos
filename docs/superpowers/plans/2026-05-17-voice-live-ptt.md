# Voice Live Push-to-Talk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** スペースキーを押している間だけマイク音声を Voice Live に送る PTT モードを `unitree-go2-agentic-voice-live` ブループリントに組み込む。

**Architecture:** `AzureVoiceLiveAgent` に `mic_gate: In[bool]` 入力ポートを追加し、接続時は session-ready の自動 mic 有効化を抑止して gate 入力で `_mic_active` を制御。新規モジュール `PttKeyboard` が `keyboard_teleop` と同じ pygame ベースで小窓を出し SPACE 押下/解放で True/False を publish。voice_live ブループリントから `WebInputAudioOnly` を取り除き `PttKeyboard` を配線。

**Tech Stack:** Python 3.12, pygame（既存依存）, dimos.core.stream (In/Out ports), pytest.

**Spec:** `docs/superpowers/specs/2026-05-17-voice-live-ptt-design.md`

**Files map:**
- Modify: `dimos/agents/realtime/azure_voice_live.py` — `mic_gate: In[bool]` ポート追加と `_mic_active` 制御差し替え
- Create: `dimos/agents/realtime/ptt_keyboard.py` — pygame PTT モジュール
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py` — WebInputAudioOnly → PttKeyboard
- Create: `tests/agents/realtime/test_mic_gate.py` — `_on_mic_gate` の挙動テスト
- Create: `tests/agents/realtime/test_ptt_keyboard.py` — 状態遷移ロジックのテスト

---

### Task 1: AzureVoiceLiveAgent に `mic_gate` ポートと制御ロジックを追加

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py:240-310` (port 宣言, start)
- Modify: `dimos/agents/realtime/azure_voice_live.py:540-560` (SESSION_UPDATED 分岐)
- Test: `tests/agents/realtime/test_mic_gate.py`

#### Step 1: Write the failing test

- [ ] **Step 1: テスト雛形を作成**

`tests/agents/realtime/test_mic_gate.py` を新規作成:

```python
"""Tests for AzureVoiceLiveAgent._on_mic_gate / mic_gate-aware startup."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent


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
    """If mic_gate has no connection, SESSION_UPDATED should still set mic active
    (legacy behavior preserved)."""
    agent = _make_agent_minimal()
    agent._mic_gate_connected = False
    agent.agent_idle = MagicMock()

    agent._maybe_activate_mic_on_session_ready()

    assert agent._mic_active.is_set()
    agent.agent_idle.publish.assert_called_once_with(True)


def test_session_updated_skips_auto_set_when_gate_wired():
    """If mic_gate is wired (PTT mode), SESSION_UPDATED must NOT auto-activate the
    mic — the gate input drives it."""
    agent = _make_agent_minimal()
    agent._mic_gate_connected = True
    agent.agent_idle = MagicMock()

    agent._maybe_activate_mic_on_session_ready()

    assert not agent._mic_active.is_set()
    agent.agent_idle.publish.assert_called_once_with(True)
```

- [ ] **Step 2: Run test — 失敗確認**

Run: `pytest tests/agents/realtime/test_mic_gate.py -v`
Expected: 4 件 FAIL（`_on_mic_gate` / `_maybe_activate_mic_on_session_ready` / `_mic_gate_connected` が未定義）

- [ ] **Step 3: ポート宣言を追加**

`dimos/agents/realtime/azure_voice_live.py` の port 宣言ブロックを編集:

```python
    human_input: In[str]
    web_audio: In[AudioEvent]
    mic_gate: In[bool]
    agent_idle: Out[bool]
```

- [ ] **Step 4: `__init__` で `_mic_gate_connected` を初期化**

`__init__` 内（`self._mic_active = threading.Event()` の直後）に追加:

```python
        self._mic_active = threading.Event()
        self._mic_gate_connected = False
```

- [ ] **Step 5: `_on_mic_gate` と `_maybe_activate_mic_on_session_ready` を追加**

クラス末尾近く（既存 `_on_mic_audio` の上、もしくは下）に追加:

```python
    def _on_mic_gate(self, active: bool) -> None:
        if active:
            self._mic_active.set()
        else:
            self._mic_active.clear()

    def _maybe_activate_mic_on_session_ready(self) -> None:
        # In PTT mode (mic_gate wired), the gate input owns _mic_active —
        # leave it cleared so audio only flows while the user holds SPACE.
        if not self._mic_gate_connected:
            self._mic_active.set()
        self.agent_idle.publish(True)
```

- [ ] **Step 6: `start()` で接続判定 + subscribe**

`start()` 内、`self._human_input_sub = self.human_input.subscribe(...)` の下に追加:

```python
        self._mic_gate_connected = self.mic_gate.connection is not None
        if self._mic_gate_connected:
            self._mic_gate_sub = Disposable(
                self.mic_gate.subscribe(self._on_mic_gate)
            )
            self.register_disposable(self._mic_gate_sub)
        else:
            self._mic_gate_sub = None
```

そして `__init__` の attribute 群に `self._mic_gate_sub: Any = None` を追加。

- [ ] **Step 7: SESSION_UPDATED 分岐を差し替え**

既存の `SESSION_UPDATED` 分岐:

```python
        if et == ServerEventType.SESSION_UPDATED:
            logger.info("Voice Live session ready: %s", event.session.id)
            self._mic_active.set()
            self.agent_idle.publish(True)
```

を以下に置き換え:

```python
        if et == ServerEventType.SESSION_UPDATED:
            logger.info("Voice Live session ready: %s", event.session.id)
            self._maybe_activate_mic_on_session_ready()
```

- [ ] **Step 8: Run test — pass 確認**

Run: `pytest tests/agents/realtime/test_mic_gate.py -v`
Expected: 4 件 PASS

- [ ] **Step 9: 既存テストが壊れていないか確認**

Run: `pytest tests/agents/realtime/ -v`
Expected: 全 PASS

- [ ] **Step 10: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py tests/agents/realtime/test_mic_gate.py
git commit -m "feat(voice-live): add mic_gate input port for push-to-talk control"
```

---

### Task 2: `PttKeyboard` モジュール新規作成

**Files:**
- Create: `dimos/agents/realtime/ptt_keyboard.py`
- Test: `tests/agents/realtime/test_ptt_keyboard.py`

イベント処理ロジック（pygame に依存しない pure 関数）を分離してテスタブルにする。

- [ ] **Step 1: Write the failing test**

`tests/agents/realtime/test_ptt_keyboard.py` を新規作成:

```python
"""Tests for PttKeyboard event processing (pygame-independent)."""
from __future__ import annotations

import pytest

from dimos.agents.realtime.ptt_keyboard import process_ptt_event, PttState


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
```

- [ ] **Step 2: Run test — 失敗確認**

Run: `pytest tests/agents/realtime/test_ptt_keyboard.py -v`
Expected: FAIL（`ptt_keyboard` module 不在）

- [ ] **Step 3: モジュール本体作成**

`dimos/agents/realtime/ptt_keyboard.py` を新規作成:

```python
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
        if self._state.active:
            self.mic_gate.publish(False)
            self._state.active = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(DEFAULT_THREAD_JOIN_TIMEOUT)
        super().stop()

    def _emit(self, value: bool) -> None:
        logger.info("PTT mic_gate=%s", value)
        self.mic_gate.publish(value)

    def _pygame_loop(self) -> None:
        pygame.init()
        pygame.key.set_repeat(0)
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
```

- [ ] **Step 4: Run test — pass 確認**

Run: `pytest tests/agents/realtime/test_ptt_keyboard.py -v`
Expected: 4 件 PASS

- [ ] **Step 5: import sanity check**

Run: `python -c "from dimos.agents.realtime.ptt_keyboard import PttKeyboard, process_ptt_event, PttState; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add dimos/agents/realtime/ptt_keyboard.py tests/agents/realtime/test_ptt_keyboard.py
git commit -m "feat(voice-live): add PttKeyboard pygame module for spacebar push-to-talk"
```

---

### Task 3: voice_live ブループリントを PttKeyboard 経路に差し替え

**Files:**
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`

- [ ] **Step 1: ブループリント編集**

ファイル全体を以下に置換:

```python
#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.realtime import AzureVoiceLiveAgent
from dimos.agents.realtime.ptt_keyboard import PttKeyboard
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

# SpeakSkill は SecurityModule の侵入者検知アラート用 (Voice Live の会話 TTS
# とは別経路)。`speak` は MCP tool として公開されると agent が会話のたびに
# 呼び二重発話になるため、AzureVoiceLiveAgent 側で excluded_tools により
# デフォルトで agent から隠している。SecurityModule は Spec 注入経由で直接呼ぶ。
#
# PttKeyboard は SPACE 押下中だけ AzureVoiceLiveAgent.mic_gate を True にする。
# WebUI 経由の音声入力は使わない（スピーカー出力のエコーで誤発火するため）。
unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    PttKeyboard.blueprint(),
    SpeakSkill.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)

__all__ = ["unitree_go2_agentic_voice_live"]
```

- [ ] **Step 2: ブループリント import sanity check**

Run: `python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import unitree_go2_agentic_voice_live; print(unitree_go2_agentic_voice_live)"`
Expected: 例外なく blueprint オブジェクトが出力されること

- [ ] **Step 3: 全 blueprint 列挙が壊れていないか**

Run: `python -c "from dimos.robot.all_blueprints import BLUEPRINTS; print('voice-live' in str(BLUEPRINTS))"`
Expected: `True`（または同等の確認）

実 attribute 名を確認するため不明なら:
```
python -c "from dimos.robot import all_blueprints; print([n for n in dir(all_blueprints) if 'voice' in n.lower()])"
```

- [ ] **Step 4: Commit**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py
git commit -m "feat(go2): wire voice-live blueprint to PttKeyboard for push-to-talk"
```

---

### Task 4: 手動 E2E 確認チェックリスト

**Files:** なし（手動確認）

このタスクはコード変更なし。実機/ローカル環境で確認した上でチェックする。

- [ ] **Step 1: ブループリント起動**

Run: `dimos run unitree-go2-agentic-voice-live`
Expected: rerun と並んで "Voice Live PTT" タイトルの小窓が出る。"Idle" + 灰丸が表示。

- [ ] **Step 2: PTT 動作確認**

PTT 窓をフォーカスし SPACE を押し下げて発話。
Expected: 表示が "Recording..." + 赤丸に変化。離すと "Idle" に戻り、~500ms 後にエージェント応答が再生される。

- [ ] **Step 3: エコー誤発火が起きないこと**

エージェント応答中に SPACE を押さず黙る。
Expected: スピーカー出力をマイクが拾っても次の応答がトリガーされない（mic_active が False のため）。

- [ ] **Step 4: barge-in（割り込み）動作**

エージェント応答中に SPACE を押して別の発話をする。
Expected: 既存通り response.cancel が走り再生が止まり、新しい発話に応答する。

- [ ] **Step 5: ESC / window close で停止**

PTT 窓で ESC または ✕ ボタンを押す。
Expected: 窓が閉じる。ブループリント停止時にハングしない。

すべて確認できたら CLAUDE 経由ではなく自分でチェックを入れる。問題があればそのタスクを issue として記録し、本プランの後続タスクとして追加する。

---

## Self-review notes

- Spec の「mic_gate ポート追加 / 既存 voice_live 編集 OK」要件をすべてカバー
- WebInput 経路は削除せずモジュール/ポートは残るので将来復活可能（Task 3 はブループリントからの参照を外すだけ）
- `_mic_gate_connected` フラグで legacy mode（gate 未配線）を維持
- pygame loop は keyboard_teleop と同じパターン（X11 ドライバ強制、deamon thread、ESC で終了）
