# blueprint / profile / env 責務分離 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** profile 名に畳み込まれた 4 軸（backend/model・hw・detection・sim）を分解し、model の blueprint↔profile 衝突を設計で消したうえで profile を 7→3 に再編する。

**Architecture:** model の書き手を profile config.json に一本化（blueprint の焼き込み廃止 + `TimedMcpClient` 専用 config の env-seeded Field default）。run-mode(sim) は invocation パラメータ化（`--simulation` / bench YAML）。machine は `.env` に寄せ、profile は category-A の中身でのみ分ける。

**Tech Stack:** Python / pydantic (ModuleConfig) / typer CLI / pytest。全変更 **fork-local** ファイルのみ（`*_ja.py`, `*_local_tts.py`, `configs/profiles/`, `scripts/bench_*`）。upstream 由来ファイル（`mcp_client.py`, `dimos.py`, `worker_manager_python.py`）は触らない。

**Spec:** `docs/superpowers/specs/2026-05-23-blueprint-profile-env-responsibility-design.md`

---

## File Structure

- `dimos/agents/llm_env_ja.py`（fork）— `resolve_llm_model()` を endpoint mirroring 専用 `mirror_llm_endpoint_env()` に置換。model 解決責務を除去。
- `dimos/agents/mcp/mcp_client_ja.py`（fork）— `TimedMcpClientConfig(McpClientConfig)` を追加（`model` を env-seeded category-A field 化）。`TimedMcpClient.config` 注釈を override。
- `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py`（fork）— model 焼き込み廃止、`mirror_llm_endpoint_env()` 呼び出しに置換。
- `configs/profiles/{qwen-text,qwen-vl,gpt4o}/`（fork, 新規）— config.json + .env.example。旧 7 profile の tracked ファイルを削除。
- `scripts/bench_llm.py`（fork）— bench YAML `simulation:` を `kwargs["g"]["simulation"]` に注入。stale コメント更新。
- `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml`（fork）— profile 名更新 + `simulation: true`。
- `tests/agents/test_profile_ja.py`（fork）— `resolve_llm_model` → `mirror_llm_endpoint_env` に追従。
- `tests/agents/mcp/test_timed_mcp_client_config.py`（fork, 新規）— model precedence のユニットテスト。
- `tests/robot/blueprints/test_local_tts_model_unbaked.py`（fork, 新規）— blueprint が model を焼き込まないことの確認。
- `tests/robot/cli/test_new_profiles.py`（fork, 新規）— 新 3 profile の config.json 検証。
- `docs/env-vs-config.md`（fork）— blueprint 軸・run-mode 軸を追記。

---

## Task 1: endpoint mirroring を model 解決から分離

**Files:**
- Modify: `dimos/agents/llm_env_ja.py:63-82`
- Modify: `tests/agents/test_profile_ja.py:48-64`

- [ ] **Step 1: 既存テストを新 API に追従させる（失敗させる）**

`tests/agents/test_profile_ja.py` の `test_apply_profile_then_resolve_llm_mirrors_openai_env` を次に置換:

```python
def test_apply_profile_then_mirror_endpoint_env(tmp_path, monkeypatch):
    # Spec §1: profile .env をロード後、endpoint mirroring が DIMOS_LLM_* → OPENAI_* を写す。
    from dimos.agents.llm_env_ja import mirror_llm_endpoint_env

    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    _make_profile(
        tmp_path,
        "p",
        env_text="DIMOS_LLM_BASE_URL=http://prof:9/v1\nDIMOS_LLM_API_KEY=k\n",
        config_text="{}",
    )
    profile_ja.apply_profile("p")
    mirror_llm_endpoint_env()
    import os
    assert os.environ["OPENAI_BASE_URL"] == "http://prof:9/v1"
    assert os.environ["OPENAI_API_KEY"] == "k"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/agents/test_profile_ja.py::test_apply_profile_then_mirror_endpoint_env -v`
Expected: FAIL — `ImportError: cannot import name 'mirror_llm_endpoint_env'`

