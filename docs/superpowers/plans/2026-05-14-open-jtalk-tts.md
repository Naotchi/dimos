# `DIMOS_TTS=open_jtalk` TTS Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `DIMOS_TTS=open_jtalk` backend to `SpeakSkill` that synthesizes Japanese speech via `pyopenjtalk` and plays it through the existing `SounddeviceAudioOutput` pipeline.

**Architecture:** New `OpenJTalkTTSNode` mirrors `OpenAITTSNode` (background thread + queue, implements `AbstractTextConsumer` / `AbstractAudioEmitter` / `AbstractTextEmitter`, emits `AudioEvent` from synthesized waveform). `SpeakSkill.start()` gains a third branch that imports `pyopenjtalk` lazily, instantiates the node, and wires `SounddeviceAudioOutput(sample_rate=48000)` to it.

**Tech Stack:** Python 3.12, `pyopenjtalk`, `reactivex`, `numpy`, existing dimos audio nodes, `pytest`, `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-05-14-open-jtalk-tts-design.md`

---

## File Structure

- **Create** `dimos/stream/audio/tts/node_open_jtalk.py` — `OpenJTalkTTSNode` class.
- **Create** `dimos/stream/audio/tts/tests/test_node_open_jtalk.py` — node unit tests.
- **Modify** `dimos/agents/skills/speak_skill.py` — add `open_jtalk` dispatch branch, update type annotation and error message.
- **Modify** `dimos/agents/skills/tests/test_speak_skill_env.py` — add `open_jtalk` branch tests, update error-message test.
- **Modify** `README.md` — document `DIMOS_TTS=open_jtalk` in the Japanese voice setup section.

---

## Task 1: OpenJTalkTTSNode — failing test

**Files:**
- Create: `dimos/stream/audio/tts/tests/test_node_open_jtalk.py`

- [ ] **Step 1: Write the failing test**

```python
# Copyright 2025-2026 Dimensional Inc.
"""OpenJTalkTTSNode unit tests."""

from __future__ import annotations

import sys
import time
import types
from unittest import mock

import numpy as np
import pytest
from reactivex import Subject


def _install_fake_pyopenjtalk() -> mock.MagicMock:
    """Install a fake pyopenjtalk module before import."""
    fake = types.ModuleType("pyopenjtalk")
    waveform = np.zeros(4800, dtype=np.float64)
    tts_mock = mock.MagicMock(return_value=(waveform, 48000))
    fake.tts = tts_mock  # type: ignore[attr-defined]
    sys.modules["pyopenjtalk"] = fake
    return tts_mock


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached within timeout")


def test_consume_text_emits_audio_event() -> None:
    tts_mock = _install_fake_pyopenjtalk()
    from dimos.stream.audio.base import AudioEvent
    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    received: list[AudioEvent] = []
    node.emit_audio().subscribe(on_next=received.append)
    node.consume_text(text_subject)

    text_subject.on_next("こんにちは")

    _wait_for(lambda: len(received) == 1)
    event = received[0]
    assert event.sample_rate == 48000
    assert event.channels == 1
    assert isinstance(event.data, np.ndarray)
    tts_mock.assert_called_once_with("こんにちは")

    node.dispose()


def test_emit_text_passes_through_input() -> None:
    _install_fake_pyopenjtalk()
    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    spoken: list[str] = []
    node.emit_text().subscribe(on_next=spoken.append)
    node.consume_text(text_subject)

    text_subject.on_next("テスト")

    _wait_for(lambda: spoken == ["テスト"])
    node.dispose()


def test_dispose_stops_worker_thread() -> None:
    _install_fake_pyopenjtalk()
    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    node.consume_text(text_subject)
    assert node.processing_thread is not None
    assert node.processing_thread.is_alive()

    node.dispose()

    node.processing_thread.join(timeout=2.0)
    assert not node.processing_thread.is_alive()


def test_synthesis_error_is_logged_and_does_not_kill_worker() -> None:
    fake = types.ModuleType("pyopenjtalk")
    fake.tts = mock.MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[attr-defined]
    sys.modules["pyopenjtalk"] = fake

    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    node.consume_text(text_subject)
    text_subject.on_next("error case")

    _wait_for(lambda: fake.tts.call_count >= 1)  # type: ignore[attr-defined]
    assert node.processing_thread is not None
    assert node.processing_thread.is_alive()
    node.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest dimos/stream/audio/tts/tests/test_node_open_jtalk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dimos.stream.audio.tts.node_open_jtalk'`.

---

## Task 2: Implement OpenJTalkTTSNode

**Files:**
- Create: `dimos/stream/audio/tts/node_open_jtalk.py`

- [ ] **Step 1: Write the implementation**

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

import threading
import time

import pyopenjtalk  # type: ignore[import-not-found]
from reactivex import Observable, Subject

