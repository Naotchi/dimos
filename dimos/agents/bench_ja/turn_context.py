"""Per-turn correlation id and chokepoint logger for agentic_ja bench events."""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from typing import Any

from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_turn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dimos_bench_ja_turn_id", default=None
)

# Cross-thread fallback. The McpClient and SpeakSkill run on their own threads
# and don't inherit the replay driver's ContextVar. We keep a process-wide
# "latest turn id" updated by new_turn() so those threads can correlate.
# Single-turn-at-a-time semantics are assumed (the agent is single-threaded
# per turn anyway), so this fallback is safe.
_latest_lock = threading.Lock()
_latest_turn_id: str | None = None


def new_turn() -> str:
    """Issue a fresh 12-char turn id, set both the contextvar and process-wide fallback."""
    global _latest_turn_id
    tid = uuid.uuid4().hex[:12]
    _turn_id.set(tid)
    with _latest_lock:
        _latest_turn_id = tid
    return tid


def current_turn() -> str | None:
    """Return ContextVar value if set on this thread, else the latest process-wide id."""
    val = _turn_id.get()
    if val is not None:
        return val
    with _latest_lock:
        return _latest_turn_id


def reset() -> None:
    """Clear the ContextVar (this thread) and the process-wide fallback."""
    global _latest_turn_id
    _turn_id.set(None)
    with _latest_lock:
        _latest_turn_id = None


def log_bench_event(kind: str, **fields: Any) -> None:
    """Emit a structured bench event with consistent envelope fields.

    Envelope fields (event_kind, turn_id, t) always take precedence over
    any user-supplied fields of the same name to keep the schema stable.
    """
    payload: dict[str, Any] = dict(fields)
    payload["event_kind"] = kind
    payload["turn_id"] = current_turn()
    payload["t"] = round(time.perf_counter(), 6)
    logger.info(f"bench {kind}", **payload)