- [ ] **Step 3: `resolve_llm_model()` を `mirror_llm_endpoint_env()` に置換**

`dimos/agents/llm_env_ja.py` の `def resolve_llm_model() -> str:` 〜 `return model`（63-82 行）を次に置換:

```python
def mirror_llm_endpoint_env() -> None:
    """Mirror ``DIMOS_LLM_BASE_URL`` / ``DIMOS_LLM_API_KEY`` into ``OPENAI_*``.

    Category B/C endpoint wiring (deploy-dependent). Existing ``OPENAI_*``
    values are only overwritten when the ``DIMOS_*`` counterpart is set.

    The model string is intentionally NOT resolved here — it is a category-A
    value owned by the module config (``TimedMcpClientConfig.model``), seeded
    from ``DIMOS_LLM_MODEL`` and overridable by the profile ``config.json``.
    """
    base_url = os.environ.get("DIMOS_LLM_BASE_URL")
    api_key = os.environ.get("DIMOS_LLM_API_KEY")

    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    effective_base = os.environ.get("OPENAI_BASE_URL", "<openai default>")
    logger.info("[LLM] endpoint base_url=%s", effective_base)
```

`DEFAULT_MODEL = "gpt-4o"`（60 行）は **残す**（Task 2 で import して seed default に使う）。
モジュール docstring（30-33 行付近）の `resolve_llm_model()` 言及は次に更新:

```python
``mirror_llm_endpoint_env()`` sets ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``
from the ``DIMOS_LLM_*`` counterparts so ``langchain``'s ``init_chat_model``
picks them up. The model name is owned by the module config, not resolved here.
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/agents/test_profile_ja.py -v`
Expected: PASS（`test_apply_profile_loads_env_with_override` と新テストの両方）

- [ ] **Step 5: commit**

```bash
git add dimos/agents/llm_env_ja.py tests/agents/test_profile_ja.py
git commit -m "refactor(llm_env_ja): split endpoint mirroring from model resolution

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: TimedMcpClient に env-seeded な category-A `model` を持たせる

**Files:**
- Modify: `dimos/agents/mcp/mcp_client_ja.py:33,39`
- Create: `tests/agents/mcp/test_timed_mcp_client_config.py`

- [ ] **Step 1: precedence のユニットテストを書く（失敗させる）**

`tests/agents/mcp/test_timed_mcp_client_config.py` を新規作成:

```python
"""TimedMcpClientConfig.model is a category-A field: explicit > env seed > default."""


def test_model_seeded_from_env(monkeypatch):
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    monkeypatch.setenv("DIMOS_LLM_MODEL", "seedmodel")
    assert TimedMcpClientConfig().model == "seedmodel"


def test_explicit_model_overrides_env(monkeypatch):
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    monkeypatch.setenv("DIMOS_LLM_MODEL", "seedmodel")
    assert TimedMcpClientConfig(model="explicit").model == "explicit"


def test_model_falls_back_to_default(monkeypatch):
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    monkeypatch.delenv("DIMOS_LLM_MODEL", raising=False)
    assert TimedMcpClientConfig().model == "gpt-4o"


def test_timed_client_resolves_subclassed_config():
    # Configurable picks the config class from the most-derived ``config:`` hint.
    from typing import get_type_hints

    from dimos.agents.mcp.mcp_client_ja import TimedMcpClient, TimedMcpClientConfig

    assert get_type_hints(TimedMcpClient)["config"] is TimedMcpClientConfig
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/agents/mcp/test_timed_mcp_client_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'TimedMcpClientConfig'`

- [ ] **Step 3: `TimedMcpClientConfig` を追加し `TimedMcpClient.config` を override**

`dimos/agents/mcp/mcp_client_ja.py` の import 群（33 行 `from dimos.agents.mcp.mcp_client import McpClient` 付近）を次に変更:

```python
import os

from pydantic import Field

