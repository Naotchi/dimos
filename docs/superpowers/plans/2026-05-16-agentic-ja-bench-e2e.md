# agentic-ja E2E Bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement reproducible end-to-end latency bench for the `unitree_go2_agentic_ja` blueprint, measuring "speech-end → first agent response audio" and "speech-end → first motion tool", with a fixture replay path that injects wav directly into `audio_subject`.

**Architecture:** Add a small fork-local `dimos/agents/bench_ja/` module that owns a `turn_id` `ContextVar` and a chokepoint logger (`log_bench_event`). All three fork-local `*_ja.py` files (`web_human_input_ja`, `mcp/mcp_client_ja`, `skills/speak_skill_ja`) are rewritten to emit through this chokepoint. A new `scripts/replay_agentic_ja.py` boots the blueprint in-process and feeds wav fixtures; the existing `scripts/bench_agentic_ja.py` is extended to compute per-turn e2e metrics and per-category aggregates. Upstream files are untouched.

**Tech Stack:** Python 3.12, ReactiveX (`reactivex`), LangChain/LangGraph (existing dependency), `pyopenjtalk` (existing), `pyyaml` (already in repo), `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-16-agentic-ja-bench-e2e-design.md`

---

## File Structure

**New (fork-local):**
- `dimos/agents/bench_ja/__init__.py`
- `dimos/agents/bench_ja/turn_context.py`
- `tests/agents/bench_ja/__init__.py`
- `tests/agents/bench_ja/test_turn_context.py`
- `scripts/replay_agentic_ja.py`
- `scripts/gen_fixtures_agentic_ja.py`
- `tests/scripts/__init__.py` (if missing)
- `tests/scripts/test_bench_agentic_ja_analyzer.py`
- `tests/bench_fixtures/agentic_ja/fixtures.yaml`
- `tests/bench_fixtures/agentic_ja/README.md`
- `tests/bench_fixtures/agentic_ja/*.wav` (generated)

**Rewritten (fork-local):**
- `dimos/agents/web_human_input_ja.py` — STT timing via `log_bench_event`, `audio_subject` accessor
- `dimos/agents/mcp/mcp_client_ja.py` — `turn_id`-aware logging, `first_motion_tool` detection
- `dimos/agents/skills/speak_skill_ja.py` — `first_audio_out` tap on `_tts_node.emit_audio()`
- `scripts/bench_agentic_ja.py` — e2e metrics, per-category aggregates, run-dir auto-pick

**Untouched (upstream):** `dimos/agents/mcp/mcp_server.py`, `dimos/agents/mcp/mcp_client.py`, `dimos/agents/skills/speak_skill.py`, `dimos/agents/web_human_input.py`.

---

## Task 1: `bench_ja/turn_context.py` with TDD

**Files:**
- Create: `dimos/agents/bench_ja/__init__.py`
- Create: `dimos/agents/bench_ja/turn_context.py`
- Create: `tests/agents/bench_ja/__init__.py`
- Create: `tests/agents/bench_ja/test_turn_context.py`

- [ ] **Step 1.1: Write failing tests**

Create `tests/agents/bench_ja/__init__.py` as empty file.

Create `tests/agents/bench_ja/test_turn_context.py`:

```python
"""Unit tests for bench_ja.turn_context."""

from unittest.mock import patch

from dimos.agents.bench_ja import turn_context


def test_current_turn_is_none_by_default():
    turn_context.reset()
    assert turn_context.current_turn() is None


def test_new_turn_sets_and_returns_id():
    tid = turn_context.new_turn()
    assert isinstance(tid, str)
    assert len(tid) == 12
    assert turn_context.current_turn() == tid


def test_new_turn_replaces_previous():
    a = turn_context.new_turn()
    b = turn_context.new_turn()
    assert a != b
    assert turn_context.current_turn() == b


def test_reset_clears():
    turn_context.new_turn()
    turn_context.reset()
    assert turn_context.current_turn() is None


def test_log_bench_event_injects_kind_turn_and_t():
    turn_context.reset()
    tid = turn_context.new_turn()
    with patch.object(turn_context, "logger") as mock_logger:
        turn_context.log_bench_event("stt_done", duration_s=0.42, audio_seconds=1.5)
    assert mock_logger.info.call_count == 1
    args, kwargs = mock_logger.info.call_args
    assert kwargs["event_kind"] == "stt_done"
    assert kwargs["turn_id"] == tid
    assert isinstance(kwargs["t"], float)
    assert kwargs["duration_s"] == 0.42
    assert kwargs["audio_seconds"] == 1.5


def test_log_bench_event_without_turn():
    turn_context.reset()
    with patch.object(turn_context, "logger") as mock_logger:
        turn_context.log_bench_event("user_audio_end", audio_seconds=1.0)
    _, kwargs = mock_logger.info.call_args
    assert kwargs["turn_id"] is None
```

- [ ] **Step 1.2: Run tests, verify they fail**

Run: `pytest tests/agents/bench_ja/test_turn_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dimos.agents.bench_ja'`

- [ ] **Step 1.3: Implement `turn_context.py`**

Create `dimos/agents/bench_ja/__init__.py`:

```python
"""Bench instrumentation utilities for the agentic_ja blueprint family.

Owns the per-turn correlation id (ContextVar) and a single chokepoint for
emitting structured bench events so the JSONL schema cannot drift across
the rewritten *_ja.py files.
"""

from dimos.agents.bench_ja.turn_context import (
    current_turn,
    log_bench_event,
    new_turn,
    reset,
)

__all__ = ["current_turn", "log_bench_event", "new_turn", "reset"]
```

Create `dimos/agents/bench_ja/turn_context.py`:

```python
"""Per-turn correlation id and chokepoint logger for agentic_ja bench events."""

from __future__ import annotations

import contextvars
import time
import uuid
from typing import Any

from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dimos_bench_ja_turn_id", default=None
)


def new_turn() -> str:
    """Issue a fresh 12-char turn id, set it on the contextvar, return it."""
    tid = uuid.uuid4().hex[:12]
    _turn_id.set(tid)
    return tid


def current_turn() -> str | None:
    return _turn_id.get()


def reset() -> None:
    _turn_id.set(None)


def log_bench_event(kind: str, **fields: Any) -> None:
    """Emit a structured bench event with consistent envelope fields.

    Always sets: event_kind, turn_id (from contextvar), t (perf_counter).
    Caller-supplied fields take precedence over envelope keys if they collide.
    """
    payload: dict[str, Any] = {
        "event_kind": kind,
        "turn_id": current_turn(),
        "t": round(time.perf_counter(), 6),
    }
    payload.update(fields)
    logger.info(f"bench {kind}", **payload)
```

- [ ] **Step 1.4: Run tests, verify they pass**

Run: `pytest tests/agents/bench_ja/test_turn_context.py -v`
Expected: PASS (6 passed)

- [ ] **Step 1.5: Commit**

```bash
git add dimos/agents/bench_ja tests/agents/bench_ja
git commit -m "feat(bench_ja): add turn_context with ContextVar + log_bench_event chokepoint"
```

---

