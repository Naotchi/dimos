# unitree-go2-agentic: LLM env 化 + ローカル TTS 切替 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitree-go2-agentic` blueprint を、LLM モデルを `DIMOS_LLM_MODEL` env で、TTS を `DIMOS_TTS` env (`openai|pyttsx3`、デフォルト `pyttsx3`) で切替可能にして起動できるようにする。

**Architecture:** `unitree_go2_agentic.py` で env から LLM モデル文字列を読み `McpClient.blueprint(model=...)` に渡す（LangChain が prefix で provider 判定）。`SpeakSkill.start()` で `DIMOS_TTS` を見て `OpenAITTSNode + SounddeviceAudioOutput` か `PyTTSNode` 単体を生成。既存の `_speak_blocking` 完了待ちは両ノードの `emit_text()` で共通。

**Tech Stack:** Python 3.12, LangChain (init_chat_model)、reactivex、pyttsx3、既存 `dimos` モジュール群。

**Spec:** `docs/superpowers/specs/2026-05-13-unitree-go2-agentic-local-tts-llm-env-design.md`

---

## File Structure

- **Modify** `pyproject.toml` — `agents` extras に `pyttsx3` 追加
- **Modify** `dimos/stream/audio/tts/node_pytts.py` — 既存の壊れた import (`dimos.stream.audio.text.abstract`) を `.base` に修正
- **Modify** `dimos/agents/skills/speak_skill.py` — `DIMOS_TTS` env による分岐、型アノテーション更新
- **Modify** `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py` — `DIMOS_LLM_MODEL` env を読む
- **Modify** `README.md` — 環境変数説明追記
- **Create** `dimos/agents/skills/tests/test_speak_skill_env.py` — env 分岐の単体テスト（不正値で fail-fast を検証）

---

## Task 1: 既存 PyTTSNode の壊れた import を修正

`node_pytts.py` は `dimos.stream.audio.text.abstract` から import しているが、実在モジュールは `dimos.stream.audio.text.base`。現状 import 即 ImportError。

**Files:**
- Modify: `dimos/stream/audio/tts/node_pytts.py:19-21`

- [ ] **Step 1: 修正前の挙動を確認**

Run:
```bash
cd /home/naoki/dimos && uv run python -c "from dimos.stream.audio.tts.node_pytts import PyTTSNode"
```
Expected: `ModuleNotFoundError: No module named 'dimos.stream.audio.text.abstract'`

- [ ] **Step 2: import パスを修正**

`dimos/stream/audio/tts/node_pytts.py` の以下を変更：

```python
from dimos.stream.audio.text.abstract import (  # type: ignore[import-untyped]
    AbstractTextTransform,
)
```

を

```python
from dimos.stream.audio.text.base import AbstractTextTransform
```

に置換（`# type: ignore` は不要、`base.py` は型情報あり）。

- [ ] **Step 3: import が通ることを確認**

Run:
```bash
cd /home/naoki/dimos && uv run python -c "from dimos.stream.audio.tts.node_pytts import PyTTSNode; print('ok')"
```
Expected: `pyttsx3` 未インストールなら `ModuleNotFoundError: No module named 'pyttsx3'`、インストール済なら `ok`。`dimos.stream.audio.text.abstract` の名前は出ないこと。

- [ ] **Step 4: コミット**

```bash
git add dimos/stream/audio/tts/node_pytts.py
git commit -m "fix(tts): correct PyTTSNode import path (.abstract -> .base)"
```

---

## Task 2: pyttsx3 を依存に追加

**Files:**
- Modify: `pyproject.toml` (agents extras セクション)

- [ ] **Step 1: 該当箇所を確認**

Run:
```bash
grep -n "faster-whisper" /home/naoki/dimos/pyproject.toml
```
Expected: `agents` extras 内の `# Audio` ブロックに `"faster-whisper>=1.0.0",` がある行が見つかる。

- [ ] **Step 2: `pyttsx3` を追加**

`pyproject.toml` の `agents = [` セクション、`# Audio` ブロック末尾に追加：

```toml
    # Audio
    "openai",
    "sounddevice",
    "faster-whisper>=1.0.0",
    "pyttsx3>=2.90",
```

- [ ] **Step 3: ロックとインストール**

Run:
```bash
cd /home/naoki/dimos && uv sync --extra agents
```
Expected: `pyttsx3` が解決・インストールされてエラーなし。

- [ ] **Step 4: import 確認**

Run:
```bash
cd /home/naoki/dimos && uv run python -c "from dimos.stream.audio.tts.node_pytts import PyTTSNode; print('ok')"
```
Expected: `ok`（Linux で espeak が無ければ `pyttsx3.init()` 時点では落ちない — class import のみのため OK）。

- [ ] **Step 5: コミット**

```bash
git add pyproject.toml uv.lock
git commit -m "deps(agents): add pyttsx3 for local TTS"
```

