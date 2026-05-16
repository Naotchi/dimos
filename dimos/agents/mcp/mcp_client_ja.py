# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""McpClient subclass with per-step LLM/tool timing + first_tool_call event.

Routes all bench events through dimos.agents.bench_ja.log_bench_event so the
schema is identical across the *_ja.py files (turn_id, t, event_kind).
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages.base import BaseMessage
from langgraph.graph.state import CompiledStateGraph

from dimos.agents.bench_ja import log_bench_event
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.utils import pretty_print_langchain_message


class TimedMcpClient(McpClient):
    """McpClient with bench instrumentation.

    Emits:
      - llm_step       : duration of each 'agent' node in the LangGraph stream
      - <node>_step    : duration of each non-'agent' node (typically 'tools')
      - first_tool_call : first tool_call observed in any LLM step, once per turn
      - turn_done      : total turn time, llm time, step count, tool call count
    """

    def _process_message(
        self, state_graph: CompiledStateGraph[Any, Any, Any, Any], message: BaseMessage
    ) -> None:
        self.agent_idle.publish(False)
        self._history.append(message)
        pretty_print_langchain_message(message)
        self.agent.publish(message)

        turn_t0 = time.perf_counter()
        step_t0 = time.perf_counter()
        step_idx = 0
        total_llm = 0.0
        n_tool_calls = 0
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

                log_bench_event(
                    kind,
                    node=node_name,
                    duration_s=round(elapsed, 4),
                    step_idx=step_idx,
                    n_messages=len(msgs),
                )
                step_idx += 1

                for msg in msgs:
                    self._history.append(msg)
                    pretty_print_langchain_message(msg)
                    self.agent.publish(msg)
                step_t0 = time.perf_counter()

        log_bench_event(
            "turn_done",
            duration_s=round(time.perf_counter() - turn_t0, 4),
            llm_s=round(total_llm, 4),
            n_steps=step_idx,
            n_tool_calls=n_tool_calls,
        )

        if self._message_queue.empty():
            self.agent_idle.publish(True)


__all__ = ["TimedMcpClient"]
