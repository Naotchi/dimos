# agentic_ja Bench Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `e2e_response_s` / `e2e_motion_s` with `agent_first_call_s` + `speak_tts_s`, rewrite the analyzer as a state machine over sequential turns, drop category/parallel splits, and re-cut fixtures to 10 varied prompts × 3 runs.

**Architecture:** Spec-driven follow-up to `docs/superpowers/specs/2026-05-16-agentic-ja-bench-simplify-design.md`. Instrumentation emits two new events (`first_tool_call`, `speak_invoke`) and removes one (`first_motion_tool`). Analyzer walks `main.jsonl` in emit order using a single-turn-open state machine — no wall-clock timestamp matching, no category-keyed aggregation. Fixtures get re-cut to widen tool coverage.

**Tech Stack:** Python 3, pytest, LangGraph (existing), pyopenjtalk (for wav synth via existing `gen_fixtures_agentic_ja.py`).

**Reference spec:** `docs/superpowers/specs/2026-05-16-agentic-ja-bench-simplify-design.md`

**Conventions:**
- `.venv` is sourced; use `python` / `pytest` directly (not `python3`, not `uv run`).
- Fork policy: only modify files listed in the spec's §1 Scope. Do not edit upstream files.
- Commit after each task. Use Conventional Commits style (matches recent git log).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `scripts/bench_agentic_ja.py` | modify | Analyzer: state-machine `build_turns`, new metric set, single-pool aggregate, simplified headline |
| `tests/scripts/test_bench_agentic_ja_analyzer.py` | rewrite | Tests for new analyzer surface |
| `dimos/agents/mcp/mcp_client_ja.py` | modify | Emit `first_tool_call` (replacing `first_motion_tool`) |
| `dimos/agents/skills/speak_skill_ja.py` | modify | Emit `speak_invoke` at entry of `speak()` |
| `tests/bench_fixtures/agentic_ja/fixtures.yaml` | rewrite | 10 new fixtures, no category field |
| `tests/bench_fixtures/agentic_ja/fx_01..fx_10.wav` | create | Regenerate via existing gen script |
| `tests/bench_fixtures/agentic_ja/speak_*.wav` | delete | Old fixtures |
| `tests/bench_fixtures/agentic_ja/concurrent_*.wav` | delete | Old fixtures |
| `tests/bench_fixtures/agentic_ja/README.md` | modify | Update for the new fixture set |

The order below puts analyzer changes first (most testable, no robot/LLM needed), then instrumentation, then fixtures.

---

## Task 1: Rewrite `build_turns` as a state machine (TDD)

**Files:**
- Modify: `scripts/bench_agentic_ja.py` (`build_turns` + helpers around it)
- Modify: `tests/scripts/test_bench_agentic_ja_analyzer.py`

Sequential turns mean the analyzer no longer needs wall-clock timestamp matching. It walks the JSONL once, tracking which turn (if any) is currently open. `user_audio_end` opens a turn; `turn_done` or `turn_timeout` closes it. Everything in between is attributed to the open turn.

- [ ] **Step 1: Replace the test file with the new fixture and state-machine tests**

Overwrite `tests/scripts/test_bench_agentic_ja_analyzer.py` with:

```python
"""Tests for the agentic_ja bench analyzer (state-machine variant)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from bench_agentic_ja import (  # noqa: E402
    _percentile,
    aggregate,
    build_turns,
    compute_per_turn_metrics,
)


def _line(d: dict) -> str:
    return json.dumps(d) + "\n"


@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    """Three turns:

    - A (live, speak in round 0):     user_audio_end -> stt_done -> first_tool_call(speak)
                                      -> speak_invoke -> first_audio_out -> turn_done
    - B (live, motion-only, no speak): user_audio_end -> first_tool_call(execute_sport_command)
                                       -> turn_done
    - C (warmup, speak): same shape as A but warmup=True
    """
    path = tmp_path / "main.jsonl"
    lines = [
        # --- Turn A: live, speak in round 0 ---
        _line({"event_kind": "user_audio_end", "turn_id": "A", "t": 0.0,
               "fixture_id": "fx_01", "run_idx": 0, "warmup": False,
               "audio_seconds": 1.0}),
        _line({"event_kind": "stt_done", "turn_id": None, "duration_s": 0.4}),
        _line({"event_kind": "first_tool_call", "turn_id": None, "t": 0.7, "tool": "speak"}),
        _line({"event_kind": "llm_step", "turn_id": None, "duration_s": 0.5,
               "node": "model", "step_idx": 0, "n_messages": 1}),
        _line({"event_kind": "speak_invoke", "turn_id": None, "t": 0.75}),
        _line({"event_kind": "first_audio_out", "turn_id": None, "t": 0.95, "tool": "speak"}),
        _line({"event_kind": "tools_step", "turn_id": None, "duration_s": 0.1,
               "node": "tools", "step_idx": 1, "n_messages": 1}),
        _line({"event_kind": "turn_done", "turn_id": None, "duration_s": 1.0,
               "llm_s": 0.5, "n_steps": 2, "n_tool_calls": 1}),

        # --- Turn B: live, motion-only, no speak ---
        _line({"event_kind": "user_audio_end", "turn_id": "B", "t": 0.0,
               "fixture_id": "fx_09", "run_idx": 0, "warmup": False,
               "audio_seconds": 0.8}),
        _line({"event_kind": "stt_done", "turn_id": None, "duration_s": 0.3}),
        _line({"event_kind": "first_tool_call", "turn_id": None, "t": 0.6,
               "tool": "execute_sport_command"}),
        _line({"event_kind": "llm_step", "turn_id": None, "duration_s": 0.4,
               "node": "model", "step_idx": 0, "n_messages": 1}),
        _line({"event_kind": "tools_step", "turn_id": None, "duration_s": 0.05,
               "node": "tools", "step_idx": 1, "n_messages": 1}),
        _line({"event_kind": "turn_done", "turn_id": None, "duration_s": 0.7,
               "llm_s": 0.4, "n_steps": 2, "n_tool_calls": 1}),

        # --- Turn C: warmup, speak ---
        _line({"event_kind": "user_audio_end", "turn_id": "C", "t": 0.0,
               "fixture_id": "fx_01", "run_idx": 0, "warmup": True,
               "audio_seconds": 1.0}),
        _line({"event_kind": "stt_done", "turn_id": None, "duration_s": 0.4}),
        _line({"event_kind": "first_tool_call", "turn_id": None, "t": 0.7, "tool": "speak"}),
        _line({"event_kind": "speak_invoke", "turn_id": None, "t": 0.75}),
        _line({"event_kind": "first_audio_out", "turn_id": None, "t": 0.95, "tool": "speak"}),
        _line({"event_kind": "turn_done", "turn_id": None, "duration_s": 1.0,
               "llm_s": 0.5, "n_steps": 2, "n_tool_calls": 1}),
    ]
    path.write_text("".join(lines))
    return path


def test_build_turns_state_machine_groups_by_open_turn(jsonl_path: Path) -> None:
    turns = build_turns(jsonl_path)
    assert set(turns.keys()) == {"A", "B", "C"}

    a = turns["A"]
    assert a["user_audio_end"]["fixture_id"] == "fx_01"
    assert a["first_tool_call"]["tool"] == "speak"
    assert len(a["speak_invokes"]) == 1
    assert a["first_audio_out"]["t"] == 0.95
    assert len(a["llm_steps"]) == 1
    assert len(a["tools_steps"]) == 1
    assert a["turn_done"]["duration_s"] == 1.0

    b = turns["B"]
    assert b["first_tool_call"]["tool"] == "execute_sport_command"
    assert "first_audio_out" not in b
    assert b["speak_invokes"] == []
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py::test_build_turns_state_machine_groups_by_open_turn -v`

