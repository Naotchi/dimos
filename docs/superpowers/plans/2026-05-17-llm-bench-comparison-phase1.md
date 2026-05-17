# LLM 比較ベンチ Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitree_go2_agentic_local_tts` blueprint を env だけ切替えて Azure gpt-4o vs ローカル vLLM Qwen3-30B-A3B を A/B 比較できる状態にする。具体的には `run_meta` / `llm_first_token` event と token usage を main.jsonl に追加し、bench.py で TTFT・step 別 latency・token 数を集計する。

**Architecture:** 既存の `replay → bench_ja log → bench.py 集計` パイプラインを拡張する。新規パーサ・解析ロジックは fork-only の `dimos/agents/bench_ja/` 配下に追加し、ロジックは pure-fn helper に抽出して unit test する。`mcp_client_ja.py` の LangGraph stream loop を `stream_mode=["updates","messages"]` の dual-mode に切替えて per-token chunk を観測し、ステップ単位で最初の token 到達時刻を `llm_first_token` として emit。

**Tech Stack:** Python 3.12, LangChain / LangGraph (既存), pytest, jq（運用側）。新規依存なし。

**Spec reference:** `docs/superpowers/specs/2026-05-17-llm-bench-comparison-phase1-design.md`

**Pre-flight check:** `mcp_client_ja.py` と `bench_ja/` は両方 fork-only ファイル（`git cat-file -e upstream/main:<path>` で確認済み）。CLAUDE.md の「upstream 編集最小化」ルールは適用されないので自由に編集してよい。

---

## File Structure

新規:
- `dimos/agents/bench_ja/llm_usage.py` — AIMessage から token usage を抽出する pure helper（テスタブル）
- `dimos/agents/bench_ja/tests/test_llm_usage.py` — その unit test
- `dimos/agents/bench_ja/stream_tracker.py` — LangGraph dual-mode stream を step 単位で消費する pure-fn iterator helper（テスタブル）
- `dimos/agents/bench_ja/tests/test_stream_tracker.py` — その unit test

変更:
- `dimos/agents/mcp/mcp_client_ja.py` — `llm_step` に `input_tokens` / `output_tokens` 付与、stream を dual-mode 化、`llm_first_token` event を emit
- `scripts/replay_agentic_local_tts.py` — `--label` 追加、起動時に `run_meta` event を emit
- `scripts/bench_agentic_local_tts.py` — `run_meta` の表示、`ttft_s` / `llm_step_0_s` / `llm_step_last_s` / `prompt_tokens` / `completion_tokens` の集計と headline 表示
- `tests/scripts/test_bench_agentic_local_tts_analyzer.py` — 新規 metric の synthetic fixture によるテスト追加

---

## Task 1: token usage 抽出 helper

**Files:**
- Create: `dimos/agents/bench_ja/llm_usage.py`
- Create: `dimos/agents/bench_ja/tests/__init__.py`
- Create: `dimos/agents/bench_ja/tests/test_llm_usage.py`

LangChain の AIMessage は `usage_metadata`（dict, `input_tokens` / `output_tokens` / `total_tokens` を含む）を持つ。LLM step の `msgs` リストから合計を抜く pure helper を作る。テスト容易性のため `mcp_client_ja.py` から分離。

- [ ] **Step 1: failing test**

`dimos/agents/bench_ja/tests/__init__.py` を空ファイルで作成。`dimos/agents/bench_ja/tests/test_llm_usage.py`:

```python
"""Tests for the AIMessage usage-metadata extractor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dimos.agents.bench_ja.llm_usage import extract_usage


@dataclass
class _FakeMsg:
    """Minimal AIMessage stand-in: only the usage_metadata attribute matters."""
    usage_metadata: dict[str, Any] | None


def test_empty_list_returns_zero_with_none_flag():
    out = extract_usage([])
    assert out == {"input_tokens": None, "output_tokens": None, "available": False}


def test_single_aimessage_with_usage():
    msgs = [_FakeMsg(usage_metadata={"input_tokens": 120, "output_tokens": 17, "total_tokens": 137})]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 120, "output_tokens": 17, "available": True}


def test_sums_across_multiple_aimessages():
    msgs = [
        _FakeMsg(usage_metadata={"input_tokens": 100, "output_tokens": 10}),
        _FakeMsg(usage_metadata={"input_tokens": 50,  "output_tokens": 3}),
    ]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 150, "output_tokens": 13, "available": True}


def test_skips_messages_without_usage_metadata():
    class _NoUsage:
        pass
    msgs = [_FakeMsg(usage_metadata={"input_tokens": 7, "output_tokens": 2}), _NoUsage()]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 7, "output_tokens": 2, "available": True}


def test_handles_none_usage_metadata():
    msgs = [_FakeMsg(usage_metadata=None), _FakeMsg(usage_metadata={"input_tokens": 5, "output_tokens": 1})]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 5, "output_tokens": 1, "available": True}
```

