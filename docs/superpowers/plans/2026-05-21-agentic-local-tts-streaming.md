# Streaming TTS for unitree-go2-agentic-local-tts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM のトークンを文末で区切って TTS へ逐次投入し、最初の音声が出るまでの時間(TTFB)を縮める。

**Architecture:** producer(`TimedMcpClient`)が LangGraph のトークンチャンクを文へ分割し新規 `agent_text: Out[str]` に publish。consumer(`AssistantSpeechNodeJa`)は `config.streaming` に応じて `agent_text`(文)か従来の `agent`(完成メッセージ)のどちらか一方を購読。文分割は純関数 `SentenceAccumulator` に切り出す。全て fork 固有ファイルで完結し、TTS ノード(SBV2/VOICEVOX)と upstream 由来ファイルは無改修。

**Tech Stack:** Python, reactivex, langgraph(stream), pydantic(ModuleConfig), pytest。設計詳細は `docs/superpowers/specs/2026-05-20-agentic-local-tts-streaming-design.md`。

---

## File Structure

- Create: `dimos/stream/audio/tts/sentence_stream.py` — `SentenceAccumulator`(状態付き純関数。トークンデルタ→完成文)。
- Create: `tests/stream/audio/tts/test_sentence_stream.py` — 上記の単体テスト。
- Modify: `dimos/agents/skills/speak_skill_ja.py` — `agent_text: In[str]`、`streaming` config、`_select_input()`、`_speak()` 共有ヘルパ、`_on_agent_text()`。
- Modify: `tests/agents/skills/test_speak_skill_ja_impl_switch.py` への追加ではなく **新規** `tests/agents/skills/test_speak_skill_ja_streaming.py` — config トグル + 購読切替えテスト。
- Modify: `dimos/agents/mcp/mcp_client_ja.py` — `agent_text: Out[str]` 追加、stream ループで文 publish + ステップ末 flush。
- Create: `tests/agents/mcp/test_timed_mcp_client_streaming.py` — `agent_text` ポート宣言テスト。

---

## Task 1: SentenceAccumulator(文分割の純関数)

**Files:**
- Create: `dimos/stream/audio/tts/sentence_stream.py`
- Test: `tests/stream/audio/tts/test_sentence_stream.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stream/audio/tts/test_sentence_stream.py
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

"""Unit tests for the streaming sentence segmenter."""

from __future__ import annotations

from dimos.stream.audio.tts.sentence_stream import SentenceAccumulator


def test_no_boundary_buffers_and_emits_nothing():
    acc = SentenceAccumulator()
    assert acc.push("こんにちは") == []
    # buffered text is returned only on flush
    assert acc.flush() == "こんにちは"


def test_single_sentence_emitted_on_terminator():
    acc = SentenceAccumulator()
    assert acc.push("こんにちは。") == ["こんにちは。"]
    assert acc.flush() is None


def test_terminator_split_across_pushes():
    acc = SentenceAccumulator()
    assert acc.push("こんに") == []
    assert acc.push("ちは。残りは") == ["こんにちは。"]
    assert acc.flush() == "残りは"


def test_multiple_sentences_in_one_push():
    acc = SentenceAccumulator()
    assert acc.push("はい。いいえ？本当！") == ["はい。", "いいえ？", "本当！"]


def test_ascii_and_newline_boundaries():
    acc = SentenceAccumulator()
    assert acc.push("OK?\nNext line\n") == ["OK?", "Next line"]


def test_consecutive_terminators_kept_together():
    acc = SentenceAccumulator()
    assert acc.push("本当？！ さて") == ["本当？！"]
    assert acc.flush() == "さて"


def test_empty_push_returns_nothing():
    acc = SentenceAccumulator()
    assert acc.push("") == []
    assert acc.flush() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/stream/audio/tts/test_sentence_stream.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dimos.stream.audio.tts.sentence_stream'`

- [ ] **Step 3: Write minimal implementation**

