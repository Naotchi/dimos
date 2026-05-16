# Voice Live Forced Speech Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee that the Azure Voice Live agent always speaks once per user turn (preface) and that query-type tools always trigger a spoken result report, by enforcing the flow in code instead of trusting the LLM.

**Architecture:** Add a per-response state machine to `AzureVoiceLiveAgent`. Buffer function calls instead of dispatching them inline. At `RESPONSE_DONE`, snapshot the state and spawn `_on_response_done` as an asyncio task so the event loop keeps draining incoming Voice Live events. From the task, force a preface `response.create` when needed, then dispatch buffered tool calls serially. Each tool checks `config.report_after_tools` to decide whether to issue a report response or stay silent.

**Tech Stack:** Python 3.11, asyncio, `azure.ai.voicelive.aio`, pytest, `unittest.mock` (`AsyncMock` / `MagicMock`).

**Spec:** `docs/superpowers/specs/2026-05-17-voice-live-forced-speech-design.md`

---

## File Structure

**Modified files:**

- `dimos/agents/realtime/azure_voice_live.py` — main logic. Add per-response state, replace inline `_dispatch_function_call` flow with buffered + serialized flow, add `_force_preface` / `_dispatch_and_wait` / `_on_response_done`. Rename existing `_response_text_buf` → `_resp_text_buf` and remove `_response_active`.
- `dimos/agents/realtime/prompts/japanese.py` — soften prompt now that code enforces order.

**Created files:**

- `tests/agents/realtime/__init__.py` — empty marker.
- `tests/agents/realtime/test_azure_voice_live_forced_speech.py` — unit tests covering the 8 scenarios from the spec.

---

## Task 1: Add `report_after_tools` config + env parser

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py:198-238`

- [ ] **Step 1: Add `_parse_tool_set` helper above `AzureVoiceLiveConfig`**

Insert just before `class AzureVoiceLiveConfig(ModuleConfig):` (around line 198):

```python
def _parse_tool_set(env_name: str, default: set[str]) -> set[str]:
    """Parse a comma-separated env var into a set, returning ``default`` if unset."""
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    return {s.strip() for s in raw.split(",") if s.strip()}
```

- [ ] **Step 2: Add `report_after_tools` field to `AzureVoiceLiveConfig`**

Add at the end of the config class body, after the `excluded_tools` field (around line 238):

```python
    # Tool names whose execution should be followed by a spoken result report.
    # Anything not in this set runs silently after a preface utterance.
    report_after_tools: set[str] = Field(
        default_factory=lambda: _parse_tool_set(
            f"{_ENV_PREFIX}REPORT_AFTER_TOOLS",
            {"observe", "current_time"},
        )
    )
```

- [ ] **Step 3: Sanity check the config loads**

Run: `python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveConfig; c = AzureVoiceLiveConfig(endpoint='x', api_key='y'); print(sorted(c.report_after_tools))"`
Expected: `['current_time', 'observe']`

Then: `DIMOS_AZURE_VOICE_LIVE_REPORT_AFTER_TOOLS='observe,foo' python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveConfig; c = AzureVoiceLiveConfig(endpoint='x', api_key='y'); print(sorted(c.report_after_tools))"`
Expected: `['foo', 'observe']`

- [ ] **Step 4: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(voice-live): add report_after_tools config"
```

---

## Task 2: Introduce per-response state and snapshot dataclass

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py:241-269` (init), and add a dataclass near `_PlaybackPacket` (around line 68)

- [ ] **Step 1: Add the snapshot dataclass**

After the existing `@dataclass class _PlaybackPacket:` block (line 68-71), insert:

```python
@dataclass
class _ResponseSnapshot:
    """Per-response state captured at RESPONSE_DONE before resetting."""
    trigger: str  # "user" | "tool_result" | "preface_forced"
    had_audio: bool
    pending_calls: list[tuple[str, str, str]]  # (call_id, name, args_json)
    text: str
```

