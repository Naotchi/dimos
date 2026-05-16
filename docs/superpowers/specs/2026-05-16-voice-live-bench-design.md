# Voice Live Bench Design

Status: design (2026-05-16). Companion to `2026-05-16-agentic-ja-bench-simplify-design.md`.

## 1. Purpose & Scope

### Purpose
Introduce end-to-end latency measurement for `unitree_go2_agentic_voice_live` (Azure Voice Live realtime) so it can be compared head-to-head with `unitree_go2_agentic_ja` (STT + LLM + TTS pipeline). Both blueprints answer the same Japanese voice prompts; we want one number that says "from user-finish-speaking to first audio response, X seconds."

### Scope
- `dimos/agents/realtime/azure_voice_live.py` — subscribe WebInput audio, emit two bench events, add a `reset_bench_turn` RPC.
- `scripts/replay_agentic_voice_live.py` — new replay driver, structurally a copy of `replay_agentic_ja.py` with a different blueprint and idle-gate.
- `scripts/bench_agentic_ja.py` — extend to handle voice-live JSONL and emit `e2e_first_audio_s` for both configurations.
- `tests/scripts/test_bench_agentic_ja_analyzer.py` — add voice-live mode coverage and the new common metric.

### Non-goals
- Reproducing `agent_first_call_s` / `speak_tts_s` semantics on voice-live. These metrics are pipeline-stage decompositions that do not have meaningful analogs in a single-model speech-to-speech architecture; they remain agentic-ja-only diagnostics.
- A `--compare` flag in the analyzer. Side-by-side comparison is left to manual inspection or follow-up work.
- Fixture changes. Voice-live reuses `tests/bench_fixtures/agentic_ja/fixtures.yaml` and the existing wavs verbatim.
- `turn_done` / `turn_total_s` for voice-live. Headline metric is `e2e_first_audio_s` only; turn-end detection is not required.
- Upstream-tracked file edits. All instrumentation lives in fork-local files.

## 2. Metrics

### Headline (cross-configuration)

| Metric | Definition | Computed in |
|---|---|---|
| `e2e_first_audio_s` | `first_audio_out.t − user_audio_end.t` | both |

This is the single comparable indicator. It captures the user-perceived "I stopped speaking → I heard the first sound back" interval, which is the only stage interval that is structurally equivalent across the two architectures.

### Voice-live-only diagnostic

| Metric | Definition | Applies to |
|---|---|---|
| `first_tool_call_s` | `first_tool_call.t − user_audio_end.t` | turns where a function call occurred |

Not directly comparable to agentic-ja's `agent_first_call_s`: agentic-ja treats `speak` as a tool, so its `first_tool_call` fires on every turn; voice-live emits `function_call` only for MCP tools, so the event is absent on speak-only turns and the populations differ.

### Agentic-ja-only diagnostics (unchanged)

`agent_first_call_s`, `speak_tts_s`, `stt_done`, `llm_step`, `tools_step`, `turn_done`, `mcp_tool:*` — as defined in `2026-05-16-agentic-ja-bench-simplify-design.md`. The analyzer extension preserves all of these for agentic-ja runs.

## 3. Event Schema (voice-live)

Voice-live reuses `dimos.agents.bench_ja.log_bench_event`. Envelope fields (`event_kind`, `turn_id`, `t`) are auto-injected as in agentic-ja.

| event_kind | Emitted by | When | Payload fields | Per-turn count |
|---|---|---|---|---|
| `user_audio_end` | `scripts/replay_agentic_voice_live.py` | Right after `POST /upload_audio` returns | `fixture_id`, `wav_seconds` | 1 |
| `first_audio_out` | `AzureVoiceLiveAgent._handle_event` (`RESPONSE_AUDIO_DELTA`) | First audio delta of the response that produces the first user-audible chunk | — | 1 |
| `first_tool_call` | `AzureVoiceLiveAgent._handle_event` (`RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE`) | First function call observed since last `reset_bench_turn()` | `tool` (function name) | 0 or 1 |
| `turn_timeout` | `scripts/replay_agentic_voice_live.py` | When the idle-settle wait exceeds `--turn-timeout` | `fixture_id` | 0 or 1 |

Events not emitted on voice-live: `stt_done`, `speak_invoke`, `llm_step`, `model_step`, `tools_step`, `turn_done`, `mcp_tool:*`. These have no clean structural analog in the realtime architecture and would risk introducing comparisons that look numeric but mean different things.

