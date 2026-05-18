# LLM Bench Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitree_go2_agentic_local_tts` を使った LLM/STT/TTS bench を、headless / YAML config 駆動 / STT・LLM・TTS 切替可能 / 結果と config が 1:1 対応する形にする。

**Architecture:** blueprint は触らない。`AssistantSpeechNodeJa` を impl 切替可能に小改修し、bench runner (`scripts/bench_llm.py`、既存 `replay_agentic_local_tts.py` を rename) が YAML config を読んで `ModuleCoordinator.build(blueprint, blueprint_args=...)` に流す。MuJoCo viewer 抑制は upstream を編集せず `xvfb-run` ラッパで対応。1 run = 1 ディレクトリで config.yaml をコピー保存。

**Tech Stack:** Python, PyYAML, dimos `Module` / `Blueprint` / `ModuleCoordinator`, MuJoCo, OpenJTalk / OpenAI TTS / pyttsx3, xvfb-run。

---

## File Structure

**Create:**
- `scripts/bench_llm.py` — config 駆動 bench runner（既存 `replay_agentic_local_tts.py` を rename + 改修）
- `scripts/bench_configs/whisper-base-gpt4o-openjtalk.yaml` — baseline config
- `scripts/bench_configs/whisper-base-gpt4o-openai-tts.yaml` — TTS=OpenAI 切替例
- `scripts/bench_configs/whisper-small-gpt4o-openjtalk.yaml` — STT model 切替例
- `tests/agents/skills/test_speak_skill_ja_impl_switch.py` — TTS impl 切替の単体テスト

**Modify:**
- `dimos/agents/skills/speak_skill_ja.py` — `AssistantSpeechNodeJa` に `impl` config 追加、内部で TTS node を切替
- `docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md`（Open Questions の 1. を「xvfb-run で対応」に更新）

**Delete:**
- `scripts/replay_agentic_local_tts.py` — `scripts/bench_llm.py` に rename

---

## Task 1: AssistantSpeechNodeJa に impl 切替を追加（OpenJTalk + OpenAI 対応）

**Files:**
- Modify: `dimos/agents/skills/speak_skill_ja.py`
- Test: `tests/agents/skills/test_speak_skill_ja_impl_switch.py`

新規 Module config を導入し、`impl` 文字列で TTS node 実装を切り替えられるようにする。`pytts` は本タスクでは含めず、将来必要になったとき追加。

- [ ] **Step 1: Write the failing test**

Create `tests/agents/skills/test_speak_skill_ja_impl_switch.py`:

```python
"""Verify AssistantSpeechNodeJa selects the right TTS node from config."""

from __future__ import annotations

import pytest

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJa,
    AssistantSpeechNodeJaConfig,
)
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode
from dimos.stream.audio.tts.node_openai import OpenAITTSNode


def _build_node(impl: str, **extra) -> AssistantSpeechNodeJa:
    """Instantiate the node without starting it (no audio device required)."""
    cfg = AssistantSpeechNodeJaConfig(impl=impl, **extra)
    node = AssistantSpeechNodeJa(config=cfg)
    return node


def test_default_impl_is_open_jtalk():
    node = _build_node(impl="open_jtalk")
    tts = node._make_tts_node()
    assert isinstance(tts, OpenJTalkTTSNode)


def test_impl_openai_returns_openai_node(monkeypatch):
    # Avoid hitting the real OpenAI client during construction.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    node = _build_node(impl="openai", openai_voice="echo", openai_model="tts-1")
    tts = node._make_tts_node()
    assert isinstance(tts, OpenAITTSNode)


def test_unknown_impl_raises():
    node = _build_node(impl="bogus")
    with pytest.raises(ValueError, match="bogus"):
        node._make_tts_node()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agents/skills/test_speak_skill_ja_impl_switch.py -v`
Expected: FAIL — `ImportError: cannot import name 'AssistantSpeechNodeJaConfig'` and `_make_tts_node` not defined.

- [ ] **Step 3: Modify `dimos/agents/skills/speak_skill_ja.py` to add config + impl factory**

Replace the entire file with:

```python
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

"""Speak assistant messages directly via local Japanese TTS.

Subscribes to ``McpClient.agent: Out[BaseMessage]`` (autoconnect wires by
``(name, type)``) and feeds the text content of each ``AIMessage`` into a
TTS node selected by ``impl`` (default ``open_jtalk``). Output goes to
``SounddeviceAudioOutput``.
"""

from __future__ import annotations

import threading
from typing import Any

import reactivex.operators as ops
from langchain_core.messages import AIMessage
from langchain_core.messages.base import BaseMessage
from reactivex import Subject
from reactivex.disposable import Disposable

from dimos.agents.bench_ja import log_bench_event
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class AssistantSpeechNodeJaConfig(ModuleConfig):
    """Config selecting the underlying TTS implementation."""

    impl: str = "open_jtalk"  # one of: open_jtalk, openai
    openai_voice: str = "echo"  # used when impl == "openai"
    openai_model: str = "tts-1"  # used when impl == "openai"


class AssistantSpeechNodeJa(Module):
    """Speak assistant message text via a configurable Japanese TTS node."""

    agent: In[BaseMessage]
    config: AssistantSpeechNodeJaConfig

    def _make_tts_node(self):
        impl = self.config.impl
        if impl == "open_jtalk":
            return OpenJTalkTTSNode()
        if impl == "openai":
            return OpenAITTSNode(
                voice=Voice(self.config.openai_voice),
                model=self.config.openai_model,
            )
        raise ValueError(f"Unknown AssistantSpeechNodeJa impl: {impl!r}")

    @rpc
    def start(self) -> None:
        super().start()

        self._first_chunk_pending = False
        self._first_chunk_lock = threading.Lock()

        self._tts_node = self._make_tts_node()
        self._audio_output = SounddeviceAudioOutput(sample_rate=48000)

        self._text_subject = Subject()
        self._tts_node.consume_text(self._text_subject)

        tapped = self._tts_node.emit_audio().pipe(ops.do_action(self._on_audio_chunk))
        self._audio_output.consume_audio(tapped)

        self.register_disposable(
            Disposable(self.agent.subscribe(self._on_agent_message))
        )

    @rpc
    def stop(self) -> None:
        if self._text_subject is not None:
            self._text_subject.on_completed()
            self._text_subject = None
        if self._tts_node is not None:
            self._tts_node.dispose()
            self._tts_node = None
        if self._audio_output is not None:
            self._audio_output.stop()
            self._audio_output = None
        super().stop()

    def _on_agent_message(self, msg: BaseMessage) -> None:
        if not isinstance(msg, AIMessage):
            return
        content = msg.content
        if not isinstance(content, str):
            return
        if content.strip() == "":
            return
        if self._text_subject is None:
            logger.warning(
                "AssistantSpeechNodeJa received agent message after stop(); dropping."
            )
            return

        log_bench_event("speak_invoke")
        with self._first_chunk_lock:
            self._first_chunk_pending = True
        self._text_subject.on_next(content)

    def _on_audio_chunk(self, _chunk: Any) -> None:
        with self._first_chunk_lock:
            if not self._first_chunk_pending:
                return
            self._first_chunk_pending = False
        log_bench_event("first_audio_out", tool="speak")


__all__ = ["AssistantSpeechNodeJa", "AssistantSpeechNodeJaConfig"]
```

Note: `Voice` is the `Enum` exported from `dimos/stream/audio/tts/node_openai.py`. `OpenJTalkTTSNode()` and `OpenAITTSNode(voice=..., model=...)` constructors are pre-existing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/agents/skills/test_speak_skill_ja_impl_switch.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify nothing else broke**

Run: `python -m pytest dimos/agents/skills/ tests/agents/skills/ -q`
Expected: All tests pass (or pre-existing failures only — note them if any and confirm they are unrelated).

- [ ] **Step 6: Commit**

```bash
git add dimos/agents/skills/speak_skill_ja.py tests/agents/skills/test_speak_skill_ja_impl_switch.py
git commit -m "feat(speak_skill_ja): switchable TTS impl (open_jtalk/openai)"
```

---

## Task 2: bench runner (`scripts/bench_llm.py`) を新設し、CLI を config 駆動に

**Files:**
- Create: `scripts/bench_llm.py`
- Delete: `scripts/replay_agentic_local_tts.py`
- Create: `scripts/bench_configs/whisper-base-gpt4o-openjtalk.yaml`

既存 `replay_agentic_local_tts.py` のロジック（fixture loop / inject_utterance / idle 同期 / bench events）を流用しつつ、CLI を `--config <path>` 1 引数にする。

- [ ] **Step 1: Create baseline config**

Create `scripts/bench_configs/whisper-base-gpt4o-openjtalk.yaml`:

```yaml
name: whisper-base-gpt4o-openjtalk
fixtures: tests/bench_fixtures/agentic_ja/fixtures.yaml
runs: 3
warmup: 1
shuffle: false
turn_timeout: 30.0

simulation:
  enabled: true
  headless: true

stt:
  model: base
  fp16: false

llm:
  model: gpt-4o
  base_url: null
  api_key_env: OPENAI_API_KEY
  system_prompt: ja_default

tts:
  impl: open_jtalk
```

- [ ] **Step 2: Create `scripts/bench_llm.py`**

```python
#!/usr/bin/env python
"""Config-driven LLM/STT/TTS bench runner.

Boots ``unitree_go2_agentic_local_tts`` with module configs injected from a
YAML file, injects fixture wavs via ``LocalMicrophoneJa.inject_utterance``,
and writes bench events to ``logs/{ts}-{config.name}/main.jsonl``. A copy
of the config plus a sha256 hash are recorded so each run is
self-describing.

For headless MuJoCo runs, invoke under ``xvfb-run`` on Linux:

    xvfb-run -a python scripts/bench_llm.py --config scripts/bench_configs/<name>.yaml

Usage:
    python scripts/bench_llm.py --config scripts/bench_configs/<name>.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from dimos.agents.bench_ja import log_bench_event, new_turn, reset
from dimos.agents.local_microphone_ja import LocalMicrophoneJa
from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
    unitree_go2_agentic_local_tts,
)
from dimos.utils.logging_config import set_run_log_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, help="Path to bench config YAML")
    return p.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    if "name" not in cfg:
        raise ValueError(f"config {path} missing required 'name' field")
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    norm = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(norm).hexdigest()[:8]


def build_blueprint_args(cfg: dict[str, Any]) -> dict[str, Any]:
    """Translate YAML config into ModuleCoordinator.build blueprint_args."""
    args: dict[str, Any] = {}

    sim = cfg.get("simulation", {})
    args["g"] = {"simulation": bool(sim.get("enabled", False))}

    stt = cfg.get("stt", {})
    if stt:
        args["WhisperHumanInputJa"] = {
            "model": stt.get("model", "base"),
            "fp16": bool(stt.get("fp16", False)),
        }

    llm = cfg.get("llm", {})
    llm_args: dict[str, Any] = {}
    if "model" in llm:
        llm_args["model"] = llm["model"]
    if llm.get("base_url"):
        llm_args["base_url"] = llm["base_url"]
    # system_prompt: only ja_default for now — others are an open extension.
    # If config requests a non-default prompt, fail loudly so it isn't silently ignored.
    sp = llm.get("system_prompt", "ja_default")
    if sp != "ja_default":
        raise NotImplementedError(
            f"system_prompt={sp!r} not implemented; only 'ja_default' supported."
        )
    if llm_args:
        args["TimedMcpClient"] = llm_args

    tts = cfg.get("tts", {})
    if tts:
        tts_args = {"impl": tts.get("impl", "open_jtalk")}
        if "openai_voice" in tts:
            tts_args["openai_voice"] = tts["openai_voice"]
        if "openai_model" in tts:
            tts_args["openai_model"] = tts["openai_model"]
        args["AssistantSpeechNodeJa"] = tts_args

    return args


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 4)


def fixture_iter(fixtures: list[dict[str, Any]], runs: int, warmup: int, shuffle: bool):
    import random

    order = list(range(len(fixtures)))
    for run_idx in range(runs):
        if shuffle:
            random.shuffle(order)
        for j in order:
            fx = fixtures[j]
            yield {**fx, "run_idx": run_idx, "warmup": run_idx < warmup}


def setup_run_dir(cfg: dict[str, Any], cfg_path: Path) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = Path("logs") / f"{ts}-{cfg['name']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg_path, out_dir / "config.yaml")
    set_run_log_dir(out_dir)
    return out_dir


def warn_if_no_display_for_sim(cfg: dict[str, Any]) -> None:
    sim = cfg.get("simulation", {})
    if not sim.get("enabled"):
        return
    if not sim.get("headless"):
        return
    if os.environ.get("DISPLAY"):
        return
    print(
        "[bench] WARN: simulation.headless=true but no DISPLAY is set. "
        "MuJoCo viewer.launch_passive will fail. Invoke via 'xvfb-run -a'.",
        file=sys.stderr,
    )


def main() -> int:
    args = parse_args()
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # MuJoCo off-screen rendering (egl). Harmless even when not in simulation.
    os.environ.setdefault("MUJOCO_GL", "egl")
    warn_if_no_display_for_sim(cfg)

    out_dir = setup_run_dir(cfg, cfg_path)
    bp_args = build_blueprint_args(cfg)

    print(f"[bench] {cfg['name']} → {out_dir}", flush=True)

    log_bench_event(
        "run_meta",
        config_name=cfg["name"],
        config_hash=config_hash(cfg),
        config=cfg,
        started_at=datetime.now().isoformat(),
    )

    coordinator = ModuleCoordinator.build(
        unitree_go2_agentic_local_tts,
        blueprint_args=bp_args,
    )
    mcp_client = coordinator.get_instance(TimedMcpClient)
    mic = coordinator.get_instance(LocalMicrophoneJa)

    idle_event = threading.Event()

    def on_idle(is_idle: bool) -> None:
        if is_idle:
            idle_event.set()
        else:
            idle_event.clear()

    mcp_client.agent_idle.subscribe(on_idle)

    fx_path = Path(cfg["fixtures"])
    manifest = yaml.safe_load(fx_path.read_text())
    fixtures = manifest["fixtures"]

    schedule = list(
        fixture_iter(
            fixtures,
            runs=int(cfg.get("runs", 3)),
            warmup=int(cfg.get("warmup", 1)),
            shuffle=bool(cfg.get("shuffle", False)),
        )
    )
    turn_timeout = float(cfg.get("turn_timeout", 30.0))
    print(f"[bench] {len(schedule)} runs scheduled", flush=True)

    for i, fx in enumerate(schedule):
        if i > 0:
            if not idle_event.wait(timeout=turn_timeout):
                print(
                    f"[bench] WARN: idle wait timed out before fx {fx['id']}",
                    file=sys.stderr,
                )
            idle_event.clear()

        wav_path = fx_path.parent / fx["wav"]
        audio_seconds = wav_seconds(wav_path)

        reset()
        new_turn()
        log_bench_event(
            "user_audio_end",
            audio_seconds=audio_seconds,
            fixture_id=fx["id"],
            run_idx=fx["run_idx"],
            warmup=fx["warmup"],
        )

        try:
            mic.inject_utterance(str(wav_path))
        except Exception as e:  # noqa: BLE001
            print(f"[bench] inject failed for {fx['id']}: {e}", file=sys.stderr)
            log_bench_event(
                "inject_failed",
                fixture_id=fx["id"],
                run_idx=fx["run_idx"],
                error=str(e),
            )
            continue

        if not idle_event.wait(timeout=turn_timeout):
            print(f"[bench] WARN: turn {fx['id']} timed out", file=sys.stderr)
            log_bench_event(
                "turn_timeout", fixture_id=fx["id"], run_idx=fx["run_idx"]
            )

    print("[bench] done", flush=True)
    coordinator.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Delete the old replay script**

```bash
git rm scripts/replay_agentic_local_tts.py
```

- [ ] **Step 4: Smoke-check the runner with a config-parse-only dry path**

Run a syntax/import check:

```bash
python -c "from importlib.machinery import SourceFileLoader; SourceFileLoader('bench_llm', 'scripts/bench_llm.py').load_module()"
```
Expected: no output (imports succeed).

Run the arg parser:

```bash
python scripts/bench_llm.py --help
```
Expected: usage string mentioning `--config`.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_llm.py scripts/bench_configs/whisper-base-gpt4o-openjtalk.yaml
git add -u scripts/replay_agentic_local_tts.py
git commit -m "feat(bench): config-driven scripts/bench_llm.py replaces replay_agentic_local_tts"
```