Expected: FAIL — the imports may succeed but the existing `build_turns` doesn't track `speak_invokes` and ignores `first_tool_call` events.

- [ ] **Step 3: Rewrite `build_turns` and remove now-dead helpers**

In `scripts/bench_agentic_ja.py`:

1. Delete `_parse_iso` (function around line 34-41) — no longer used.
2. Replace `build_turns` (lines ~73-151) with the state-machine version:

```python
def build_turns(jsonl_path: Path) -> dict[str, dict[str, Any]]:
    """Group bench events by their currently-open turn.

    Turns are strictly sequential (the replay script gates each new turn on
    idle_event set by turn_done), so the analyzer can walk main.jsonl in emit
    order and attribute every event after `user_audio_end` to that turn until
    `turn_done` / `turn_timeout` closes it. No wall-clock matching needed.
    """
    rows = _read_jsonl(jsonl_path)
    turns: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "llm_steps": [],
            "tools_steps": [],
            "mcp_tools": [],
            "speak_invokes": [],
        }
    )

    current: str | None = None
    for row in rows:
        kind = row.get("event_kind")
        if kind == "user_audio_end":
            current = row.get("turn_id")
            if current is None:
                continue
            turns[current]["user_audio_end"] = row
            continue

        if current is None:
            # Event arrived before any turn was opened (shouldn't happen in
            # well-formed logs, but be defensive).
            continue

        if kind == "stt_done":
            turns[current].setdefault("stt_done", row)
        elif kind in ("llm_step", "model_step"):
            turns[current]["llm_steps"].append(row)
        elif kind == "tools_step" or (
            isinstance(kind, str)
            and kind.endswith("_step")
            and kind not in ("llm_step", "model_step")
        ):
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
            duration = _parse_duration(row.get("duration"))
            turns[current]["mcp_tools"].append(
                {"tool": row.get("tool", "?"), "duration": duration}
            )

    return dict(turns)
```

Note the dropped fields: no more `_t0_wall` / `_ts_wall` injection, no `t` on `mcp_tools` entries (it was only used by `_is_concurrent_speak_motion`, which is being removed in Task 3).

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py::test_build_turns_state_machine_groups_by_open_turn -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_agentic_ja.py tests/scripts/test_bench_agentic_ja_analyzer.py
git commit -m "$(cat <<'EOF'
refactor(bench_agentic_ja): walk main.jsonl as a state machine

Replaces the two-pass wall-clock bucketing of build_turns with a single
in-order traversal that tracks one open turn at a time. Turns are
sequential by construction (replay script gates on idle_event), so this
is sufficient and removes the _parse_iso / audio_ends / turn_at machinery.

Also collects speak_invoke events and first_tool_call events so the
follow-up metric rewrite has the data it needs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: New per-turn metrics (`agent_first_call_s`, `speak_tts_s`)

**Files:**
- Modify: `scripts/bench_agentic_ja.py` (`compute_per_turn_metrics`, `_METRIC_KEYS`)
- Modify: `tests/scripts/test_bench_agentic_ja_analyzer.py` (add test)

- [ ] **Step 1: Add the failing test**

Append to `tests/scripts/test_bench_agentic_ja_analyzer.py`:

```python
def test_compute_per_turn_metrics_new_indicators(jsonl_path: Path) -> None:
    turns = build_turns(jsonl_path)
    metrics = compute_per_turn_metrics(turns)

    # Turn A: speak in round 0
    a = metrics["A"]
    assert a["fixture_id"] == "fx_01"
    assert a["warmup"] is False
    # first_tool_call.t = 0.7, user_audio_end.t = 0.0
    assert a["agent_first_call_s"] == pytest.approx(0.7)
    # first_audio_out.t = 0.95, first speak_invoke.t = 0.75
    assert a["speak_tts_s"] == pytest.approx(0.20)
    assert a["stt_s"] == pytest.approx(0.4)
    assert a["llm_total_s"] == pytest.approx(0.5)
    assert a["tools_total_s"] == pytest.approx(0.1)
    assert a["turn_total_s"] == pytest.approx(1.0)
    # Old indicators must not exist.
    assert "e2e_response_s" not in a
    assert "e2e_motion_s" not in a
    assert "category" not in a

    # Turn B: motion-only, no speak
    b = metrics["B"]
    assert b["agent_first_call_s"] == pytest.approx(0.6)
    assert b["speak_tts_s"] is None  # excluded because no speak
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py::test_compute_per_turn_metrics_new_indicators -v`

Expected: FAIL — `agent_first_call_s` / `speak_tts_s` not yet computed.

- [ ] **Step 3: Rewrite `compute_per_turn_metrics` and `_METRIC_KEYS`**

In `scripts/bench_agentic_ja.py`:

Replace the existing `compute_per_turn_metrics` (lines ~154-199) with:

```python
def compute_per_turn_metrics(turns: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Convert grouped events into per-turn numeric metrics.

    Two headline indicators are computed:

    - ``agent_first_call_s``: ``first_tool_call.t - user_audio_end.t``.
      Measures STT + LLM latency. Defined for every turn that has a
      ``first_tool_call`` event.
    - ``speak_tts_s``: ``first_audio_out.t - first speak_invoke.t``.
      Measures TTS synth + first-chunk latency. Defined only for turns
      where ``speak`` was invoked.

    Splitting these two intervals avoids the contamination that ``e2e_response_s``
    suffered when motion preceded speak in a later LLM round.
    """
    metrics: dict[str, dict[str, Any]] = {}
    for turn_id, data in turns.items():
        ue = data.get("user_audio_end")
        if not ue:
            continue
        t0 = ue.get("t")

        ftc = data.get("first_tool_call")
        fao = data.get("first_audio_out")
        stt = data.get("stt_done")
        td = data.get("turn_done")
        speak_invokes = data.get("speak_invokes", [])

        def _delta(row: dict[str, Any] | None, t_base: float | None) -> float | None:
            if row is None or t_base is None:
                return None
            t = row.get("t")
            return (t - t_base) if t is not None else None

        speak_tts_s: float | None = None
        if speak_invokes and fao is not None:
            speak_tts_s = _delta(fao, speak_invokes[0].get("t"))

        llm_durations = [
            _parse_duration(s.get("duration_s")) or 0.0 for s in data.get("llm_steps", [])
        ]
        tools_durations = [
            _parse_duration(s.get("duration_s")) or 0.0 for s in data.get("tools_steps", [])
        ]
        metrics[turn_id] = {
            "fixture_id": ue.get("fixture_id"),
            "run_idx": ue.get("run_idx"),
            "warmup": bool(ue.get("warmup")),
            "audio_seconds": ue.get("audio_seconds"),
            "agent_first_call_s": _delta(ftc, t0),
            "speak_tts_s": speak_tts_s,
            "stt_s": _parse_duration(stt.get("duration_s")) if stt else None,
            "llm_total_s": sum(llm_durations) if llm_durations else None,
            "tools_total_s": sum(tools_durations) if tools_durations else None,
            "turn_total_s": _parse_duration(td.get("duration_s")) if td else None,
            "n_mcp_tools": len(data.get("mcp_tools", [])),
            "mcp_tools": data.get("mcp_tools", []),
            "timeout": "turn_timeout" in data,
        }
    return metrics
```

Also replace `_METRIC_KEYS` (lines ~251-258) with:

```python
_METRIC_KEYS = (
    "agent_first_call_s",
    "speak_tts_s",
    "stt_s",
    "llm_total_s",
    "tools_total_s",
    "turn_total_s",
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py::test_compute_per_turn_metrics_new_indicators -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_agentic_ja.py tests/scripts/test_bench_agentic_ja_analyzer.py
git commit -m "$(cat <<'EOF'
feat(bench_agentic_ja): agent_first_call_s and speak_tts_s metrics

Replaces e2e_response_s / e2e_motion_s with two orthogonal segments:
agent_first_call_s (user_audio_end -> first_tool_call) and speak_tts_s
(first speak_invoke -> first_audio_out). The new split is robust to
motion-then-speak turns where the old e2e_response_s silently absorbed
motion execution time.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Single-pool aggregate (drop category / parallel)

**Files:**
- Modify: `scripts/bench_agentic_ja.py` (`aggregate`, related helpers, `main`)
- Modify: `tests/scripts/test_bench_agentic_ja_analyzer.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/scripts/test_bench_agentic_ja_analyzer.py`:

```python
def test_aggregate_single_pool_drops_warmup(jsonl_path: Path) -> None:
    turns = build_turns(jsonl_path)
    metrics = compute_per_turn_metrics(turns)
    agg = aggregate(metrics)

    # Single-pool aggregate: two live turns (A and B), warmup C dropped.
    assert agg["n_turns"] == 2
    assert agg["metrics"]["agent_first_call_s"]["n"] == 2
    assert agg["metrics"]["agent_first_call_s"]["min"] == pytest.approx(0.6)
    assert agg["metrics"]["agent_first_call_s"]["max"] == pytest.approx(0.7)
    # speak_tts_s: only turn A contributed.
    assert agg["metrics"]["speak_tts_s"]["n"] == 1
    assert agg["metrics"]["speak_tts_s"]["p50"] == pytest.approx(0.20)

    # No category split, no concurrent_parallel block.
    assert "by_category" not in agg
    assert "concurrent_parallel" not in agg