### turn_id

`turn_id` rides in the envelope (auto-injected by `log_bench_event`) for log-grepping consistency with agentic-ja, but the analyzer does not depend on it. Pairing is positional, not by turn_id.

## 4. AzureVoiceLiveAgent Changes

Three localized changes in `dimos/agents/realtime/azure_voice_live.py`.

### 4.1 Subscribe to WebInput audio

In addition to the existing `SounddeviceAudioSource` subscription, subscribe `WebInput.emit_audio()` to the same `_on_mic_audio` handler. The handler is input-source agnostic (AudioEvent → PCM16 → base64 → `input_audio_buffer.append`), so adding a second source requires no new processing logic. The `_mic_active` gate applies to both sources uniformly.

Stop-path cleanup adds a `dispose()` on the WebInput subscription mirroring the existing mic subscription disposal.

This change is independently useful: it lets the WebUI's audio upload path drive the voice-live agent in normal use, not just in bench replay.

### 4.2 Emit `first_audio_out`

Add a `_first_audio_emitted` flag. In the `RESPONSE_AUDIO_DELTA` branch, emit `log_bench_event("first_audio_out")` once when the flag is false, then set it. The flag is reset by `reset_bench_turn()` (see 4.3), not on `RESPONSE_CREATED`, so a turn that spans multiple responses (e.g., function call → continuation) still emits exactly one `first_audio_out` from the agent side.

### 4.3 Emit `first_tool_call` and add `reset_bench_turn` RPC

Add a `_first_tool_call_emitted` flag with the same shape as `_first_audio_emitted`. In the `RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE` branch, emit `log_bench_event("first_tool_call", tool=event.name)` when the flag is false, then set it. Dispatch is unchanged.

Add an `@rpc reset_bench_turn(self) -> None` method that clears both per-turn bench flags (`_first_audio_emitted`, `_first_tool_call_emitted`). The replay driver calls this immediately after `new_turn()`, before posting the wav. Explicit reset keeps the per-turn state transition observable and testable rather than coupling it to an implicit signal like `agent_idle` or `current_turn()` change.

## 5. Replay Driver

`scripts/replay_agentic_voice_live.py` — new file, modeled after `scripts/replay_agentic_ja.py`. The three diffs:

### 5.1 Blueprint

Imports and boots `unitree_go2_agentic_voice_live` instead of `unitree_go2_agentic_ja`.

### 5.2 Idle-settle gating

Agentic-ja's replay waits on `JapaneseSpeakSkill.idle_event`. Voice-live exposes `AzureVoiceLiveAgent.agent_idle` as an observable that toggles `False`/`True` across `RESPONSE_CREATED` / `RESPONSE_DONE`. Because tool-using turns produce multiple `RESPONSE_DONE` events (initial response carrying the function call, then the continuation response after the tool result is sent back), the replay treats the turn as complete only when `agent_idle=True` holds continuously for `--idle-settle-ms` (default 500 ms).

The settle waiter subscribes to `agent_idle`, arms a `threading.Timer` whenever `True` is observed, and cancels the timer on any `False` re-entry. The timer firing sets a `threading.Event` that `wait(timeout)` consumes per turn.

The settle wait is internal replay control. It is not emitted as a bench event. The only failure-mode event from this gate is `turn_timeout` when the overall wait exceeds `--turn-timeout`.

### 5.3 Output directory

Default run directory becomes `logs/{ts}-bench-agentic-voice-live/`. Override via `--out`.

### 5.4 Per-turn flow

```
for fx in fixture_iter(...):
    waiter.clear()
    tid = new_turn()
    agent.reset_bench_turn()
    requests.post(UPLOAD_URL, files={"file": open(fx.wav, "rb")})
    log_bench_event("user_audio_end", fixture_id=fx.id, wav_seconds=wav_seconds(fx.wav))
    if not waiter.wait(timeout=args.turn_timeout):
        log_bench_event("turn_timeout", fixture_id=fx.id)
        continue
```

CLI flags mirror `replay_agentic_ja.py` (`--fixtures`, `--runs`, `--warmup`, `--shuffle`, `--simulation`, `--turn-timeout`, `--initial-idle-timeout`, `--out`) plus `--idle-settle-ms`. The default fixtures path is `tests/bench_fixtures/agentic_ja/fixtures.yaml`.