```python
# dimos/stream/audio/tts/sentence_stream.py
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

"""Stateful sentence segmentation for streaming TTS input.

Accumulates LLM token deltas and yields complete sentences as soon as a
sentence-final boundary is seen, so a TTS node can start synthesizing the
first sentence before the full response finishes generating. Pure (no I/O,
no threads) so it is unit-testable in isolation — mirrors the design of
``dimos/agents/bench_ja/stream_tracker.py``.
"""

from __future__ import annotations

import re

# A run of non-terminator chars followed by one-or-more sentence-final
# terminators (JA + ASCII) or newlines. Consecutive terminators stay with
# the sentence they close (e.g. "本当？！").
_SENTENCE_RE = re.compile(r"[^。！？!?\n]*[。！？!?\n]+")


class SentenceAccumulator:
    """Buffer token deltas and emit complete sentences on boundaries."""

    def __init__(self) -> None:
        self._buf = ""

    def push(self, delta: str) -> list[str]:
        """Append ``delta`` and return any sentences now complete."""
        if not delta:
            return []
        self._buf += delta
        sentences: list[str] = []
        last_end = 0
        for m in _SENTENCE_RE.finditer(self._buf):
            sentence = m.group().strip()
            if sentence:
                sentences.append(sentence)
            last_end = m.end()
        self._buf = self._buf[last_end:]
        return sentences

    def flush(self) -> str | None:
        """Return the trailing partial sentence (if any) and clear the buffer."""
        rest = self._buf.strip()
        self._buf = ""
        return rest or None


__all__ = ["SentenceAccumulator"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/stream/audio/tts/test_sentence_stream.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add dimos/stream/audio/tts/sentence_stream.py tests/stream/audio/tts/test_sentence_stream.py
git commit -m "feat(tts): add SentenceAccumulator for streaming TTS input

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: consumer の streaming config + 購読切替え

**Files:**
- Modify: `dimos/agents/skills/speak_skill_ja.py`
- Test: `tests/agents/skills/test_speak_skill_ja_streaming.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/skills/test_speak_skill_ja_streaming.py
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

"""Verify AssistantSpeechNodeJa streaming toggle + input-port selection."""

from __future__ import annotations

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJa,
    AssistantSpeechNodeJaConfig,
)


def test_streaming_default_true(monkeypatch):
    monkeypatch.delenv("DIMOS_TTS_STREAMING", raising=False)
    assert AssistantSpeechNodeJaConfig().streaming is True


def test_streaming_env_seed_false(monkeypatch):
    monkeypatch.setenv("DIMOS_TTS_STREAMING", "0")
    assert AssistantSpeechNodeJaConfig().streaming is False


def test_streaming_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("DIMOS_TTS_STREAMING", "0")
    assert AssistantSpeechNodeJaConfig(streaming=True).streaming is True


def test_select_input_streaming_uses_agent_text():
    node = AssistantSpeechNodeJa(impl="open_jtalk", streaming=True)
    stream, cb = node._select_input()
    assert stream is node.agent_text
    assert cb == node._on_agent_text


def test_select_input_non_streaming_uses_agent():
    node = AssistantSpeechNodeJa(impl="open_jtalk", streaming=False)
    stream, cb = node._select_input()
    assert stream is node.agent
    assert cb == node._on_agent_message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agents/skills/test_speak_skill_ja_streaming.py -q`
Expected: FAIL — `AttributeError`/`ValidationError` on `streaming` (field not defined) and `_select_input`.

- [ ] **Step 3: Add `_default_tts_streaming`, the config field, the `agent_text` In, and `_select_input` / `_speak` / `_on_agent_text`**

In `dimos/agents/skills/speak_skill_ja.py`, add the env-seed helper next to `_default_tts_impl` (after the `_default_tts_impl` function, before `class VoicevoxParamsConfig`):

```python
# DIMOS_TTS_STREAMING seeds the `streaming` default for interactive runs.
# Explicit config / YAML / bench always wins (category A behavior toggle).
def _default_tts_streaming() -> bool:
    raw = os.environ.get("DIMOS_TTS_STREAMING")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")
```

Add the field to `AssistantSpeechNodeJaConfig` (after `idle_grace_s`):

```python
    streaming: bool = Field(default_factory=_default_tts_streaming)
```

Add the new input port to `AssistantSpeechNodeJa` (after `agent: In[BaseMessage]`):

```python
    agent_text: In[str]
