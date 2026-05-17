# local-tts: 発話を assistant message に統合 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitree-go2-agentic-local-tts` から `speak` tool を廃止し、LLM の assistant message text content を OpenJTalk TTS にそのまま流して発話する。

**Architecture:** `dimos/agents/skills/speak_skill_ja.py` の `JapaneseSpeakSkill`（`@skill speak()` を提供する Module）を `AssistantSpeechNodeJa`（`In[BaseMessage] agent` を subscribe する Module）に全面差し替え。`McpClient.agent: Out[BaseMessage]` から autoconnect（`(name, type)` 一致）で wire され、`AIMessage` かつ非空 `str` content のみを `OpenJTalkTTSNode` に enqueue → `SounddeviceAudioOutput(48 kHz)` に再生。並列再生（fire-and-forget）。bench event 名 (`speak_invoke` / `first_audio_out` tool=`"speak"`) は維持。

**Tech Stack:** Python 3.12, reactivex, langchain-core, langgraph, pyopenjtalk, sounddevice, pytest. Spec: `docs/superpowers/specs/2026-05-17-agentic-local-tts-speak-via-assistant-message-design.md`。

---

## File Structure

- **Modify:** `dimos/agents/skills/speak_skill_ja.py` — 中身を全面差し替え。`JapaneseSpeakSkill` → `AssistantSpeechNodeJa`。
- **Modify:** `dimos/robot/unitree/go2/blueprints/agentic/_common_agentic_ja.py` — import と blueprint 名を差し替え（2 行）。
- **Modify:** `dimos/agents/system_prompt_ja.py` — 「# コミュニケーション」段落と「デリバリー/ピックアップ」内の `speak` 言及を書き換え。
- **Create:** `tests/agents/skills/__init__.py`（空）+ `tests/agents/skills/test_assistant_speech_node_ja.py` — 新規ユニットテスト。
- **Touch nothing:** upstream 由来の `speak_skill.py` / `speak_skill_spec.py` / `mcp_client.py` / `system_prompt.py` / 英語版・voice-live blueprint。

---

## Task 1: 新規テストファイルの足場と最初の失敗テスト

**Files:**
- Create: `tests/agents/skills/__init__.py`
- Create: `tests/agents/skills/test_assistant_speech_node_ja.py`

- [ ] **Step 1: テストディレクトリの `__init__.py` を作る**

```bash
mkdir -p tests/agents/skills
```

`tests/agents/skills/__init__.py` の内容は空ファイル（0 バイト）。

- [ ] **Step 2: 失敗するテストファイルを書く**

`tests/agents/skills/test_assistant_speech_node_ja.py`:

```python
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

"""Unit tests for AssistantSpeechNodeJa."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Construct AssistantSpeechNodeJa with TTS / audio output mocked."""
    # Patch OpenJTalkTTSNode and SounddeviceAudioOutput before import so start()
    # uses the mocks.
    fake_tts = MagicMock(name="OpenJTalkTTSNode_instance")
    fake_tts.emit_audio.return_value = MagicMock(name="audio_observable")
    fake_audio = MagicMock(name="SounddeviceAudioOutput_instance")

    monkeypatch.setattr(
        "dimos.agents.skills.speak_skill_ja.OpenJTalkTTSNode",
        lambda *a, **kw: fake_tts,
    )
    monkeypatch.setattr(
        "dimos.agents.skills.speak_skill_ja.SounddeviceAudioOutput",
        lambda *a, **kw: fake_audio,
    )

    # Import after monkeypatch so the class binds to the patched names.
    from dimos.agents.skills.speak_skill_ja import AssistantSpeechNodeJa

    n = AssistantSpeechNodeJa(config_args={})
    n.start()
    # Capture the Subject the node feeds with text so tests can assert on it.
    n._test_fake_tts = fake_tts  # type: ignore[attr-defined]
    n._test_fake_audio = fake_audio  # type: ignore[attr-defined]
    return n


def test_ai_message_with_text_is_enqueued(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(AIMessage(content="こんにちは"))

    assert sent == ["こんにちは"]


def test_ai_message_empty_content_with_tool_calls_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(
        AIMessage(
            content="",
            tool_calls=[{"name": "navigate", "args": {}, "id": "x"}],
        )
    )

    assert sent == []


def test_ai_message_whitespace_only_content_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(AIMessage(content="   \n  "))

    assert sent == []


def test_human_message_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(HumanMessage(content="ユーザの発話"))

    assert sent == []


def test_tool_message_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(ToolMessage(content="tool result", tool_call_id="x"))

    assert sent == []


def test_ai_message_list_content_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(
        AIMessage(
            content=[
                {"type": "text", "text": "見て"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ]
        )
    )

    assert sent == []


def test_speak_invoke_bench_event_emitted_once_per_message(node: Any) -> None:
    with patch("dimos.agents.skills.speak_skill_ja.log_bench_event") as logbench:
        node._on_agent_message(AIMessage(content="こんにちは"))
        node._on_agent_message(AIMessage(content="さようなら"))

    speak_invokes = [c for c in logbench.call_args_list if c.args == ("speak_invoke",)]
    assert len(speak_invokes) == 2


def test_first_audio_out_emitted_once_per_message(node: Any) -> None:
    with patch("dimos.agents.skills.speak_skill_ja.log_bench_event") as logbench:
        node._on_agent_message(AIMessage(content="こんにちは"))
        # Simulate two audio chunks for the first message.
        node._on_audio_chunk(object())
        node._on_audio_chunk(object())

        # Next message resets the latch.
        node._on_agent_message(AIMessage(content="さようなら"))
        node._on_audio_chunk(object())

    first_audio_calls = [
        c for c in logbench.call_args_list
        if c.args == ("first_audio_out",) and c.kwargs.get("tool") == "speak"
    ]
    assert len(first_audio_calls) == 2
```

- [ ] **Step 3: テストを走らせて期待通り失敗することを確認**

Run: `python -m pytest tests/agents/skills/test_assistant_speech_node_ja.py -v`
Expected: ImportError / ModuleNotFoundError 系で全て FAIL（`AssistantSpeechNodeJa` がまだ存在しないため）。

- [ ] **Step 4: コミット**

```bash
git add tests/agents/skills/__init__.py tests/agents/skills/test_assistant_speech_node_ja.py
git commit -m "test(local-tts): failing tests for AssistantSpeechNodeJa"
```

---

## Task 2: `speak_skill_ja.py` を `AssistantSpeechNodeJa` に全面差し替え

**Files:**
- Modify: `dimos/agents/skills/speak_skill_ja.py` (entire file)

- [ ] **Step 1: ファイルを以下の内容で完全に置き換える**

`dimos/agents/skills/speak_skill_ja.py`:

```python
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

"""Speak assistant messages directly via local Japanese TTS.

Subscribes to ``McpClient.agent: Out[BaseMessage]`` (autoconnect wires by
``(name, type)``) and feeds the text content of each ``AIMessage`` straight
into ``OpenJTalkTTSNode`` -> ``SounddeviceAudioOutput``. Replaces the previous
``JapaneseSpeakSkill`` which exposed a ``speak`` tool to the LLM.
"""

from __future__ import annotations

import threading
from typing import Any

import reactivex.operators as ops
from langchain_core.messages import AIMessage
from langchain_core.messages.base import BaseMessage
from reactivex import Subject
from reactivex.disposable import Disposable

from dimos.agents.bench_ja import log_bench_event
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode


class AssistantSpeechNodeJa(Module):
    """Speak assistant message text via local Japanese TTS.

    Wired by autoconnect to ``McpClient.agent`` (Out[BaseMessage]) via the
    matching ``agent: In[BaseMessage]`` field name + type.
    """

    agent: In[BaseMessage]

    _tts_node: OpenJTalkTTSNode | None
    _audio_output: SounddeviceAudioOutput | None
    _text_subject: Subject[str] | None
    _first_chunk_pending: bool
    _first_chunk_lock: threading.Lock

    @rpc
    def start(self) -> None:
        super().start()

        self._first_chunk_pending = False
        self._first_chunk_lock = threading.Lock()

        self._tts_node = OpenJTalkTTSNode()
        self._audio_output = SounddeviceAudioOutput(sample_rate=48000)

        self._text_subject = Subject()
        self._tts_node.consume_text(self._text_subject)

        tapped = self._tts_node.emit_audio().pipe(ops.do_action(self._on_audio_chunk))
        self._audio_output.consume_audio(tapped)

        self.register_disposable(
            Disposable(self.agent.subscribe(self._on_agent_message))
        )

    @rpc
    def stop(self) -> None:
        if self._text_subject is not None:
            self._text_subject.on_completed()
            self._text_subject = None
        if self._tts_node is not None:
            self._tts_node.dispose()
            self._tts_node = None
        if self._audio_output is not None:
            self._audio_output.stop()
            self._audio_output = None
        super().stop()

    def _on_agent_message(self, msg: BaseMessage) -> None:
        if not isinstance(msg, AIMessage):
            return
        content = msg.content
        if not isinstance(content, str):
            return
        if content.strip() == "":
            return
        if self._text_subject is None:
            return

        log_bench_event("speak_invoke")
        with self._first_chunk_lock:
            self._first_chunk_pending = True
        self._text_subject.on_next(content)

    def _on_audio_chunk(self, _chunk: Any) -> None:
        """Fire ``first_audio_out`` exactly once per ``_on_agent_message`` call."""
        with self._first_chunk_lock:
            if not self._first_chunk_pending:
                return
            self._first_chunk_pending = False
        log_bench_event("first_audio_out", tool="speak")


__all__ = ["AssistantSpeechNodeJa"]
```

- [ ] **Step 2: ユニットテストを走らせて全部 pass することを確認**

Run: `python -m pytest tests/agents/skills/test_assistant_speech_node_ja.py -v`
Expected: PASS (8 tests)。

- [ ] **Step 3: コミット**

```bash
git add dimos/agents/skills/speak_skill_ja.py
git commit -m "feat(local-tts): replace JapaneseSpeakSkill with AssistantSpeechNodeJa"
```

---

## Task 3: blueprint の差し替え

**Files:**
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/_common_agentic_ja.py`

- [ ] **Step 1: import 行を差し替え**

`_common_agentic_ja.py` の該当行:

```python
from dimos.agents.skills.speak_skill_ja import JapaneseSpeakSkill
```

を以下に書き換え:

```python
from dimos.agents.skills.speak_skill_ja import AssistantSpeechNodeJa
```

- [ ] **Step 2: blueprint list の `JapaneseSpeakSkill.blueprint()` を差し替え**

```python
    JapaneseSpeakSkill.blueprint(),
```

を以下に書き換え:

```python
    AssistantSpeechNodeJa.blueprint(),
```

- [ ] **Step 3: blueprint が import エラーなくロードできることを確認**

Run: `python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import unitree_go2_agentic_local_tts; print(unitree_go2_agentic_local_tts)"`
Expected: blueprint オブジェクトが表示され、例外なし。