---

## Task 3: SpeakSkill に env 分岐を実装（失敗テスト先行）

`SpeakSkill.start()` で `DIMOS_TTS` を読んでノード選択。不正値は `ValueError`。テストでは `_tts_node` の型を確認することで分岐が効いていることを検証する（実音は鳴らさない）。

**Files:**
- Create: `dimos/agents/skills/tests/test_speak_skill_env.py`
- Modify: `dimos/agents/skills/speak_skill.py`

- [ ] **Step 1: テストディレクトリ確認**

Run:
```bash
ls /home/naoki/dimos/dimos/agents/skills/tests/ 2>/dev/null || echo "no dir"
```
Expected: ディレクトリの有無を確認。無ければ次ステップで作成。

- [ ] **Step 2: 失敗テストを書く**

Create `dimos/agents/skills/tests/test_speak_skill_env.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
"""SpeakSkill env-driven TTS selection tests."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.stream.audio.tts.node_openai import OpenAITTSNode
from dimos.stream.audio.tts.node_pytts import PyTTSNode


def _make_skill() -> SpeakSkill:
    # SpeakSkill is a Module; for unit testing we construct it without the
    # full coordination harness and call start() directly.
    return SpeakSkill()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "pyttsx3"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_pyttsx3_uses_pytts_node(pytts_cls: mock.MagicMock) -> None:
    skill = _make_skill()
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    try:
        skill.start()
        pytts_cls.assert_called_once()
        assert skill._audio_output is None
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "openai"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.SounddeviceAudioOutput")
@mock.patch("dimos.agents.skills.speak_skill.OpenAITTSNode")
def test_start_openai_uses_openai_node(
    openai_cls: mock.MagicMock, sd_cls: mock.MagicMock
) -> None:
    skill = _make_skill()
    openai_cls.return_value = mock.MagicMock(spec=OpenAITTSNode)
    sd_cls.return_value = mock.MagicMock()
    try:
        skill.start()
        openai_cls.assert_called_once()
        sd_cls.assert_called_once_with(sample_rate=24000)
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "bogus"}, clear=False)
def test_start_invalid_env_raises() -> None:
    skill = _make_skill()
    with pytest.raises(ValueError, match="DIMOS_TTS"):
        skill.start()


@mock.patch.dict(os.environ, {}, clear=True)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_default_is_pyttsx3(pytts_cls: mock.MagicMock) -> None:
    skill = _make_skill()
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    try:
        skill.start()
        pytts_cls.assert_called_once()
    finally:
        skill.stop()
```

- [ ] **Step 3: テストが失敗することを確認**

Run:
```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/skills/tests/test_speak_skill_env.py -v
```
Expected: 4 件すべて FAIL（実装未変更のため env を読まず常に `OpenAITTSNode` が呼ばれる、または `PyTTSNode` が import されていないため `AttributeError`）。

- [ ] **Step 4: SpeakSkill を実装**

`dimos/agents/skills/speak_skill.py` を以下のとおり修正。

import セクションに追加：

```python
import os
```

既存の `from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice` の直下に追加：

```python
from dimos.stream.audio.tts.node_pytts import PyTTSNode
```

`class SpeakSkill(Module):` の `_tts_node` 型注釈を変更：

```python
class SpeakSkill(Module):
    _tts_node: OpenAITTSNode | PyTTSNode | None = None
    _audio_output: SounddeviceAudioOutput | None = None
```

`start()` を以下に置換：

```python
    @rpc
    def start(self) -> None:
        super().start()
        backend = os.environ.get("DIMOS_TTS", "pyttsx3").lower()
        if backend == "openai":
            self._tts_node = OpenAITTSNode(speed=1.2, voice=Voice.ONYX)
            self._audio_output = SounddeviceAudioOutput(sample_rate=24000)
            self._audio_output.consume_audio(self._tts_node.emit_audio())
        elif backend == "pyttsx3":
            self._tts_node = PyTTSNode()
            self._audio_output = None
        else:
            raise ValueError(
                f"DIMOS_TTS must be 'openai' or 'pyttsx3', got: {backend!r}"
            )
```

`stop()` は既存の `if self._audio_output:` ガードのおかげで pyttsx3 モード（`_audio_output is None`）でも動作する。変更不要。

- [ ] **Step 5: テスト通過確認**

Run:
```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/skills/tests/test_speak_skill_env.py -v
```
Expected: 4 件すべて PASS。

- [ ] **Step 6: コミット**

```bash
git add dimos/agents/skills/speak_skill.py dimos/agents/skills/tests/test_speak_skill_env.py
git commit -m "feat(speak): switch TTS backend via DIMOS_TTS env (default pyttsx3)"
```

---

## Task 4: unitree_go2_agentic blueprint で LLM を env から取る

