# bench を profile 参照型に統一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bench (`scripts/bench_llm.py`) を profile (`configs/profiles/<name>/`) 参照型に統一し、module パラメータの single source を profile に置く。

**Architecture:** profile 解決ロジックを fork 固有モジュール `dimos/agents/profile_ja.py` に切り出し、upstream 由来 CLI はそれに委譲する。bench は `apply_profile` → blueprint 遅延 import → `load_config_args`（upstream 由来を再利用）の順で `dimos run --profile` と同一の env/config 解決経路を通る。`build_blueprint_args` / `apply_llm_env` / `DIMOS_TTS_STREAMING` を撤去する。

**Tech Stack:** Python, pytest, pydantic (ModuleConfig), typer (CLI), yaml/json。`.venv` は source 済みで `pytest` / `python` をそのまま使う（`python3` 禁止）。

**Spec:** `docs/superpowers/specs/2026-05-21-unify-bench-and-profile-config-design.md`

---

## File Structure

- **新規** `dimos/agents/profile_ja.py`（fork 固有）— profile 名 → `.env` ロード + `config.json` パス解決。`resolve_profile()` / `apply_profile()` / `PROFILES_ROOT` を公開。
- **新規** `tests/agents/test_profile_ja.py`（fork 固有）— profile_ja の unit test。
- **最小編集** `dimos/robot/cli/dimos.py`（upstream 由来）— ローカルの `_resolve_profile`/`_apply_profile`/`_PROFILES_ROOT` を削除し profile_ja から import（fork 差分は純減）。
- **編集** `dimos/agents/skills/speak_skill_ja.py`（fork 固有）— `DIMOS_TTS_STREAMING` / `_default_tts_streaming` 削除、`streaming: bool = True`。
- **編集** `tests/agents/skills/test_speak_skill_ja_streaming.py`（fork 固有）— env seed テストを config field 直接テストに置換。
- **全面改修** `scripts/bench_llm.py`（fork 固有）— `build_blueprint_args`/`apply_llm_env` 削除、profile 参照型 `main()`、redacted endpoint。
- **新規** `tests/scripts/test_bench_llm_config.py`（fork 固有）— `load_config` 検証 + `redacted_endpoint` の unit test。
- **編集** `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml` — profile 参照型に。
- **編集** `configs/profiles/local-qwen-voicevox-sim/config.json` — `assistantspeechnodeja.streaming` 追記。
- **削除** `scripts/bench_configs/whisper-base-gpt4o-*.yaml`（4 本）、`whisper-small-gpt4o-openjtalk.yaml`。

---

## Task 1: profile_ja モジュールの切り出し

**Files:**
- Create: `dimos/agents/profile_ja.py`
- Test: `tests/agents/test_profile_ja.py`
- 参照（移植元）: `dimos/robot/cli/dimos.py:164-201`

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_profile_ja.py
from __future__ import annotations

import pytest

from dimos.agents import profile_ja


def _make_profile(tmp_path, name, env_text=None, config_text=None):
    pdir = tmp_path / name
    pdir.mkdir(parents=True)
    if env_text is not None:
        (pdir / ".env").write_text(env_text)
    if config_text is not None:
        (pdir / "config.json").write_text(config_text)
    return pdir


def test_resolve_profile_returns_existing_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    _make_profile(tmp_path, "p", env_text="X=1", config_text="{}")
    env_path, config_path = profile_ja.resolve_profile("p")
    assert env_path == (tmp_path / "p" / ".env")
    assert config_path == (tmp_path / "p" / "config.json")


def test_resolve_profile_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        profile_ja.resolve_profile("nope")


