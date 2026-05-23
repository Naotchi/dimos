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

import os
import time
from typing import Any

from langchain_core.messages.base import BaseMessage
from langchain_core.tools import StructuredTool
from langgraph.graph.state import CompiledStateGraph

from pydantic import Field

from dimos.agents.bench_ja import log_bench_event
from dimos.agents.bench_ja.llm_usage import extract_usage
from dimos.agents.bench_ja.stream_tracker import StepFirstTokenTracker
from dimos.agents.llm_env_ja import DEFAULT_MODEL
from dimos.agents.mcp.mcp_client import McpClient, McpClientConfig
from dimos.agents.utils import pretty_print_langchain_message
from dimos.core.stream import Out
from dimos.stream.audio.tts.sentence_stream import SentenceAccumulator


class TimedMcpClientConfig(McpClientConfig):
    """Fork-local config: ``model`` becomes a category-A field seeded from env.

    Precedence: ``profile config.json value > DIMOS_LLM_MODEL env seed > "gpt-4o"``.
    The blueprint no longer bakes the model, so the profile config.json is the
    sole writer and there is no blueprint↔profile collision to resolve.
    """

    model: str = Field(
        default_factory=lambda: os.environ.get("DIMOS_LLM_MODEL", DEFAULT_MODEL)
    )


class TimedMcpClient(McpClient):
    """McpClient with bench instrumentation.

    Emits:
      - llm_first_token : first AIMessage chunk per LLM step (TTFT signal)
      - llm_step        : duration of each 'agent'/'model' node, plus per-step
                          input_tokens / output_tokens summed from AIMessages
      - <node>_step     : duration of each non-LLM node (typically 'tools')
      - first_tool_call : first tool_call observed in any LLM step, once per turn
      - turn_done       : total turn time, llm time, step count, tool call count
    """

    config: TimedMcpClientConfig
    agent_text: Out[str]

    # Tools whose result is an *image artefact*. The base ``McpClient`` cannot
    # put an image inside the tool response (OpenAI-compatible chat APIs reject
    # images in the tool role), so ``call_tool`` returns only a placeholder
    # ("Tool call started with UUID… updated soon") and injects the image as a
    # *separate* HumanMessage on the message queue → a second graph run.
    #
    # Without intervention the placeholder turn still runs the model, which then
    # answers from whatever image is already in history — i.e. the *previous*
    # observe artefact — and that stale answer gets spoken before the real image
    # arrives (the "called twice, first has the previous image" symptom).
    #
    # Marking these tools ``return_direct`` makes ``create_agent`` exit the graph
    # right after the tool node (langchain.agents.factory: "exit when all
    # executed tools have return_direct=True"), so no model answer is produced on
    # the placeholder turn. The single answer comes from the follow-up turn that
    # carries the fresh image. The placeholder is a ToolMessage, so the speak
    # node (AIMessage-only) never voices it.
    RETURN_DIRECT_TOOLS: frozenset[str] = frozenset({"observe"})

    def _mcp_tool_to_langchain(self, mcp_tool: dict[str, Any]) -> StructuredTool:
        tool = super()._mcp_tool_to_langchain(mcp_tool)
        if tool.name in self.RETURN_DIRECT_TOOLS:
            tool.return_direct = True
        return tool

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
        sentence_acc = SentenceAccumulator()

        # LangGraph's prebuilt agent node has been called "agent" historically
        # and "model" in newer versions; treat both as the LLM step.
        llm_nodes = ("agent", "model")
        tracker = StepFirstTokenTracker(llm_node_names=llm_nodes)

        for mode, payload in state_graph.stream(
            {"messages": self._history},
            stream_mode=["updates", "messages"],
        ):
            # Token-level chunks: emit llm_first_token at most once per step.
            for ev in tracker.feed(mode, payload):
                if ev["kind"] == "llm_first_token":
                    log_bench_event("llm_first_token", step_idx=ev["step_idx"])
                continue

            # Stream sentences out of LLM token chunks as soon as they form,
            # so the TTS node can start on sentence 1 before the turn ends.
            if mode == "messages":
                chunk, meta = payload
                node = meta.get("langgraph_node") if isinstance(meta, dict) else None
                if node in llm_nodes:
                    # Assumes str-content deltas (OpenAI-compatible chat
                    # backends, incl. the local Qwen/vLLM target). A future
                    # content-block-streaming provider would yield list
                    # content here and emit nothing on agent_text.
                    content = getattr(chunk, "content", "")
                    if isinstance(content, str) and content:
                        for sentence in sentence_acc.push(content):
                            self.agent_text.publish(sentence)
                continue

            if mode != "updates":
                continue

            update = payload
            for node_name, node_output in update.items():
                elapsed = time.perf_counter() - step_t0
                msgs = node_output.get("messages", []) if isinstance(node_output, dict) else []
                kind = "llm_step" if node_name in llm_nodes else f"{node_name}_step"

                extra: dict[str, Any] = {}
                if node_name in llm_nodes:
                    total_llm += elapsed
                    usage = extract_usage(msgs)
                    extra["input_tokens"] = usage["input_tokens"]
                    extra["output_tokens"] = usage["output_tokens"]
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
                    **extra,
                )
                step_idx += 1

                for msg in msgs:
                    self._history.append(msg)
                    pretty_print_langchain_message(msg)
                    self.agent.publish(msg)
                if node_name in llm_nodes:
                    rest = sentence_acc.flush()
                    if rest:
                        self.agent_text.publish(rest)
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


__all__ = ["TimedMcpClient", "TimedMcpClientConfig"]