---

## Task 3: 切替例 config を 2 つ追加

**Files:**
- Create: `scripts/bench_configs/whisper-base-gpt4o-openai-tts.yaml`
- Create: `scripts/bench_configs/whisper-small-gpt4o-openjtalk.yaml`

config 駆動の切替が機能していることを示す具体例を 2 つ用意する。実行は別途ユーザが行う（本タスクではファイル作成のみ）。

- [ ] **Step 1: TTS 切替 config を作る**

Create `scripts/bench_configs/whisper-base-gpt4o-openai-tts.yaml`:

```yaml
name: whisper-base-gpt4o-openai-tts
fixtures: tests/bench_fixtures/agentic_ja/fixtures.yaml
runs: 3
warmup: 1
shuffle: false
turn_timeout: 30.0

simulation:
  enabled: true
  headless: true

stt:
  model: base
  fp16: false

llm:
  model: gpt-4o
  base_url: null
  api_key_env: OPENAI_API_KEY
  system_prompt: ja_default

tts:
  impl: openai
  openai_voice: echo
  openai_model: tts-1
```

- [ ] **Step 2: STT model size 切替 config を作る**

Create `scripts/bench_configs/whisper-small-gpt4o-openjtalk.yaml`:

```yaml
name: whisper-small-gpt4o-openjtalk
fixtures: tests/bench_fixtures/agentic_ja/fixtures.yaml
runs: 3
warmup: 1
shuffle: false
turn_timeout: 30.0

simulation:
  enabled: true
  headless: true

stt:
  model: small
  fp16: false

llm:
  model: gpt-4o
  base_url: null
  api_key_env: OPENAI_API_KEY
  system_prompt: ja_default

tts:
  impl: open_jtalk
```

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_configs/whisper-base-gpt4o-openai-tts.yaml \
        scripts/bench_configs/whisper-small-gpt4o-openjtalk.yaml