from dimos.stream.audio.base import AbstractAudioEmitter, AudioEvent
from dimos.stream.audio.text.base import AbstractTextConsumer, AbstractTextEmitter
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

SAMPLE_RATE = 48000


class OpenJTalkTTSNode(AbstractTextConsumer, AbstractAudioEmitter, AbstractTextEmitter):
    """Japanese TTS node backed by pyopenjtalk.

    Consumes text, synthesizes Japanese speech via the bundled Mei HTS voice,
    emits AudioEvent objects on emit_audio(), and re-emits the spoken text on
    emit_text(). Mirrors OpenAITTSNode's background-thread + queue pattern.
    """

    def __init__(self) -> None:
        self.audio_subject = Subject()  # type: ignore[var-annotated]
        self.text_subject = Subject()  # type: ignore[var-annotated]
        self.subscription = None
        self.processing_thread: threading.Thread | None = None
        self.is_running = True
        self.text_queue: list[str] = []
        self.queue_lock = threading.Lock()

    def emit_audio(self) -> Observable:  # type: ignore[type-arg]
        return self.audio_subject

    def emit_text(self) -> Observable:  # type: ignore[type-arg]
        return self.text_subject

    def consume_text(self, text_observable: Observable) -> "AbstractTextConsumer":  # type: ignore[type-arg]
        logger.info("Starting OpenJTalkTTSNode")
        self.processing_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processing_thread.start()
        self.subscription = text_observable.subscribe(  # type: ignore[assignment]
            on_next=self._queue_text,
            on_error=lambda e: logger.error(f"Error in OpenJTalkTTSNode: {e}"),
        )
        return self

    def _queue_text(self, text: str) -> None:
        if not text.strip():
            return
        with self.queue_lock:
            self.text_queue.append(text)

    def _process_queue(self) -> None:
        while self.is_running:
            text_to_process: str | None = None
            with self.queue_lock:
                if self.text_queue:
                    text_to_process = self.text_queue.pop(0)
            if text_to_process is not None:
                self._synthesize_speech(text_to_process)
            else:
                time.sleep(0.05)

    def _synthesize_speech(self, text: str) -> None:
        try:
            waveform, sample_rate = pyopenjtalk.tts(text)
            self.text_subject.on_next(text)
            audio_event = AudioEvent(
                data=waveform,
                sample_rate=SAMPLE_RATE,
                timestamp=time.time(),
                channels=1,
            )
            logger.debug(f"OpenJTalk audio sample rate: {sample_rate}Hz")
            self.audio_subject.on_next(audio_event)
        except Exception as e:
            logger.error(f"Error synthesizing speech: {e}")

    def dispose(self) -> None:
        logger.info("Disposing OpenJTalkTTSNode")
        self.is_running = False
        with self.queue_lock:
            self.text_queue.clear()
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=2.0)
        if self.subscription:
            self.subscription.dispose()
            self.subscription = None
        self.audio_subject.on_completed()
        self.text_subject.on_completed()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest dimos/stream/audio/tts/tests/test_node_open_jtalk.py -v`
Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add dimos/stream/audio/tts/node_open_jtalk.py dimos/stream/audio/tts/tests/test_node_open_jtalk.py
git commit -m "feat(tts): add OpenJTalkTTSNode for Japanese synthesis"
```

---

## Task 3: SpeakSkill env dispatch — failing tests

**Files:**
- Modify: `dimos/agents/skills/tests/test_speak_skill_env.py`

- [ ] **Step 1: Add failing tests for the open_jtalk branch and updated error message**

Append the following tests to `dimos/agents/skills/tests/test_speak_skill_env.py` (after the existing tests):

```python
@mock.patch.dict(os.environ, {"DIMOS_TTS": "open_jtalk"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.SounddeviceAudioOutput")
def test_start_open_jtalk_uses_open_jtalk_node(sd_cls: mock.MagicMock) -> None:
    sd_cls.return_value = mock.MagicMock()
    with mock.patch(
        "dimos.stream.audio.tts.node_open_jtalk.OpenJTalkTTSNode"
    ) as node_cls:
        node_cls.return_value = mock.MagicMock()
        skill = _make_skill()
        try:
            skill.start()
            node_cls.assert_called_once_with()
            sd_cls.assert_called_once_with(sample_rate=48000)
        finally:
            skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "bogus"}, clear=False)
def test_start_invalid_env_message_lists_open_jtalk() -> None:
    skill = _make_skill()
    try:
        with pytest.raises(ValueError, match="open_jtalk"):
            skill.start()
    finally:
        skill.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest dimos/agents/skills/tests/test_speak_skill_env.py -v`