def test_resolve_profile_rejects_unsafe_name(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    with pytest.raises(ValueError):
        profile_ja.resolve_profile("../escape")


def test_apply_profile_loads_env_with_override(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    monkeypatch.setenv("DIMOS_LLM_BASE_URL", "preexisting")
    _make_profile(tmp_path, "p", env_text="DIMOS_LLM_BASE_URL=fromprofile\n", config_text="{}")
    config_path = profile_ja.apply_profile("p")
    import os
    assert os.environ["DIMOS_LLM_BASE_URL"] == "fromprofile"
    assert config_path == (tmp_path / "p" / "config.json")


def test_apply_profile_then_resolve_llm_mirrors_openai_env(tmp_path, monkeypatch):
    # Spec §9.2: the bench loads the profile .env, then imports the blueprint
    # whose module-level resolve_llm_model() mirrors DIMOS_LLM_* → OPENAI_*.
    # This asserts that exact chain (apply_profile must precede resolution).
    from dimos.agents.llm_env_ja import resolve_llm_model

    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    _make_profile(
        tmp_path,
        "p",
        env_text="DIMOS_LLM_BASE_URL=http://prof:9/v1\nDIMOS_LLM_API_KEY=k\n",
        config_text="{}",
    )
    profile_ja.apply_profile("p")
    resolve_llm_model()
    import os
    assert os.environ["OPENAI_BASE_URL"] == "http://prof:9/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_profile_ja.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dimos.agents.profile_ja'`

- [ ] **Step 3: Write the module**

```python
# dimos/agents/profile_ja.py
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

"""Resolve and apply named profiles under ``configs/profiles/<name>/``.

A profile bundles ``.env`` (deploy-dependent secrets/endpoints, category B/C)
and ``config.json`` (module parameters = blueprint_args, category A). Both
``dimos run --profile`` and the bench runner share this loader so they boot
with the identical env + config resolution path.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

PROFILES_ROOT = Path("configs/profiles")


def resolve_profile(name: str) -> tuple[Path | None, Path | None]:
    """Resolve a profile name to (env_path, config_path).

    Either may be None if absent. Raises FileNotFoundError if neither
    exists, ValueError on unsafe names.
    """
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise ValueError(f"Invalid profile name: {name!r}")

    pdir = (PROFILES_ROOT / name).resolve()
    env_path = pdir / ".env"
    config_path = pdir / "config.json"

    env_exists = env_path.is_file()
    config_exists = config_path.is_file()
    if not env_exists and not config_exists:
        raise FileNotFoundError(
            f"Profile {name!r} not found: neither {env_path} nor {config_path} exists"
        )

    return (env_path if env_exists else None, config_path if config_exists else None)


def apply_profile(name: str) -> Path | None:
    """Apply a profile: load its .env with override, return its config.json path.

    The .env (if present) is loaded into process env with override=True so
    the profile wins over any pre-existing shell variables. Returns the
    config.json Path if the profile has one, else None.
    """
    env_path, config_path = resolve_profile(name)
    if env_path is not None:
        load_dotenv(env_path, override=True)
    return config_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_profile_ja.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/profile_ja.py tests/agents/test_profile_ja.py
git commit -m "feat(profile_ja): extract profile resolver into fork module"
```

---

## Task 2: CLI を profile_ja に委譲

**Files:**
- Modify: `dimos/robot/cli/dimos.py:164-201`（`_PROFILES_ROOT` と 2 関数を削除し import に置換）
- Test: 既存 CLI テスト（`dimos/robot/cli/test_*.py`）の回帰確認

`_resolve_profile`/`_apply_profile`/`_PROFILES_ROOT` の呼び出し元は `dimos.py` 内のみ（`:198`, `:260`）。呼び出し名は維持し、定義を import に差し替える。

- [ ] **Step 1: Add the import near the other imports**

`dimos/robot/cli/dimos.py` の既存 import 群（`load_dotenv` を import している箇所付近）に追加:

```python
from dimos.agents.profile_ja import (
    apply_profile as _apply_profile,
    resolve_profile as _resolve_profile,
)
```

- [ ] **Step 2: Delete the moved definitions**

`dimos/robot/cli/dimos.py:164-201` の以下を**削除**する（`load_config_args`（204 行〜）は残す）:

```python
_PROFILES_ROOT = Path("configs/profiles")


def _resolve_profile(name: str) -> tuple[Path | None, Path | None]:
    ...  # 全体
    return (env_path if env_exists else None, config_path if config_exists else None)


def _apply_profile(name: str) -> Path | None:
    ...  # 全体
    return config_path
```

呼び出し側 `profile_config = _apply_profile(profile)`（旧 :260）はそのまま。`load_dotenv` の import がこのファイル内で他に使われていなければ未使用警告になるので、未使用なら import 行も削除する（`grep -n "load_dotenv" dimos/robot/cli/dimos.py` で確認）。

- [ ] **Step 3: Run CLI + e2e tests to verify no regression**

Run: `pytest dimos/robot/cli/ -v`
Expected: PASS（profile 関連を含む既存テストが通る）

- [ ] **Step 4: Smoke-check the help path still imports**

Run: `python -c "import dimos.robot.cli.dimos"`
Expected: 例外なく終了（import 副作用で profile_ja が解決される）

- [ ] **Step 5: Commit**

```bash
git add dimos/robot/cli/dimos.py
git commit -m "refactor(cli): delegate profile resolution to profile_ja"
```

---

## Task 3: DIMOS_TTS_STREAMING を撤去し streaming を config-only に

**Files:**
- Modify: `dimos/agents/skills/speak_skill_ja.py:74-80`（`_default_tts_streaming` 削除）, `:144`（Field 既定値）
- Modify: `tests/agents/skills/test_speak_skill_ja_streaming.py:25-37`

`import os` は他の env default_factory（`DIMOS_TTS_BACKEND`, VOICEVOX/SBV2 系）で使用中なので**残す**。

- [ ] **Step 1: Rewrite the streaming tests first (red)**

`tests/agents/skills/test_speak_skill_ja_streaming.py` の `test_streaming_default_true` / `test_streaming_env_seed_false` / `test_streaming_explicit_overrides_env`（25-37 行）を以下に**置換**:

```python
def test_streaming_default_true():
    assert AssistantSpeechNodeJaConfig().streaming is True


def test_streaming_explicit_false():
    assert AssistantSpeechNodeJaConfig(streaming=False).streaming is False


def test_streaming_ignores_env(monkeypatch):
    # DIMOS_TTS_STREAMING was removed; the env must have no effect.
    monkeypatch.setenv("DIMOS_TTS_STREAMING", "0")
    assert AssistantSpeechNodeJaConfig().streaming is True
```

`test_select_input_streaming_uses_agent_text` / `test_select_input_non_streaming_uses_agent`（40 行以降）はそのまま残す。

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/agents/skills/test_speak_skill_ja_streaming.py -v`
Expected: FAIL — `test_streaming_ignores_env` が現状の env seed 実装で False を返し失敗する

- [ ] **Step 3: Remove the env seed from speak_skill_ja.py**

`dimos/agents/skills/speak_skill_ja.py:74-80` の以下を**削除**:

```python
# DIMOS_TTS_STREAMING seeds the `streaming` default for interactive runs.
# Explicit config / YAML / bench always wins (category A behavior toggle).
def _default_tts_streaming() -> bool:
    raw = os.environ.get("DIMOS_TTS_STREAMING")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")
```

`:144` の field 定義を置換:

```python
    streaming: bool = Field(default_factory=_default_tts_streaming)
```
↓
```python
    streaming: bool = True
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/agents/skills/test_speak_skill_ja_streaming.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/skills/speak_skill_ja.py tests/agents/skills/test_speak_skill_ja_streaming.py
git commit -m "refactor(speak_ja): drop DIMOS_TTS_STREAMING, make streaming config-only"
```

---

## Task 4: bench_llm.py を profile 参照型に改修（config 解決部）

**Files:**
- Modify: `scripts/bench_llm.py`（`build_blueprint_args`/`apply_llm_env` 削除、`load_config` 検証、`main()` の解決部、`redacted_endpoint` 追加）
- Test: `tests/scripts/test_bench_llm_config.py`

bench config YAML は `name`/`profile`/`fixtures`/`runs`/`warmup`/`shuffle`/`turn_timeout`/`tts_drain_timeout`/`headless` のみを持つ。`profile` 必須。

- [ ] **Step 1: Write failing unit tests for the pure helpers**

```python
# tests/scripts/test_bench_llm_config.py
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "bench_llm", Path(__file__).resolve().parents[2] / "scripts" / "bench_llm.py"
)
bench_llm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench_llm)