- [ ] **Step 2: verify test fails**

```
python -m pytest dimos/agents/bench_ja/tests/test_llm_usage.py -v
```

Expected: ImportError / ModuleNotFoundError on `dimos.agents.bench_ja.llm_usage`.

- [ ] **Step 3: implement extract_usage**

`dimos/agents/bench_ja/llm_usage.py`:

```python
"""Pure helpers for extracting token-usage data from LangChain AIMessages."""

from __future__ import annotations

from typing import Any, Iterable


def extract_usage(msgs: Iterable[Any]) -> dict[str, Any]:
    """Sum input_tokens / output_tokens across messages that expose usage_metadata.

    Returns a dict with three keys:
      - input_tokens (int | None)
      - output_tokens (int | None)
      - available (bool): False when no message in the iterable carried usage_metadata

    "Available=False" lets bench_ja log the keys with None values, which the
    analyzer can then distinguish from "0 tokens were used".
    """
    in_total = 0
    out_total = 0
    seen = False
    for m in msgs:
        meta = getattr(m, "usage_metadata", None)
        if not meta:
            continue
        seen = True
        in_total += int(meta.get("input_tokens", 0) or 0)
        out_total += int(meta.get("output_tokens", 0) or 0)
    if not seen:
        return {"input_tokens": None, "output_tokens": None, "available": False}
    return {"input_tokens": in_total, "output_tokens": out_total, "available": True}
```

- [ ] **Step 4: verify test passes**

```
python -m pytest dimos/agents/bench_ja/tests/test_llm_usage.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: commit**

```
git add dimos/agents/bench_ja/llm_usage.py \
        dimos/agents/bench_ja/tests/__init__.py \
        dimos/agents/bench_ja/tests/test_llm_usage.py
git commit -m "feat(bench_ja): extract token usage from AIMessages"
```

---

## Task 2: dual-mode stream tracker（TTFT per LLM step）

**Files:**
- Create: `dimos/agents/bench_ja/stream_tracker.py`
- Create: `dimos/agents/bench_ja/tests/test_stream_tracker.py`

LangGraph `stream_mode=["updates","messages"]` は `(mode, payload)` のタプル列を返す。`mode=="messages"` の payload は `(AIMessageChunk, meta)`、`mode=="updates"` の payload は `{node_name: {...}}`。

ステップ毎の「最初の token chunk」を検出するための pure-fn iterator を作る。stream を直接食わせる代わりに `Iterable[tuple[str, Any]]` を引数に取ることで unit testable。

- [ ] **Step 1: failing test**

`dimos/agents/bench_ja/tests/test_stream_tracker.py`:

```python
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
```

- [ ] **Step 2: verify fail**

```
python -m pytest dimos/agents/bench_ja/tests/test_stream_tracker.py -v
```

Expected: ImportError.

- [ ] **Step 3: implement tracker**

`dimos/agents/bench_ja/stream_tracker.py`:

```python
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
```

- [ ] **Step 4: verify pass**

```
python -m pytest dimos/agents/bench_ja/tests/test_stream_tracker.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: commit**

```
git add dimos/agents/bench_ja/stream_tracker.py \
        dimos/agents/bench_ja/tests/test_stream_tracker.py
git commit -m "feat(bench_ja): step-aware first-token tracker for dual-mode LangGraph stream"
```

---

## Task 3: mcp_client_ja で dual-mode stream + usage 統合

**Files:**
- Modify: `dimos/agents/mcp/mcp_client_ja.py:44-105`

`_process_message` を `stream_mode=["updates","messages"]` の dual-mode 化し、`StepFirstTokenTracker` で `llm_first_token` event を emit。`llm_step` event の payload に `input_tokens` / `output_tokens` を追加。