**Files:**
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py`

- [ ] **Step 1: 現状確認**

Run:
```bash
cat /home/naoki/dimos/dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py
```
Expected: `McpClient.blueprint(),` がそのまま呼ばれている（model 引数なし）。

- [ ] **Step 2: env を読むよう変更**

`dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py` を以下に置換：

```python
#!/usr/bin/env python3
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

import os

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial

_LLM_MODEL = os.environ.get("DIMOS_LLM_MODEL", "gpt-4o")

unitree_go2_agentic = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    McpClient.blueprint(model=_LLM_MODEL),
    _common_agentic,
)

__all__ = ["unitree_go2_agentic"]
```

- [ ] **Step 3: import 検証**

Run:
```bash
cd /home/naoki/dimos && DIMOS_LLM_MODEL=ollama:qwen3:8b uv run python -c "
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic
# autoconnect の中の BlueprintAtom に model='ollama:qwen3:8b' が入っているか確認
import json
print('blueprint import ok')
"
```
Expected: `blueprint import ok`。

- [ ] **Step 4: コミット**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py
git commit -m "feat(go2-agentic): read LLM model from DIMOS_LLM_MODEL env"
```

---

## Task 5: README に env 説明追記

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 該当箇所確認**

Run:
```bash
grep -n "dimos run unitree-go2-agentic" /home/naoki/dimos/README.md
```
Expected: 行番号（既存の例の場所）が出る。

- [ ] **Step 2: 起動例の直下に env 説明ブロックを追加**

`README.md` の `dimos run unitree-go2-agentic --daemon   # Start in background` の行の直後（次のコードフェンス／節の前）に以下を挿入：

````markdown

### Environment variables (`unitree-go2-agentic`)

| Variable | Default | Description |
|---|---|---|
| `DIMOS_LLM_MODEL` | `gpt-4o` | LangChain model string. Examples: `gpt-4o`, `ollama:qwen3:8b`, `anthropic:claude-opus-4-5` |
| `DIMOS_TTS` | `pyttsx3` | TTS backend: `openai` (cloud) or `pyttsx3` (local). `pyttsx3` requires `espeak`/`libespeak1` on Linux. |

Example: fully local stack (no OpenAI key required):

```sh
DIMOS_LLM_MODEL=ollama:qwen3:8b dimos run unitree-go2-agentic
```

````

- [ ] **Step 3: コミット**

```bash
git add README.md
git commit -m "docs(readme): document DIMOS_LLM_MODEL / DIMOS_TTS env for go2-agentic"
```

---

## Task 6: スモーク（手動）

CI で実機・音声を回さない方針のため、ここはローカル手動。実機が無くても `dimos run --help` レベルの起動／import 通過を確認する。

- [ ] **Step 1: blueprint 一覧に出ることを確認**

Run:
```bash
cd /home/naoki/dimos && uv run dimos run --help 2>&1 | grep -i "unitree-go2-agentic"
```
Expected: `unitree-go2-agentic` がリストに出る。

- [ ] **Step 2: env 違いで import が通ること（実機接続せず）**

Run:
```bash
cd /home/naoki/dimos && \
  DIMOS_LLM_MODEL=ollama:qwen3:8b DIMOS_TTS=pyttsx3 \
  uv run python -c "
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic
print('ok pyttsx3+ollama')
" && \
  DIMOS_TTS=openai \
  uv run python -c "
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic
print('ok openai')
"
```
Expected: 両方とも `ok ...` で終了コード 0。

- [ ] **Step 3: 既存テスト群が壊れていないか**

Run:
```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/skills/ -x -q
```
Expected: PASS（speak_skill 周辺の既存テストがあれば全通過）。

- [ ] **Step 4: 不正値で fail-fast を再確認（手動でも）**

Run:
```bash
cd /home/naoki/dimos && DIMOS_TTS=bogus uv run python -c "
from dimos.agents.skills.speak_skill import SpeakSkill
s = SpeakSkill()
try:
    s.start()
except ValueError as e:
    print('expected:', e)
"
```
Expected: `expected: DIMOS_TTS must be 'openai' or 'pyttsx3', got: 'bogus'`

---

## 完了条件

- [ ] Task 1–5 のコミットが揃っている
- [ ] `pytest dimos/agents/skills/tests/test_speak_skill_env.py` が 4/4 PASS
- [ ] `DIMOS_LLM_MODEL=ollama:... DIMOS_TTS=pyttsx3 dimos run unitree-go2-agentic` が（実機の有無に依らず）import / 起動初期段階を通過
- [ ] README に env 表が追加されている

## 非対象（明示）

- STT (`WhisperNode`) の変更
- `unitree_go2_agentic_ollama.py` / `unitree_go2_agentic_huggingface.py` の変更
- pyttsx3 以外のローカル TTS（Piper/Coqui 等）
- MCP Server / Client / `_common_agentic` 構造の変更