from dimos.agents.llm_env_ja import DEFAULT_MODEL
from dimos.agents.mcp.mcp_client import McpClient, McpClientConfig
```

`class TimedMcpClient(McpClient):`（39 行）の **直前**に config サブクラスを追加:

```python
class TimedMcpClientConfig(McpClientConfig):
    """Fork-local config: ``model`` becomes a category-A field seeded from env.

    Precedence: ``profile config.json value > DIMOS_LLM_MODEL env seed > "gpt-4o"``.
    The blueprint no longer bakes the model, so the profile config.json is the
    sole writer and there is no blueprint↔profile collision to resolve.
    """

    model: str = Field(
        default_factory=lambda: os.environ.get("DIMOS_LLM_MODEL", DEFAULT_MODEL)
    )
```

`class TimedMcpClient(McpClient):` の本体先頭（`agent_text: Out[str]` の直前）に config 注釈を追加:

```python
class TimedMcpClient(McpClient):
    """McpClient with bench instrumentation.
    ...
    """

    config: TimedMcpClientConfig

    agent_text: Out[str]
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/agents/mcp/test_timed_mcp_client_config.py -v`
Expected: PASS（4 件すべて）

- [ ] **Step 5: commit**

```bash
git add dimos/agents/mcp/mcp_client_ja.py tests/agents/mcp/test_timed_mcp_client_config.py
git commit -m "feat(mcp_client_ja): TimedMcpClientConfig with env-seeded category-A model

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: blueprint の model 焼き込みを廃止

**Files:**
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py:37,50,58`
- Create: `tests/robot/blueprints/test_local_tts_model_unbaked.py`

- [ ] **Step 1: blueprint が model を焼き込まないことのテストを書く（失敗させる）**

`tests/robot/blueprints/test_local_tts_model_unbaked.py` を新規作成:

```python
"""The local-tts agentic blueprint must NOT bake `model` into the TimedMcpClient atom.

Model is owned by the profile config.json (category A); baking it here would
re-introduce a blueprint↔profile collision (Spec §1).
"""


def test_timed_mcp_client_atom_has_no_model():
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
        unitree_go2_agentic_local_tts as bp,
    )

    atom = next(b for b in bp.blueprints if b.module is TimedMcpClient)
    assert "model" not in atom.kwargs
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/robot/blueprints/test_local_tts_model_unbaked.py -v`
Expected: FAIL — `assert "model" not in atom.kwargs`（現状 `model=_LLM_MODEL` が焼き込まれているため）

- [ ] **Step 3: 焼き込みを廃止し endpoint mirroring 呼び出しに置換**

`dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py`:

37 行 を変更:
```python
from dimos.agents.llm_env_ja import mirror_llm_endpoint_env
```

48-50 行（`# LLM endpoint switching...` コメント + `_LLM_MODEL = resolve_llm_model()`）を次に置換:
```python
# LLM endpoint wiring: DIMOS_LLM_BASE_URL / DIMOS_LLM_API_KEY → OPENAI_*.
# Called at import (main process, after apply_profile, before worker fork).
# The model string is owned by the profile config.json (TimedMcpClientConfig),
# not baked here. See dimos/agents/llm_env_ja.py.
mirror_llm_endpoint_env()
```

58 行 を変更（`model=_LLM_MODEL,` を削除）:
```python
    TimedMcpClient.blueprint(system_prompt=SYSTEM_PROMPT_JA),
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/robot/blueprints/test_local_tts_model_unbaked.py tests/agents/mcp/test_timed_mcp_client_config.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py tests/robot/blueprints/test_local_tts_model_unbaked.py
git commit -m "refactor(local-tts blueprint): stop baking LLM model; profile owns it

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: profile を 7→3 に再編

**Files:**
- Create: `configs/profiles/qwen-text/{config.json,.env.example}`
- Create: `configs/profiles/qwen-vl/{config.json,.env.example}`
- Create: `configs/profiles/gpt4o/{config.json,.env.example}`
- Delete (tracked files): 旧 7 profile の `config.json` / `.env.example`
- Create: `tests/robot/cli/test_new_profiles.py`

- [ ] **Step 1: 新 profile 検証テストを書く（失敗させる）**

`tests/robot/cli/test_new_profiles.py` を新規作成:

```python
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "name,model",
    [
        ("qwen-text", "openai:qwen/qwen3-30b-a3b-2507"),
        ("qwen-vl", "openai:qwen/qwen3.6-35b-a3b"),
        ("gpt4o", "openai:gpt-4o"),
    ],
)
def test_profile_config_shape(name, model):
    cfg = json.loads(Path(f"configs/profiles/{name}/config.json").read_text())
    # run-mode は profile が持たない（Spec §2）
    assert "g" not in cfg
    # model は category A として profile が所有（Spec §1）
    assert cfg["timedmcpclient"]["model"] == model
    # machine 非依存の共通 category-A 値
    assert cfg["rerunbridgemodule"]["memory_limit"] == "25%"
    assert cfg["assistantspeechnodeja"]["impl"] == "voicevox"