このタスクは LangGraph に依存する統合層で unit test 困難。動作確認は Task 6 の smoke run に委ねる（pure logic 部分は Task 1/2 で既にテスト済み）。

- [ ] **Step 1: rewrite _process_message**

`dimos/agents/mcp/mcp_client_ja.py`:

```python
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
from dimos.agents.bench_ja.llm_usage import extract_usage
from dimos.agents.bench_ja.stream_tracker import StepFirstTokenTracker
from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.utils import pretty_print_langchain_message


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
```

- [ ] **Step 2: import check**

```
python -c "from dimos.agents.mcp.mcp_client_ja import TimedMcpClient; print('ok')"
```

Expected: `ok`. Catches typos and circular-import issues before the heavier smoke run in Task 6.

- [ ] **Step 3: commit**

```
git add dimos/agents/mcp/mcp_client_ja.py
git commit -m "feat(mcp_client_ja): emit llm_first_token + per-step token usage"
```

---

## Task 4: replay.py に `--label` と `run_meta` event

**Files:**
- Modify: `scripts/replay_agentic_local_tts.py:46-65, 135-145`

CLI に `--label`、起動直後（`configure_log_dir` の後、`boot_blueprint` の前）に 1 回だけ `run_meta` event を emit。

ロジックは小さいので unit test は省略（pure-fn 化のコストが見合わない）。確認は Task 6 の smoke run で行う。

- [ ] **Step 1: edit replay.py**

`scripts/replay_agentic_local_tts.py` の以下を変更。

`parse_args` 内、`--out` の直前あたりに追加:

```python
    p.add_argument(
        "--label",
        default=None,
        help="Free-form label for this run (recorded in main.jsonl run_meta event). "
             "Defaults to DIMOS_LLM_MODEL.",
    )
```

ファイル先頭の import に `import os` を追加（既存なら追加不要）。

`main()` の `configure_log_dir(...)` 行のあとに以下を挿入:

```python
    label = args.label or os.environ.get("DIMOS_LLM_MODEL") or "unlabeled"
    log_bench_event(
        "run_meta",
        label=label,
        model=os.environ.get("DIMOS_LLM_MODEL"),
        base_url=(
            os.environ.get("DIMOS_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        ),
        api_key_source=(
            "DIMOS_LLM_API_KEY"
            if os.environ.get("DIMOS_LLM_API_KEY")
            else ("OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else None)
        ),
        started_at=datetime.now().isoformat(),
    )
```

import 確認: `log_bench_event` は既に `from dimos.agents.bench_ja import ...` 経由で入っている。`datetime` も既に import 済み。`os` は **新規追加が必要**：

ファイル先頭、`import sys` の直後に挿入:

```python
import os
```

- [ ] **Step 2: smoke check (no blueprint boot)**

```
python -c "
import argparse, sys
sys.argv = ['replay', '--label', 'test-label', '--runs', '1']
sys.path.insert(0, 'scripts')
from replay_agentic_local_tts import parse_args
print(parse_args())
"
```

Expected: Namespace に `label='test-label'` が含まれる。

- [ ] **Step 3: commit**

```
git add scripts/replay_agentic_local_tts.py
git commit -m "feat(replay): emit run_meta bench event with --label and env snapshot"
```

---

## Task 5: bench.py に新規 metric と run_meta 表示

**Files:**
- Modify: `scripts/bench_agentic_local_tts.py`
- Modify: `tests/scripts/test_bench_agentic_local_tts_analyzer.py`

per-turn metric に `ttft_s` / `llm_step_0_s` / `llm_step_last_s` / `prompt_tokens` / `completion_tokens` を追加。`run_meta` event をパースして CLI 出力の冒頭に表示。

このタスクは fixture を増やして TDD で進める（既存 analyzer test に追加）。

- [ ] **Step 1: failing test — token / step / ttft metrics**

`tests/scripts/test_bench_agentic_local_tts_analyzer.py` の末尾に追記:

```python
def _line(d):  # local alias if scope makes it ambiguous; otherwise reuse top-level
    import json
    return json.dumps(d) + "\n"


def test_ttft_and_per_step_and_tokens(tmp_path):
    """A two-step LLM turn with token usage and llm_first_token events."""
    path = tmp_path / "main.jsonl"
    lines = [
        _line({"event_kind": "user_audio_end", "turn_id": "T", "t": 0.0,
               "fixture_id": "fx", "run_idx": 0, "warmup": False,
               "audio_seconds": 1.0}),
        _line({"event_kind": "stt_done",        "turn_id": "T", "duration_s": 0.40, "t": 0.40}),
        _line({"event_kind": "llm_first_token", "turn_id": "T", "t": 0.55, "step_idx": 0}),
        _line({"event_kind": "llm_step",        "turn_id": "T", "t": 0.80,
               "node": "agent", "duration_s": 0.40, "step_idx": 0, "n_messages": 1,
               "input_tokens": 120, "output_tokens": 17}),
        _line({"event_kind": "tools_step",      "turn_id": "T", "t": 0.90,
               "node": "tools", "duration_s": 0.10, "step_idx": 1, "n_messages": 1}),
        _line({"event_kind": "llm_first_token", "turn_id": "T", "t": 1.00, "step_idx": 1}),
        _line({"event_kind": "llm_step",        "turn_id": "T", "t": 1.20,
               "node": "agent", "duration_s": 0.30, "step_idx": 2, "n_messages": 1,
               "input_tokens": 200, "output_tokens": 9}),
        _line({"event_kind": "turn_done",       "turn_id": "T", "duration_s": 1.20}),
    ]
    path.write_text("".join(lines))

    turns = build_turns(path)
    m = compute_per_turn_metrics(turns)["T"]

    # ttft_s = first llm_first_token.t - user_audio_end.t - stt_s
    #        = 0.55 - 0.0 - 0.40 = 0.15
    assert abs(m["ttft_s"] - 0.15) < 1e-6
    assert abs(m["llm_step_0_s"]   - 0.40) < 1e-6
    assert abs(m["llm_step_last_s"] - 0.30) < 1e-6
    assert m["prompt_tokens"]     == 320
    assert m["completion_tokens"] == 26


def test_run_meta_is_parsed(tmp_path):
    path = tmp_path / "main.jsonl"
    import json
    from bench_agentic_local_tts import read_run_meta
    path.write_text(
        json.dumps({"event_kind": "run_meta",
                    "label": "azure-gpt-4o",
                    "model": "gpt-4o",
                    "base_url": "https://x.openai.azure.com/openai/v1"}) + "\n"
    )
    meta = read_run_meta(path)
    assert meta["label"]    == "azure-gpt-4o"
    assert meta["model"]    == "gpt-4o"
    assert meta["base_url"] == "https://x.openai.azure.com/openai/v1"


def test_run_meta_returns_empty_dict_when_missing(tmp_path):
    path = tmp_path / "main.jsonl"
    path.write_text("")
    from bench_agentic_local_tts import read_run_meta
    assert read_run_meta(path) == {}


def test_ttft_none_when_no_first_token_event(tmp_path):
    path = tmp_path / "main.jsonl"
    import json
    path.write_text("".join([
        json.dumps({"event_kind": "user_audio_end", "turn_id": "T", "t": 0.0,
                    "fixture_id": "fx", "run_idx": 0, "warmup": False,
                    "audio_seconds": 1.0}) + "\n",
        json.dumps({"event_kind": "stt_done", "turn_id": "T", "duration_s": 0.4, "t": 0.4}) + "\n",
        json.dumps({"event_kind": "llm_step", "turn_id": "T", "t": 0.8,
                    "node": "agent", "duration_s": 0.4, "step_idx": 0, "n_messages": 1,
                    "input_tokens": None, "output_tokens": None}) + "\n",
        json.dumps({"event_kind": "turn_done", "turn_id": "T", "duration_s": 0.8}) + "\n",
    ]))
    turns = build_turns(path)
    m = compute_per_turn_metrics(turns)["T"]
    assert m["ttft_s"] is None
    assert m["prompt_tokens"] is None
    assert m["completion_tokens"] is None
```

- [ ] **Step 2: verify failure**

```
python -m pytest tests/scripts/test_bench_agentic_local_tts_analyzer.py -v
```

Expected: 4 new tests fail (missing `read_run_meta`, missing new metric keys).

- [ ] **Step 3: implement in bench_agentic_local_tts.py**

`build_turns()` の `turn` 辞書初期化に `"llm_first_tokens": []` を追加し、新規 event 種別の dispatch を追加。ファイル diff 概要:

(a) `defaultdict(lambda: {...})` の dict 初期化に `"llm_first_tokens": []` を追加。