def test_percentile_basic_unchanged() -> None:
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
    assert _percentile([], 0.5) != _percentile([], 0.5)  # NaN
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v`

Expected: the new aggregate test FAILS (current `aggregate` returns `{"by_category": …, "concurrent_parallel": …}`). The `_percentile` test should pass.

- [ ] **Step 3: Rewrite `aggregate` and trim dead code**

In `scripts/bench_agentic_ja.py`:

1. Delete `_is_concurrent_speak_motion` (around lines 202-225).
2. Delete `_CATEGORY_METRICS` (around lines 261-273).
3. Replace `aggregate` (around lines 276-306) with:

```python
def aggregate(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate non-warmup turns into a single pool.

    Returns the count of live turns and a per-metric summary
    (n / mean / p50 / p95 / max / min) for every key in _METRIC_KEYS.
    """
    live = [m for m in metrics.values() if not m["warmup"]]
    return {
        "n_turns": len(live),
        "metrics": {k: _summarize([m.get(k) for m in live]) for k in _METRIC_KEYS},
    }
```

4. Rewrite the headline section of `main` (around lines 346-376). Replace from the `# Headline SLO numbers` comment through the `for cat, data in agg["by_category"].items():` block with:

```python
    live = [m for m in metrics.values() if not m["warmup"]]
    print(f"run: {run_dir}")
    print(f"turns analyzed (non-warmup): {len(live)} / {len(metrics)} total")

    # Headline indicators.
    print("\n== headline ==")
    afc = agg["metrics"]["agent_first_call_s"]
    sts = agg["metrics"]["speak_tts_s"]
    print(
        f"agent_first_call_s   n={afc['n']}/{len(live)}  "
        f"p50={afc['p50']:.2f}s  p95={afc['p95']:.2f}s"
    )
    n_no_speak = len(live) - sts["n"]
    no_speak_note = f"  ({n_no_speak} turn(s) had no speak)" if n_no_speak else ""
    print(
        f"speak_tts_s          n={sts['n']}/{len(live)}  "
        f"p50={sts['p50']:.2f}s  p95={sts['p95']:.2f}s{no_speak_note}"
    )

    _print_table(f"all turns (n={len(live)})", agg["metrics"])
```

5. Remove the concurrent-parallel printout block immediately after (the `if cp["metrics"] is not None: _print_table(...)`).

6. Also strip the `concurrent_parallel` / `by_category` fields from the JSON dump — the current writer just dumps `agg` so it will follow the new shape automatically.

- [ ] **Step 4: Run all analyzer tests**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v`

Expected: all tests PASS (state machine, new metrics, single-pool aggregate, percentile).

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_agentic_ja.py tests/scripts/test_bench_agentic_ja_analyzer.py
git commit -m "$(cat <<'EOF'
refactor(bench_agentic_ja): single-pool aggregate, drop category split

Removes _is_concurrent_speak_motion, _CATEGORY_METRICS, and the
concurrent_parallel surface. All non-warmup turns are now aggregated
into one pool; the headline prints agent_first_call_s and speak_tts_s
side by side with a note when some turns had no speak.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Smoke-run analyzer on an existing log (no robot needed)

This task verifies the rewritten analyzer doesn't crash on a real log file that contains the **old** event schema (mix of `first_motion_tool` etc). The old events should be silently ignored — we should still get an output, just with low n.

**Files:**
- None (read-only smoke test)

- [ ] **Step 1: Locate a recent run directory**

```bash
ls -dt logs/*-bench-agentic-ja logs/*-unitree-go2-agentic-ja 2>/dev/null | head -3
```

If no real run is available, skip this task and proceed to Task 5 — the unit tests are the authoritative gate.

- [ ] **Step 2: Run the analyzer**

```bash
python scripts/bench_agentic_ja.py
```

Expected: exits 0, prints a `headline` block and an `all turns` table. `speak_tts_s` will likely have `n=0` (no `speak_invoke` events in old logs) — that's fine, this run just confirms the analyzer is robust to old logs.

- [ ] **Step 3: (No commit — read-only smoke)**

---

## Task 5: Emit `first_tool_call` from `TimedMcpClient`

**Files:**
- Modify: `dimos/agents/mcp/mcp_client_ja.py:55-79`

This drops the speak-vs-motion filter and emits the very first `tool_call` regardless of tool name, with the actual tool name in the payload.

- [ ] **Step 1: Update the emit logic**

In `dimos/agents/mcp/mcp_client_ja.py`:

Replace the docstring fragment at line 40:

```
      - first_motion_tool : first tool_call where tool name != 'speak', once per turn
```

with:

```
      - first_tool_call : first tool_call observed in any LLM step, once per turn
```

Replace lines 56-79 (the body around `motion_logged` and the inner `for tc in tool_calls:` loop) with:

```python
        first_tool_logged = False

        # LangGraph's prebuilt agent node has been called "agent" historically
        # and "model" in newer versions; treat both as the LLM step.
        llm_nodes = ("agent", "model")
        for update in state_graph.stream({"messages": self._history}, stream_mode="updates"):
            for node_name, node_output in update.items():
                elapsed = time.perf_counter() - step_t0
                msgs = node_output.get("messages", []) if isinstance(node_output, dict) else []
                kind = "llm_step" if node_name in llm_nodes else f"{node_name}_step"

                if node_name in llm_nodes:
                    total_llm += elapsed
                    for m in msgs:
                        tool_calls = getattr(m, "tool_calls", []) or []
                        n_tool_calls += len(tool_calls)
                        if not first_tool_logged:
                            for tc in tool_calls:
                                tool_name = (
                                    tc.get("name") if isinstance(tc, dict)
                                    else getattr(tc, "name", None)
                                )
                                if tool_name:
                                    log_bench_event("first_tool_call", tool=tool_name)
                                    first_tool_logged = True
                                    break
```

- [ ] **Step 2: Sanity-check imports / syntax**

Run: `python -c "from dimos.agents.mcp.mcp_client_ja import TimedMcpClient"`

Expected: no exception.

- [ ] **Step 3: Confirm analyzer tests still pass**

Run: `pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v`

Expected: PASS (unchanged — this task doesn't touch the analyzer).

- [ ] **Step 4: Commit**

```bash
git add dimos/agents/mcp/mcp_client_ja.py
git commit -m "$(cat <<'EOF'
feat(mcp_client_ja): emit first_tool_call (replaces first_motion_tool)

The previous first_motion_tool filtered out 'speak' and only fired for
non-speak tool calls. With the new metric split (agent_first_call_s),
the analyzer wants the timestamp of the very first tool call regardless
of tool name; the payload carries the actual tool name string so
diagnostics can still see what fired first.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Emit `speak_invoke` from `JapaneseSpeakSkill`

**Files:**
- Modify: `dimos/agents/skills/speak_skill_ja.py:67-81`

- [ ] **Step 1: Add the emit at the entry of `speak()`**

In `dimos/agents/skills/speak_skill_ja.py`, replace the `speak` method (lines 67-81) with:

```python
    @skill
    def speak(self, text: str, blocking: bool = True) -> str:
        """Speak text out loud through the robot's speakers.

        USE THIS TOOL AS OFTEN AS NEEDED. People can't normally see what you say in text, but can hear what you speak.

        Try to be as concise as possible. Remember that speaking takes time, so get to the point quickly.

        Example usage:

            speak("こんにちは、ロボットアシスタントです。")
        """
        log_bench_event("speak_invoke")
        with self._first_chunk_lock:
            self._first_chunk_pending = True
        return super().speak(text, blocking=blocking)
```

The `speak_invoke` emit is unconditional — every call produces one event. The analyzer takes the first one per turn to compute `speak_tts_s`.

- [ ] **Step 2: Sanity-check imports / syntax**

Run: `python -c "from dimos.agents.skills.speak_skill_ja import JapaneseSpeakSkill"`

Expected: no exception.

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/skills/speak_skill_ja.py
git commit -m "$(cat <<'EOF'
feat(speak_skill_ja): emit speak_invoke on every speak() call

Marks the moment speak is invoked, so the analyzer can measure
speak_tts_s = first_audio_out.t - first speak_invoke.t per turn.
Independent of any user_audio_end, so motion-then-speak turns no longer
contaminate the TTS-only segment.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Rewrite fixtures.yaml (10 prompts, no category)

**Files:**
- Modify: `tests/bench_fixtures/agentic_ja/fixtures.yaml`
- Modify: `tests/bench_fixtures/agentic_ja/README.md`

- [ ] **Step 1: Overwrite fixtures.yaml**

Write to `tests/bench_fixtures/agentic_ja/fixtures.yaml`:

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

- [ ] **Step 2: Update the README**

Overwrite `tests/bench_fixtures/agentic_ja/README.md`:

```markdown
# agentic_ja bench fixtures

WAV fixtures for `scripts/replay_agentic_ja.py`.

- 16 kHz mono PCM WAV, synthesized from the `text` field of `fixtures.yaml` via pyopenjtalk.
- Regenerate with `python scripts/gen_fixtures_agentic_ja.py`.
- 10 prompts chosen for varied tool coverage (`speak` only, `sport + speak`, `current_time`, `relative_move`, `sport` only); default replay is 3 runs per fixture.
- Caveat: pyopenjtalk synthesis is what `JapaneseSpeakSkill` also uses, so Whisper may transcribe these unrealistically well versus human speech. Acceptable for in-stack regression bench; not a substitute for human-recorded fixtures when comparing STT providers.
```

- [ ] **Step 3: Delete the old wav files**

```bash
rm tests/bench_fixtures/agentic_ja/speak_001.wav \
   tests/bench_fixtures/agentic_ja/speak_002.wav \
   tests/bench_fixtures/agentic_ja/speak_003.wav \
   tests/bench_fixtures/agentic_ja/concurrent_001.wav \
   tests/bench_fixtures/agentic_ja/concurrent_002.wav \
   tests/bench_fixtures/agentic_ja/concurrent_003.wav
```

Confirm the remaining contents:

```bash
ls tests/bench_fixtures/agentic_ja/
```

Expected: `fixtures.yaml` and `README.md` only (no wavs yet — Step 4 generates them).

- [ ] **Step 4: Regenerate wav files**

```bash
python scripts/gen_fixtures_agentic_ja.py
```

Expected output ends with `done: generated=10, skipped(existing)=0`. The directory should now contain `fx_01.wav` … `fx_10.wav`.

- [ ] **Step 5: Commit**

```bash
git add tests/bench_fixtures/agentic_ja/fixtures.yaml \
        tests/bench_fixtures/agentic_ja/README.md \
        tests/bench_fixtures/agentic_ja/fx_01.wav \
        tests/bench_fixtures/agentic_ja/fx_02.wav \
        tests/bench_fixtures/agentic_ja/fx_03.wav \
        tests/bench_fixtures/agentic_ja/fx_04.wav \
        tests/bench_fixtures/agentic_ja/fx_05.wav \
        tests/bench_fixtures/agentic_ja/fx_06.wav \
        tests/bench_fixtures/agentic_ja/fx_07.wav \
        tests/bench_fixtures/agentic_ja/fx_08.wav \
        tests/bench_fixtures/agentic_ja/fx_09.wav \
        tests/bench_fixtures/agentic_ja/fx_10.wav
git rm tests/bench_fixtures/agentic_ja/speak_001.wav \
       tests/bench_fixtures/agentic_ja/speak_002.wav \
       tests/bench_fixtures/agentic_ja/speak_003.wav \
       tests/bench_fixtures/agentic_ja/concurrent_001.wav \
       tests/bench_fixtures/agentic_ja/concurrent_002.wav \
       tests/bench_fixtures/agentic_ja/concurrent_003.wav 2>/dev/null || true
git commit -m "$(cat <<'EOF'
feat(bench_fixtures): recut to 10 varied prompts; drop category field

Replaces speak_only / concurrent split (6 wavs) with 10 prompts that
exercise speak only, sport+speak, current_time, relative_move, and
sport-only paths. Default replay is 3 runs per fixture = 30 measured
turns per bench session.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final cross-check

**Files:**
- None (verification only)

- [ ] **Step 1: Re-run the full analyzer test suite**

```bash
pytest tests/scripts/test_bench_agentic_ja_analyzer.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Confirm there are no leftover references to retired symbols**

```bash
grep -rn "e2e_response_s\|e2e_motion_s\|first_motion_tool\|_is_concurrent_speak_motion\|_CATEGORY_METRICS\|single_round_rate" \
    scripts/bench_agentic_ja.py \
    tests/scripts/test_bench_agentic_ja_analyzer.py \
    dimos/agents/mcp/mcp_client_ja.py \
    dimos/agents/skills/speak_skill_ja.py \
    || echo "clean"
```

Expected: prints `clean` (grep exit code 1, the `||` branch). If any line matches, go back and remove it.

- [ ] **Step 3: Confirm the spec acceptance criteria**

Re-read `docs/superpowers/specs/2026-05-16-agentic-ja-bench-simplify-design.md` §7 ("Acceptance") and tick each item:

- [ ] Bench would run end-to-end on the new fixtures (verified by Task 4's analyzer smoke + Task 5/6 syntax check; full live run requires robot + LLM creds).
- [ ] Analyzer prints `agent_first_call_s` / `speak_tts_s` headline + breakdown table; no `e2e_response_s` / `e2e_motion_s` / `parallel` references.
- [ ] Unit tests pass.
- [ ] No upstream-tracked file modified outside the four fork-local targets.

- [ ] **Step 4: (No commit unless something needed fixing)**