git commit -m "feat(bench): example configs for TTS impl and STT model swaps"
```

---

## Task 4: spec の Open Question を更新

**Files:**
- Modify: `docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md`

Open Questions 1（MuJoCo viewer 抑制）の調査結果を反映する。

- [ ] **Step 1: spec の Open Questions セクションを書き換える**

Edit `docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md`:

Find:

```markdown
## Open Questions

1. MuJoCo viewer 抑制が `MUJOCO_GL=egl` だけで足りるか、それとも simulation backend 側に明示的な viewer flag があるか。実装計画フェーズで `dimos/simulation` 配下のコードを調査して確定する。
2. `system_prompt` の `ja_default` / `minimal` のスイッチは新規実装か、既存に該当する名前付きプロンプトがあるか。
```

Replace with:

```markdown
## Open Questions / Decisions

1. **MuJoCo viewer 抑制（解決）:** GO2 の sim path は `dimos/robot/unitree/mujoco_connection.py` → `dimos/simulation/mujoco/mujoco_process.py:111` で `viewer.launch_passive(...)` を直接呼ぶため、コードで viewer を止めるには upstream 編集が要る。これは CLAUDE.md の「upstream には最小差分」ルールに反する。対応として **bench は `xvfb-run -a` 経由で実行**することにし、`MUJOCO_GL=egl` を bench スクリプトでセットして off-screen rendering を有効化する。bench runner は `simulation.headless=true` かつ `DISPLAY` 未設定のとき警告を出す。
2. **`system_prompt` 名前付きスイッチ:** 当面 `ja_default` のみサポート。`minimal` 等は YAGNI として後回し。config に `ja_default` 以外が渡ったら bench runner が `NotImplementedError` で fail-fast する。
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md
git commit -m "docs(specs): resolve LLM bench design open questions"
```

---

## Self-Review Notes

- **Spec coverage:**
  - Goal 1 (GUI なし): Task 2 で `MUJOCO_GL=egl` + xvfb-run 運用 + DISPLAY 未設定警告。Task 4 で spec を更新。
  - Goal 2 (CLI 引数を中で / YAML config): Task 2 で `--config` 1 引数に。
  - Goal 3 (STT/LLM/TTS 切替): Task 1 で TTS impl 切替を実装、Task 2 で STT/LLM/TTS を blueprint_args に流す経路を実装、Task 3 で切替例を 2 つ作る。
  - Goal 4 (結果と config の 1:1): Task 2 で `logs/{ts}-{name}/{config.yaml, main.jsonl}` 構造 + `run_meta` に `config_name` / `config_hash` / `config` 全文。
- **Non-goals 順守:** analyzer / `summary.json` / 横断インデックスはタスクなし。新規 blueprint も作っていない。
- **TDD:** TTS impl 切替は test 先行（Task 1）。bench runner はロジックの大部分が既存 `replay_agentic_local_tts.py` の移植 + 純粋関数化された config パスで、`build_blueprint_args` / `config_hash` は unit-testable だが本タスクでは smoke check（import + --help）に留めた。これは「既存スクリプトの rename + 構造組換え」が中心で、機能的差分は config 解釈だけのため。実行ベースの統合テストはユーザが手元で実行して確認する想定。
- **Placeholders:** "TBD" / "TODO" なし。すべてのコード step に実コードを書いた。
- **Type consistency:** `AssistantSpeechNodeJaConfig` / `_make_tts_node()` は Task 1 内で完結。`blueprint_args` のキー名 (`WhisperHumanInputJa` / `TimedMcpClient` / `AssistantSpeechNodeJa` / `g`) は既存の dimos 規約に従っている（`replay_agentic_local_tts.py` の `blueprint_args={}` 呼び出しと同じ枠組み）。
