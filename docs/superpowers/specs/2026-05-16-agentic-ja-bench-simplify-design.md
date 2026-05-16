# agentic_ja Bench Simplification Design

Status: design (2026-05-16). Follow-up to `2026-05-16-agentic-ja-bench-e2e-design.md` based on first-run operational findings.

## 1. Purpose & Scope

### Purpose
Tighten the `unitree_go2_agentic_ja` bench based on what we learned from the initial run:

- Drop misleading indicators (`first_motion_tool`, `parallel` heuristic) that did not measure what their names suggested.
- Decompose end-to-end latency into two orthogonal, contamination-free metrics: `agent_first_call_s` (STT + LLM) and `speak_tts_s` (TTS synth + first chunk).
- Replace the analyzer's wall-clock timestamp bucketing with a simple in-order state machine, since turns are strictly sequential.
- Re-cut fixtures to give varied tool coverage (10 × 3 runs) instead of speak-only + concurrent split.

### Scope
- `tests/bench_fixtures/agentic_ja/` — fixture yaml and wav regeneration.
- `scripts/bench_agentic_ja.py` — analyzer rewrite (state-machine `build_turns`, new metrics, removed aggregations).
- `dimos/agents/mcp/mcp_client_ja.py` — replace `first_motion_tool` emit with `first_tool_call`.
- `dimos/agents/skills/speak_skill_ja.py` — add `speak_invoke` emit at entry of `speak()`.
- `tests/scripts/test_bench_agentic_ja_analyzer.py` — follow analyzer changes.

### Non-goals
- `turn_id` propagation across subprocess boundary (not needed — turns are sequential, state machine suffices).
- Suppressing the LangGraph "final AIMessage" that duplicates the spoken text (low priority; does not affect headline metrics).
- LangGraph node-name regression test (`agent` vs `model` already handled).
- `user_audio_end` timing rework (already fixed in current branch).

## 2. Metrics & Event Schema

### Headline metrics

| Metric | Definition | Measures | Applies to |
|---|---|---|---|
| `agent_first_call_s` | `first_tool_call.t − user_audio_end.t` | STT + LLM | all turns |
| `speak_tts_s` | `first_audio_out.t − first speak_invoke.t` | TTS synth + first chunk | turns where `speak` is called |

These are deliberately independent segments. The user-perceived latency for a clean speak turn is approximately `agent_first_call_s + speak_tts_s`, but each metric is robust whether or not motion precedes speak — neither is polluted by motion execution time.

The previous `e2e_response_s` and `e2e_motion_s` are removed:
- `e2e_response_s` (== `user_audio_end → first_audio_out`) silently included motion execution time when `speak` ran in a later LLM round than the first non-speak tool.
- `e2e_motion_s` measured the moment the LLM emitted a motion `tool_call`, not when the robot physically moved (`execute_sport_command` returns immediately after WebRTC publish, ~2 ms).

### Breakdown metrics (unchanged)

`stt_done`, `llm_step`, `tools_step`, `turn_done`, plus upstream-emitted `mcp_tool:*` correlated to the current turn by the state machine. Kept for diagnostic detail; not part of the SLO surface.

### Event schema changes

**Removed:**

| event_kind | Reason |
|---|---|
| `first_motion_tool` | Misleading — measured LLM tool_call emission, not robot motion. Replaced by `first_tool_call`. |

**Added:**

| event_kind | Emitted by | When | Required fields |
|---|---|---|---|
| `first_tool_call` | `TimedMcpClient` | First `tool_call` observed in any LLM step output (once per turn, regardless of tool name) | `t`, `tool` (actual tool name string) |
| `speak_invoke` | `JapaneseSpeakSkill` | Entry of `speak()`, before TTS synthesis begins. Emitted every time speak is called (multiple per turn allowed) | `t` |

**Unchanged:** `user_audio_end`, `stt_done`, `first_audio_out`, `llm_step`, `model_step`, `tools_step`, `turn_done`, `turn_timeout`.

### Why both `speak_invoke` and `first_audio_out`?

`speak_invoke` marks when the speak tool was called by the LLM; `first_audio_out` marks when the first audio chunk is produced by TTS. The delta is the pure TTS pipeline latency. Decoupling these from `user_audio_end` means motion-then-speak turns no longer contaminate the speak metric — the speak measurement always starts when speak itself is invoked, not when the user finished speaking.

## 3. Analyzer (`scripts/bench_agentic_ja.py`)

### State-machine `build_turns`

Turns are strictly sequential (replay script gates each new turn on `idle_event` set by `turn_done`), so the analyzer walks the JSONL in emit order and tracks the current open turn:

```python
def build_turns(jsonl_path):
    turns = defaultdict(lambda: {
        "llm_steps": [], "tools_steps": [], "mcp_tools": [],
        "speak_invokes": [],
    })
    current = None
    for row in _read_jsonl(jsonl_path):
        kind = row.get("event_kind")
        if kind == "user_audio_end":
            current = row["turn_id"]
            turns[current]["user_audio_end"] = row
        elif current is not None:
            if kind == "stt_done":
                turns[current].setdefault("stt_done", row)
            elif kind in ("llm_step", "model_step"):
                turns[current]["llm_steps"].append(row)
            elif kind == "tools_step":
                turns[current]["tools_steps"].append(row)
            elif kind == "first_tool_call":
                turns[current].setdefault("first_tool_call", row)
            elif kind == "speak_invoke":
                turns[current]["speak_invokes"].append(row)
            elif kind == "first_audio_out":
                turns[current].setdefault("first_audio_out", row)
            elif kind == "turn_done":
                turns[current]["turn_done"] = row
                current = None
            elif kind == "turn_timeout":
                turns[current]["turn_timeout"] = row
                current = None
            elif row.get("event") == "MCP tool done":
                turns[current]["mcp_tools"].append(row)
    return turns
```

