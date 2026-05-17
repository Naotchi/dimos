"""Tests for the dual-mode (updates+messages) stream tracker."""

from __future__ import annotations

from typing import Any

from dimos.agents.bench_ja.stream_tracker import StepFirstTokenTracker


def _msg_chunk(node: str) -> tuple[str, Any]:
    # LangGraph "messages" mode yields (AIMessageChunk, metadata).
    # We only look at metadata["langgraph_node"], so any object with content works.
    return ("messages", (object(), {"langgraph_node": node}))


def _update(node: str) -> tuple[str, Any]:
    return ("updates", {node: {"messages": []}})


def test_first_messages_chunk_for_agent_node_yields_first_token():
    tracker = StepFirstTokenTracker(llm_node_names=("agent", "model"))
    events = list(tracker.feed_many([_msg_chunk("agent"), _msg_chunk("agent"), _update("agent")]))
    # Only the first chunk should yield a first_token event for step 0.
    assert events == [{"kind": "llm_first_token", "step_idx": 0}]


def test_messages_for_non_llm_node_are_ignored():
    tracker = StepFirstTokenTracker(llm_node_names=("agent", "model"))
    events = list(tracker.feed_many([_msg_chunk("tools"), _msg_chunk("agent")]))
    assert events == [{"kind": "llm_first_token", "step_idx": 0}]


def test_step_advances_on_llm_node_update():
    tracker = StepFirstTokenTracker(llm_node_names=("agent", "model"))
    events = list(tracker.feed_many([
        _msg_chunk("agent"),   # step 0 first token
        _update("agent"),      # close step 0
        _msg_chunk("agent"),   # step 1 first token
        _msg_chunk("agent"),   # already seen in step 1; ignored
        _update("agent"),      # close step 1
    ]))
    assert events == [
        {"kind": "llm_first_token", "step_idx": 0},
        {"kind": "llm_first_token", "step_idx": 1},
    ]


def test_non_llm_updates_do_not_advance_step():
    tracker = StepFirstTokenTracker(llm_node_names=("agent", "model"))
    events = list(tracker.feed_many([
        _msg_chunk("agent"),
        _update("agent"),
        _update("tools"),     # tools update should NOT consume a step
        _msg_chunk("agent"),  # step 1 first token
    ]))
    assert events == [
        {"kind": "llm_first_token", "step_idx": 0},
        {"kind": "llm_first_token", "step_idx": 1},
    ]


def test_no_messages_chunks_emits_nothing():
    tracker = StepFirstTokenTracker(llm_node_names=("agent", "model"))
    events = list(tracker.feed_many([_update("agent"), _update("tools")]))
    assert events == []