(b) `build_turns` の event 分岐に以下を追加（`elif kind == "speak_invoke":` の直後あたり）:

```python
        elif kind == "llm_first_token":
            turns[current]["llm_first_tokens"].append(row)
```

(c) `compute_per_turn_metrics()` の metric 辞書に以下を追加:

```python
        llm_steps = data.get("llm_steps", [])
        first_tokens = data.get("llm_first_tokens", [])
        stt_s = _parse_duration(stt.get("duration_s")) if stt else None

        ttft_s: float | None = None
        if first_tokens and t0 is not None and stt_s is not None:
            ttft_s = first_tokens[0].get("t", 0.0) - t0 - stt_s

        llm_step_0_s = (
            _parse_duration(llm_steps[0].get("duration_s")) if llm_steps else None
        )
        llm_step_last_s = (
            _parse_duration(llm_steps[-1].get("duration_s")) if llm_steps else None
        )

        prompt_tokens_vals = [s.get("input_tokens") for s in llm_steps]
        completion_tokens_vals = [s.get("output_tokens") for s in llm_steps]
        prompt_tokens = (
            sum(v for v in prompt_tokens_vals if v is not None)
            if any(v is not None for v in prompt_tokens_vals) else None
        )
        completion_tokens = (
            sum(v for v in completion_tokens_vals if v is not None)
            if any(v is not None for v in completion_tokens_vals) else None
        )
```

そして `metrics[turn_id] = { ... }` の dict に以下のキーを追加:

```python
            "ttft_s": ttft_s,
            "llm_step_0_s": llm_step_0_s,
            "llm_step_last_s": llm_step_last_s,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
```

(d) `_AGENTIC_LOCAL_TTS_METRIC_KEYS` を以下に拡張:

```python
_AGENTIC_LOCAL_TTS_METRIC_KEYS = (
    "e2e_first_audio_s",
    "agent_first_call_s",
    "speak_tts_s",
    "stt_s",
    "ttft_s",
    "llm_step_0_s",
    "llm_step_last_s",
    "llm_total_s",
    "prompt_tokens",
    "completion_tokens",
    "tools_total_s",
    "turn_total_s",
)
```

(e) `read_run_meta()` 関数を新規追加（ファイル末尾の関数定義群、`_pick_run` の手前あたり）:

```python
def read_run_meta(jsonl_path: Path) -> dict[str, Any]:
    """Find the first run_meta event in main.jsonl, return its payload (envelope stripped).

    Returns an empty dict if no run_meta event is present (older runs).
    """
    if not jsonl_path.exists():
        return {}
    for row in _read_jsonl(jsonl_path):
        if row.get("event_kind") == "run_meta":
            payload = {k: v for k, v in row.items()
                       if k not in ("event_kind", "turn_id", "t",
                                    "event", "level", "logger", "timestamp",
                                    "func_name", "lineno")}
            return payload
    return {}
```

(f) `main()` の早い段階（`mode = ...` の直後）に追加し、`print(f"mode: {mode}")` の直後に表示:

```python
    meta = read_run_meta(jsonl)
    if meta:
        print(f"label: {meta.get('label', '?')}  model: {meta.get('model', '?')}  "
              f"base_url: {meta.get('base_url', '?')}")
```

- [ ] **Step 4: verify pass**

```
python -m pytest tests/scripts/test_bench_agentic_local_tts_analyzer.py -v
```

Expected: 全テスト pass（既存テストも壊れていない）。新規 4 件含む。

- [ ] **Step 5: commit**

```
git add scripts/bench_agentic_local_tts.py tests/scripts/test_bench_agentic_local_tts_analyzer.py
git commit -m "feat(bench): add ttft/step_0/step_last/token metrics and run_meta display"
```

---

## Task 6: smoke run（Azure baseline で end-to-end 確認）

**Files:** 変更なし。手動実行のみ。

新規 event / metric が実機 main.jsonl に乗ることを最小コストで確認する。**fixture の正しさや latency 数値そのものは検証しない**（それは Phase 2）。

- [ ] **Step 1: 既存 fixture で短い replay を回す**

```
python scripts/replay_agentic_local_tts.py \
  --fixtures tests/bench_fixtures/agentic_ja/fixtures.yaml \
  --runs 1 --warmup 0 \
  --label smoke-azure-gpt-4o
```