@pytest.mark.parametrize("name", ["qwen-text", "qwen-vl", "gpt4o"])
def test_profile_has_env_example(name):
    # .env.example はテンプレとして commit。実 .env は各マシンで作成し gitignore。
    assert (Path(f"configs/profiles/{name}") / ".env.example").is_file()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `pytest tests/robot/cli/test_new_profiles.py -v`
Expected: FAIL — `FileNotFoundError: configs/profiles/qwen-text/config.json`

- [ ] **Step 3: 新 3 profile を作成**

`configs/profiles/qwen-text/config.json`:
```json
{
  "rerunbridgemodule": {
    "memory_limit": "25%"
  },
  "localmicrophoneja": {
    "mic_mode": "vad"
  },
  "whisperhumaninputja": {
    "model": "large-v3",
    "fp16": true
  },
  "timedmcpclient": {
    "model": "openai:qwen/qwen3-30b-a3b-2507"
  },
  "assistantspeechnodeja": {
    "impl": "voicevox",
    "streaming": true
  }
}
```

`configs/profiles/qwen-vl/config.json`:（`timedmcpclient.model` のみ差し替え）
```json
{
  "rerunbridgemodule": {
    "memory_limit": "25%"
  },
  "localmicrophoneja": {
    "mic_mode": "vad"
  },
  "whisperhumaninputja": {
    "model": "large-v3",
    "fp16": true
  },
  "timedmcpclient": {
    "model": "openai:qwen/qwen3.6-35b-a3b"
  },
  "assistantspeechnodeja": {
    "impl": "voicevox",
    "streaming": true
  }
}
```

`configs/profiles/gpt4o/config.json`:
```json
{
  "rerunbridgemodule": {
    "memory_limit": "25%"
  },
  "localmicrophoneja": {
    "mic_mode": "vad"
  },
  "whisperhumaninputja": {
    "model": "large-v3",
    "fp16": true
  },
  "timedmcpclient": {
    "model": "openai:gpt-4o"
  },
  "assistantspeechnodeja": {
    "impl": "voicevox",
    "streaming": true
  }
}
```

`configs/profiles/qwen-text/.env.example` と `configs/profiles/qwen-vl/.env.example`（同内容）:
```
# Local OpenAI-compatible LLM endpoint (LM Studio / vLLM / Ollama).
# The same profile runs on desktop or DGX Spark — the machine is whichever
# .env is filled here. Copy this file to .env and adjust per machine.
DIMOS_LLM_BASE_URL=http://localhost:1234/v1
DIMOS_LLM_API_KEY=dummy
```

`configs/profiles/gpt4o/.env.example`:
```
# Azure OpenAI (v1, OpenAI-compatible) or OpenAI cloud endpoint.
# Copy this file to .env and fill in real values (key is a secret — never commit).
DIMOS_LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1
DIMOS_LLM_API_KEY=<azure-key>
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest tests/robot/cli/test_new_profiles.py -v`
Expected: PASS

- [ ] **Step 5: 旧 7 profile の tracked ファイルを削除**

