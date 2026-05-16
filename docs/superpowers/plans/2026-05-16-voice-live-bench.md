# Voice Live Bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce end-to-end latency measurement for `unitree_go2_agentic_voice_live` so it can be compared head-to-head with `unitree_go2_agentic_ja` on a single common metric (`e2e_first_audio_s`).

**Architecture:** After merging the agentic-ja bench infrastructure into `feat/voice-live`, instrument `AzureVoiceLiveAgent` with two bench-event emissions and a per-turn reset RPC; wire the voice-live blueprint to consume audio from the same `/upload_audio` endpoint used by agentic-ja's replay driver; create a parallel replay script (`replay_agentic_voice_live.py`); extend the existing analyzer (`scripts/bench_agentic_ja.py`) with auto-detected mode and a new common `e2e_first_audio_s` metric.

**Tech Stack:** Python, asyncio, Azure Voice Live SDK, ReactiveX (`rx`), pytest. Fork-local files only — no upstream-tracked edits.

**Spec:** `docs/superpowers/specs/2026-05-16-voice-live-bench-design.md`

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `dimos/agents/realtime/azure_voice_live.py` | Modify | Emit `first_audio_out` / `first_tool_call`, add `reset_bench_turn` RPC, subscribe to a WebInput-side audio stream |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py` | Modify | Swap `WebInput.blueprint()` → `JapaneseWebInput.blueprint()` so audio is exposed |
| `scripts/replay_agentic_voice_live.py` | Create | Drive the voice-live blueprint with fixture wavs, emit `user_audio_end`, gate on `agent_idle` settle |
| `scripts/bench_agentic_ja.py` | Modify | Add `e2e_first_audio_s` metric, voice-live mode auto-detection, voice-live headline output |
| `tests/scripts/test_bench_agentic_ja_analyzer.py` | Modify | Cover `e2e_first_audio_s` for agentic-ja, voice-live mode minimal events, ja-only metric omission, mode detection |

---

## Task 1: Merge agentic-ja bench infrastructure into feat/voice-live

**Files:** None (git operation only)

The bench scripts, fixtures, `dimos/agents/bench_ja/`, `JapaneseWebInput`, and analyzer all live on `feat/go2-agentic-local-tts-llm-env` (51 commits ahead of `feat/voice-live`, sharing common ancestor `41f6ab5ad`). Merge that branch into the working branch before touching anything else.

- [ ] **Step 1: Verify working tree is clean**

```bash
git status
```

Expected: `working tree clean` (or only the just-committed spec doc on `feat/voice-live`).

- [ ] **Step 2: Fetch and confirm both branch tips are reachable locally**

```bash
git rev-parse feat/voice-live feat/go2-agentic-local-tts-llm-env
```

Expected: two distinct SHAs printed.

- [ ] **Step 3: Start the merge**

```bash
git merge feat/go2-agentic-local-tts-llm-env --no-ff -m "merge: pull agentic-ja bench infra into voice-live"
```

If merge completes cleanly, skip to Step 5. If conflicts are reported, continue.

- [ ] **Step 4: Resolve conflicts**

Likely conflict surfaces:
- `dimos/robot/unitree/go2/blueprints/agentic/` — voice-live blueprint coexists with agentic-ja blueprint; keep both files
- `pyproject.toml` / `uv.lock` — accept the union of dependencies (resolve in favor of `feat/go2-agentic-local-tts-llm-env` if either branch added bench-related deps)
- `dimos/agents/realtime/` — should not conflict (voice-live is the only branch touching this)

For each conflicted file, inspect with `git diff`, resolve, then `git add <file>`. Once all are staged, run `git commit` (the merge message is already prepared).

- [ ] **Step 5: Verify the bench infrastructure is now present**

```bash
ls dimos/agents/bench_ja/ scripts/bench_agentic_ja.py scripts/replay_agentic_ja.py tests/scripts/test_bench_agentic_ja_analyzer.py tests/bench_fixtures/agentic_ja/fixtures.yaml
```

Expected: every path exists.

- [ ] **Step 6: Verify the voice-live blueprint is still importable**

```bash
python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import unitree_go2_agentic_voice_live; print(unitree_go2_agentic_voice_live)"
```

Expected: prints a blueprint object without ImportError.

- [ ] **Step 7: Verify the agentic-ja analyzer tests still pass on the merged tree**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v
```