- [ ] **Step 4: コミット**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/_common_agentic_ja.py
git commit -m "feat(local-tts): wire AssistantSpeechNodeJa in blueprint"
```

---

## Task 4: system prompt から speak 言及を除去

**Files:**
- Modify: `dimos/agents/system_prompt_ja.py`

- [ ] **Step 1: 「# コミュニケーション」段落を差し替え**

`dimos/agents/system_prompt_ja.py` 内の以下のブロック:

```
# コミュニケーション
ユーザはスピーカー経由であなたの声を聞きますが、テキストは見えません。ユーザに何か伝えたい時は **必ず `speak` ツールを呼び出してください**。テキストだけで返答してはいけません — テキストはユーザには見えません。`speak` を呼ばない応答は無音と同じで、ユーザは何も受け取れません。発話は簡潔に、1〜2文で、日本語で。
```

を以下に置き換え:

```
# コミュニケーション
ユーザはスピーカー経由であなたの声を聞きます。ユーザに伝えたいことは応答テキストとしてそのまま日本語で書いてください。応答テキストはそのまま読み上げられます。発話は簡潔に、1〜2文で。tool だけを実行して黙りたい時は応答テキストを空にして tool_calls だけを返してください。
```

- [ ] **Step 2: 「## デリバリーとピックアップ」内の `speak` 言及を書き換え**

該当ブロック:

```
## デリバリーとピックアップ
- デリバリー: `speak` で到着を告げ、`wait` で 5 秒待ってから次の行動に移る
- ピックアップ: `speak` で手伝いを依頼し、応答を待ってから次の行動に移る
```

を以下に置き換え:

```
## デリバリーとピックアップ
- デリバリー: 応答テキストで到着を告げ、`wait` で 5 秒待ってから次の行動に移る
- ピックアップ: 応答テキストで手伝いを依頼し、応答を待ってから次の行動に移る
```

- [ ] **Step 3: prompt が import できることと、`speak` 単語が消えていることを確認**

Run:
```bash
python -c "from dimos.agents.system_prompt_ja import SYSTEM_PROMPT_JA; assert 'speak' not in SYSTEM_PROMPT_JA, 'speak still present'; print('OK')"
```
Expected: `OK` が表示される。

- [ ] **Step 4: コミット**

```bash
git add dimos/agents/system_prompt_ja.py
git commit -m "feat(local-tts): drop speak tool references from system prompt"
```

---

## Task 5: 既存 bench テストの回帰確認

**Files:**
- Test: `tests/scripts/test_bench_agentic_local_tts_analyzer.py`（変更なし、走らせるだけ）
- Test: `tests/agents/skills/test_assistant_speech_node_ja.py`（同上）

- [ ] **Step 1: bench analyzer テストが引き続き通ることを確認**

Run: `python -m pytest tests/scripts/test_bench_agentic_local_tts_analyzer.py -v`
Expected: PASS（bench event 名 `speak_invoke` / `first_audio_out` を流用しているので影響なし）。

- [ ] **Step 2: 関連テスト一式を走らせる**

Run: `python -m pytest tests/agents/skills/ tests/scripts/test_bench_agentic_local_tts_analyzer.py -v`
Expected: 全て PASS。

- [ ] **Step 3: 失敗があればそこで止めて修正、なければ次へ**

このタスクではコミットなし（コードは触らない確認のみ）。

---

## Task 6: 手動 smoke test（任意・実機ある時のみ）

このタスクは pytest では検証しきれない実機挙動の確認。CI では skip し、ローカルで実機 / ステージング環境がある時に実施。

- [ ] **Step 1: blueprint を起動**

Run: `python -m dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts`（プロジェクトの実行慣習に従う）
Expected: 例外なく起動し、MCP server / マイク / スピーカーが初期化される。

- [ ] **Step 2: 音声入力 → 発話の確認**

Daneel に「こんにちは」と話しかける。
Expected: 応答テキストがスピーカーから日本語で読み上げられる。

- [ ] **Step 3: tool 単独ターンが無音になることを確認**

「真っ直ぐ歩いて」など、tool だけで完結し response text が空になりやすい指示を出す。
Expected: 動作はするが、不要な発話は出ない。

- [ ] **Step 4: multi-step（tool → 発話 → tool）の自然さを確認**

「玄関に行って、着いたら教えて」のような指示。
Expected: navigate 中に余分な発話なし、到着後に応答テキストで知らせる発話が出る。

このタスクではコミットなし。

---

## Self-Review Notes

- Spec の「コンポーネント」「データフロー」「エラー処理」「テスト」セクションは Task 1–5 で全カバー。
- Spec の「触らないもの」リストは Task 中で明示的に変更対象から除外（`speak_skill.py`、`mcp_client.py`、`system_prompt.py`、英語 / voice-live blueprint）。
- `speak_invoke` / `first_audio_out` の event 名と `tool="speak"` ラベルは Task 2 の `_on_agent_message` / `_on_audio_chunk` で維持。Task 5 で analyzer 互換を CI 確認。
- field 名 `agent` と autoconnect の `(name, type)` 一致は Task 2 のコード（`agent: In[BaseMessage]`）と Task 3 の blueprint 起動確認で担保。