```

Add `_select_input` (place it just above `start`):

```python
    def _select_input(self):
        """Pick (stream, callback) for this run based on ``config.streaming``.

        Streaming feeds pre-segmented sentences from the producer's
        ``agent_text`` port; non-streaming consumes whole ``AIMessage``s
        from ``agent`` (legacy behavior). Only one is ever subscribed, so
        no double-speak even though autoconnect wires both ports.
        """
        if self.config.streaming:
            return self.agent_text, self._on_agent_text
        return self.agent, self._on_agent_message
```

- [ ] **Step 4: Switch `start()` to use `_select_input`, and refactor the speak path into `_speak`**

Replace the subscription block at the end of `start()`:

```python
        self.register_disposable(
            Disposable(self.agent.subscribe(self._on_agent_message))
        )
```

with:

```python
        stream, callback = self._select_input()
        self.register_disposable(Disposable(stream.subscribe(callback)))
```

Replace the whole `_on_agent_message` method with the following three methods (a thin `AIMessage` adapter, a thin str adapter, and the shared `_speak`):

```python
    def _on_agent_message(self, msg: BaseMessage) -> None:
        if not isinstance(msg, AIMessage):
            return
        content = msg.content
        if not isinstance(content, str):
            return
        self._speak(content)

    def _on_agent_text(self, text: str) -> None:
        self._speak(text)

    def _speak(self, text: str) -> None:
        """Feed one text unit into TTS, firing utterance-start once per idle edge.

        ``speak_invoke`` / ``first_audio_out`` anchor on the idle->busy
        transition, so a streaming turn that submits many sentences logs a
        single utterance start (matching the bench ``speak_tts_s`` metric,
        which uses ``speak_invokes[0]``).
        """
        if text.strip() == "":
            return
        if self._text_subject is None:
            logger.warning(
                "AssistantSpeechNodeJa received agent message after stop(); dropping."
            )
            return

        with self._idle_lock:
            starting = self._is_idle
            if starting:
                self._is_idle = False
                if self._idle_timer is not None:
                    self._idle_timer.cancel()
                    self._idle_timer = None
                self.tts_idle.publish(False)
                log_bench_event("tts_idle", idle=False)
        if starting:
            log_bench_event("speak_invoke")
            with self._first_chunk_lock:
                self._first_chunk_pending = True
        self._text_subject.on_next(text)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/agents/skills/test_speak_skill_ja_streaming.py tests/agents/skills/test_speak_skill_ja_impl_switch.py tests/agents/skills/test_sbv2_params_config.py -q`
Expected: PASS (5 + 6 + 4 = 15 passed)

- [ ] **Step 6: Commit**

```bash
git add dimos/agents/skills/speak_skill_ja.py tests/agents/skills/test_speak_skill_ja_streaming.py
git commit -m "feat(speak_skill_ja): add streaming toggle + agent_text input

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: producer から文をストリーム発行

**Files:**
- Modify: `dimos/agents/mcp/mcp_client_ja.py`
- Test: `tests/agents/mcp/test_timed_mcp_client_streaming.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/mcp/test_timed_mcp_client_streaming.py
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

"""TimedMcpClient declares an agent_text Out[str] port for streaming TTS."""

from __future__ import annotations

from typing import get_args, get_origin, get_type_hints

from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.core.stream import Out


def test_timed_mcp_client_declares_agent_text_out_str():
    hints = get_type_hints(TimedMcpClient)
    assert "agent_text" in hints, "TimedMcpClient must declare agent_text port"
    ann = hints["agent_text"]
    assert get_origin(ann) is Out
    assert get_args(ann)[0] is str
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agents/mcp/test_timed_mcp_client_streaming.py -q`
Expected: FAIL — `AssertionError: TimedMcpClient must declare agent_text port`

- [ ] **Step 3: Add the port, import, and streaming logic**

In `dimos/agents/mcp/mcp_client_ja.py`:

Add imports near the top (after the existing `from dimos.agents.bench_ja...` imports):

```python
from dimos.core.stream import Out
from dimos.stream.audio.tts.sentence_stream import SentenceAccumulator
```