- [ ] **Step 2: Replace old per-response state in `__init__`**

In `AzureVoiceLiveAgent.__init__` (around line 250-269), find:

```python
        self._response_active = False
        self._response_text_buf: list[str] = []
        self._first_audio_emitted = False
        self._first_tool_call_emitted = False
```

Replace with:

```python
        self._resp_had_audio: bool = False
        self._resp_pending_calls: list[tuple[str, str, str]] = []
        self._resp_text_buf: list[str] = []
        self._resp_trigger: str = "user"
        self._next_trigger: str | None = None
        self._resp_done_event: asyncio.Event | None = None
        self._first_audio_emitted = False
        self._first_tool_call_emitted = False
```

Note: `_response_active` is removed entirely; its only consumer (`response.cancel` guard) is replaced in Task 4.

- [ ] **Step 3: Verify imports**

The file already imports `asyncio` and `dataclass`. No new imports needed.

- [ ] **Step 4: Run unit tests in repo to confirm nothing else uses the old names**

Run: `grep -rn "_response_active\|_response_text_buf" /home/naoki/dimos/dimos/ /home/naoki/dimos/tests/ /home/naoki/dimos/scripts/`
Expected: matches only inside `azure_voice_live.py` (which we are about to rewrite). If anything else matches, stop and surface it before continuing.

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "refactor(voice-live): introduce per-response state and snapshot"
```

---

## Task 3: Rewire `_handle_event` to buffer function calls and dispatch via task

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py:541-596` (the `_handle_event` method)

- [ ] **Step 1: Add the helper `_snapshot_response_state` above `_handle_event`**

Insert just before `async def _handle_event` (around line 541):

```python
    def _snapshot_response_state(self) -> _ResponseSnapshot:
        snap = _ResponseSnapshot(
            trigger=self._resp_trigger,
            had_audio=self._resp_had_audio,
            pending_calls=list(self._resp_pending_calls),
            text="".join(self._resp_text_buf).strip(),
        )
        self._resp_had_audio = False
        self._resp_pending_calls = []
        self._resp_text_buf = []
        self._resp_trigger = "user"
        return snap
```

- [ ] **Step 2: Rewrite `_handle_event`**

Replace the entire `async def _handle_event(self, event: Any) -> None:` body (lines 541-596) with:

```python
    async def _handle_event(self, event: Any) -> None:
        et = event.type
        if et == ServerEventType.SESSION_UPDATED:
            logger.info("Voice Live session ready: %s", event.session.id)
            self._mic_active.set()
            self.agent_idle.publish(True)
        elif et == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            if self._playback is not None:
                self._playback.skip_pending()
            if self._conn is not None:
                try:
                    await self._conn.response.cancel()
                except Exception as exc:  # noqa: BLE001
                    if "no active response" not in str(exc).lower():
                        logger.warning("response.cancel failed: %s", exc)
            # Release any pending serialization waiter so dispatch can unwind.
            if self._resp_done_event is not None:
                self._resp_done_event.set()
            self._resp_pending_calls = []
            self.agent_idle.publish(False)
        elif et == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            transcript = (getattr(event, "transcript", "") or "").strip()
            if transcript:
                logger.info("user: %s", transcript)
                self.agent.publish(HumanMessage(content=transcript))
        elif et == ServerEventType.RESPONSE_CREATED:
            self._resp_trigger = self._next_trigger or "user"
            self._next_trigger = None
            self._resp_had_audio = False
            self._resp_pending_calls = []
            self._resp_text_buf = []
            self.agent_idle.publish(False)
        elif et == ServerEventType.RESPONSE_AUDIO_DELTA:
            if not self._first_audio_emitted:
                log_bench_event("first_audio_out")
                self._first_audio_emitted = True
            self._resp_had_audio = True
            if self._playback is not None:
                self._playback.enqueue(event.delta)
        elif et == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            self._resp_text_buf.append(event.delta or "")
        elif et == ServerEventType.RESPONSE_TEXT_DELTA:
            self._resp_text_buf.append(event.delta or "")
        elif et == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            if not self._first_tool_call_emitted:
                log_bench_event("first_tool_call", tool=event.name)
                self._first_tool_call_emitted = True
            self._resp_pending_calls.append(
                (event.call_id, event.name, event.arguments)
            )
        elif et == ServerEventType.RESPONSE_DONE:
            snap = self._snapshot_response_state()
            if snap.text:
                logger.info("assistant: %s", snap.text)
                self.agent.publish(AIMessage(content=snap.text))
            # Release any serialization waiter (preface/tool_result responses).
            if self._resp_done_event is not None:
                self._resp_done_event.set()
            if snap.trigger == "user":
                asyncio.create_task(self._on_response_done(snap))
            else:
                # preface_forced / tool_result: finalize only, no further routing.
                self.agent_idle.publish(True)
        elif et == ServerEventType.ERROR:
            logger.error("Voice Live error: %s", event.error.message)
        else:
            logger.debug("Voice Live unhandled event: %s", et)
```