> 注意: 旧 profile dir 内の実 `.env`（gitignore・secret）は `git rm` の対象外。
> 操作者は新 profile の `.env` に必要な endpoint/key を移してから旧 dir を手動削除すること。
> ここでは tracked ファイル（config.json / .env.example）のみ削除する。

```bash
git rm configs/profiles/local-qwen-voicevox/config.json \
       configs/profiles/local-qwen-voicevox-sim/config.json \
       configs/profiles/local-qwen-voicevox-spark/config.json \
       configs/profiles/local-qwen-voicevox-spark-sim/config.json \
       configs/profiles/local-qwen-voicevox-spark-detection/config.json \
       configs/profiles/local-qwen-voicevox-spark-detection-sim/config.json \
       configs/profiles/azure-gpt4o-voicevox-sim/config.json
# .env.example が tracked な旧 profile も削除（存在するものだけ）
git rm --ignore-unmatch \
       configs/profiles/local-qwen-voicevox-spark-sim/.env.example \
       configs/profiles/azure-gpt4o-voicevox-sim/.env.example
```

- [ ] **Step 6: commit**

```bash
git add configs/profiles/qwen-text configs/profiles/qwen-vl configs/profiles/gpt4o tests/robot/cli/test_new_profiles.py
git commit -m "refactor(profiles): collapse 7 profiles into qwen-text / qwen-vl / gpt4o

Drop sim (now --simulation flag) and machine (now .env) from profile identity;
profiles differ only by category-A model. memory_limit unified to 25%.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: bench の simulation 供給を YAML フィールド化

**Files:**
- Modify: `scripts/bench_llm.py:9-11,45-47,72,152-155`
- Modify: `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml:2`

- [ ] **Step 1: bench config YAML を新 profile + simulation フィールドに更新**

`scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml` の 2 行目を変更し、`headless: true` の後に `simulation: true` を追加:

```yaml
name: whisper-largev3-qwen3-30b-a3b-2507-voicevox
profile: qwen-text
fixtures: tests/bench_fixtures/agentic_ja/fixtures.yaml
runs: 3
warmup: 1
shuffle: false
turn_timeout: 30.0
headless: true
simulation: true
```

- [ ] **Step 2: bench main() に simulation 注入を追加**

`scripts/bench_llm.py` の `kwargs = load_config_args(blueprint.config(), [], config_path)`（152 行）の **直後**に追加:

```python
    # Run-mode is an invocation parameter, not a profile concern (Spec §2).
    # The bench YAML carries `simulation:` so the run stays reproducible.
    kwargs.setdefault("g", {})["simulation"] = bool(cfg.get("simulation", False))
```

- [ ] **Step 3: stale なコメント/docstring を更新**

`scripts/bench_llm.py` 内の `resolve_llm_model` 言及を `mirror_llm_endpoint_env` に更新する:

- 9-11 行のモジュール docstring中の一文を:
```python
``.env`` before the blueprint is imported so that the blueprint's module-level
``mirror_llm_endpoint_env()`` sees the correct ``DIMOS_LLM_*`` values.
```
- 45-47 行の NOTE を:
```python
# NOTE: the agentic blueprint is intentionally NOT imported at top level. It
# calls mirror_llm_endpoint_env() at module load, which reads DIMOS_LLM_* env.
# Import is deferred to main() AFTER apply_profile().
```
- 72 行 `redacted_endpoint` の docstring を:
```python
    ``mirror_llm_endpoint_env`` (fired at blueprint import after ``apply_profile``)
