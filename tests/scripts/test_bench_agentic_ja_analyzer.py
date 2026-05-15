"""Unit tests for the bench_agentic_ja analyzer."""

import json
from pathlib import Path

import pytest

from scripts.bench_agentic_ja import (
    _percentile,
    aggregate,
    build_turns,
    compute_per_turn_metrics,
)


def _line(d):
    return json.dumps(d) + "\n"


@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    """Two turns: one with both speak and motion, one with only speak."""
    p = tmp_path / "main.jsonl"
    lines = [
        # Turn A: speak + motion, NOT warmup
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

        # Turn B: speak only, IS warmup
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
    assert "speak_only" not in agg["by_category"]  # warmup-only category dropped
    assert agg["by_category"]["both"]["e2e_response_s"]["n"] == 1


def test_percentile_basic():
    import math
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0
    # Empty list → NaN (NaN != NaN)
    assert math.isnan(_percentile([], 0.5))
