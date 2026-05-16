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
