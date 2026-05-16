# Azure Voice Live — ActiveResponse Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the 6 scattered `_resp_*` / `_next_trigger` / `_resp_done_event` state variables in `AzureVoiceLiveAgent` into a single `_ActiveResponse` object owning its done event, issued via a `ResponseRequest` value object — behavior-equivalent.

**Architecture:** Per-response accumulator state lives in one `_ActiveResponse` dataclass constructed at `RESPONSE_CREATED` and discarded at `RESPONSE_DONE` / `SPEECH_STARTED`. Agent-initiated responses go through `_issue_response(ResponseRequest)`, which pre-builds the `_ActiveResponse`, places it in a `_pending_active` slot for `RESPONSE_CREATED` to promote, and awaits the instance's own `done` event — no shared Event, no `_next_trigger` racing.

**Tech Stack:** Python 3.12 / asyncio / azure.ai.voicelive / pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-17-azure-voice-live-active-response-refactor-design.md`

**Test contract:** `tests/agents/realtime/test_azure_voice_live_forced_speech.py` (6 cases) — must stay green throughout. This is a pure structural refactor; the existing tests are the safety net.

---

## Task 1: Add `ResponseRequest` and `_ActiveResponse` dataclasses

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py` (insert after the existing `_ResponseSnapshot` dataclass, around line 84)

This task adds the new types without wiring them in. The forced-speech tests remain green because nothing references the new types yet.

- [ ] **Step 1: Add the two dataclasses**

Insert the following block in `dimos/agents/realtime/azure_voice_live.py` immediately after the existing `_ResponseSnapshot` dataclass (after line 84):

```python
@dataclass(frozen=True)
class ResponseRequest:
    """Parameters for an agent-initiated response.create call.

    Used by _issue_response to bundle trigger label, instructions, and
    modalities into one value object. Replaces the implicit
    "_next_trigger = ...; await response.create(...)" protocol.
    """
    trigger: str  # "preface_forced" | "tool_result" | future labels
    instructions: str
    modalities: tuple[str, ...] = ("audio", "text")


@dataclass
class _ActiveResponse:
    """Per-response accumulator + completion event.

    Lifetime: constructed at RESPONSE_CREATED (or pre-built by
    _issue_response and promoted at RESPONSE_CREATED), discarded at
    RESPONSE_DONE / SPEECH_STARTED. Owns its own `done` event so there
    is no nullable single-Event reuse across responses.
    """
    trigger: str  # "user" | "preface_forced" | "tool_result"
    had_audio: bool = False
    pending_calls: list[tuple[str, str, str]] = field(default_factory=list)
    text_buf: list[str] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self) -> _ResponseSnapshot:
        return _ResponseSnapshot(
            trigger=self.trigger,
            had_audio=self.had_audio,
            pending_calls=list(self.pending_calls),
            text="".join(self.text_buf).strip(),
        )
```

Add `field` to the existing `from dataclasses import dataclass` import line at the top of the file:

```python
from dataclasses import dataclass, field
```

- [ ] **Step 2: Run forced-speech tests to confirm no regression**

Run: `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py -v`
Expected: All 6 tests PASS (the new types are unused so nothing changes).

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "refactor(voice-live): add ResponseRequest and _ActiveResponse dataclasses

Introduce the two value objects that the upcoming refactor will use to
replace the scattered _resp_* state and the implicit _next_trigger
protocol. No wiring yet; existing tests stay green."
```

---

## Task 2: Atomic swap — replace state variables, event handler, and issuance helpers

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

This is the structural change. Because old and new state cannot run in parallel without inventing a hack path, the swap is done in one commit. The forced-speech tests verify correctness immediately after.

- [ ] **Step 1: Replace the `__init__` response-state block**

In `AzureVoiceLiveAgent.__init__` (around lines 291–296), replace this block:

```python
        self._resp_had_audio: bool = False
        self._resp_pending_calls: list[tuple[str, str, str]] = []
        self._resp_text_buf: list[str] = []
        self._resp_trigger: str = "user"
        self._next_trigger: str | None = None
        self._resp_done_event: asyncio.Event | None = None
```

with:

```python
        self._active: _ActiveResponse | None = None
        self._pending_active: _ActiveResponse | None = None
```

- [ ] **Step 2: Delete `_snapshot_response_state` and add `_issue_response`**

Delete the entire `_snapshot_response_state` method (currently around lines 550–561):

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

Add `_issue_response` immediately before `_force_preface`:

```python
    async def _issue_response(self, req: ResponseRequest) -> _ResponseSnapshot:
        """Issue an agent-initiated response and wait for it to finish.

        Pre-builds the _ActiveResponse and places it in _pending_active.
        RESPONSE_CREATED promotes it to self._active. Awaits the instance's
        own done event (set by RESPONSE_DONE or by SPEECH_STARTED barge-in).
        """
        active = _ActiveResponse(trigger=req.trigger)
        self._pending_active = active
        await self._conn.response.create(
            response={
                "modalities": list(req.modalities),
                "instructions": req.instructions,
            }
        )
        await active.done.wait()
        return active.snapshot()