## Task 2: Rewrite `web_human_input_ja.py` with `_audio_subject` accessor and turn-aware STT timing

**Files:**
- Rewrite: `dimos/agents/web_human_input_ja.py`

**Context:** Current implementation (verified before plan): WebUI button-press → `audio_subject` (a `reactivex.Subject[AudioEvent]`) → `AudioNormalizer` → `WhisperNode`. STT timing is done with an ad-hoc FIFO (`_make_stt_timer`). We replace the FIFO with a single `on_text` tap that pulls `audio_seconds` off the FIFO and logs through `log_bench_event`. `audio_subject` was previously a local in `start()`; promote it to `self._audio_subject` so the replay script can publish to it.

- [ ] **Step 2.1: Write the new file**

Rewrite `dimos/agents/web_human_input_ja.py`:

```python
#!/usr/bin/env python
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

"""Japanese WebInput variant: Whisper(ja) + bench instrumentation.

Differs from upstream WebInput in two ways:
1. WhisperNode is configured with language='ja'.
2. STT timing is emitted via dimos.agents.bench_ja.log_bench_event so the
   bench event schema stays consistent across the *_ja.py files.

Also exposes self._audio_subject so bench replay scripts can publish
fixture audio into the pipeline without going through the WebUI.
"""

from __future__ import annotations

import time
from threading import Thread
from typing import TYPE_CHECKING, Any

import reactivex as rx
import reactivex.operators as ops

from dimos.agents.bench_ja import log_bench_event
from dimos.agents.web_human_input import WebInput
from dimos.core.core import rpc
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.stream.audio.stt.node_whisper import WhisperNode
from dimos.utils.logging_config import setup_logger
from dimos.web.robot_web_interface import RobotWebInterface

if TYPE_CHECKING:
    from dimos.stream.audio.base import AudioEvent

logger = setup_logger()


def _make_stt_timer() -> tuple[Any, Any]:
    """Return (audio_tap, text_tap) operators that emit stt_done bench events.

    The audio tap pushes (t0, audio_seconds) onto a FIFO when an AudioEvent
    enters Whisper; the text tap pops it when the transcription emerges and
    logs the round-trip via bench_ja.log_bench_event. Whisper is synchronous
    (one audio in -> one text out), so a plain list FIFO is sufficient.
    """
    pending: list[dict[str, Any]] = []

    def on_audio(event: "AudioEvent") -> None:
        sr = float(getattr(event, "sample_rate", 0) or 16000)
        n = int(getattr(event.data, "shape", [0])[0] or 0)
        pending.append(
            {
                "t0": time.perf_counter(),
                "audio_seconds": round(n / sr, 4) if sr else None,
            }
        )

    def on_text(text: str) -> None:
        info = pending.pop(0) if pending else {"t0": time.perf_counter(), "audio_seconds": None}
        elapsed = time.perf_counter() - info["t0"]
        log_bench_event(
            "stt_done",
            duration_s=round(elapsed, 4),
            audio_seconds=info.get("audio_seconds"),
            text_len=len(text),
        )

    return ops.do_action(on_audio), ops.do_action(on_text)


class JapaneseWebInput(WebInput):
    """WebInput that runs Whisper in Japanese and emits bench-schema events.

    Implementation mirrors upstream WebInput.start() so we don't depend on the
    parent's internals; we just hold our own references (notably to
    self._audio_subject) so bench replay scripts can drive the pipeline.
    """

    _audio_subject: rx.subject.Subject  # exposed for bench replay; treat as internal
    _web_interface: RobotWebInterface
    _human_transport: pLCMTransport
    _thread: Thread

    @rpc
    def start(self) -> None:
        from dimos.core.module import Module

        Module.start(self)

        self._human_transport = pLCMTransport("/human_input")
        self._audio_subject = rx.subject.Subject()

        self._web_interface = RobotWebInterface(
            port=5555,
            text_streams={"agent_responses": rx.subject.Subject()},
            audio_subject=self._audio_subject,
        )

        normalizer = AudioNormalizer()
        stt_node = WhisperNode(modelopts={"language": "ja", "fp16": False})

        normalizer.consume_audio(self._audio_subject.pipe(ops.share()))
        audio_tap, text_tap = _make_stt_timer()
        stt_node.consume_audio(normalizer.emit_audio().pipe(audio_tap))

        unsub = self._web_interface.query_stream.subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        unsub = stt_node.emit_text().pipe(text_tap).subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        self._thread = Thread(target=self._web_interface.run, daemon=True)
        self._thread.start()

        logger.info("JapaneseWebInput started at http://localhost:5555")
```

- [ ] **Step 2.2: Confirm import works**

Run: `python -c "from dimos.agents.web_human_input_ja import JapaneseWebInput; print(JapaneseWebInput)"`
Expected: prints the class repr, no import error.

- [ ] **Step 2.3: Commit**

```bash
git add dimos/agents/web_human_input_ja.py
git commit -m "refactor(web_human_input_ja): route STT timing through bench_ja, expose _audio_subject"
```

---

## Task 3: Rewrite `mcp_client_ja.py` with turn-aware logging and `first_motion_tool`

**Files:**
- Rewrite: `dimos/agents/mcp/mcp_client_ja.py`

**Context:** `TimedMcpClient` overrides `_process_message` (upstream `McpClient._process_message`). Inside the LangGraph stream loop, the `agent` node emits messages that may carry `tool_calls`. We detect the first non-`speak` `tool_call` per turn and emit `first_motion_tool`. The replay script calls `new_turn()` before publishing audio, but `_process_message` runs on the McpClient's internal thread (`_thread_loop`) so the ContextVar may not propagate; we therefore expose a thin helper that lets the caller re-set the turn id before processing or, more robustly, we always read `current_turn()` from this thread which inherits the contextvar via `ContextVar` thread-local semantics (Python contextvars *are* per-thread; we'll verify in the integration step and add explicit propagation if needed).

For MVP, since the replay script sets `turn_id` and then waits for ack, we cannot rely on thread-inheritance. We use a simpler scheme: a process-global "latest turn id" set by `log_bench_event("user_audio_end", ...)` and read by other emitters. Since contextvars are per-thread, we'll add a small fallback in `turn_context.py` later if needed — but first let's verify in Task 8.

Actually, the design says ContextVar. For thread crossing, ContextVars must be explicitly copied via `contextvars.copy_context()`. The pragmatic choice for MVP: in addition to the ContextVar, keep a module-level "last_turn_id" string updated by `new_turn()`, and `current_turn()` returns ContextVar value or falls back to last_turn_id. This is a tiny safety net.

- [ ] **Step 3.1: Add cross-thread fallback to `turn_context.py`**

Edit `dimos/agents/bench_ja/turn_context.py`:

Replace the entire file with:

```python
"""Per-turn correlation id and chokepoint logger for agentic_ja bench events."""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from typing import Any

from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dimos_bench_ja_turn_id", default=None
)

# Cross-thread fallback. The McpClient and SpeakSkill run on their own threads
# and don't inherit the replay driver's ContextVar. We keep a process-wide
# "latest turn id" updated by new_turn() so those threads can correlate.
# Single-turn-at-a-time semantics are assumed (the agent is single-threaded
# per turn anyway), so this fallback is safe.
_latest_lock = threading.Lock()
_latest_turn_id: str | None = None


def new_turn() -> str:
    """Issue a fresh 12-char turn id, set both the contextvar and process-wide fallback."""
    global _latest_turn_id
    tid = uuid.uuid4().hex[:12]
    _turn_id.set(tid)
    with _latest_lock:
        _latest_turn_id = tid
    return tid


def current_turn() -> str | None:
    """Return ContextVar value if set on this thread, else the latest process-wide id."""
    val = _turn_id.get()
    if val is not None:
        return val
    with _latest_lock:
        return _latest_turn_id


def reset() -> None:
    """Clear the ContextVar (this thread) and the process-wide fallback."""
    global _latest_turn_id
    _turn_id.set(None)
    with _latest_lock:
        _latest_turn_id = None


def log_bench_event(kind: str, **fields: Any) -> None:
    """Emit a structured bench event with consistent envelope fields."""
    payload: dict[str, Any] = {
        "event_kind": kind,
        "turn_id": current_turn(),
        "t": round(time.perf_counter(), 6),
    }
    payload.update(fields)
    logger.info(f"bench {kind}", **payload)
```

- [ ] **Step 3.2: Add a test for the cross-thread fallback**

Append to `tests/agents/bench_ja/test_turn_context.py`:

```python
def test_new_turn_visible_from_another_thread():
    import threading

    turn_context.reset()
    tid = turn_context.new_turn()
    captured: list[str | None] = []

    def reader():
        captured.append(turn_context.current_turn())

    t = threading.Thread(target=reader)
    t.start()
    t.join()
    assert captured == [tid]
```

- [ ] **Step 3.3: Run tests**

Run: `pytest tests/agents/bench_ja/test_turn_context.py -v`
Expected: PASS (7 passed)

- [ ] **Step 3.4: Rewrite `mcp_client_ja.py`**

Rewrite `dimos/agents/mcp/mcp_client_ja.py`:

```python
#!/usr/bin/env python
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

"""McpClient subclass with per-step LLM/tool timing + first_motion_tool event.

Routes all bench events through dimos.agents.bench_ja.log_bench_event so the
schema is identical across the *_ja.py files (turn_id, t, event_kind).
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages.base import BaseMessage
from langgraph.graph.state import CompiledStateGraph

from dimos.agents.bench_ja import log_bench_event
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.utils import pretty_print_langchain_message


class TimedMcpClient(McpClient):
    """McpClient with bench instrumentation.

    Emits:
      - llm_step       : duration of each 'agent' node in the LangGraph stream
      - <node>_step    : duration of each non-'agent' node (typically 'tools')
      - first_motion_tool : first tool_call where tool name != 'speak', once per turn
      - turn_done      : total turn time, llm time, step count, tool call count
    """

    def _process_message(
        self, state_graph: CompiledStateGraph[Any, Any, Any, Any], message: BaseMessage
    ) -> None:
        self.agent_idle.publish(False)
        self._history.append(message)
        pretty_print_langchain_message(message)
        self.agent.publish(message)

        turn_t0 = time.perf_counter()
        step_t0 = time.perf_counter()
        step_idx = 0
        total_llm = 0.0
        n_tool_calls = 0
        motion_logged = False

        for update in state_graph.stream({"messages": self._history}, stream_mode="updates"):
            for node_name, node_output in update.items():
                elapsed = time.perf_counter() - step_t0
                msgs = node_output.get("messages", []) if isinstance(node_output, dict) else []
                kind = "llm_step" if node_name == "agent" else f"{node_name}_step"

                if node_name == "agent":
                    total_llm += elapsed
                    for m in msgs:
                        tool_calls = getattr(m, "tool_calls", []) or []
                        n_tool_calls += len(tool_calls)
                        if not motion_logged:
                            for tc in tool_calls:
                                tool_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                                if tool_name and tool_name != "speak":
                                    log_bench_event("first_motion_tool", tool=tool_name)
                                    motion_logged = True
                                    break

                log_bench_event(
                    kind,
                    node=node_name,
                    duration_s=round(elapsed, 4),
                    step_idx=step_idx,
                    n_messages=len(msgs),
                )
                step_idx += 1

                for msg in msgs:
                    self._history.append(msg)
                    pretty_print_langchain_message(msg)
                    self.agent.publish(msg)
                step_t0 = time.perf_counter()

        log_bench_event(
            "turn_done",
            duration_s=round(time.perf_counter() - turn_t0, 4),
            llm_s=round(total_llm, 4),
            n_steps=step_idx,
            n_tool_calls=n_tool_calls,
        )

        if self._message_queue.empty():
            self.agent_idle.publish(True)


__all__ = ["TimedMcpClient"]
```

- [ ] **Step 3.5: Verify import**

Run: `python -c "from dimos.agents.mcp.mcp_client_ja import TimedMcpClient; print(TimedMcpClient)"`
Expected: class repr, no import error.

- [ ] **Step 3.6: Commit**

```bash
git add dimos/agents/bench_ja/turn_context.py tests/agents/bench_ja/test_turn_context.py dimos/agents/mcp/mcp_client_ja.py
git commit -m "feat(mcp_client_ja): emit first_motion_tool and route timings through bench_ja"
```

---

## Task 4: Rewrite `speak_skill_ja.py` to emit `first_audio_out`

**Files:**
- Rewrite: `dimos/agents/skills/speak_skill_ja.py`

**Context:** Currently `JapaneseSpeakSkill.start()` does `self._audio_output.consume_audio(self._tts_node.emit_audio())`. To detect "first audio chunk played per `speak()` invocation", we tap `_tts_node.emit_audio()` with a `do_action`. The flag "first chunk pending for this speak() call" is set when `speak()` is invoked (we override `speak()` to set it before delegating). On the first audio chunk, we log `first_audio_out` and clear the flag.

- [ ] **Step 4.1: Rewrite the file**

Rewrite `dimos/agents/skills/speak_skill_ja.py`:

```python
#!/usr/bin/env python
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

"""Japanese SpeakSkill variant: pyopenjtalk TTS + first_audio_out bench event."""

from __future__ import annotations

import threading
from typing import Any

import reactivex.operators as ops

from dimos.agents.bench_ja import log_bench_event
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode


class JapaneseSpeakSkill(SpeakSkill):
    """SpeakSkill that synthesizes Japanese via pyopenjtalk and emits first_audio_out.

    - Overrides start() to bypass SpeakSkill.start (which would init OpenAITTSNode)
      and wire OpenJTalkTTSNode -> SounddeviceAudioOutput at 48 kHz.
    - Taps the audio stream with do_action so the first chunk emitted after each
      speak() call fires a 'first_audio_out' bench event.
    """

    _first_chunk_pending: bool
    _first_chunk_lock: threading.Lock

    @rpc
    def start(self) -> None:
        # Skip SpeakSkill.start (which constructs OpenAITTSNode); call grandparent.
        Module.start(self)

        self._first_chunk_pending = False
        self._first_chunk_lock = threading.Lock()

        self._tts_node = OpenJTalkTTSNode()  # type: ignore[assignment]
        self._audio_output = SounddeviceAudioOutput(sample_rate=48000)

        tapped = self._tts_node.emit_audio().pipe(ops.do_action(self._on_audio_chunk))
        self._audio_output.consume_audio(tapped)

    def _on_audio_chunk(self, _chunk: Any) -> None:
        """Fire first_audio_out exactly once per speak() invocation."""
        with self._first_chunk_lock:
            if not self._first_chunk_pending:
                return
            self._first_chunk_pending = False
        log_bench_event("first_audio_out", tool="speak")

    def speak(self, text: str, blocking: bool = True) -> str:
        """Arm the first-chunk flag, then delegate to upstream speak()."""
        with self._first_chunk_lock:
            self._first_chunk_pending = True
        return super().speak(text, blocking=blocking)
```