Expected: all tests PASS. If anything fails, the merge resolution introduced a regression — fix before continuing.

---

## Task 2: Confirm audio-source attribute on JapaneseWebInput

**Files:**
- Read: `dimos/agents/web_human_input_ja.py`

`JapaneseWebInput` was modified upstream-of-this-plan to expose its audio subject (commit `75ba38554 refactor(web_human_input_ja): route STT timing through bench_ja, expose _audio_subject`). We need the exact attribute / method name to subscribe from `AzureVoiceLiveAgent` in Task 6.

- [ ] **Step 1: Locate the audio exposure**

```bash
grep -n "audio_subject\|emit_audio\|_audio_subject" dimos/agents/web_human_input_ja.py
```

Expected: at least one `self._audio_subject = ...` assignment and one publicly-visible reference (`audio_subject` property, method, or direct attribute).

- [ ] **Step 2: Record the access pattern**

Read the grep output and note the exact attribute name (`_audio_subject`, `audio_subject`, or `emit_audio()`). Tasks 5–6 will reference this — substitute the actual name where the plan says `<audio_source>`.

No code change in this task. The output is the access pattern to use in Task 6.

---

## Task 3: Analyzer — add `e2e_first_audio_s` metric

**Files:**
- Modify: `scripts/bench_agentic_ja.py`
- Modify: `tests/scripts/test_bench_agentic_ja_analyzer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/scripts/test_bench_agentic_ja_analyzer.py`:

```python
def test_e2e_first_audio_s_computed_for_agentic_ja(jsonl_path: Path) -> None:
    turns = build_turns(jsonl_path)
    metrics = compute_per_turn_metrics(turns)
    # Turn A: user_audio_end.t=0.0, first_audio_out.t=0.95
    assert metrics["A"]["e2e_first_audio_s"] == pytest.approx(0.95)
    # Turn B: no first_audio_out (motion-only) → key absent
    assert "e2e_first_audio_s" not in metrics["B"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py::test_e2e_first_audio_s_computed_for_agentic_ja -v
```

Expected: FAIL with `KeyError: 'e2e_first_audio_s'` or similar.

- [ ] **Step 3: Add the metric to `compute_per_turn_metrics`**

In `scripts/bench_agentic_ja.py`, inside the per-turn loop in `compute_per_turn_metrics`, after the existing `agent_first_call_s` / `speak_tts_s` block:

```python
        fao = data.get("first_audio_out")
        if fao is not None and t0 is not None:
            m["e2e_first_audio_s"] = fao["t"] - t0
```

(Place this where `t0`, `m`, `fao` are already in scope. Match the existing variable names exactly.)

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py::test_e2e_first_audio_s_computed_for_agentic_ja -v
```

Expected: PASS.

- [ ] **Step 5: Run the full analyzer test file to confirm no regression**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Add the metric to headline output**

In `scripts/bench_agentic_ja.py`, find the `aggregate` (or `main` / print-headline) function that prints `agent_first_call_s p50=… p95=… n=…`. Above that line, add an equivalent line for `e2e_first_audio_s` so it appears first in the headline block:

```python
    _print_metric("e2e_first_audio_s", _collect(metrics, "e2e_first_audio_s"))