Expected: `logs/<ts>-bench-agentic-local-tts/main.jsonl` が生成され、エラーなく完走（少なくとも 1 turn 走る）。

- [ ] **Step 2: 新規 event が乗っていることを確認**

```
RUN=$(ls -td logs/*-bench-agentic-local-tts | head -1)
echo "run: $RUN"
jq -r 'select(.event_kind=="run_meta")' $RUN/main.jsonl
jq -r 'select(.event_kind=="llm_first_token") | {turn_id, step_idx, t}' $RUN/main.jsonl
jq -r 'select(.event_kind=="llm_step") | {turn_id, step_idx, input_tokens, output_tokens, duration_s}' $RUN/main.jsonl
```

Expected:
- `run_meta` line 1 件: `label="smoke-azure-gpt-4o"`、`model=gpt-4o`
- `llm_first_token` が turn ごとに 1 件以上
- `llm_step` の各行に `input_tokens` / `output_tokens` キーが乗る（値が `null` なら model がストリーミング非対応の可能性 — その場合は §「Step 4 (条件付き)」へ）

- [ ] **Step 3: bench.py 集計を確認**

```
python scripts/bench_agentic_local_tts.py $RUN
```

Expected: 出力に以下が表示される
- `label: smoke-azure-gpt-4o  model: gpt-4o  base_url: ...`
- headline に `ttft_s` 行
- aggregate テーブルに `ttft_s` / `llm_step_0_s` / `llm_step_last_s` / `prompt_tokens` / `completion_tokens` 行

- [ ] **Step 4 (条件付き): `llm_first_token` が出ない / `input_tokens` が null の場合**

これは spec §6 「リスク」のうち TTFT callback / usage_metadata の availability の問題。発生したら以下のいずれかで対応:

(a) **`input_tokens` のみ null**: `llm_env_ja.py` の検証ログを追加し、`ChatOpenAI` の `streaming` 設定を確認。`init_chat_model` が `streaming=True` を渡しているか不明な場合は、blueprint 側で `init_chat_model(_LLM_MODEL, streaming=True)` を明示的に呼んで instance を `TimedMcpClient.blueprint(model=...)` に渡すよう修正する別タスクを切る。

(b) **`llm_first_token` が出ない**: `stream_mode=["updates","messages"]` で "messages" イベントが yield されていない可能性。`python -c "from langgraph.graph import StateGraph; import langgraph; print(langgraph.__version__)"` でバージョン確認、`pyproject.toml` 上限を上げるなどの調査タスクを切る。

これらは Phase 1 完了条件のうち §7 の 2 番目（main.jsonl への field 載せ）に影響するため、新規 GH issue / 別 plan ファイルとして切り出す。本タスク内では深追いしない。

- [ ] **Step 5: smoke commit（不要 — 変更ファイルなし）**

skip（実行ログは logs/ 配下にのみ生成、git ignore 対象）。

---

## Self-Review チェック

- ✅ Spec §3 (`run_meta` event) → Task 4
- ✅ Spec §4.2 (TTFT callback) → Task 2 + Task 3（callback ではなく LangGraph dual-mode stream に変更したが、出力 event は spec 通り `llm_first_token` で TTFT が取れる）
- ✅ Spec §4.3 (token usage) → Task 1 + Task 3
- ✅ Spec §4.4 (`llm_first_token` を headline primary) → Task 5 で `ttft_s` を独立 metric として追加。既存 `agent_first_call_s` (= `first_tool_call_s`) は破壊せず併存
- ✅ Spec §4.5 (per-turn metrics 追加) → Task 5
- ✅ Spec §4.6 (warmup ガイダンス) → これは skill doc 側で扱う事項（コード変更不要）。skill doc は本 plan の対象外（Phase 1 完了後に別途）
- ✅ Spec §6 リスク (TTFT callback / usage_metadata availability) → Task 6 Step 4 で smoke 時点で検出するフックを置いた

**設計差分メモ**: spec §4.2 は `BaseCallbackHandler` + `on_llm_new_token` 経由を想定していたが、plan では LangGraph `stream_mode=["updates","messages"]` 経由に変更。理由は (1) 既存 model 構築パス（`init_chat_model(str)` 経由）を触らずに済む、(2) `streaming=True` の有無に依存せず LangGraph が自動でストリーミングする、の 2 点。spec 完了条件 §7 は満たす（`llm_first_token` event を main.jsonl に出す、という目的は同じ）。