Expected: 2 failures — `test_start_open_jtalk_uses_open_jtalk_node` (ValueError "must be 'openai' or 'pyttsx3'") and `test_start_invalid_env_message_lists_open_jtalk` (message does not contain `open_jtalk`).

---

## Task 4: Wire open_jtalk branch into SpeakSkill

**Files:**
- Modify: `dimos/agents/skills/speak_skill.py:39` (type annotation) and `:48-59` (dispatch block)

- [ ] **Step 1: Update the dispatch and type annotation**

Replace lines 39 and 48–59 of `dimos/agents/skills/speak_skill.py`.

Change line 39 from:

```python
    _tts_node: OpenAITTSNode | PyTTSNode | None = None
```

to:

```python
    _tts_node: OpenAITTSNode | PyTTSNode | "OpenJTalkTTSNode" | None = None
```

Replace the dispatch block (current lines 48–59):

```python
        backend = os.environ.get("DIMOS_TTS", "pyttsx3").lower()
        if backend == "openai":
            self._tts_node = OpenAITTSNode(speed=1.2, voice=Voice.ONYX)
            self._audio_output = SounddeviceAudioOutput(sample_rate=24000)
            self._audio_output.consume_audio(self._tts_node.emit_audio())
        elif backend == "pyttsx3":
            self._tts_node = PyTTSNode(voice_lang=self.config.voice_lang)
            self._audio_output = None
        else:
            raise ValueError(
                f"DIMOS_TTS must be 'openai' or 'pyttsx3', got: {backend!r}"
            )
```

with:

```python
        backend = os.environ.get("DIMOS_TTS", "pyttsx3").lower()
        if backend == "openai":
            self._tts_node = OpenAITTSNode(speed=1.2, voice=Voice.ONYX)
            self._audio_output = SounddeviceAudioOutput(sample_rate=24000)
            self._audio_output.consume_audio(self._tts_node.emit_audio())
        elif backend == "pyttsx3":
            self._tts_node = PyTTSNode(voice_lang=self.config.voice_lang)
            self._audio_output = None
        elif backend == "open_jtalk":
            from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

            self._tts_node = OpenJTalkTTSNode()
            self._audio_output = SounddeviceAudioOutput(sample_rate=48000)
            self._audio_output.consume_audio(self._tts_node.emit_audio())
        else:
            raise ValueError(
                f"DIMOS_TTS must be 'openai', 'pyttsx3', or 'open_jtalk', got: {backend!r}"
            )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest dimos/agents/skills/tests/test_speak_skill_env.py -v`
Expected: all tests pass (including the two new ones from Task 3).

- [ ] **Step 3: Run the wider test suite to confirm no regressions**

Run: `pytest dimos/stream/audio/tts/tests dimos/agents/skills/tests -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add dimos/agents/skills/speak_skill.py dimos/agents/skills/tests/test_speak_skill_env.py
git commit -m "feat(speak-skill): wire DIMOS_TTS=open_jtalk backend"
```

---

## Task 5: README documentation

**Files:**
- Modify: `README.md` (Japanese voice setup section)

- [ ] **Step 1: Locate the Japanese voice setup section**

Run: `grep -n "DIMOS_TTS\|Japanese\|pyttsx3" README.md`

Identify the section that describes `DIMOS_TTS=pyttsx3` Japanese voice setup (added in commit `42a34cf6b`).

- [ ] **Step 2: Add an `open_jtalk` entry**

Insert (immediately after the `pyttsx3` Japanese setup notes, preserving existing surrounding prose):

```markdown
### `DIMOS_TTS=open_jtalk` (recommended for Japanese)

`pyopenjtalk` bundles the Mei HTS voice and synthesizes Japanese without any
OS-level voice install:

```bash
pip install pyopenjtalk
export DIMOS_TTS=open_jtalk
```

Audio is played through the same `SounddeviceAudioOutput` pipeline used by the
OpenAI backend (sample rate 48 kHz).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document DIMOS_TTS=open_jtalk backend"
```

---

## Self-Review

- **Spec coverage:** Architecture (Task 2), dispatch (Task 4), parameters (Task 2 — fixed in code), data flow (Tasks 2+4), error handling (Task 2 `_synthesize_speech`, Task 4 `else` branch), testing (Tasks 1, 3), documentation (Task 5). All spec sections covered.
- **Placeholder scan:** No TBD/TODO. All code shown inline.
- **Type consistency:** `OpenJTalkTTSNode` constructor: no args. Used identically in Task 3 test (`node_cls.assert_called_once_with()`), Task 4 dispatch (`OpenJTalkTTSNode()`). `SAMPLE_RATE = 48000` matches `sample_rate=48000` in Task 4 dispatch and Task 3 test assertion. `emit_audio` / `emit_text` / `consume_text` / `dispose` signatures match the abstract bases and the test usage.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-14-open-jtalk-tts.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