- [ ] **Step 3: Remove now-dead `_dispatch_function_call` and `_run_function_call` methods**

Find `def _dispatch_function_call(...)` (around line 372) and `def _run_function_call(...)` (around line 379) — delete both methods entirely. They are replaced by `_dispatch_and_wait` / `_invoke_mcp` in Task 4.

Also delete `_send_function_output` (around line 397) — it is replaced inline in Task 4's `_dispatch_and_wait`.

- [ ] **Step 4: Add stub `_on_response_done` so the module still imports**

Add after `_snapshot_response_state`:

```python
    async def _on_response_done(self, snap: _ResponseSnapshot) -> None:
        # Filled in by Task 4.
        self.agent_idle.publish(True)
```

- [ ] **Step 5: Verify the module still imports**

Run: `python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "refactor(voice-live): buffer tool calls and route via task on RESPONSE_DONE"
```

---

## Task 4: Implement `_force_preface`, `_invoke_mcp`, `_dispatch_and_wait`, and full `_on_response_done`

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py` (replace stub + add helpers)

- [ ] **Step 1: Add `_invoke_mcp` synchronous helper**

Below `_dispatch_and_wait` location (will be placed near the other private helpers). Add:

```python
    def _invoke_mcp(self, name: str, arguments_json: str) -> str:
        assert self._mcp is not None
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except Exception as exc:  # noqa: BLE001
            return f"Error: invalid arguments JSON: {exc}"
        try:
            result = self._mcp.call_tool(name, args)
            return _extract_tool_text(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP tool %s failed", name)
            return f"Error: {exc}"
```

- [ ] **Step 2: Add `_force_preface`**

```python
    async def _force_preface(
        self, pending_calls: list[tuple[str, str, str]]
    ) -> None:
        if pending_calls:
            names = ", ".join(name for _, name, _ in pending_calls)
            instructions = (
                f"これから {names} を実行することを、"
                f"日本語で1〜2語の短い音声で伝えてください。ツールは呼ばない。"
            )
        else:
            instructions = "ユーザに日本語で短く一言返事をしてください。"

        self._next_trigger = "preface_forced"
        self._resp_done_event = asyncio.Event()
        await self._conn.response.create(
            response={
                "modalities": ["audio", "text"],
                "instructions": instructions,
            }
        )
        await self._resp_done_event.wait()
        self._resp_done_event = None
```

- [ ] **Step 3: Add `_dispatch_and_wait`**

```python
    async def _dispatch_and_wait(
        self, call_id: str, name: str, arguments_json: str
    ) -> None:
        assert self._loop is not None and self._tool_pool is not None
        output = await self._loop.run_in_executor(
            self._tool_pool, self._invoke_mcp, name, arguments_json
        )
        await self._conn.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            }
        )
        if name in self.config.report_after_tools:
            self._next_trigger = "tool_result"
            self._resp_done_event = asyncio.Event()
            await self._conn.response.create(
                response={
                    "modalities": ["audio", "text"],
                    "instructions": (
                        "直前のツール結果を日本語で1文に要約して"
                        "音声で報告してください。"
                    ),
                }
            )
            await self._resp_done_event.wait()
            self._resp_done_event = None
        # Silent path: no response.create — session waits for next user input.