def test_load_config_requires_name(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("profile: x\n")
    with pytest.raises(ValueError, match="name"):
        bench_llm.load_config(p)


def test_load_config_requires_profile(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("name: x\n")
    with pytest.raises(ValueError, match="profile"):
        bench_llm.load_config(p)


def test_redacted_endpoint_omits_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://host:1234/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    kwargs = {"timedmcpclient": {"model": "openai:qwen"}}
    ep = bench_llm.redacted_endpoint(kwargs)
    assert ep["base_url"] == "http://host:1234/v1"
    assert ep["model"] == "openai:qwen"
    assert "secret-key" not in str(ep)
    assert "api_key" not in ep
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/scripts/test_bench_llm_config.py -v`
Expected: FAIL — `load_config` が `profile` 必須でない / `redacted_endpoint` 未定義

- [ ] **Step 3: Update load_config and add redacted_endpoint; delete build_blueprint_args/apply_llm_env**

`scripts/bench_llm.py` の `load_config`（現 :51-55）を置換:

```python
def load_config(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    for required in ("name", "profile"):
        if required not in cfg:
            raise ValueError(f"config {path} missing required {required!r} field")
    return cfg
```

`build_blueprint_args`（:63-106）と `apply_llm_env`（:109-128）を**削除**し、代わりに追加:

```python
def redacted_endpoint(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Capture the resolved LLM endpoint for the run record, minus secrets.

    ``resolve_llm_model`` (fired at blueprint import after ``apply_profile``)
    mirrors the profile's DIMOS_LLM_* into OPENAI_*. We record base_url + model
    so the run is self-describing; the api_key is intentionally never logged.
    """
    return {
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "model": (kwargs.get("timedmcpclient") or {}).get("model"),
    }
```

`config_hash` は resolved kwargs を受け取る前提で残す（引数はそのまま `dict`）。

- [ ] **Step 4: Run to verify the helper tests pass**

Run: `pytest tests/scripts/test_bench_llm_config.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Rewrite main()'s resolution section + warn helper**

`scripts/bench_llm.py` 冒頭の blueprint eager import を**削除**:

```python
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
    unitree_go2_agentic_local_tts,
)
```

`warn_if_no_display_for_sim`（:157-169）を resolved kwargs ベースに置換:

```python
def warn_if_no_display_for_sim(cfg: dict[str, Any], kwargs: dict[str, Any]) -> None:
    sim_on = bool((kwargs.get("g") or {}).get("simulation"))
    if not sim_on or not cfg.get("headless"):
        return
    if os.environ.get("DISPLAY"):
        return
    print(
        "[bench] WARN: simulation on + headless but no DISPLAY. "
        "MuJoCo viewer.launch_passive will fail. Invoke via 'xvfb-run -a'.",
        file=sys.stderr,
    )
```

`main()` の先頭〜build までを置換（fixture ループ以降は現状維持）:

```python
def main() -> int:
    import copy

    from dimos.agents.profile_ja import apply_profile, resolve_profile

    args = parse_args()
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # Load the profile .env BEFORE importing the blueprint: the blueprint module
    # calls resolve_llm_model() at import time, which reads DIMOS_LLM_* and
    # mirrors them into OPENAI_*. Importing earlier would miss the profile env.
    apply_profile(cfg["profile"])
    from dimos.robot.cli.dimos import load_config_args
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
        unitree_go2_agentic_local_tts as blueprint,
    )

    _, config_path = resolve_profile(cfg["profile"])
    if config_path is None:
        raise ValueError(f"profile {cfg['profile']!r} has no config.json")
    kwargs = load_config_args(blueprint.config(), [], config_path)

    os.environ.setdefault("MUJOCO_GL", "egl")
    warn_if_no_display_for_sim(cfg, kwargs)

    out_dir = setup_run_dir(cfg, cfg_path, config_path, kwargs)
    print(f"[bench] {cfg['name']} ({cfg['profile']}) → {out_dir}", flush=True)

    # build() pops "g" from kwargs in place, so snapshot for the record first.
    resolved_snapshot = copy.deepcopy(kwargs)
    log_bench_event(
        "run_meta",
        config_name=cfg["name"],
        profile=cfg["profile"],
        resolved_config=resolved_snapshot,
        resolved_endpoint=redacted_endpoint(kwargs),
        config_hash=config_hash(resolved_snapshot),
        started_at=datetime.now().isoformat(),
    )

    coordinator = ModuleCoordinator.build(blueprint, kwargs)
    mcp_client = coordinator.get_instance(TimedMcpClient)
    mic = coordinator.get_instance(LocalMicrophoneJa)
    speech = coordinator.get_instance(AssistantSpeechNodeJa)
```

（この下の `idle_event = threading.Event()` 以降は現状のまま。）

- [ ] **Step 6: Run the helper tests again + import smoke**

Run: `pytest tests/scripts/test_bench_llm_config.py -v && python -c "import importlib.util,pathlib; s=importlib.util.spec_from_file_location('b', pathlib.Path('scripts/bench_llm.py')); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok', hasattr(m,'redacted_endpoint'), not hasattr(m,'build_blueprint_args'))"`
Expected: PASS（3 passed）+ `ok True True`

- [ ] **Step 7: Commit**

```bash
git add scripts/bench_llm.py tests/scripts/test_bench_llm_config.py
git commit -m "refactor(bench_llm): resolve modules via profile, drop translation layer"
```

---

## Task 5: 再現性記録（resolved_config.json + コピー）

**Files:**
- Modify: `scripts/bench_llm.py` の `setup_run_dir`（現 :148-154）

`run_meta` イベントは Task 4 で resolved_config/resolved_endpoint を出力済み。ここでは logs ディレクトリへのファイルダンプを整える。

- [ ] **Step 1: Rewrite setup_run_dir**

`scripts/bench_llm.py:148-154` を置換:

```python
def setup_run_dir(
    cfg: dict[str, Any],
    cfg_path: Path,
    config_path: Path,
    kwargs: dict[str, Any],
) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = Path("logs") / f"{ts}-{cfg['name']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Self-describing run record: the bench YAML, the referenced profile
    # config.json, and the resolved blueprint_args actually passed to build().
    # The profile .env is NOT copied (no secrets in logs); the endpoint is
    # captured redacted via run_meta.resolved_endpoint instead.
    shutil.copy2(cfg_path, out_dir / "bench.yaml")
    shutil.copy2(config_path, out_dir / "profile_config.json")
    (out_dir / "resolved_config.json").write_text(
        json.dumps(kwargs, indent=2, ensure_ascii=False, sort_keys=True)
    )
    set_run_log_dir(out_dir)
    return out_dir
```

- [ ] **Step 2: Verify the module still imports and signature matches the caller**

Run: `python -c "import importlib.util,pathlib,inspect; s=importlib.util.spec_from_file_location('b', pathlib.Path('scripts/bench_llm.py')); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(list(inspect.signature(m.setup_run_dir).parameters))"`
Expected: `['cfg', 'cfg_path', 'config_path', 'kwargs']`（Task 4 の呼び出しと一致）

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_llm.py
git commit -m "feat(bench_llm): write self-describing run record (resolved_config + copies)"
```

---

## Task 6: bench config の移行 + profile streaming + 旧 config 削除

**Files:**
- Modify: `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml`
- Modify: `configs/profiles/local-qwen-voicevox-sim/config.json`
- Delete: `scripts/bench_configs/whisper-base-gpt4o-openai-tts.yaml`, `whisper-base-gpt4o-openjtalk.yaml`, `whisper-base-gpt4o-sbv2.yaml`, `whisper-base-gpt4o-voicevox.yaml`, `whisper-small-gpt4o-openjtalk.yaml`

- [ ] **Step 1: Add streaming to the profile config.json**

`configs/profiles/local-qwen-voicevox-sim/config.json` の `assistantspeechnodeja` を更新:

```json
  "assistantspeechnodeja": {
    "impl": "voicevox",
    "streaming": true
  }
```

- [ ] **Step 2: Rewrite the active bench config to profile-reference form**

`scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml` を全置換:

```yaml
name: whisper-largev3-qwen3-30b-a3b-2507-voicevox
profile: local-qwen-voicevox-sim
fixtures: tests/bench_fixtures/agentic_ja/fixtures.yaml
runs: 3
warmup: 1
shuffle: false
turn_timeout: 30.0
headless: true
```

- [ ] **Step 3: Delete the legacy gpt4o bench configs**

```bash
git rm scripts/bench_configs/whisper-base-gpt4o-openai-tts.yaml \
       scripts/bench_configs/whisper-base-gpt4o-openjtalk.yaml \
       scripts/bench_configs/whisper-base-gpt4o-sbv2.yaml \
       scripts/bench_configs/whisper-base-gpt4o-voicevox.yaml \
       scripts/bench_configs/whisper-small-gpt4o-openjtalk.yaml
```

- [ ] **Step 4: Validate the migrated config loads + profile resolves**

Run: `python -c "import importlib.util,pathlib; s=importlib.util.spec_from_file_location('b', pathlib.Path('scripts/bench_llm.py')); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); from dimos.agents.profile_ja import resolve_profile; c=m.load_config(pathlib.Path('scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml')); print(c['profile']); print(resolve_profile(c['profile']))"`
Expected: `local-qwen-voicevox-sim` と `(... .env(None可) , .../config.json)` のタプルが表示され例外なし

- [ ] **Step 5: Validate the profile config.json parses against the blueprint schema**

Run: `python -c "from dimos.robot.cli.dimos import load_config_args; from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import unitree_go2_agentic_local_tts as bp; import pathlib; k=load_config_args(bp.config(), [], pathlib.Path('configs/profiles/local-qwen-voicevox-sim/config.json')); print(k['assistantspeechnodeja'])"`
Expected: `{'impl': 'voicevox', 'streaming': True}` を含む dict が表示（schema 検証パス）

- [ ] **Step 6: Commit**

```bash
git add scripts/bench_configs/ configs/profiles/local-qwen-voicevox-sim/config.json
git commit -m "feat(bench): migrate qwen-voicevox to profile-ref, drop legacy gpt4o configs"
```

---

## Final Verification

- [ ] **Full test sweep**

Run: `pytest tests/agents/test_profile_ja.py tests/agents/skills/test_speak_skill_ja_streaming.py tests/scripts/test_bench_llm_config.py dimos/robot/cli/ -v`
Expected: 全 PASS

- [ ] **No dangling references to removed symbols**

Run: `grep -rn "DIMOS_TTS_STREAMING\|build_blueprint_args\|apply_llm_env\|_default_tts_streaming" dimos/ scripts/ tests/ | grep -v "/.venv/"`
Expected: 出力なし（docs/ の歴史的記録は対象外なので grep 範囲に含めない）

- [ ] **(任意・実機) end-to-end bench run**

Run: `xvfb-run -a python scripts/bench_llm.py --config scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml`
Expected: `logs/<ts>-...` に `bench.yaml` / `profile_config.json` / `resolved_config.json` / `main.jsonl` が生成され、`main.jsonl` の `run_meta` に `profile` / `resolved_config` / `resolved_endpoint`(api_key 無し) が含まれる。LM Studio 等の endpoint が起動している前提。