## 6. Analyzer Extension

`scripts/bench_agentic_ja.py` is extended in place; no second analyzer script.

### 6.1 Mode detection

Detect mode from the run-dir basename:

- `*-bench-agentic-ja/` → `agentic-ja` (all metrics, schema as today)
- `*-bench-agentic-voice-live/` → `voice-live` (subset: `e2e_first_audio_s`, `first_tool_call_s`)
- `--config {ja,voice-live,auto}` for explicit override (default `auto`)

### 6.2 Per-turn metric additions

In `compute_per_turn_metrics`:

- `e2e_first_audio_s = first_audio_out.t - user_audio_end.t` whenever both events exist on a turn. This applies to both modes — the agentic-ja headline gains this metric too.
- `first_tool_call_s = first_tool_call.t - user_audio_end.t` whenever both exist (voice-live mode only in headline output; agentic-ja can compute it but already exposes the equivalent through `agent_first_call_s`, so it is suppressed from agentic-ja output to avoid duplication).

### 6.3 Headline output

Agentic-ja mode:

```
e2e_first_audio_s   p50=… p95=… n=…
agent_first_call_s  p50=… p95=… n=…
speak_tts_s         p50=… p95=… n=…  (excludes no-speak turns)
turn_total_s        p50=… p95=… n=…
```

Voice-live mode:

```
e2e_first_audio_s   p50=… p95=… n=…
first_tool_call_s   p50=… p95=… n=…  (tool-bearing turns only)
```

Existing diagnostic breakdowns (`stt_done`, `llm_step` sum/count, etc.) remain printed in agentic-ja mode and are simply absent in voice-live mode (their events do not exist in the JSONL).

### 6.4 State-machine `build_turns`

The existing in-order state machine handles voice-live JSONL without changes: voice-live emits a strict subset of event kinds, and absent events are naturally skipped. The only positional invariant required is "each `user_audio_end` opens a new turn; subsequent events attribute to that turn until the next `user_audio_end` or end of file." This is what agentic-ja already does.

## 7. Tests

In `tests/scripts/test_bench_agentic_ja_analyzer.py`:

- `test_e2e_first_audio_s_computed_for_agentic_ja` — synthetic agentic-ja JSONL produces a correct `e2e_first_audio_s` consistent with `agent_first_call_s + speak_tts_s` on speak-only turns.
- `test_voice_live_mode_minimal_events` — synthetic voice-live JSONL (only `user_audio_end`, `first_audio_out`, optional `first_tool_call`) computes `e2e_first_audio_s` per turn and produces headline output without crashing on absent ja-only events.
- `test_voice_live_mode_omits_ja_only_metrics` — voice-live mode output does not contain `speak_tts_s`, `agent_first_call_s`, `turn_total_s` lines.
- `test_mode_auto_detection_by_dir_name` — directory basename routes mode correctly.

Out of scope for unit tests: the replay driver (executed via manual e2e bench, as with agentic-ja), the `AzureVoiceLiveAgent` event handler (WebSocket event mocking is heavy; `log_bench_event` itself is already tested), and the WebInput audio subscription wiring (verified via manual e2e through the WebUI upload path).

## 8. Acceptance

- `unitree_go2_agentic_voice_live` blueprint accepts audio uploaded via WebInput's `/upload_audio` endpoint and produces an Azure Voice Live response (manual: upload a wav from the WebUI, hear the spoken response).
- `python scripts/replay_agentic_voice_live.py --runs 3 --warmup 1` completes all 10 fixtures × 3 runs = 30 measured turns without timeout under nominal conditions.
- The resulting `logs/{ts}-bench-agentic-voice-live/main.jsonl` contains 30 `user_audio_end` events and 30 corresponding `first_audio_out` events.
- `python scripts/bench_agentic_ja.py logs/{ts}-bench-agentic-voice-live` auto-detects voice-live mode and prints `e2e_first_audio_s` percentiles and `first_tool_call_s` percentiles.
- Running the same analyzer against an existing `logs/*-bench-agentic-ja/` adds an `e2e_first_audio_s` line and does not regress the existing `agent_first_call_s` / `speak_tts_s` / `turn_total_s` output.
- `pytest tests/scripts/test_bench_agentic_ja_analyzer.py` passes.
- No edits to upstream-tracked files under `dimos/`. `azure_voice_live.py`, the new replay script, the analyzer extensions, and the test additions are all fork-local.