```

- [ ] **Step 4: Replace the stub `_on_response_done` with the real one**

```python
    async def _on_response_done(self, snap: _ResponseSnapshot) -> None:
        try:
            if not snap.had_audio:
                await self._force_preface(snap.pending_calls)
            for call_id, name, args in snap.pending_calls:
                await self._dispatch_and_wait(call_id, name, args)
        except Exception:
            logger.exception("_on_response_done failed")
        finally:
            self.agent_idle.publish(True)
```

Note: this method is only spawned when `snap.trigger == "user"` (see Task 3 routing), so we don't re-check it here.

- [ ] **Step 5: Remove now-dead `_send_function_output` reference check**

Run: `grep -n "_send_function_output\|_dispatch_function_call\|_run_function_call" /home/naoki/dimos/dimos/agents/realtime/azure_voice_live.py`
Expected: no matches. If any remain, delete them.

- [ ] **Step 6: Smoke-test import + module-level wiring**

Run: `python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent, _ResponseSnapshot; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(voice-live): force preface + tool result speech via state machine"
```

---

## Task 5: Update the Japanese system prompt

**Files:**
- Modify: `dimos/agents/realtime/prompts/japanese.py`

- [ ] **Step 1: Replace the prompt body**

Open the file and replace the assignment of `JAPANESE_SYSTEM_PROMPT` with:

```python
JAPANESE_SYSTEM_PROMPT = """\
あなたは Unitree Go2 という四足歩行ロボットに搭載された日本語音声アシスタントです。

行動原則:
- ユーザの発話には簡潔で自然な日本語の音声で応答する。
- ロボットの動作や情報取得を指示されたら、提供されているツールを呼び出して実行する。
- ツールを呼ぶ前に、必ず短い一言（例:「はい、進みます」「確認します」）を音声で発してから呼び出す。
- 必要に応じてカメラやセンサーのツールを使って状況を確認してから動く。
- ツール結果に「エラー」と書かれていた場合は、内容を要約してユーザに伝える。
"""
```

- [ ] **Step 2: Verify the import path still works**

Run: `python -c "from dimos.agents.realtime.prompts.japanese import JAPANESE_SYSTEM_PROMPT; assert 'preface' not in JAPANESE_SYSTEM_PROMPT.lower(); print(len(JAPANESE_SYSTEM_PROMPT))"`
Expected: a length value printed (sanity check, no assertion error).

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/realtime/prompts/japanese.py
git commit -m "docs(voice-live): refresh JP prompt to match enforced flow"
```

---

## Task 6: Test scaffolding — fixtures and mocks

**Files:**
- Create: `tests/agents/realtime/__init__.py`
- Create: `tests/agents/realtime/test_azure_voice_live_forced_speech.py`

- [ ] **Step 1: Create the package marker**

Write `tests/agents/realtime/__init__.py` with empty content.

- [ ] **Step 2: Create the test file with shared fixtures**

Write `tests/agents/realtime/test_azure_voice_live_forced_speech.py`:

```python
"""Forced-speech state machine tests for AzureVoiceLiveAgent.

The Voice Live WS is mocked. We drive `_handle_event` directly with fake
event objects, then assert on `mock_conn.response.create.call_args_list`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from azure.ai.voicelive.models import ServerEventType

from dimos.agents.realtime.azure_voice_live import (
    AzureVoiceLiveAgent,
    AzureVoiceLiveConfig,
)


@dataclass
class _FakeEvent:
    type: Any
    delta: str | None = None
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None
    transcript: str | None = None
    session: Any = None
    error: Any = None


def _make_agent() -> AzureVoiceLiveAgent:
    """Construct an agent with WS-side dependencies replaced by mocks."""
    config = AzureVoiceLiveConfig(
        endpoint="https://fake",
        api_key="fake",
        report_after_tools={"observe", "current_time"},
    )
    agent = AzureVoiceLiveAgent(config=config)

    # Mock the Voice Live connection.
    conn = MagicMock()
    conn.response.create = AsyncMock()
    conn.response.cancel = AsyncMock()
    conn.conversation.item.create = AsyncMock()
    agent._conn = conn

    # Mock MCP adapter.
    mcp = MagicMock()
    mcp.call_tool = MagicMock(
        return_value={"content": [{"type": "text", "text": "ok"}]}
    )
    agent._mcp = mcp

    # Mock executor + loop wiring for run_in_executor.
    loop = asyncio.get_event_loop()
    agent._loop = loop
    # Replace _tool_pool with an inline executor stub.
    class _InlineExec:
        def submit(self, fn, *a, **kw):
            raise NotImplementedError
    agent._tool_pool = _InlineExec()
    # Override run_in_executor to run synchronously in the same loop.
    async def _run_in_executor(_pool, fn, *args):
        return fn(*args)
    loop.run_in_executor = _run_in_executor  # type: ignore[assignment]

    # Mock publishers we don't care about.
    agent.agent = MagicMock()
    agent.agent_idle = MagicMock()
    return agent


async def _emit(agent: AzureVoiceLiveAgent, *events: _FakeEvent) -> None:
    for ev in events:
        await agent._handle_event(ev)


async def _drain_tasks() -> None:
    """Yield until all currently-scheduled tasks finish."""
    for _ in range(50):
        pending = [t for t in asyncio.all_tasks() if not t.done()]
        pending = [t for t in pending if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.sleep(0)
    raise AssertionError("tasks did not settle")
```

- [ ] **Step 3: Run the file (no tests yet) to confirm imports**

Run: `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py -q`
Expected: `no tests ran` (or similar). No import errors.

- [ ] **Step 4: Commit**

```bash
git add tests/agents/realtime/__init__.py tests/agents/realtime/test_azure_voice_live_forced_speech.py
git commit -m "test(voice-live): scaffolding for forced-speech tests"
```

---

## Task 7: Test — preface NOT forced when audio was emitted (scenarios 1 and 3)

**Files:**
- Modify: `tests/agents/realtime/test_azure_voice_live_forced_speech.py`

- [ ] **Step 1: Add scenario 1 test (audio yes, no tool)**

Append to the test file:

```python
@pytest.mark.asyncio
async def test_no_preface_when_audio_present_no_tool():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_DELTA, delta=b"\x00\x01"),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA, delta="hi"),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await _drain_tasks()
    assert agent._conn.response.create.call_count == 0
```

- [ ] **Step 2: Add scenario 3 test (audio yes, action tool)**

```python
@pytest.mark.asyncio
async def test_audio_present_then_silent_action_tool():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_DELTA, delta=b"\x01"),
        _FakeEvent(
            type=ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE,
            call_id="c1",
            name="relative_move",
            arguments='{"forward": 1.0}',
        ),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await _drain_tasks()
    # function_call_output sent but NO response.create (silent action).
    assert agent._conn.conversation.item.create.await_count == 1
    assert agent._conn.response.create.call_count == 0
    agent._mcp.call_tool.assert_called_once_with("relative_move", {"forward": 1.0})
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py -v`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/agents/realtime/test_azure_voice_live_forced_speech.py
git commit -m "test(voice-live): preface skipped when audio present (action tools silent)"
```

---

## Task 8: Test — preface forced when no audio (scenarios 2 and 5)

**Files:**
- Modify: `tests/agents/realtime/test_azure_voice_live_forced_speech.py`

- [ ] **Step 1: Helper for completing a forced sub-response**

Append before the new tests:

```python
async def _complete_pending_subresponse(agent: AzureVoiceLiveAgent) -> None:
    """Simulate Voice Live finishing the preface/tool_result response."""
    # The agent task is awaiting _resp_done_event after issuing response.create.
    # Emit RESPONSE_CREATED + RESPONSE_DONE for that response.
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_DELTA, delta=b"\x01"),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
```

- [ ] **Step 2: Add scenario 2 test (no audio, no tool)**

```python
@pytest.mark.asyncio
async def test_preface_forced_when_no_audio_no_tool():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    # Let _on_response_done run up to its await.
    await asyncio.sleep(0)
    await _complete_pending_subresponse(agent)
    await _drain_tasks()

    assert agent._conn.response.create.await_count == 1
    call = agent._conn.response.create.await_args_list[0]
    instructions = call.kwargs["response"]["instructions"]
    assert "短く一言" in instructions