### Removed analyzer pieces

- `_parse_iso` and any ISO/wall-clock timestamp parsing.
- `_t0_wall` / `_ts_wall` fields injected into rows.
- `audio_ends` list and the `turn_at` binary search.
- `_is_concurrent_speak_motion`.
- `_CATEGORY_METRICS` and any category-keyed aggregation.
- `single_round_rate` / `parallel` aggregation.
- Old metric computations: `e2e_response_s`, `e2e_motion_s`.

### Headline output

Single pool over all (non-warmup) turns:

```
agent_first_call_s   p50=…  p95=…  n=30
speak_tts_s          p50=…  p95=…  n=27   (excludes turns without speak)
```

Plus the existing breakdown (`stt_done`, `llm_step` sum/count, `tools_step` sum/count, `turn_done.total`) preserved for diagnostics.

### `speak_tts_s` computation

For each turn:

- If `speak_invokes` is non-empty and `first_audio_out` is present: `speak_tts_s = first_audio_out.t − speak_invokes[0].t`.
- Otherwise: turn is omitted from the `speak_tts_s` pool (and counted in the "no speak" denominator footnote).

## 4. Instrumentation Changes

### `dimos/agents/mcp/mcp_client_ja.py`

Replace `first_motion_tool` emission with `first_tool_call`:

- Tool-name filter (`!= "speak"`) is removed.
- Emit on the first `tool_call` observed in any LLM step's output, once per turn.
- Payload: `tool` is the actual tool name string (`"speak"`, `"execute_sport_command"`, `"relative_move"`, etc).

The `llm_nodes = ("agent", "model")` two-name compatibility introduced in the previous spec is preserved.

### `dimos/agents/skills/speak_skill_ja.py`

Add a `speak_invoke` event at the entry of `speak()`, before TTS synthesis begins. This is emitted for every speak call in a turn (no de-duplication). The existing `first_audio_out` emission on the first audio chunk is unchanged (still once-per-turn, first speak only).

### Files not touched

- `dimos/agents/bench_ja/turn_context.py` — unchanged.
- `scripts/replay_agentic_ja.py` — unchanged.
- Upstream files — not modified (fork policy).

## 5. Fixtures

### `tests/bench_fixtures/agentic_ja/fixtures.yaml`

Drop `category` field. 10 fixtures chosen for varied tool coverage:

```yaml
version: 1
fixtures:
  - id: fx_01
    wav: fx_01.wav
    text: "おはよう"
    notes: "speak only — short greeting"
  - id: fx_02
    wav: fx_02.wav
    text: "自己紹介してください"
    notes: "speak only — intro"
  - id: fx_03
    wav: fx_03.wav
    text: "ありがとう"
    notes: "speak only — thanks"
  - id: fx_04
    wav: fx_04.wav
    text: "立ち上がって挨拶してください"
    notes: "sport + speak"
  - id: fx_05
    wav: fx_05.wav
    text: "お座りしてよろしくって言って"
    notes: "sport + speak"
  - id: fx_06
    wav: fx_06.wav
    text: "踊って、その後感想を言って"
    notes: "sport + speak (sequential likely)"
  - id: fx_07
    wav: fx_07.wav
    text: "今何時か教えて"
    notes: "current_time + speak"
  - id: fx_08
    wav: fx_08.wav
    text: "1メートル前に進んで、着いたら教えて"
    notes: "relative_move + speak (sequential)"
  - id: fx_09
    wav: fx_09.wav
    text: "伏せて"
    notes: "sport only — no speak"
  - id: fx_10
    wav: fx_10.wav
    text: "今日の予定を3つ提案して"
    notes: "speak only — longer output"
```

### wav regeneration

Re-run `python scripts/gen_fixtures_agentic_ja.py` to produce `fx_01.wav … fx_10.wav` (16 kHz mono PCM via pyopenjtalk, same pipeline as before).

### Files removed

```
tests/bench_fixtures/agentic_ja/speak_001.wav … speak_003.wav
tests/bench_fixtures/agentic_ja/concurrent_001.wav … concurrent_003.wav
```

### Run configuration

3 runs per fixture (+ existing warmup behavior) = 30 measured turns per bench session.

## 6. Tests

`tests/scripts/test_bench_agentic_ja_analyzer.py`:

- Remove all `concurrent` / `category` / `single_round_rate` / `parallel` tests.
- Replace `e2e_response_s` / `e2e_motion_s` / `first_motion_tool` references with `agent_first_call_s` / `speak_tts_s` / `first_tool_call` / `speak_invoke`.
- Update synthetic JSONL fixtures to reflect:
  - state-machine ordering (no wall-clock fields required to drive matching),
  - new event kinds,
  - turns with/without `speak_invoke` (to validate the "no speak" exclusion in `speak_tts_s`).

## 7. Acceptance

- Bench runs to completion against `unitree_go2_agentic_ja` with the new fixture set.
- Analyzer prints `agent_first_call_s` and `speak_tts_s` headline lines plus existing breakdown, with no `e2e_response_s` / `e2e_motion_s` / `parallel` references.
- Unit tests pass.
- No edits to upstream-tracked files outside the four fork-local targets listed in §1 Scope.