```

- [ ] **Step 3: Replace `_force_preface`**

Replace the entire body of `_force_preface` (currently around lines 576–597) with:

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

        await self._issue_response(ResponseRequest(
            trigger="preface_forced",
            instructions=instructions,
        ))
```

- [ ] **Step 4: Replace `_dispatch_and_wait`**

Replace the entire body of `_dispatch_and_wait` (currently around lines 599–623) with:

```python
    async def _dispatch_and_wait(
        self, call_id: str, name: str, arguments_json: str
    ) -> None:
        assert self._loop is not None and self._tool_pool is not None
        output = await self._loop.run_in_executor(
            self._tool_pool, self._invoke_mcp, name, arguments_json
        )
        await self._conn.conversation.item.create(
            item=FunctionCallOutputItem(call_id=call_id, output=output)
        )
        if name in self.config.report_after_tools:
            await self._issue_response(ResponseRequest(
                trigger="tool_result",
                instructions=(
                    "直前のツール結果を日本語で1文に要約して"
                    "音声で報告してください。"
                ),
            ))
        # Silent path: no response.create — session waits for next user input.
```

- [ ] **Step 5: Rewrite the event handler branches**

In `_handle_event`, replace the six branches that touch the old state. The full new versions:

**`SPEECH_STARTED` branch** (currently around lines 641–654):

```python
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
            if self._active is not None:
                self._active.pending_calls = []
                self._active.done.set()
                self._active = None
            self._pending_active = None
            self.agent_idle.publish(False)
```

**`RESPONSE_CREATED` branch** (currently around lines 660–666):

```python
        elif et == ServerEventType.RESPONSE_CREATED:
            if self._pending_active is not None:
                self._active = self._pending_active
                self._pending_active = None
            else:
                self._active = _ActiveResponse(trigger="user")
            self.agent_idle.publish(False)
```

**`RESPONSE_AUDIO_DELTA` branch** (currently around lines 667–673):

```python
        elif et == ServerEventType.RESPONSE_AUDIO_DELTA:
            if not self._first_audio_emitted:
                log_bench_event("first_audio_out")
                self._first_audio_emitted = True
            if self._active is not None:
                self._active.had_audio = True
            if self._playback is not None:
                self._playback.enqueue(event.delta)
```

**`RESPONSE_AUDIO_TRANSCRIPT_DELTA` branch** (currently around line 674):

```python
        elif et == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            if self._active is not None:
                self._active.text_buf.append(event.delta or "")
```

**`RESPONSE_TEXT_DELTA` branch** (currently around line 676):

```python
        elif et == ServerEventType.RESPONSE_TEXT_DELTA:
            if self._active is not None:
                self._active.text_buf.append(event.delta or "")
```

**`RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE` branch** (currently around lines 678–684):

```python
        elif et == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            if not self._first_tool_call_emitted:
                log_bench_event("first_tool_call", tool=event.name)
                self._first_tool_call_emitted = True
            if self._active is not None:
                self._active.pending_calls.append(
                    (event.call_id, event.name, event.arguments)
                )
```

**`RESPONSE_DONE` branch** (currently around lines 685–697):

```python
        elif et == ServerEventType.RESPONSE_DONE:
            active = self._active
            self._active = None
            snap = active.snapshot() if active is not None else None
            if active is not None:
                active.done.set()
            if snap is not None and snap.text:
                logger.info("assistant: %s", snap.text)
                self.agent.publish(AIMessage(content=snap.text))
            if snap is not None and snap.trigger == "user":
                asyncio.create_task(self._on_response_done(snap))
            else:
                # preface_forced / tool_result: finalize only, no further routing.
                self.agent_idle.publish(True)
```

- [ ] **Step 6: Run forced-speech tests**

Run: `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py -v`
Expected: All 6 tests PASS:
- `test_no_preface_when_audio_present_no_tool`
- `test_audio_present_then_silent_action_tool`
- `test_preface_forced_when_no_audio_no_tool`
- `test_preface_forced_before_action_tool_then_silent`
- `test_report_after_observe`
- `test_silent_preface_response_does_not_re_force`
- `test_silent_tool_result_response_does_not_re_force`
- `test_speech_started_releases_pending_waiter`

If any test fails, the most likely causes are:
1. **`SPEECH_STARTED` waiter not released**: confirm `self._active.done.set()` is unconditional inside `if self._active is not None:`.
2. **`trigger == "user"` re-routing fires for preface_forced/tool_result**: confirm `_pending_active.trigger` is set correctly in `_issue_response` and that RESPONSE_CREATED promotes it intact.
3. **DELTA arriving before CREATED**: shouldn't happen per Voice Live protocol, but the `if self._active is not None:` guards make it a no-op rather than an exception.