Declare the port on the class (immediately under `class TimedMcpClient(McpClient):`'s docstring, before `_process_message`):

```python
    agent_text: Out[str]
```

Initialize a per-turn accumulator at the start of `_process_message` (right after `first_tool_logged = False`):

```python
        sentence_acc = SentenceAccumulator()
```

Inside the `for mode, payload in state_graph.stream(...)` loop, **after** the existing `for ev in tracker.feed(mode, payload): ...` block and **before** `if mode != "updates": continue`, insert sentence emission for token chunks:

```python
            # Stream sentences out of LLM token chunks as soon as they form,
            # so the TTS node can start on sentence 1 before the turn ends.
            if mode == "messages":
                chunk, meta = payload
                node = meta.get("langgraph_node") if isinstance(meta, dict) else None
                if node in llm_nodes:
                    content = getattr(chunk, "content", "")
                    if isinstance(content, str) and content:
                        for sentence in sentence_acc.push(content):
                            self.agent_text.publish(sentence)
                continue
```

Inside the `for node_name, node_output in update.items():` block, **after** the existing `for msg in msgs: ... self.agent.publish(msg)` loop and before `step_t0 = time.perf_counter()`, flush the trailing partial sentence when an LLM step closes:

```python
                if node_name in llm_nodes:
                    rest = sentence_acc.flush()
                    if rest:
                        self.agent_text.publish(rest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agents/mcp/test_timed_mcp_client_streaming.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the existing mcp_client_ja-related tests to confirm no regression**

Run: `python -m pytest tests/agents/mcp/ -q`
Expected: PASS (no failures; collected tests pass)

- [ ] **Step 6: Commit**

```bash
git add dimos/agents/mcp/mcp_client_ja.py tests/agents/mcp/test_timed_mcp_client_streaming.py
git commit -m "feat(mcp_client_ja): publish sentence-level agent_text for streaming TTS

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: 全体テスト + 手動検証

**Files:** none (verification only)

- [ ] **Step 1: Run the full affected test set**

Run: `python -m pytest tests/stream/audio/tts/test_sentence_stream.py tests/agents/skills/ tests/agents/mcp/ -q`
Expected: PASS (no failures)

- [ ] **Step 2: Manual run — streaming ON (default)**

`_process_message` は LangGraph の `CompiledStateGraph` を要するため単体テスト対象外。実機で確認する。

Run(VOICEVOX エンジン or SBV2 が使える環境で):
```bash
python -m dimos.robot.unitree.go2.run --blueprint unitree-go2-agentic-local-tts
```
(起動コマンドはプロファイル/既存手順に合わせる。`configs/profiles/local-qwen-voicevox-sim/` を流用可。)

Expected 観察:
- 1発話の応答が**文ごとに区切れて順次再生**される(全文合成完了を待たない)。
- ログの `first_audio_out.t - speak_invoke.t`(= `speak_tts_s`)が一括モードより小さい。

- [ ] **Step 3: Manual run — streaming OFF(退避動作=現状)**

Run:
```bash
DIMOS_TTS_STREAMING=0 python -m dimos.robot.unitree.go2.run --blueprint unitree-go2-agentic-local-tts
```
Expected: 従来どおり**全文を一括合成**して1回で再生(回帰していないこと)。

- [ ] **Step 4: bench で ON/OFF 比較(任意)**

`scripts/bench_agentic_local_tts.py` のリプレイで `DIMOS_TTS_STREAMING=1` と `=0` を流し、`speak_tts_s` を比較。streaming で短縮されることを確認。

---

## Self-Review Notes

- **Spec coverage:** producer 文発行(Task 3)、consumer トグル+購読切替え(Task 2)、純関数分割(Task 1)、bench イベントの idle 端再定義(Task 2 `_speak`)、TTS ノード無改修(設計どおり変更なし)、手動検証(Task 4)。spec の全項目に対応タスクあり。
- **Type consistency:** `agent_text` は producer `Out[str]` / consumer `In[str]` で一致。`SentenceAccumulator.push -> list[str]` / `flush -> str | None` は Task 1 定義と Task 3 利用で一致。`_select_input -> (stream, callback)` は Task 2 内で一致。
- **Bench 互換:** `speak_tts_s` は `first_audio_out - speak_invokes[0]`(ターン先頭)で算出。`_speak` は idle→busy 端で1回だけ `speak_invoke`/`first_chunk_pending` を発火するため、先頭アンカーが保たれ集計が壊れない。
- **upstream 不変:** 触るのは fork 固有ファイル(`mcp_client_ja.py` / `speak_skill_ja.py` / 新規)のみ。base `mcp_client.py`・`node_output.py`・TTS ノードは無改修。