```

- [ ] **Step 3: Add scenario 5 test (no audio + action tool → preface mentioning tool, then silent)**

```python
@pytest.mark.asyncio
async def test_preface_forced_before_action_tool_then_silent():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(
            type=ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE,
            call_id="c2",
            name="relative_move",
            arguments='{"forward": 1.0}',
        ),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await asyncio.sleep(0)
    await _complete_pending_subresponse(agent)
    await _drain_tasks()

    # Exactly one response.create (the preface). Tool ran. No second response.create.
    assert agent._conn.response.create.await_count == 1
    preface_call = agent._conn.response.create.await_args_list[0]
    assert "relative_move" in preface_call.kwargs["response"]["instructions"]
    agent._mcp.call_tool.assert_called_once_with("relative_move", {"forward": 1.0})
    assert agent._conn.conversation.item.create.await_count == 1
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/agents/realtime/test_azure_voice_live_forced_speech.py
git commit -m "test(voice-live): preface forced when audio missing"
```

---

## Task 9: Test — report-after-tool flow (scenario 4) and infinite-loop guards (6, 7)

**Files:**
- Modify: `tests/agents/realtime/test_azure_voice_live_forced_speech.py`

- [ ] **Step 1: Add scenario 4 test (audio yes + report tool → result report)**

```python
@pytest.mark.asyncio
async def test_report_after_observe():
    agent = _make_agent()
    agent._mcp.call_tool.return_value = {
        "content": [{"type": "text", "text": "front: chair"}]
    }
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_DELTA, delta=b"\x01"),
        _FakeEvent(
            type=ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE,
            call_id="c3",
            name="observe",
            arguments="{}",
        ),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await asyncio.sleep(0)
    await _complete_pending_subresponse(agent)
    await _drain_tasks()

    assert agent._conn.response.create.await_count == 1
    report_call = agent._conn.response.create.await_args_list[0]
    assert "要約" in report_call.kwargs["response"]["instructions"]
    assert agent._conn.conversation.item.create.await_count == 1
```

- [ ] **Step 2: Add scenario 6 test (preface_forced response without audio does NOT re-force)**

```python
@pytest.mark.asyncio
async def test_silent_preface_response_does_not_re_force():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),  # trigger=user
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),     # no audio → forces preface
    )
    await asyncio.sleep(0)
    # Emit a SILENT preface_forced sub-response (no audio delta).
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),  # trigger=preface_forced
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await _drain_tasks()

    # Only the original forced preface response.create — no recursive re-force.
    assert agent._conn.response.create.await_count == 1