```

- [ ] **Step 4: bench スモークで simulation 注入を確認**

Run:
```bash
python -c "
import yaml, copy
from dimos.agents.profile_ja import apply_profile
cfg = yaml.safe_load(open('scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml'))
config_path = apply_profile(cfg['profile'])
from dimos.robot.cli.dimos import load_config_args
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import unitree_go2_agentic_local_tts as bp
kwargs = load_config_args(bp.config(), [], config_path)
kwargs.setdefault('g', {})['simulation'] = bool(cfg.get('simulation', False))
assert kwargs['g']['simulation'] is True, kwargs.get('g')
assert kwargs['timedmcpclient']['model'] == 'openai:qwen/qwen3-30b-a3b-2507'
print('OK: simulation injected =', kwargs['g']['simulation'], '| model =', kwargs['timedmcpclient']['model'])
"
```
Expected: `OK: simulation injected = True | model = openai:qwen/qwen3-30b-a3b-2507`（例外なし）

- [ ] **Step 5: commit**

```bash
git add scripts/bench_llm.py scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml
git commit -m "feat(bench): supply simulation via bench YAML field instead of profile g block

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: env-vs-config.md に blueprint 軸・run-mode 軸を追記

**Files:**
- Modify: `docs/env-vs-config.md`

- [ ] **Step 1: 責務モデルの節を追記**

`docs/env-vs-config.md` 末尾（`## profile ... との関係` 節の後）に追加:

```markdown
## blueprint / run-mode 軸（2026-05 追記）

env vs config（A/B/C）は「値の置き場」を決めるが、実際の責務分離はもう 2 軸を含む。

| 軸 | 所有する concern | 選択方法 |
|---|---|---|
| **blueprint**（コード） | トポロジ: module 構成・transport・remapping・capability の有無（detection wiring 等） | `dimos run <bp>` 位置引数 |
| **profile / config.json**（A） | デプロイ調整値: `timedmcpclient.model`（自由切替）, mic_mode, whisper params, tts impl, memory_limit | `--profile NAME` |
| **profile / .env**（B/C） | secret + endpoint。machine（spark/desktop）の本質はここ | profile 同梱（gitignore） |
| **run-mode（`g.*`）** | `simulation` 等の invocation パラメータ。profile でも blueprint でもない | `dimos run --simulation` / bench YAML `simulation:` |

### 重要原則: 衝突は precedence で裁くのではなく設計で消す

`model` は capability(blueprint) と backend(profile) に跨る共有値だが、**書き手を profile config.json
に一本化**することで衝突源を消す。blueprint は model を焼き込まない（`TimedMcpClientConfig.model` が
`DIMOS_LLM_MODEL` を seed default に持つ category-A field）。precedence
（`explicit > env seed > default`）は衝突を裁くルールではなく、衝突を消した後のフォールバック順。

> detection blueprint には VL モデルの profile（`qwen-vl` / `gpt4o`）を当てる、は enforce しない運用規約。

詳細は `docs/superpowers/specs/2026-05-23-blueprint-profile-env-responsibility-design.md`。
```

- [ ] **Step 2: commit**

```bash
git add docs/env-vs-config.md
git commit -m "docs(env-vs-config): add blueprint and run-mode axes to responsibility model

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## 最終検証

- [ ] **全テスト green**

Run:
```bash
pytest tests/agents/test_profile_ja.py \
       tests/agents/mcp/test_timed_mcp_client_config.py \
       tests/robot/blueprints/test_local_tts_model_unbaked.py \
       tests/robot/cli/test_new_profiles.py \
       tests/robot/cli/test_profile_resolution.py -v
```
Expected: 全 PASS（`test_profile_resolution.py` は実 profile 名非依存なので影響なし）

- [ ] **手動起動マトリクス（任意・要 .env 記入）**

各 profile dir に `.env` を用意した上で:
```
dimos run unitree-go2-agentic-local-tts           --profile qwen-text --simulation
dimos run unitree-go2-agentic-local-tts-detection --profile qwen-vl   --simulation
dimos run unitree-go2-agentic-local-tts-detection --profile gpt4o     --simulation
```
Expected: いずれも起動し、ログの `[LLM] endpoint base_url=...` が profile の .env を反映。

---

## スコープ外（spec の注記どおり）

- `dimos.py:294` の `g` マージ不全（CLI `g` フラグが config.json の `g` を置換）。upstream 編集になるため扱わない。本プランで profile から `g` を除去するため実害は回避される。
- blueprint への modality requirement 宣言 / resolver 機構は作らない（YAGNI）。detection×VL profile の組合せは運用責任。