- [ ] **Step 7: Run the broader realtime test directory as a sanity check**

Run: `pytest tests/agents/realtime/ -v`
Expected: All tests PASS. There is also `dimos/agents/realtime/test_azure_voice_live.py` (23 lines) — if it imports the agent, it should still pass.

- [ ] **Step 8: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "refactor(voice-live): collapse response state into _ActiveResponse

Replace 6 scattered response-state variables (_resp_had_audio,
_resp_pending_calls, _resp_text_buf, _resp_trigger, _next_trigger,
_resp_done_event) with a single _ActiveResponse instance owning its
own done event. Agent-initiated responses go through _issue_response,
which pre-builds the _ActiveResponse and lets RESPONSE_CREATED promote
it — removing the implicit '_next_trigger = ...; await response.create'
protocol and the shared nullable Event.

Behavior unchanged: forced-speech tests stay green as the contract.

Spec: docs/superpowers/specs/2026-05-17-azure-voice-live-active-response-refactor-design.md"
```

---

## Task 3: Manual smoke test on the `unitree_go2_agentic_voice_live` blueprint

**Files:**
- Run: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`

The automated tests cover state-machine logic but not the live WS interaction. A short manual session validates the refactor end-to-end.

- [ ] **Step 1: Confirm the blueprint can be launched**

Check the blueprint entry point and required env vars by reading the first ~50 lines:

Run: `head -60 dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`

Confirm the env vars `DIMOS_AZURE_VOICE_LIVE_ENDPOINT` and `DIMOS_AZURE_VOICE_LIVE_API_KEY` are exported in the current shell. If not, ask the user to provide them or skip to Step 4.

- [ ] **Step 2: Launch the blueprint**

Run (in a terminal where the user can interact with the mic): the launch command for `unitree_go2_agentic_voice_live`. The exact command depends on the user's local launcher — check `scripts/` or `README` if unsure. A reasonable default:

`python -m dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live`

Expected: WS connects, mic activates (or PTT-armed), agent_idle goes True.

- [ ] **Step 3: Exercise the three critical paths**

In one session, trigger each:

1. **Normal user turn** → speak a sentence, agent should respond with audio. (Verifies `trigger="user"` flow through RESPONSE_CREATED → DELTA → DONE → `_on_response_done`.)
2. **Tool call with `report_after_tools` member** → ask something that invokes a tool in `config.report_after_tools` (e.g., "今何時？" if `current_time` is in the set). The agent should call the tool, then issue a follow-up audio report. (Verifies `_dispatch_and_wait` → `_issue_response(trigger="tool_result")`.)
3. **Barge-in** → while the agent is speaking, start talking. Playback should stop and the agent should begin processing the new utterance. (Verifies `SPEECH_STARTED` releases `_active.done` and clears `_pending_active`.)

If any path produces a wedged session (no audio out, agent_idle stuck False), inspect `logger` output for the unhandled event type and re-check the matching branch in Task 2 Step 5.

- [ ] **Step 4: Stop the blueprint cleanly**

Send SIGINT (Ctrl-C) and confirm shutdown completes within ~5 seconds (the `stop()` method joins the WS thread with a 5s timeout).

- [ ] **Step 5: No commit needed**

This task is verification only. If issues are found, return to Task 2 to fix and re-run.

---

## Self-review checklist (for the plan author)

- [x] Every spec section maps to a task:
  - "新規型" → Task 1
  - "インスタンス状態の集約" / `_snapshot_response_state` 削除 → Task 2 Steps 1–2
  - "発火 helper" → Task 2 Step 2
  - "event handler の変更" → Task 2 Step 5
  - "テスト互換性" → Task 2 Steps 6–7
  - "移行手順" → Tasks 1–3 in order
- [x] No "TBD" / "implement later" / "handle edge cases" placeholders — all code shown verbatim.
- [x] Method names consistent: `_issue_response`, `_force_preface`, `_dispatch_and_wait`, `_active`, `_pending_active`, `_ActiveResponse.done`, `_ActiveResponse.snapshot()`.
- [x] Type consistency: `pending_calls: list[tuple[str, str, str]]` matches the existing `_ResponseSnapshot` field type. `modalities: tuple[str, ...]` matches the `list(req.modalities)` conversion in `_issue_response`.

---

## Risks and recovery

- **Mid-Task-2 broken state**: if any forced-speech test fails after Step 6, do NOT commit. Read the test output, locate the failing assertion, re-check the matching event-handler branch against Step 5 of this task. The diagnostic checklist in Step 6 covers the three most likely causes.
- **Smoke test reveals a runtime issue not covered by tests**: most likely culprit is `_pending_active` not being cleared in some failure path. Add unit coverage in `test_azure_voice_live_forced_speech.py` for the missed case before fixing, so the regression is locked in by a test.