```

(Use the existing helper function name as defined in the script — `_print_metric` is illustrative; substitute the real name found by reading the function around the existing `agent_first_call_s` print.)

- [ ] **Step 7: Smoke-run the analyzer against any existing agentic-ja run-dir**

```bash
ls logs/*-bench-agentic-ja 2>/dev/null | head -1 | xargs -I{} python scripts/bench_agentic_ja.py {}
```

Expected: output now includes an `e2e_first_audio_s` line; existing `agent_first_call_s` / `speak_tts_s` / `turn_total_s` lines still present.

If no agentic-ja run-dir exists locally, skip this step — the unit tests already cover the metric.

- [ ] **Step 8: Commit**

```bash
git add scripts/bench_agentic_ja.py tests/scripts/test_bench_agentic_ja_analyzer.py
git commit -m "$(cat <<'EOF'
feat(bench_agentic_ja): add e2e_first_audio_s metric

The cross-architecture comparable metric for voice-live vs. agentic-ja.
Computed as first_audio_out.t - user_audio_end.t whenever both events
are present on a turn.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Analyzer — voice-live mode detection and headline

**Files:**
- Modify: `scripts/bench_agentic_ja.py`
- Modify: `tests/scripts/test_bench_agentic_ja_analyzer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/scripts/test_bench_agentic_ja_analyzer.py`:

```python
@pytest.fixture
def voice_live_jsonl_path(tmp_path: Path) -> Path:
    """Two voice-live turns:
    - V1 (speak-only): user_audio_end -> first_audio_out (no first_tool_call)
    - V2 (tool + speak): user_audio_end -> first_tool_call -> first_audio_out
    """
    path = tmp_path / "main.jsonl"
    lines = [
        _line({"event_kind": "user_audio_end", "turn_id": "V1", "t": 0.0,
               "fixture_id": "fx_01", "run_idx": 0, "warmup": False,
               "wav_seconds": 1.0}),
        _line({"event_kind": "first_audio_out", "turn_id": "V1", "t": 0.8}),

        _line({"event_kind": "user_audio_end", "turn_id": "V2", "t": 0.0,
               "fixture_id": "fx_07", "run_idx": 0, "warmup": False,
               "wav_seconds": 1.2}),
        _line({"event_kind": "first_tool_call", "turn_id": "V2", "t": 0.6,
               "tool": "current_time"}),
        _line({"event_kind": "first_audio_out", "turn_id": "V2", "t": 1.1}),
    ]
    path.write_text("".join(lines))
    return path


def test_voice_live_mode_minimal_events(voice_live_jsonl_path: Path) -> None:
    turns = build_turns(voice_live_jsonl_path)
    metrics = compute_per_turn_metrics(turns)
    assert metrics["V1"]["e2e_first_audio_s"] == pytest.approx(0.8)
    assert metrics["V2"]["e2e_first_audio_s"] == pytest.approx(1.1)
    assert metrics["V2"]["first_tool_call_s"] == pytest.approx(0.6)
    assert "first_tool_call_s" not in metrics["V1"]


def test_voice_live_mode_omits_ja_only_metrics(
    voice_live_jsonl_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from bench_agentic_ja import main as analyzer_main
    analyzer_main([str(voice_live_jsonl_path.parent), "--config", "voice-live"])
    out = capsys.readouterr().out
    assert "e2e_first_audio_s" in out
    assert "agent_first_call_s" not in out
    assert "speak_tts_s" not in out
    assert "turn_total_s" not in out


def test_mode_auto_detection_by_dir_name(tmp_path: Path) -> None:
    from bench_agentic_ja import detect_mode
    assert detect_mode(tmp_path / "2026-05-16-bench-agentic-ja") == "agentic-ja"
    assert detect_mode(tmp_path / "2026-05-16-bench-agentic-voice-live") == "voice-live"
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py::test_voice_live_mode_minimal_events tests/scripts/test_bench_agentic_ja_analyzer.py::test_voice_live_mode_omits_ja_only_metrics tests/scripts/test_bench_agentic_ja_analyzer.py::test_mode_auto_detection_by_dir_name -v
```

Expected: all FAIL (missing `first_tool_call_s`, missing `detect_mode`, headline still contains ja-only lines).

- [ ] **Step 3: Add `first_tool_call_s` per-turn metric**

In `compute_per_turn_metrics`, near the new `e2e_first_audio_s` block from Task 3:

```python
        ftc = data.get("first_tool_call")
        if ftc is not None and t0 is not None:
            m["first_tool_call_s"] = ftc["t"] - t0
```

- [ ] **Step 4: Add `detect_mode` function**

At module level in `scripts/bench_agentic_ja.py`:

```python
def detect_mode(run_dir: Path) -> str:
    """Infer 'agentic-ja' or 'voice-live' from the run-dir basename."""
    name = Path(run_dir).name
    if "voice-live" in name:
        return "voice-live"
    return "agentic-ja"
```

- [ ] **Step 5: Wire `--config` CLI flag and mode-aware headline**

In `parse_args` (or the existing argparse setup), add:

```python
    p.add_argument(
        "--config", choices=("auto", "agentic-ja", "voice-live"), default="auto",
        help="Analyzer mode. 'auto' infers from run-dir basename.",
    )
```

In the headline-printing function, accept a `mode` argument and gate the ja-only metrics:

```python
def print_headline(metrics: dict[str, dict[str, Any]], mode: str) -> None:
    _print_metric("e2e_first_audio_s", _collect(metrics, "e2e_first_audio_s"))
    if mode == "agentic-ja":
        _print_metric("agent_first_call_s", _collect(metrics, "agent_first_call_s"))
        _print_metric("speak_tts_s", _collect(metrics, "speak_tts_s"))
        _print_metric("turn_total_s", _collect(metrics, "turn_total_s"))
    else:  # voice-live
        _print_metric("first_tool_call_s", _collect(metrics, "first_tool_call_s"))
```

In `main` (or equivalent entry point), resolve `--config auto` via `detect_mode(run_dir)` and pass the result to `print_headline`. The diagnostic breakdowns (`stt_done`, `llm_step` sum/count, `tools_step` sum/count, etc.) should also be gated behind `mode == "agentic-ja"` — those events don't exist in voice-live JSONL.

(Substitute the actual function names from the existing script — `print_headline` / `_print_metric` / `_collect` are illustrative; preserve the existing structure and just add the branching.)

- [ ] **Step 6: Run all analyzer tests**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v
```

Expected: all PASS, including the three new tests and all pre-existing ones.

- [ ] **Step 7: Commit**

```bash
git add scripts/bench_agentic_ja.py tests/scripts/test_bench_agentic_ja_analyzer.py
git commit -m "$(cat <<'EOF'
feat(bench_agentic_ja): voice-live mode with auto-detection

Analyzer now branches on run-dir basename (or explicit --config) and
emits a reduced headline (e2e_first_audio_s + first_tool_call_s) for
voice-live runs, omitting metrics that have no structural analog in
a single-model speech-to-speech architecture.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: AzureVoiceLiveAgent — bench flags, emissions, and reset RPC

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

- [ ] **Step 1: Add bench imports**

At the top of `dimos/agents/realtime/azure_voice_live.py`, near the other `dimos.*` imports:

```python
from dimos.agents.bench_ja import log_bench_event
```

- [ ] **Step 2: Initialize per-turn flags**

In `AzureVoiceLiveAgent.__init__`, after the other `self._...` initializations (e.g., right after `self._response_text_buf: list[str] = []`):

```python
        self._first_audio_emitted = False
        self._first_tool_call_emitted = False
```

- [ ] **Step 3: Add `reset_bench_turn` RPC**

Place this method next to the other `@rpc` methods (e.g., near `stop`):

```python
    @rpc
    def reset_bench_turn(self) -> None:
        """Clear per-turn bench flags. Called by the bench replay driver
        immediately after issuing a new turn_id, before posting wav audio."""
        self._first_audio_emitted = False
        self._first_tool_call_emitted = False
```

- [ ] **Step 4: Emit `first_audio_out` on first audio delta of the turn**

In `_handle_event`, modify the `RESPONSE_AUDIO_DELTA` branch:

```python
        elif et == ServerEventType.RESPONSE_AUDIO_DELTA:
            if not self._first_audio_emitted:
                log_bench_event("first_audio_out")
                self._first_audio_emitted = True
            if self._playback is not None:
                self._playback.enqueue(event.delta)
```

- [ ] **Step 5: Emit `first_tool_call` on first function-call args done**

In `_handle_event`, modify the `RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE` branch:

```python
        elif et == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            if not self._first_tool_call_emitted:
                log_bench_event("first_tool_call", tool=event.name)
                self._first_tool_call_emitted = True
            self._dispatch_function_call(
                call_id=event.call_id,
                name=event.name,
                arguments=event.arguments,
            )
```

- [ ] **Step 6: Sanity-check the module imports**

```bash
python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print(AzureVoiceLiveAgent)"
```

Expected: prints the class object without ImportError.

- [ ] **Step 7: Run the placeholder realtime test to confirm nothing collateral broke**

```bash
pytest dimos/agents/realtime/test_azure_voice_live.py -v
```

Expected: tests pass or are skipped as before (the file is a placeholder per recent commit `ed1952b4f`).

- [ ] **Step 8: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "$(cat <<'EOF'
feat(realtime): emit first_audio_out and first_tool_call bench events

Adds bench instrumentation to AzureVoiceLiveAgent that mirrors the
agentic-ja bench schema: one emission per turn for the first audio
delta and the first function-call args-done. Per-turn flags are reset
via the new reset_bench_turn RPC, called by the bench replay driver
between turns.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: AzureVoiceLiveAgent — subscribe to WebInput audio

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`

The bench replay driver pushes wav audio to `/upload_audio` (port 5555), which is served by `RobotWebInterface` running inside the WebInput module. To get that audio into the Voice Live WebSocket, the agent must subscribe to the audio stream exposed by `JapaneseWebInput` (which exposes `<audio_source>` — substitute the exact attribute name discovered in Task 2).

- [ ] **Step 1: Swap the blueprint to use `JapaneseWebInput`**

In `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`, replace the WebInput import and blueprint registration:

```python
from dimos.agents.web_human_input_ja import JapaneseWebInput
```

```python
unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    JapaneseWebInput.blueprint(),
    SpeakSkill.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)
```

`JapaneseWebInput` is the fork-local variant that exposes its audio subject; `WebInput` (upstream-tracked) does not, so this swap is necessary and out-of-scope for upstream edits.

- [ ] **Step 2: Add `_web_audio_sub` storage**

In `AzureVoiceLiveAgent.__init__`, add (next to `self._mic_subscription`):

```python
        self._web_audio_sub: Any = None
```

- [ ] **Step 3: Subscribe to JapaneseWebInput's audio in `start`**

In `AzureVoiceLiveAgent.start`, after the existing `self._mic_subscription = self._mic.emit_audio().subscribe(on_next=self._on_mic_audio)` line, add a subscription to the WebInput audio source. Use the access pattern discovered in Task 2 (substitute `<audio_source>` below — e.g., `web_input._audio_subject` or `web_input.emit_audio()`):

```python
        web_input = self.get_module("JapaneseWebInput")
        if web_input is not None:
            self._web_audio_sub = web_input.<audio_source>.subscribe(
                on_next=self._on_mic_audio
            )
```

The handler `_on_mic_audio` is input-source agnostic (it normalizes any AudioEvent into PCM16 b64 and appends to `input_audio_buffer`), so a second source needs no special-casing. Both sources share the `_mic_active` gate.

If the exact `get_module(...)` lookup pattern doesn't apply (i.e., `Module` doesn't expose `get_module`), use the codebase's existing inter-module reference idiom — grep for `get_module(` in `dimos/agents/realtime/` and `dimos/core/coordination/` to find the established pattern, then adapt.

- [ ] **Step 4: Dispose the subscription in `stop`**

In `AzureVoiceLiveAgent.stop`, mirror the existing `_mic_subscription` disposal:

```python
        if self._web_audio_sub is not None:
            try:
                self._web_audio_sub.dispose()
            except Exception:
                pass
            self._web_audio_sub = None
```

- [ ] **Step 5: Verify the blueprint still loads**

```bash
python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import unitree_go2_agentic_voice_live; print(unitree_go2_agentic_voice_live)"
```

Expected: prints a blueprint object without ImportError.

- [ ] **Step 6: Manual smoke (optional but recommended)**

Boot the blueprint, open the WebUI at `http://localhost:5555`, click the audio upload button with a short Japanese wav, confirm Azure Voice Live returns audible audio. This validates the audio path end-to-end before the replay script tries to drive it programmatically.

If a robot / Azure credentials are unavailable in this environment, skip this step — Task 8 covers the equivalent verification via the bench replay.

- [ ] **Step 7: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py
git commit -m "$(cat <<'EOF'
feat(realtime): consume audio from JapaneseWebInput

Switches the voice-live blueprint to JapaneseWebInput (fork-local,
exposes its audio subject) and subscribes AzureVoiceLiveAgent to that
audio stream alongside the mic source. Enables the bench replay to
inject fixture wavs via the existing /upload_audio HTTP endpoint, and
makes the WebUI's audio upload work in normal use as a side benefit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Replay driver — `scripts/replay_agentic_voice_live.py`

**Files:**
- Create: `scripts/replay_agentic_voice_live.py`

Mirror `scripts/replay_agentic_ja.py` with three local changes: blueprint, idle-settle gating, default run-dir.

- [ ] **Step 1: Copy the agentic-ja replay as a starting point**

```bash
cp scripts/replay_agentic_ja.py scripts/replay_agentic_voice_live.py
```

- [ ] **Step 2: Swap the blueprint import**

In the new file, replace:

```python
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_ja import (
    unitree_go2_agentic_ja,
)
```

with:

```python
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import (
    unitree_go2_agentic_voice_live,
)
```

Update every `unitree_go2_agentic_ja` reference in the file body (blueprint boot call, `ModuleCoordinator` setup) to `unitree_go2_agentic_voice_live`.

- [ ] **Step 3: Replace the idle-gate with a settle waiter**

The agentic-ja replay waits on `JapaneseSpeakSkill.idle_event` (a `threading.Event` set by `turn_done`). Voice-live has no `turn_done`; instead it exposes `AzureVoiceLiveAgent.agent_idle` as an observable that toggles False/True around responses.

Replace the idle-event acquisition with this class (place it near the top of the file, after imports):

```python
class IdleSettleWaiter:
    """Sets an internal Event once agent_idle has been True for settle_ms
    continuously. Cancels the pending settle on any False re-entry."""

    def __init__(self, agent_idle_observable: Any, settle_ms: float) -> None:
        self._evt = threading.Event()
        self._settle_s = settle_ms / 1000.0
        self._timer: threading.Timer | None = None
        self._sub = agent_idle_observable.subscribe(on_next=self._on_idle)

    def _on_idle(self, is_idle: bool) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if is_idle:
            self._timer = threading.Timer(self._settle_s, self._evt.set)
            self._timer.daemon = True
            self._timer.start()

    def clear(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._evt.clear()

    def wait(self, timeout: float) -> bool:
        return self._evt.wait(timeout)

    def dispose(self) -> None:
        try:
            self._sub.dispose()
        except Exception:
            pass
        if self._timer is not None:
            self._timer.cancel()
```

In the per-turn loop, replace `idle_event.wait(...)` / `idle_event.clear()` with `waiter.wait(...)` / `waiter.clear()`. Construct the waiter once the blueprint is up:

```python
agent = coord.get_module("AzureVoiceLiveAgent")
waiter = IdleSettleWaiter(agent.agent_idle, settle_ms=args.idle_settle_ms)
```

(Substitute the actual coordinator accessor for retrieving a started module — match what `replay_agentic_ja.py` uses for `JapaneseSpeakSkill`.)

- [ ] **Step 4: Call `reset_bench_turn` before each wav post**

In the per-turn loop, just after `new_turn()` and before `requests.post(UPLOAD_URL, ...)`:

```python
        tid = new_turn()
        agent.reset_bench_turn()
        ...
        resp = requests.post(UPLOAD_URL, files={"file": open(fx.wav, "rb")})
        log_bench_event(
            "user_audio_end",
            fixture_id=fx.id,
            wav_seconds=wav_seconds(fx.wav),
        )
```

Drop any `category=` kwarg if present in the agentic-ja replay's `user_audio_end` payload (the simplify spec already removed it, but if any vestige remains, ensure it isn't here).

- [ ] **Step 5: Add `--idle-settle-ms` CLI flag**

In `parse_args`, add:

```python
    p.add_argument(
        "--idle-settle-ms", type=float, default=500.0,
        help="agent_idle must stay True this many ms before a turn is considered complete.",
    )
```

- [ ] **Step 6: Change the default run-dir**

In the argparse `--out` setup or wherever the default run-dir is built, replace `-bench-agentic-ja` with `-bench-agentic-voice-live`:

```python
    default_out = f"logs/{datetime.now().strftime('%Y%m%d-%H%M%S')}-bench-agentic-voice-live"
```

(Match the exact pattern used in `replay_agentic_ja.py`.)

- [ ] **Step 7: Dispose the waiter on shutdown**

In the cleanup / finally block at the end of `main`, before tearing down the coordinator:

```python
    waiter.dispose()
```

- [ ] **Step 8: Verify the script parses CLI args without booting**

```bash
python scripts/replay_agentic_voice_live.py --help
```

Expected: argparse help text prints, including `--idle-settle-ms`. No traceback.

- [ ] **Step 9: Commit**

```bash
git add scripts/replay_agentic_voice_live.py
git commit -m "$(cat <<'EOF'
feat(bench): replay_agentic_voice_live driver

Parallel to replay_agentic_ja but targets the voice-live blueprint.
Gates inter-turn timing on AzureVoiceLiveAgent.agent_idle settling
to True for --idle-settle-ms (default 500ms), which handles the
multi-response turns produced when function calls are involved.
Calls AzureVoiceLiveAgent.reset_bench_turn() between turns so the
per-turn first_audio_out / first_tool_call flags are cleared.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: End-to-end smoke run and analyzer verification

**Files:** None (verification only)

- [ ] **Step 1: Run a small bench session**

```bash
python scripts/replay_agentic_voice_live.py --runs 1 --warmup 1
```

Expected: completes 10 fixtures (1 warmup pass + 1 measured pass per fixture, depending on existing replay semantics) without `turn_timeout` events. Final log line confirms `logs/{ts}-bench-agentic-voice-live/main.jsonl` was written.

If Azure credentials or robot hardware are unavailable, document this in the run log and proceed to Step 4 using a synthetic JSONL crafted to match the voice-live schema (the analyzer is the verification target here, not the network round-trip).

- [ ] **Step 2: Inspect the JSONL**

```bash
RUN_DIR=$(ls -td logs/*-bench-agentic-voice-live | head -1)
grep -c '"event_kind":"user_audio_end"' "$RUN_DIR/main.jsonl"
grep -c '"event_kind":"first_audio_out"' "$RUN_DIR/main.jsonl"
```

Expected: equal counts (within `turn_timeout` tolerance), matching the number of measured turns.

- [ ] **Step 3: Run the analyzer**

```bash
python scripts/bench_agentic_ja.py "$RUN_DIR"
```

Expected: headline includes `e2e_first_audio_s p50=… p95=… n=…` and `first_tool_call_s p50=… p95=… n=…` (n smaller, since some fixtures are speak-only and don't emit `first_tool_call`). No `agent_first_call_s` / `speak_tts_s` / `turn_total_s` lines.

- [ ] **Step 4: Confirm agentic-ja analyzer output is unchanged**

If a recent agentic-ja run-dir exists:

```bash
python scripts/bench_agentic_ja.py $(ls -td logs/*-bench-agentic-ja | head -1)
```

Expected: headline includes all four metrics (`e2e_first_audio_s`, `agent_first_call_s`, `speak_tts_s`, `turn_total_s`) and existing diagnostic breakdowns (`stt_done`, `llm_step`, etc.).

- [ ] **Step 5: Final pytest pass**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Verify no upstream-tracked file was modified**

```bash
git diff main --stat | grep -v "^ docs/superpowers/\|^ scripts/bench_agentic\|^ scripts/replay_agentic\|^ dimos/agents/bench_ja/\|^ dimos/agents/realtime/\|^ dimos/agents/web_human_input_ja\|^ dimos/agents/skills/speak_skill_ja\|^ dimos/agents/mcp/mcp_client_ja\|^ dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_ja\|^ dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live\|^ tests/bench_fixtures/agentic_ja/\|^ tests/scripts/test_bench_agentic_ja_analyzer\|^ tests/agents/bench_ja/"
```

Expected: no output (every changed file matches a fork-local allow-list pattern). If output appears, inspect those files and revert any unintended upstream-tracked edits.

- [ ] **Step 7: No final commit required**

Task 8 is verification only — all code changes are already committed in Tasks 1–7.