- [ ] **Step 4.2: Verify import**

Run: `python -c "from dimos.agents.skills.speak_skill_ja import JapaneseSpeakSkill; print(JapaneseSpeakSkill)"`
Expected: class repr, no import error.

- [ ] **Step 4.3: Commit**

```bash
git add dimos/agents/skills/speak_skill_ja.py
git commit -m "feat(speak_skill_ja): emit first_audio_out on first TTS chunk per speak() call"
```

---

## Task 5: Fixture manifest + generator script

**Files:**
- Create: `tests/bench_fixtures/agentic_ja/fixtures.yaml`
- Create: `tests/bench_fixtures/agentic_ja/README.md`
- Create: `scripts/gen_fixtures_agentic_ja.py`

- [ ] **Step 5.1: Create `fixtures.yaml`**

Create `tests/bench_fixtures/agentic_ja/fixtures.yaml`:

```yaml
version: 1
fixtures:
  # speak_only: short greetings / Q&A that should yield 'speak' only.
  - id: speak_001
    category: speak_only
    wav: speak_001.wav
    text: "おはよう"
    notes: "short greeting"
  - id: speak_002
    category: speak_only
    wav: speak_002.wav
    text: "自己紹介してください"
    notes: "self introduction"
  - id: speak_003
    category: speak_only
    wav: speak_003.wav
    text: "今日の天気はどう"
    notes: "small talk"
  - id: speak_004
    category: speak_only
    wav: speak_004.wav
    text: "ありがとう"
    notes: "thanks"

  # motion_only: imperative motion commands. May still produce a brief speak
  # ack, but the primary intent is a motion tool.
  - id: motion_001
    category: motion_only
    wav: motion_001.wav
    text: "前に進んで"
    notes: "move forward"
  - id: motion_002
    category: motion_only
    wav: motion_002.wav
    text: "お座り"
    notes: "sit"
  - id: motion_003
    category: motion_only
    wav: motion_003.wav
    text: "立って"
    notes: "stand"
  - id: motion_004
    category: motion_only
    wav: motion_004.wav
    text: "右を向いて"
    notes: "turn right"

  # both: combined commands that should yield both speak and motion.
  - id: both_001
    category: both
    wav: both_001.wav
    text: "立ち上がって挨拶してください"
    notes: "stand + greet"
  - id: both_002
    category: both
    wav: both_002.wav
    text: "前に進んで、進んだら教えて"
    notes: "move + announce"
  - id: both_003
    category: both
    wav: both_003.wav
    text: "お座りして自己紹介して"
    notes: "sit + introduce"
  - id: both_004
    category: both
    wav: both_004.wav
    text: "右を向いて何が見えるか教えて"
    notes: "turn + report"
```

- [ ] **Step 5.2: Create README**

Create `tests/bench_fixtures/agentic_ja/README.md`:

```markdown
# agentic_ja bench fixtures

WAV fixtures for `scripts/replay_agentic_ja.py`.

- 16 kHz mono PCM WAV, synthesized from `text` field of `fixtures.yaml` via pyopenjtalk.
- Regenerate with `python scripts/gen_fixtures_agentic_ja.py`.
- Caveat: pyopenjtalk synthesis is what `JapaneseSpeakSkill` also uses, so Whisper may transcribe these unrealistically well versus human speech. Acceptable for in-stack regression bench; not a substitute for human-recorded fixtures when comparing STT providers.
```

- [ ] **Step 5.3: Inspect OpenJTalkTTSNode to learn how to synthesize a wav**

Run: `grep -n "def \|class \|sample_rate\|wav" /home/naoki/dimos/dimos/stream/audio/tts/node_open_jtalk.py | head -30`

Read the relevant section to understand:
- How to call the TTS to get raw PCM
- Sample rate it produces
- Whether it can write a wav directly or just produces audio events

The generator must write **16 kHz mono PCM WAV**. If `OpenJTalkTTSNode` emits at a different rate, resample to 16 kHz (use `scipy.signal.resample_poly` or `numpy`-based linear interp).

- [ ] **Step 5.4: Write the generator script**

Create `scripts/gen_fixtures_agentic_ja.py`:

```python
#!/usr/bin/env python
"""Generate 16 kHz mono WAV fixtures from fixtures.yaml using pyopenjtalk.

Idempotent: skips entries whose target wav already exists. Run after editing
`text` fields in fixtures.yaml.

Usage:
    python scripts/gen_fixtures_agentic_ja.py
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pyopenjtalk
import yaml

FIXTURE_DIR = Path("tests/bench_fixtures/agentic_ja")
TARGET_SR = 16000


def synth_to_wav(text: str, out_path: Path) -> None:
    """Synthesize `text` with pyopenjtalk, resample to 16kHz mono, write WAV."""
    audio, src_sr = pyopenjtalk.tts(text)
    # pyopenjtalk returns float64 in roughly [-32768, 32767] (int16 scale).
    audio = np.asarray(audio, dtype=np.float64)

    if src_sr != TARGET_SR:
        # Polyphase resampling.
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(src_sr), TARGET_SR)
        audio = resample_poly(audio, TARGET_SR // g, int(src_sr) // g)

    # Clip and convert to int16.
    audio = np.clip(audio, -32768.0, 32767.0).astype(np.int16)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_SR)
        w.writeframes(audio.tobytes())


def main(argv: list[str]) -> int:
    manifest_path = FIXTURE_DIR / "fixtures.yaml"
    if not manifest_path.exists():
        sys.exit(f"missing {manifest_path}")

    manifest = yaml.safe_load(manifest_path.read_text())
    fixtures = manifest.get("fixtures", [])

    n_generated = 0
    n_skipped = 0
    for fx in fixtures:
        wav_path = FIXTURE_DIR / fx["wav"]
        if wav_path.exists():
            n_skipped += 1
            continue
        print(f"generating {wav_path} ({fx['text']!r})")
        synth_to_wav(fx["text"], wav_path)
        n_generated += 1

    print(f"done: generated={n_generated}, skipped(existing)={n_skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 5.5: Run the generator**

Run: `python scripts/gen_fixtures_agentic_ja.py`
Expected: prints `generating ...` for 12 files, then `done: generated=12, skipped(existing)=0`.

Verify: `ls tests/bench_fixtures/agentic_ja/*.wav | wc -l` → 12.

Verify a sample wav: `python -c "import wave; w = wave.open('tests/bench_fixtures/agentic_ja/speak_001.wav'); print(w.getframerate(), w.getnchannels(), w.getnframes())"`
Expected: `16000 1 <some frame count>` (non-zero frames).

- [ ] **Step 5.6: Commit**

```bash
git add tests/bench_fixtures/agentic_ja scripts/gen_fixtures_agentic_ja.py
git commit -m "feat(bench): add agentic_ja fixture manifest, generator, and 12 starter wavs"
```

---

## Task 6: Replay script

**Files:**
- Create: `scripts/replay_agentic_ja.py`

**Context:** The CLI entrypoint `dimos.robot.cli.dimos.run` (in `dimos/robot/cli/dimos.py:195`) shows the canonical boot sequence:

```python
from dimos.utils.logging_config import set_run_log_dir
from dimos.core.coordination.module_coordinator import ModuleCoordinator

set_run_log_dir(log_dir)  # MUST be before build; routes main.jsonl to log_dir
coordinator = ModuleCoordinator.build(blueprint, kwargs={})
# coordinator.loop() would block; we skip it and orchestrate from main thread.
```

`ModuleCoordinator` exposes:
- `coordinator.get_instance(SomeModuleClass) -> ModuleProxy`
- `coordinator._deployed_modules` (dict by class) — for debugging
- `coordinator.stop()` for teardown

Default module deployment is `"python"` (see `dimos/core/module.py:113`). Whether this runs the module in-process or in a worker subprocess depends on the `WorkerManager` registered for `"python"`. The bench needs `audio_subject` to be reachable from the bench process; if modules run in subprocesses, the `ModuleProxy` returned by `get_instance` may not expose `_audio_subject` directly.

**Risk and fallback:** If the proxy does not expose `_audio_subject`, the implementer must either (a) force the `JapaneseWebInput` and `TimedMcpClient` modules to a same-process deployment (if dimos supports that — likely via a config / kwarg on the coordinator), or (b) inject audio via the WebSocket interface that `RobotWebInterface` already serves on port 5555. Option (b) is more work but is deployment-agnostic. Decide at Step 6.3 based on a quick test.

- [ ] **Step 6.1: Test reachability of `_audio_subject` from outside JapaneseWebInput's process**

Run a minimal probe to see whether `coordinator.get_instance(JapaneseWebInput)` returns an object with `_audio_subject` you can call `.on_next(...)` on:

```python
# probe_reachability.py (throwaway, do not commit)
import time
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.utils.logging_config import set_run_log_dir
from pathlib import Path
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_ja import unitree_go2_agentic_ja
from dimos.agents.web_human_input_ja import JapaneseWebInput

set_run_log_dir(Path("logs/_probe"))
coord = ModuleCoordinator.build(unitree_go2_agentic_ja, kwargs={})
time.sleep(2)
proxy = coord.get_instance(JapaneseWebInput)
print("proxy type:", type(proxy).__name__)
print("has _audio_subject:", hasattr(proxy, "_audio_subject"))
coord.stop()
```

Run: `python probe_reachability.py`
Expected outcomes:
- `has _audio_subject: True` → continue with audio_subject injection (Step 6.2).
- `has _audio_subject: False` → drop into WebSocket-injection fallback (Step 6.2-alt).

Delete the probe script when done; the result determines which branch of Step 6.2 to implement.

- [ ] **Step 6.2: Write the replay script (audio_subject path)**

Create `scripts/replay_agentic_ja.py`:

```python
#!/usr/bin/env python
"""Replay wav fixtures through the unitree_go2_agentic_ja blueprint.

Boots the blueprint in-process, looks up the JapaneseWebInput instance, and
publishes fixture wavs to its _audio_subject. Emits a bench event
(user_audio_end) immediately after the last chunk is published so the
analyzer can compute end-to-end latencies relative to that timestamp.

Usage:
    python scripts/replay_agentic_ja.py \
        --fixtures tests/bench_fixtures/agentic_ja/fixtures.yaml \
        --runs 3 --warmup 1
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np
import yaml

from dimos.agents.bench_ja import log_bench_event, new_turn, reset
from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.agents.web_human_input_ja import JapaneseWebInput
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_ja import (
    unitree_go2_agentic_ja,
)
from dimos.stream.audio.base import AudioEvent
from dimos.utils.logging_config import set_run_log_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", default="tests/bench_fixtures/agentic_ja/fixtures.yaml")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--realtime", action="store_true",
                   help="Publish chunks with sleep matching playback (default: burst).")
    p.add_argument("--chunk-ms", type=int, default=200)
    p.add_argument("--turn-timeout", type=float, default=30.0)
    p.add_argument("--out", default=None,
                   help="Override log run dir (default: logs/{ts}-bench-agentic-ja).")
    return p.parse_args()


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr


def chunked(audio: np.ndarray, sr: int, chunk_ms: int) -> Iterator[np.ndarray]:
    step = max(1, int(sr * chunk_ms / 1000))
    for i in range(0, len(audio), step):
        yield audio[i : i + step]


def fixture_iter(fixtures: list[dict], runs: int, warmup: int, shuffle: bool):
    import random
    order = list(range(len(fixtures)))
    for run_idx in range(runs):
        if shuffle:
            random.shuffle(order)
        for j in order:
            fx = fixtures[j]
            yield {
                **fx,
                "run_idx": run_idx,
                "warmup": run_idx < warmup,
            }


def configure_log_dir(out_override: str | None) -> Path:
    if out_override:
        path = Path(out_override)
    else:
        ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        path = Path("logs") / f"{ts}-bench-agentic-ja"
    path.mkdir(parents=True, exist_ok=True)
    set_run_log_dir(path)
    return path


def boot_blueprint() -> tuple[ModuleCoordinator, JapaneseWebInput, TimedMcpClient]:
    """Build the blueprint and return (coordinator, web_input_proxy, mcp_client_proxy).

    Mirrors dimos.robot.cli.dimos.run minus daemon/loop bits: we want the
    modules running but not the blocking event loop, so this thread can drive
    the bench loop.
    """
    coordinator = ModuleCoordinator.build(unitree_go2_agentic_ja, kwargs={})
    web_input = coordinator.get_instance(JapaneseWebInput)
    mcp_client = coordinator.get_instance(TimedMcpClient)
    return coordinator, web_input, mcp_client


def wait_idle(client: TimedMcpClient, idle: threading.Event, timeout: float) -> bool:
    idle.clear()
    # Replace with actual agent_idle subscription wired in main().
    return idle.wait(timeout=timeout)


def main() -> int:
    args = parse_args()
    out_dir = configure_log_dir(args.out)
    print(f"[replay] logging to {out_dir}", flush=True)

    fx_path = Path(args.fixtures)
    manifest = yaml.safe_load(fx_path.read_text())
    fixtures = manifest["fixtures"]

    coordinator, web_input, mcp_client = boot_blueprint()

    # Subscribe to agent_idle. The event flips True when idle, False on busy.
    idle_event = threading.Event()

    def on_idle(is_idle: bool) -> None:
        if is_idle:
            idle_event.set()
        else:
            idle_event.clear()

    mcp_client.agent_idle.subscribe(on_idle)

    if not idle_event.wait(timeout=60.0):
        print("[replay] timed out waiting for initial agent_idle", file=sys.stderr)
        return 2

    fixtures_iter = list(fixture_iter(fixtures, args.runs, args.warmup, args.shuffle))
    print(f"[replay] {len(fixtures_iter)} runs scheduled", flush=True)

    for i, fx in enumerate(fixtures_iter):
        idle_event.clear()
        # Will reset once we know agent is idle before next fixture.
        if not idle_event.wait(timeout=args.turn_timeout):
            # Best-effort: continue even if previous turn timed out.
            print(f"[replay] WARN: idle wait timed out before fx {fx['id']}", file=sys.stderr)

        wav_path = fx_path.parent / fx["wav"]
        audio, sr = load_wav(wav_path)
        audio_seconds = round(len(audio) / sr, 4)
        chunk_step = max(1, int(sr * args.chunk_ms / 1000))

        idle_event.clear()
        for chunk in chunked(audio, sr, args.chunk_ms):
            web_input._audio_subject.on_next(  # noqa: SLF001 — bench-only hook
                AudioEvent(data=chunk, sample_rate=sr)
            )
            if args.realtime:
                time.sleep(len(chunk) / sr)

        # t=0 is now: last chunk has been published.
        reset()
        new_turn()
        log_bench_event(
            "user_audio_end",
            audio_seconds=audio_seconds,
            fixture_id=fx["id"],
            category=fx["category"],
            run_idx=fx["run_idx"],
            warmup=fx["warmup"],
        )

        if not idle_event.wait(timeout=args.turn_timeout):
            print(f"[replay] WARN: turn {fx['id']} timed out", file=sys.stderr)
            log_bench_event(
                "turn_timeout",
                fixture_id=fx["id"],
                run_idx=fx["run_idx"],
            )

    print("[replay] done", flush=True)
    coordinator.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6.2-alt: WebSocket fallback (only if Step 6.1 showed `_audio_subject` is unreachable)**

If the proxy doesn't expose `_audio_subject`, replace the `audio_subject.on_next(...)` call in the loop with a WebSocket client that connects to `ws://localhost:5555/<audio-endpoint>` (find the exact path by inspecting `dimos/web/robot_web_interface.py`) and sends PCM frames in the same format the WebUI uses. The rest of the script — fixture iteration, `user_audio_end` emission, `agent_idle` watching — stays identical. Document the chosen path in the script's module docstring.

- [ ] **Step 6.3: Commit scaffold**

```bash
git add scripts/replay_agentic_ja.py
git commit -m "feat(bench): replay_agentic_ja with audio injection and user_audio_end"
```

- [ ] **Step 6.4: End-to-end dry run on one fixture pass**

Run: `python scripts/replay_agentic_ja.py --runs 1 --warmup 0`
Expected: completes 12 turns without crashing; `logs/<ts>-bench-agentic-ja/main.jsonl` contains lines with `event_kind` in {`user_audio_end`, `stt_done`, `llm_step`, `tools_step`, `first_motion_tool`, `first_audio_out`, `turn_done`}.

If anything fails, iterate on `boot_blueprint()` / `configure_log_dir()` and commit again with a fix message.

---

## Task 7: Analyzer rewrite (`scripts/bench_agentic_ja.py`)

**Files:**
- Rewrite: `scripts/bench_agentic_ja.py`
- Create: `tests/scripts/__init__.py` (empty, if not present)
- Create: `tests/scripts/test_bench_agentic_ja_analyzer.py`

The analyzer must:
- Accept an optional run-dir argument; default to latest `logs/*-bench-agentic-ja/`, fallback to `logs/*-unitree-go2-agentic-ja/`.
- Read `main.jsonl`, group by `turn_id`, attach `mcp_tool:*` events by timestamp range.
- Compute per-turn `e2e_response_s`, `e2e_motion_s`, `stt_s`, `llm_total_s`, `tools_total_s`, `turn_total_s`.
- Drop `warmup=True` turns.
- Aggregate overall and per category (from `user_audio_end.category`).
- Output terminal table by default; `--json` and `--md` are optional flags (markdown can be deferred to a follow-up commit — print a placeholder if not implemented in MVP).

- [ ] **Step 7.1: Write tests against a synthetic JSONL**

Create `tests/scripts/__init__.py` if missing (empty file).

Create `tests/scripts/test_bench_agentic_ja_analyzer.py`:

```python
"""Unit tests for the bench_agentic_ja analyzer."""

import json
from pathlib import Path

import pytest

from scripts.bench_agentic_ja import (
    build_turns,
    compute_per_turn_metrics,
    aggregate,
    _percentile,
)


def _line(d):
    return json.dumps(d) + "\n"


@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    """Two turns: one with both speak and motion, one with only speak."""
    p = tmp_path / "main.jsonl"
    lines = [
        # Turn A: speak + motion
        _line({"event_kind": "user_audio_end", "turn_id": "A", "t": 100.0,
               "fixture_id": "fx1", "category": "both", "run_idx": 0, "warmup": False,
               "audio_seconds": 1.2}),
        _line({"event_kind": "stt_done", "turn_id": "A", "t": 100.3,
               "duration_s": 0.3, "audio_seconds": 1.2, "text_len": 5}),
        _line({"event_kind": "llm_step", "turn_id": "A", "t": 100.9,
               "duration_s": 0.6, "node": "agent", "step_idx": 0, "n_messages": 1}),
        _line({"event_kind": "first_motion_tool", "turn_id": "A", "t": 100.95, "tool": "move"}),
        _line({"event_kind": "tools_step", "turn_id": "A", "t": 101.0,
               "duration_s": 0.05, "node": "tools", "step_idx": 1, "n_messages": 1}),
        _line({"event_kind": "first_audio_out", "turn_id": "A", "t": 101.1, "tool": "speak"}),
        # Upstream mcp_tool events have no turn_id; bucketed by timestamp.
        _line({"event": "MCP tool done", "t": 101.05, "tool": "move", "duration": 0.04}),
        _line({"event_kind": "turn_done", "turn_id": "A", "t": 101.5,
               "duration_s": 1.5, "llm_s": 0.6, "n_steps": 2, "n_tool_calls": 1}),

        # Turn B: speak only, warmup
        _line({"event_kind": "user_audio_end", "turn_id": "B", "t": 200.0,
               "fixture_id": "fx2", "category": "speak_only", "run_idx": 0, "warmup": True,
               "audio_seconds": 0.8}),
        _line({"event_kind": "stt_done", "turn_id": "B", "t": 200.2,
               "duration_s": 0.2, "audio_seconds": 0.8, "text_len": 3}),
        _line({"event_kind": "llm_step", "turn_id": "B", "t": 200.7,
               "duration_s": 0.5, "node": "agent", "step_idx": 0, "n_messages": 1}),
        _line({"event_kind": "first_audio_out", "turn_id": "B", "t": 200.8, "tool": "speak"}),
        _line({"event_kind": "turn_done", "turn_id": "B", "t": 201.0,
               "duration_s": 1.0, "llm_s": 0.5, "n_steps": 1, "n_tool_calls": 0}),
    ]
    p.write_text("".join(lines))
    return p


def test_build_turns_groups_by_turn_id(jsonl_path):
    turns = build_turns(jsonl_path)
    assert set(turns.keys()) == {"A", "B"}
    assert turns["A"]["user_audio_end"]["fixture_id"] == "fx1"
    assert turns["A"]["first_motion_tool"]["tool"] == "move"
    # mcp_tool:* gets bucketed by timestamp.
    assert any(e["tool"] == "move" for e in turns["A"]["mcp_tools"])


def test_compute_per_turn_metrics(jsonl_path):
    turns = build_turns(jsonl_path)
    metrics = compute_per_turn_metrics(turns)
    a = metrics["A"]
    assert a["e2e_response_s"] == pytest.approx(1.1)
    assert a["e2e_motion_s"] == pytest.approx(0.95)
    assert a["stt_s"] == pytest.approx(0.3)
    assert a["llm_total_s"] == pytest.approx(0.6)
    assert a["warmup"] is False
    assert a["category"] == "both"

    b = metrics["B"]
    assert b["e2e_response_s"] == pytest.approx(0.8)
    assert b["e2e_motion_s"] is None
    assert b["warmup"] is True


def test_aggregate_drops_warmup(jsonl_path):
    turns = build_turns(jsonl_path)
    metrics = compute_per_turn_metrics(turns)
    agg = aggregate(metrics)
    # Only turn A (non-warmup) contributes.
    assert agg["overall"]["e2e_response_s"]["n"] == 1
    assert agg["overall"]["e2e_motion_s"]["n"] == 1
    assert "speak_only" not in agg["by_category"]  # warmup dropped
    assert agg["by_category"]["both"]["e2e_response_s"]["n"] == 1


def test_percentile_basic():
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0
    assert _percentile([], 0.5) != _percentile([], 0.5)  # NaN
```

- [ ] **Step 7.2: Run tests, verify they fail (analyzer functions don't exist yet)**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v`
Expected: FAIL with ImportError for `build_turns` / `compute_per_turn_metrics` / `aggregate`.

- [ ] **Step 7.3: Implement the analyzer**

Rewrite `scripts/bench_agentic_ja.py`:

```python
#!/usr/bin/env python
"""Aggregate end-to-end latency from a unitree-go2-agentic-ja bench run.

Usage:
    python scripts/bench_agentic_ja.py [logs/<run-dir>] [--json FILE]

Without a run-dir argument, picks the latest logs/*-bench-agentic-ja/, then
falls back to logs/*-unitree-go2-agentic-ja/ for backward compatibility.

Reads main.jsonl and prints per-turn end-to-end latencies and stage breakdown:
  - e2e_response_s  (user_audio_end -> first_audio_out)
  - e2e_motion_s    (user_audio_end -> first_motion_tool)
  - stt_s / llm_total_s / tools_total_s / turn_total_s
  - mcp_tool:*      (per-tool durations from upstream "MCP tool done" events,
                     bucketed into turns by timestamp range)

Aggregates overall and per category (speak_only / motion_only / both).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * q))))
    return xs[i]


def _parse_duration(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"^([0-9.]+)\s*s?$", v.strip())
        if m:
            return float(m.group(1))
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def build_turns(jsonl_path: Path) -> dict[str, dict[str, Any]]:
    """Group bench events by turn_id; bucket mcp_tool:* by timestamp range."""
    rows = _read_jsonl(jsonl_path)
    turns: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"llm_steps": [], "tools_steps": [], "mcp_tools": []}
    )

    # First pass: events with turn_id.
    for row in rows:
        turn_id = row.get("turn_id")
        kind = row.get("event_kind")
        if not turn_id or not kind:
            continue
        if kind == "user_audio_end":
            turns[turn_id]["user_audio_end"] = row
        elif kind == "stt_done":
            turns[turn_id]["stt_done"] = row
        elif kind == "llm_step":
            turns[turn_id]["llm_steps"].append(row)
        elif kind in ("tools_step",) or (kind and kind.endswith("_step") and kind != "llm_step"):
            turns[turn_id]["tools_steps"].append(row)
        elif kind == "first_motion_tool":
            turns[turn_id]["first_motion_tool"] = row
        elif kind == "first_audio_out":
            turns[turn_id]["first_audio_out"] = row
        elif kind == "turn_done":
            turns[turn_id]["turn_done"] = row
        elif kind == "turn_timeout":
            turns[turn_id]["turn_timeout"] = row

    # Second pass: bucket mcp_tool:* by timestamp range [user_audio_end.t, turn_done.t].
    ranges = []
    for turn_id, data in turns.items():
        if "user_audio_end" in data and "turn_done" in data:
            ranges.append((data["user_audio_end"]["t"], data["turn_done"]["t"], turn_id))
    ranges.sort()

    for row in rows:
        if row.get("event") != "MCP tool done":
            continue
        t = row.get("t")
        if t is None:
            continue
        for t0, t1, turn_id in ranges:
            if t0 <= t <= t1:
                duration = _parse_duration(row.get("duration"))
                turns[turn_id]["mcp_tools"].append(
                    {"tool": row.get("tool", "?"), "duration": duration, "t": t}
                )
                break

    return dict(turns)


def compute_per_turn_metrics(turns: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Convert grouped events into per-turn numeric metrics."""
    metrics: dict[str, dict[str, Any]] = {}
    for turn_id, data in turns.items():
        ue = data.get("user_audio_end")
        if not ue:
            continue
        t0 = ue["t"]

        fao = data.get("first_audio_out")
        fmt = data.get("first_motion_tool")
        stt = data.get("stt_done")
        td = data.get("turn_done")

        metrics[turn_id] = {
            "fixture_id": ue.get("fixture_id"),
            "category": ue.get("category"),
            "run_idx": ue.get("run_idx"),
            "warmup": bool(ue.get("warmup")),
            "audio_seconds": ue.get("audio_seconds"),
            "e2e_response_s": (fao["t"] - t0) if fao else None,
            "e2e_motion_s": (fmt["t"] - t0) if fmt else None,
            "stt_s": _parse_duration(stt.get("duration_s")) if stt else None,
            "llm_total_s": sum(_parse_duration(s.get("duration_s")) or 0.0 for s in data.get("llm_steps", [])) or None,
            "tools_total_s": sum(_parse_duration(s.get("duration_s")) or 0.0 for s in data.get("tools_steps", [])) or None,
            "turn_total_s": _parse_duration(td.get("duration_s")) if td else None,
            "n_mcp_tools": len(data.get("mcp_tools", [])),
            "timeout": "turn_timeout" in data,
        }
    return metrics


def _summarize(values: list[float]) -> dict[str, float]:
    """n / mean / p50 / p95 / max / min over a list of finite floats."""
    finite = [v for v in values if v is not None and not math.isnan(v)]
    n = len(finite)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "p50": float("nan"),
                "p95": float("nan"), "max": float("nan"), "min": float("nan")}
    return {
        "n": n,
        "mean": statistics.fmean(finite),
        "p50": _percentile(finite, 0.5),
        "p95": _percentile(finite, 0.95),
        "max": max(finite),
        "min": min(finite),
    }


_METRIC_KEYS = ("e2e_response_s", "e2e_motion_s", "stt_s", "llm_total_s", "tools_total_s", "turn_total_s")


def aggregate(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate non-warmup turns: overall + per category + mcp_tool:* tallies."""
    live = [m for m in metrics.values() if not m["warmup"]]

    overall = {k: _summarize([m.get(k) for m in live]) for k in _METRIC_KEYS}

    by_cat: dict[str, dict[str, Any]] = defaultdict(dict)
    for cat in {m.get("category") for m in live if m.get("category")}:
        cat_metrics = [m for m in live if m.get("category") == cat]
        by_cat[cat] = {k: _summarize([m.get(k) for m in cat_metrics]) for k in _METRIC_KEYS}

    return {"overall": overall, "by_category": dict(by_cat)}


def _pick_run(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    candidates = sorted(Path("logs").glob("*-bench-agentic-ja"))
    if not candidates:
        candidates = sorted(Path("logs").glob("*-unitree-go2-agentic-ja"))
    if not candidates:
        sys.exit("no logs/*-bench-agentic-ja or logs/*-unitree-go2-agentic-ja runs found")
    return candidates[-1]


def _print_table(title: str, rows: dict[str, dict[str, float]]) -> None:
    print(f"\n== {title} ==")
    print(f"{'metric':<18} {'n':>4} {'mean':>8} {'p50':>8} {'p95':>8} {'max':>8} {'min':>8}")
    for k, s in rows.items():
        low_n = " [low-n]" if 0 < s["n"] < 5 else ""
        print(
            f"{k:<18} {s['n']:>4} {s['mean']:>8.3f} {s['p50']:>8.3f} "
            f"{s['p95']:>8.3f} {s['max']:>8.3f} {s['min']:>8.3f}{low_n}"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", default=None)
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args(argv[1:])

    run_dir = _pick_run(args.run_dir)
    jsonl = run_dir / "main.jsonl"
    if not jsonl.exists():
        sys.exit(f"missing {jsonl}")

    turns = build_turns(jsonl)
    metrics = compute_per_turn_metrics(turns)
    agg = aggregate(metrics)

    live = [m for m in metrics.values() if not m["warmup"]]
    print(f"run: {run_dir}")
    print(f"turns analyzed (non-warmup): {len(live)} / {len(metrics)} total")

    _print_table("overall", agg["overall"])
    for cat, rows in sorted(agg["by_category"].items()):
        _print_table(f"category: {cat}", rows)

    # Per-tool mcp_tool:* summary across all live turns.
    tool_buckets: dict[str, list[float]] = defaultdict(list)
    for m_id, m in metrics.items():
        if m["warmup"]:
            continue
        for e in turns[m_id].get("mcp_tools", []):
            if e["duration"] is not None:
                tool_buckets[f"mcp_tool:{e['tool']}"].append(e["duration"])
    if tool_buckets:
        tool_rows = {k: _summarize(v) for k, v in sorted(tool_buckets.items())}
        _print_table("mcp_tool:*", tool_rows)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "run_dir": str(run_dir),
            "n_turns": len(metrics),
            "n_live": len(live),
            "per_turn": metrics,
            "aggregate": agg,
        }, indent=2, default=str))
        print(f"\nJSON written to {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 7.4: Run tests, verify they pass**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7.5: Run analyzer on the dry-run log from Task 6**

Run: `python scripts/bench_agentic_ja.py`
Expected: terminal table with overall + per-category sections; numbers may be `nan`/`low-n` since the dry run was tiny.

- [ ] **Step 7.6: Commit**

```bash
git add scripts/bench_agentic_ja.py tests/scripts
git commit -m "feat(bench_agentic_ja): compute per-turn e2e metrics and category aggregates"
```

---

## Task 8: End-to-end verification

**Goal:** Run the full bench at production parameters and confirm meaningful aggregates appear.

- [ ] **Step 8.1: Run the full bench**

Run:
```bash
python scripts/replay_agentic_ja.py --runs 3 --warmup 1
```

Expected: replays 12 fixtures × 3 = 36 turns total, no Python tracebacks. The first 12 turns are flagged `warmup=true` and excluded by the analyzer. Output: `logs/<ts>-bench-agentic-ja/main.jsonl`.

- [ ] **Step 8.2: Analyze**

Run: `python scripts/bench_agentic_ja.py`

Expected output:
- `turns analyzed (non-warmup): 24 / 36 total`
- `overall` table with `e2e_response_s` having `n=24` (assuming every turn produced a speak)
- `e2e_motion_s` with `n` close to 16 (motion_only + both = 8 + 8 fixtures × 3 runs minus warmup), exact number depends on LLM behavior
- Per-category rows for `speak_only`, `motion_only`, `both`
- `mcp_tool:*` rows showing per-tool durations including `mcp_tool:speak`

- [ ] **Step 8.3: Sanity check the numbers**

Read the table and confirm:
- `e2e_response_s` p50 is plausibly between 0.5 and 3 s on a typical workstation.
- `stt_s + llm_total_s + tools_total_s` is roughly ≤ `turn_total_s` (with overhead).
- `e2e_motion_s` < `e2e_response_s` for `motion_only` turns where the agent moves before speaking, or vice versa for `both` turns where it speaks before moving. (Either pattern is acceptable; document what the LLM tends to do.)

If `e2e_motion_s` has `n=0`, the LLM may not be calling motion tools — inspect `main.jsonl` for missing `first_motion_tool` events. The fix may be tuning the system prompt or fixture text, not the bench itself; record findings in commit message.

- [ ] **Step 8.4: Commit JSON snapshot of the bench (optional but useful)**

Run: `python scripts/bench_agentic_ja.py --json docs/superpowers/specs/2026-05-16-agentic-ja-bench-baseline.json`

If the snapshot is small (< 50 KB) and useful as a baseline, commit it:

```bash
git add docs/superpowers/specs/2026-05-16-agentic-ja-bench-baseline.json
git commit -m "chore(bench): record baseline agentic_ja bench numbers"
```

Otherwise skip this step.

- [ ] **Step 8.5: Update spec status**

Edit `docs/superpowers/specs/2026-05-16-agentic-ja-bench-e2e-design.md`:

Change the first line from
```
Status: design approved 2026-05-16, awaiting implementation plan.
```
to
```
Status: implemented 2026-05-16. See docs/superpowers/plans/2026-05-16-agentic-ja-bench-e2e.md.
```

- [ ] **Step 8.6: Final commit**

```bash
git add docs/superpowers/specs/2026-05-16-agentic-ja-bench-e2e-design.md
git commit -m "docs(specs): mark agentic-ja bench e2e design as implemented"
```

---

## Done

The bench is now reproducible and ready for future cloud provider swap comparisons. The analyzer's event schema (`user_audio_end` / `first_audio_out` / `first_motion_tool` + `turn_id`) is reusable: any future blueprint that emits the same envelope through `dimos.agents.bench_ja.log_bench_event` will work with this analyzer unchanged.
