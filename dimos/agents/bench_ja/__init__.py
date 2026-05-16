"""Bench instrumentation utilities for the agentic_ja blueprint family.

Owns the per-turn correlation id (ContextVar) and a single chokepoint for
emitting structured bench events so the JSONL schema cannot drift across
the rewritten *_ja.py files.
"""

from dimos.agents.bench_ja.turn_context import (
    current_turn,
    log_bench_event,
    new_turn,
    reset,
)

__all__ = ["current_turn", "log_bench_event", "new_turn", "reset"]