```

- [ ] **Step 3: Add scenario 7 test (tool_result response without audio does NOT re-force)**

```python
@pytest.mark.asyncio
async def test_silent_tool_result_response_does_not_re_force():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_DELTA, delta=b"\x01"),
        _FakeEvent(
            type=ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE,
            call_id="c4",
            name="observe",
            arguments="{}",
        ),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await asyncio.sleep(0)
    # SILENT tool_result sub-response.
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),  # trigger=tool_result
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await _drain_tasks()

    assert agent._conn.response.create.await_count == 1  # only the report request
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/agents/realtime/test_azure_voice_live_forced_speech.py
git commit -m "test(voice-live): report-after-tool flow + no-re-force guards"
```

---

## Task 10: Test — barge-in releases waiter (scenario 8)

**Files:**
- Modify: `tests/agents/realtime/test_azure_voice_live_forced_speech.py`

- [ ] **Step 1: Add scenario 8 test**

```python
@pytest.mark.asyncio
async def test_speech_started_releases_pending_waiter():
    agent = _make_agent()
    # Provide a playback stub so SPEECH_STARTED doesn't trip on None.
    agent._playback = MagicMock()

    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(
            type=ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE,
            call_id="c5",
            name="relative_move",
            arguments="{}",
        ),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),  # triggers preface
    )
    await asyncio.sleep(0)
    # Now agent is awaiting _resp_done_event. Fire barge-in.
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED),
    )
    await _drain_tasks()  # must not raise "tasks did not settle"

    agent._playback.skip_pending.assert_called_once()
    agent._conn.response.cancel.assert_awaited_once()
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py -v`
Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/realtime/test_azure_voice_live_forced_speech.py
git commit -m "test(voice-live): barge-in releases pending waiter"
```

---

## Task 11: Full suite + lint sanity check

**Files:** (no edits, verification only)

- [ ] **Step 1: Run the realtime test suite**

Run: `pytest tests/agents/realtime/ -v`
Expected: 8 passed.

- [ ] **Step 2: Run the wider agent test suite to confirm no regression**

Run: `pytest tests/agents/ -q`
Expected: all green. If anything red, investigate and fix in a follow-up commit before declaring done.

- [ ] **Step 3: Confirm no leftover dead names**

Run: `grep -rn "_response_active\|_response_text_buf\|_dispatch_function_call\|_run_function_call\|_send_function_output" /home/naoki/dimos/dimos/ /home/naoki/dimos/tests/`
Expected: no output.

- [ ] **Step 4: Confirm `_resp_*` and snapshot symbols are referenced consistently**

Run: `grep -n "_ResponseSnapshot\|_resp_trigger\|_resp_had_audio\|_resp_pending_calls\|_resp_text_buf\|_resp_done_event\|_next_trigger" /home/naoki/dimos/dimos/agents/realtime/azure_voice_live.py | head -40`
Expected: matches in `__init__`, `_handle_event`, `_snapshot_response_state`, `_on_response_done`, `_force_preface`, `_dispatch_and_wait`.

- [ ] **Step 5: Final commit (if any cleanup happened)**

If steps above triggered fixes, commit them:

```bash
git add -A
git commit -m "chore(voice-live): final cleanup after forced-speech implementation"
```

If nothing changed, skip this step.

---

## Self-Review Summary

**Spec coverage check:**

| Spec section | Covered by |
|---|---|
| 要件 1 (preface 常時保証) | Task 4 `_force_preface` + Tasks 7-8 tests |
| 要件 2 (tool 別 report) | Task 4 `_dispatch_and_wait` + Task 9 tests |
| 要件 3 (発話順序 / preface 先) | Task 4 `_on_response_done` ordering + Task 8 scenario 5 |
| 要件 4 (無限ループ防止) | Task 3 routing (`asyncio.create_task` only when trigger=user) + Task 9 scenarios 6, 7 |
| State 設計 | Task 2 |
| Event loop 直列化 | Task 3 (asyncio.create_task in `_handle_event`) |
| Config 追加 | Task 1 |
| Prompt 更新 | Task 5 |
| バージイン対応 | Task 3 (SPEECH_STARTED handler) + Task 10 |
| bench/replay 互換 | Task 3 keeps `_first_audio_emitted` / `_first_tool_call_emitted` + `reset_bench_turn` untouched |

No gaps.

**Placeholder scan:** None found.

**Type consistency:** `_ResponseSnapshot` defined in Task 2, used identically in Tasks 3, 4. `_resp_*` field names consistent across init, handler, snapshot, and tests. `report_after_tools` set membership checked the same way in `_dispatch_and_wait` and tests.