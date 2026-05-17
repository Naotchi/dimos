"""Pure-fn helper that turns LangGraph dual-mode stream events into bench events.

LangGraph with ``stream_mode=["updates","messages"]`` yields tuples of
``(mode, payload)``:

  - ("messages", (AIMessageChunk, metadata)) -- per-token chunks; metadata has
    ``langgraph_node`` set to the producing node name.
  - ("updates",  {node_name: node_output}) -- emitted when a node finishes.

To compute TTFT *per LLM step* we observe the first "messages" chunk after
each "updates" event for an LLM node (and one initial chunk before the first
"updates" event for step 0). Pulling this state machine out of mcp_client_ja
keeps it unit-testable.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator


class StepFirstTokenTracker:
    """Tracks the first LLM-node token chunk seen in each step.

    ``feed_many`` consumes an iterable of ``(mode, payload)`` events and yields
    plain dicts describing bench events to emit. Currently only one event kind
    is emitted: ``{"kind": "llm_first_token", "step_idx": int}``.
    """

    def __init__(self, llm_node_names: tuple[str, ...] = ("agent", "model")) -> None:
        self._llm_nodes = set(llm_node_names)
        self._current_step = 0
        self._seen_token_in_step = False

    def feed(self, mode: str, payload: Any) -> Iterator[dict[str, Any]]:
        if mode == "messages":
            _chunk, meta = payload
            node = meta.get("langgraph_node") if isinstance(meta, dict) else None
            if node in self._llm_nodes and not self._seen_token_in_step:
                self._seen_token_in_step = True
                yield {"kind": "llm_first_token", "step_idx": self._current_step}
        elif mode == "updates":
            if isinstance(payload, dict):
                # Only LLM-node updates close an LLM step. Tool updates pass through.
                for node_name in payload:
                    if node_name in self._llm_nodes:
                        self._current_step += 1
                        self._seen_token_in_step = False
                        break

    def feed_many(self, events: Iterable[tuple[str, Any]]) -> Iterator[dict[str, Any]]:
        for mode, payload in events:
            yield from self.feed(mode, payload)
