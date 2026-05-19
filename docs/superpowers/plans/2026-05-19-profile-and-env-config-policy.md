# Profile CLI + env/config policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dimos run --profile NAME` で env+config を1コマンドでスワップできるようにし、env vs config 責務分離ルールを明文化、VOICEVOX/SBV2 パラメータを env-only から config field に昇格する。

**Architecture:** profile は `configs/profiles/NAME/` ディレクトリ。`.env` を `dotenv.load_dotenv(override=True)` で読んだ後、`config.json` を upstream の既存 `load_config_args` 経路にそのまま流す。`--profile` と `-c` は排他。Phase 1=機構、Phase 2=政策ドキュメント、Phase 3=既存 env-only パラメータの config 昇格。

**Tech Stack:** Python 3.12, Typer/Click, python-dotenv (既存依存), Pydantic v2 ModuleConfig, pytest

**Spec:** `docs/superpowers/specs/2026-05-19-profile-and-env-config-policy-design.md`

---

## File Structure

**Created:**
- `configs/profiles/local-qwen-voicevox/config.json` (移行)
- `configs/profiles/local-qwen-voicevox/.env.example`
- `docs/env-vs-config.md`
- `tests/robot/cli/test_profile_resolution.py`
- `tests/agents/skills/test_voicevox_params_config.py`
- `tests/agents/skills/test_sbv2_params_config.py`

**Modified:**
- `dimos/robot/cli/dimos.py` (run コマンドに `--profile` 追加、解決ヘルパ追加)
- `dimos/agents/skills/speak_skill_ja.py` (VoicevoxParamsConfig / Sbv2ParamsConfig 追加、`_make_tts_node` 引数渡し)
- `dimos/stream/audio/tts/node_voicevox.py` (env 直読み削除)
- `dimos/stream/audio/tts/node_style_bert_vits2.py` (env 直読み削除)

**Deleted:**
- `configs/local_qwen_voicevox.json` (profile dir 内に移動)

---

## Phase 1: profile 機構

### Task 1.1: profile 解決ヘルパ (TDD)

**Files:**
- Modify: `dimos/robot/cli/dimos.py` (新規ヘルパ追加)
- Test: `tests/robot/cli/test_profile_resolution.py`

- [ ] **Step 1: テストファイル骨格を書く**

`tests/robot/cli/test_profile_resolution.py` を新規作成：

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for `_resolve_profile` helper used by `dimos run --profile NAME`."""

from __future__ import annotations

from pathlib import Path

import pytest

from dimos.robot.cli.dimos import _resolve_profile


def test_resolve_existing_profile(tmp_path, monkeypatch):
    profiles_root = tmp_path / "configs" / "profiles"
    pdir = profiles_root / "local-qwen-voicevox"
    pdir.mkdir(parents=True)
    (pdir / "config.json").write_text("{}")
    (pdir / ".env").write_text("FOO=bar\n")

    monkeypatch.chdir(tmp_path)
    env_path, config_path = _resolve_profile("local-qwen-voicevox")
    assert env_path == pdir / ".env"
    assert config_path == pdir / "config.json"


def test_resolve_profile_with_only_config(tmp_path, monkeypatch):
    pdir = tmp_path / "configs" / "profiles" / "only-config"
    pdir.mkdir(parents=True)
    (pdir / "config.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    env_path, config_path = _resolve_profile("only-config")
    assert env_path is None
    assert config_path == pdir / "config.json"


def test_resolve_profile_with_only_env(tmp_path, monkeypatch):
    pdir = tmp_path / "configs" / "profiles" / "only-env"
    pdir.mkdir(parents=True)
    (pdir / ".env").write_text("X=1\n")
    monkeypatch.chdir(tmp_path)
    env_path, config_path = _resolve_profile("only-env")
    assert env_path == pdir / ".env"
    assert config_path is None


def test_resolve_missing_profile_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        _resolve_profile("nonexistent")


def test_resolve_empty_profile_raises(tmp_path, monkeypatch):
    pdir = tmp_path / "configs" / "profiles" / "empty"
    pdir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        _resolve_profile("empty")


@pytest.mark.parametrize("name", ["../escape", "foo/bar", ".hidden", "", "."])
def test_reject_unsafe_names(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        _resolve_profile(name)
```

- [ ] **Step 2: テスト実行して失敗を確認**

Run: `python -m pytest tests/robot/cli/test_profile_resolution.py -v`
Expected: ImportError or AttributeError (`_resolve_profile` 未定義)

- [ ] **Step 3: ヘルパを実装**

`dimos/robot/cli/dimos.py` の `load_config_args` 関数の **直前** (line 164 の前) に追加：

```python
_PROFILES_ROOT = Path("configs/profiles")


def _resolve_profile(name: str) -> tuple[Path | None, Path | None]:
    """Resolve a profile name to (env_path, config_path).

    Returns paths to .env and config.json under configs/profiles/NAME/.
    Either may be None if absent. Raises FileNotFoundError if neither
    exists, ValueError on unsafe names.
    """
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise ValueError(f"Invalid profile name: {name!r}")

    pdir = _PROFILES_ROOT / name
    env_path = pdir / ".env"
    config_path = pdir / "config.json"

    env_exists = env_path.is_file()
    config_exists = config_path.is_file()
    if not env_exists and not config_exists:
        raise FileNotFoundError(
            f"Profile {name!r} not found: neither {env_path} nor {config_path} exists"
        )

    return (env_path if env_exists else None, config_path if config_exists else None)
```

- [ ] **Step 4: テスト実行して通ることを確認**

Run: `python -m pytest tests/robot/cli/test_profile_resolution.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add dimos/robot/cli/dimos.py tests/robot/cli/test_profile_resolution.py
git commit -m "feat(cli): add _resolve_profile helper for --profile NAME"
```

---

### Task 1.2: `--profile` フラグを `run` コマンドに配線

**Files:**
- Modify: `dimos/robot/cli/dimos.py:194-273` (`run` コマンドのシグネチャと前処理)

- [ ] **Step 1: テスト追加（CLI 統合テスト）**

`tests/robot/cli/test_profile_resolution.py` に追記：

```python
import os
from typer.testing import CliRunner


def test_profile_and_config_are_mutually_exclusive(tmp_path, monkeypatch):
    """`--profile` and `-c` together should error."""
    from dimos.robot.cli.dimos import main

    pdir = tmp_path / "configs" / "profiles" / "p1"
    pdir.mkdir(parents=True)
    (pdir / "config.json").write_text("{}")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["run", "go2-base", "--profile", "p1", "-c", str(pdir / "config.json")],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower() or "exclusive" in result.output.lower()


def test_profile_loads_env_with_override(tmp_path, monkeypatch):
    """Profile `.env` overrides shell env (verified via process env after load)."""
    from dimos.robot.cli.dimos import _apply_profile

    pdir = tmp_path / "configs" / "profiles" / "p2"
    pdir.mkdir(parents=True)
    (pdir / ".env").write_text("DIMOS_TEST_KEY=from_profile\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DIMOS_TEST_KEY", "from_shell")

    config_path = _apply_profile("p2")
    assert os.environ["DIMOS_TEST_KEY"] == "from_profile"
    assert config_path is None  # no config.json in this profile
```

- [ ] **Step 2: テスト実行して失敗を確認**

Run: `python -m pytest tests/robot/cli/test_profile_resolution.py::test_profile_and_config_are_mutually_exclusive tests/robot/cli/test_profile_resolution.py::test_profile_loads_env_with_override -v`
Expected: FAIL (`_apply_profile` 未定義 / CLI に `--profile` 未追加)

- [ ] **Step 3: `_apply_profile` ヘルパ追加**

`dimos/robot/cli/dimos.py` の `_resolve_profile` の直後に追加：

```python
def _apply_profile(name: str) -> Path | None:
    """Apply a profile: load its .env with override, return its config.json path.

    The .env (if present) is loaded into process env with override=True so
    the profile wins over any pre-existing shell variables. Returns the
    config.json Path if the profile has one, else None.
    """
    env_path, config_path = _resolve_profile(name)
    if env_path is not None:
        load_dotenv(env_path, override=True)
    return config_path
```

- [ ] **Step 4: `run` コマンドに `--profile` を追加**

`dimos/robot/cli/dimos.py:194-204` の `run` シグネチャを書き換え：

```python
@main.command()
def run(
    ctx: typer.Context,
    robot_types: list[str] = typer.Argument(..., help="Blueprints or modules to run"),
    daemon: bool = typer.Option(False, "--daemon", "-d", help="Run in background"),
    disable: list[str] = typer.Option([], "--disable", help="Module names to disable"),
    blueprint_args: list[str] = typer.Option((), "--option", "-o"),
    config_path: Path = typer.Option(
        CONFIG_DIR / "dimos", "--config", "-c", help="Path to config file"
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Named profile under configs/profiles/NAME/ (loads .env + config.json). Mutually exclusive with -c.",
    ),
    show_help: bool = typer.Option(False, "--help"),
) -> None:
    """Start a robot blueprint"""
    logger.info("Starting DimOS")

    if profile is not None:
        src = ctx.get_parameter_source("config_path")
        if src == click.core.ParameterSource.COMMANDLINE:
            raise typer.BadParameter(
                "`--profile` and `-c/--config` are mutually exclusive."
            )
        profile_config = _apply_profile(profile)
        if profile_config is not None:
            config_path = profile_config
```

The rest of the `run` body remains unchanged. Insert the `if profile is not None:` block **after** `logger.info("Starting DimOS")` (currently line 207) but **before** the local imports that follow.

- [ ] **Step 5: テスト実行して通ることを確認**

Run: `python -m pytest tests/robot/cli/test_profile_resolution.py -v`
Expected: all tests pass (11 total)

- [ ] **Step 6: 手動 smoke check (CLI が起動する)**

```bash
python -m dimos.robot.cli.dimos run --help 2>&1 | grep -i profile
```
Expected: `--profile` の help 行が出る

- [ ] **Step 7: Commit**

```bash
git add dimos/robot/cli/dimos.py tests/robot/cli/test_profile_resolution.py
git commit -m "feat(cli): add --profile flag to 'dimos run'

Profile resolves to configs/profiles/NAME/{.env,config.json}. .env is
loaded with override=True so profile wins over shell env. Mutually
exclusive with -c."
```

---

### Task 1.3: 既存 config を profile dir に migrate

**Files:**
- Create: `configs/profiles/local-qwen-voicevox/config.json` (移動)
- Create: `configs/profiles/local-qwen-voicevox/.env.example`
- Delete: `configs/local_qwen_voicevox.json`

- [ ] **Step 1: profile dir を作成し config を移動**

```bash
mkdir -p configs/profiles/local-qwen-voicevox
git mv configs/local_qwen_voicevox.json configs/profiles/local-qwen-voicevox/config.json
```

- [ ] **Step 2: `.env.example` を作成**

`configs/profiles/local-qwen-voicevox/.env.example`:

```bash
# Endpoint / secret for local LLM via OpenAI-compatible server (LM Studio, vLLM, etc.)
# Copy this file to .env and fill in the values.
DIMOS_LLM_BASE_URL=http://192.168.11.16:1234/v1
DIMOS_LLM_API_KEY=dummy
```

- [ ] **Step 3: `.gitignore` 確認**

Run: `grep -n "^\.env$\|profiles" .gitignore`
Expected: `.env` line that matches `configs/profiles/*/.env` (recursive). No action needed if matched.

If `.env` does NOT match the profile dir's .env (verify with `git check-ignore -v configs/profiles/local-qwen-voicevox/.env` after creating one for test), add explicit line:

```
configs/profiles/*/.env
```

- [ ] **Step 4: smoke verify**

```bash
python -m dimos.robot.cli.dimos run --help >/dev/null && echo OK
ls configs/profiles/local-qwen-voicevox/
```
Expected: `OK` printed, dir contains `config.json` and `.env.example`.

- [ ] **Step 5: Commit**

```bash
git add configs/profiles/local-qwen-voicevox/ .gitignore
git commit -m "feat(configs): migrate local_qwen_voicevox.json into profile dir

configs/profiles/local-qwen-voicevox/{config.json,.env.example}.
Invoke with: dimos run unitree-go2-agentic-local-tts --profile local-qwen-voicevox"
```

---

## Phase 2: 政策ドキュメント

### Task 2.1: `docs/env-vs-config.md` 新規作成

**Files:**
- Create: `docs/env-vs-config.md`

- [ ] **Step 1: ドキュメント作成**

`docs/env-vs-config.md`:

```markdown
# env vs config 責務分離

dimos の設定値を「環境変数 (env)」と「設定ファイル (config)」のどちらに置くかを決めるためのルール。

## 3 カテゴリ

| カテゴリ | 例 | 置き場所 | 理由 |
|---|---|---|---|
| **A. 振る舞いの選択** | backend impl, model 名, fp16, speaker_id, 速度 | **config field**（env は default seed のみ） | 再現性が要る。bench/CI で YAML/JSON に残る。 |
| **B. 秘匿情報 / デプロイ依存エンドポイント** | API key, private base URL | **env only** | secret/マシン依存値は config file に書けない。 |
| **C. プロセス境界の env** | `OPENAI_API_KEY`, `OPENAI_BASE_URL` | **env only**（外部 SDK が直接読む） | dimos の管理外。 |

## 優先順位

`explicit config field > env (seed) > Field default`

A のフィールドは `Field(default_factory=lambda: os.environ.get(...))` で env を seed として読み取る。explicit な config 値が常に勝つ。

例:

```python
class VoicevoxParamsConfig(ModuleConfig):
    speaker_id: int = Field(
        default_factory=lambda: int(os.environ.get("DIMOS_VOICEVOX_SPEAKER_ID", "74"))
    )
```

- `VoicevoxParamsConfig()` → env に `DIMOS_VOICEVOX_SPEAKER_ID=99` があれば 99、なければ 74
- `VoicevoxParamsConfig(speaker_id=42)` → env に何が入っていても 42

## Anti-pattern

1. **A の値を env だけでしか変えられない**: bench YAML / profile config.json に書けないため、比較実験が記録に残らない。値を見ても何で動いていたか分からない。
2. **B/C を config file に書く**: 誤って secret を commit するリスク。マシン依存値が他マシンで動かない。
3. **シードを 2 箇所で読む**: Config の Field default と Node の `__init__` の両方で env を読むと、優先順位ルールが破綻する。env 読みは Config 層に集約する。

## profile (`dimos run --profile NAME`) との関係

profile は `configs/profiles/NAME/` ディレクトリに `config.json` と `.env` を同梱した「カテゴリ横断のバンドル名」を提供する。

- profile/`config.json` → category A の値が入る
- profile/`.env` → category B/C の値が入る
- profile/`.env.example` → テンプレ。commit する。実体の `.env` は gitignore。

profile は **値の置き場ルールを変えない**。category A を `.env` に書いてもなお動くが、それは anti-pattern (1) なので避ける。

詳細は `docs/superpowers/specs/2026-05-19-profile-and-env-config-policy-design.md` 参照。
```

- [ ] **Step 2: Commit**

```bash
git add docs/env-vs-config.md
git commit -m "docs: add env vs config responsibility separation policy"
```

---

## Phase 3a: VOICEVOX を category A に migrate

### Task 3a.1: `VoicevoxParamsConfig` 追加 + Config テスト (TDD)

**Files:**
- Test: `tests/agents/skills/test_voicevox_params_config.py` (新規)
- Modify: `dimos/agents/skills/speak_skill_ja.py` (config class 追加)

- [ ] **Step 1: テストを書く**

`tests/agents/skills/test_voicevox_params_config.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Verify VoicevoxParamsConfig honors `explicit > env seed > default`."""

from __future__ import annotations

import pytest

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJaConfig,
    VoicevoxParamsConfig,
)


def test_default_speaker_id_is_74():
    cfg = VoicevoxParamsConfig()
    assert cfg.speaker_id == 74


def test_default_factory_reads_env_seed(monkeypatch):
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEAKER_ID", "99")
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEED_SCALE", "1.5")
    cfg = VoicevoxParamsConfig()
    assert cfg.speaker_id == 99
    assert cfg.speed_scale == pytest.approx(1.5)


def test_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEAKER_ID", "99")
    cfg = VoicevoxParamsConfig(speaker_id=42)
    assert cfg.speaker_id == 42


def test_all_params_defaults():
    cfg = VoicevoxParamsConfig()
    assert cfg.speed_scale == 1.0
    assert cfg.pitch_scale == 0.0
    assert cfg.intonation_scale == 1.0
    assert cfg.volume_scale == 1.0


def test_nested_in_assistant_speech_config():
    cfg = AssistantSpeechNodeJaConfig(
        impl="voicevox",
        voicevox={"speaker_id": 5, "speed_scale": 1.3},
    )
    assert cfg.voicevox.speaker_id == 5
    assert cfg.voicevox.speed_scale == pytest.approx(1.3)
    assert cfg.voicevox.pitch_scale == 0.0  # untouched default
```

- [ ] **Step 2: テスト実行して失敗を確認**

Run: `python -m pytest tests/agents/skills/test_voicevox_params_config.py -v`
Expected: ImportError on `VoicevoxParamsConfig`

- [ ] **Step 3: `VoicevoxParamsConfig` を実装**

`dimos/agents/skills/speak_skill_ja.py:74` の `AssistantSpeechNodeJaConfig` の **直前** に挿入：

```python
class VoicevoxParamsConfig(ModuleConfig):
    """VOICEVOX synthesis params (category A; env vars are default seeds only)."""

    speaker_id: int = Field(
        default_factory=lambda: int(os.environ.get("DIMOS_VOICEVOX_SPEAKER_ID", "74"))
    )
    speed_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_SPEED_SCALE", "1.0"))
    )
    pitch_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_PITCH_SCALE", "0.0"))
    )
    intonation_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_INTONATION_SCALE", "1.0"))
    )
    volume_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_VOLUME_SCALE", "1.0"))
    )
```

And nest into `AssistantSpeechNodeJaConfig` (modify the existing class):

```python
class AssistantSpeechNodeJaConfig(ModuleConfig):
    """Config selecting the underlying TTS implementation."""

    impl: TtsImpl = Field(default_factory=_default_tts_impl)
    voicevox: VoicevoxParamsConfig = Field(default_factory=VoicevoxParamsConfig)
    openai_voice: Voice = Voice.ECHO  # used when impl == "openai"
    openai_model: str = "tts-1"  # used when impl == "openai"
    idle_grace_s: float = 1.0  # silence-watchdog tail after last chunk's playback end
```

Update `__all__` at the bottom:

```python
__all__ = [
    "AssistantSpeechNodeJa",
    "AssistantSpeechNodeJaConfig",
    "VoicevoxParamsConfig",
]
```

- [ ] **Step 4: テスト実行して通ることを確認**

Run: `python -m pytest tests/agents/skills/test_voicevox_params_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/skills/speak_skill_ja.py tests/agents/skills/test_voicevox_params_config.py
git commit -m "feat(speak_skill_ja): add VoicevoxParamsConfig (category A)"
```

---

### Task 3a.2: `_make_tts_node` を explicit 引数に書き換え + node 側の env 読み剥がし

**Files:**
- Modify: `dimos/stream/audio/tts/node_voicevox.py:57-93` (env 直読みを削除)
- Modify: `dimos/agents/skills/speak_skill_ja.py:109-111` (`_make_tts_node` の voicevox 分岐)

- [ ] **Step 1: node 側テストを書く (env が無視されることを確認)**

`tests/agents/skills/test_voicevox_params_config.py` に追記：

```python
def test_node_does_not_read_env(monkeypatch):
    """VoicevoxTTSNode no longer reads DIMOS_VOICEVOX_* env at __init__ time.

    Network probe is mocked so we don't need a running engine.
    """
    import dimos.stream.audio.tts.node_voicevox as vv_mod

    class _FakeResp:
        text = "0.0.0-test"
        def raise_for_status(self): pass

    monkeypatch.setattr(vv_mod.requests, "get", lambda *a, **k: _FakeResp())
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEAKER_ID", "99")
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEED_SCALE", "9.9")

    node = vv_mod.VoicevoxTTSNode(speaker_id=42, speed_scale=1.0)
    assert node._speaker_id == 42  # explicit kwarg, env ignored
    assert node._speed_scale == pytest.approx(1.0)
```

- [ ] **Step 2: テスト実行して失敗を確認**

Run: `python -m pytest tests/agents/skills/test_voicevox_params_config.py::test_node_does_not_read_env -v`
Expected: FAIL (`_speaker_id` becomes 99, because node currently reads env)

- [ ] **Step 3: `VoicevoxTTSNode.__init__` から env 読みを剥がす**

`dimos/stream/audio/tts/node_voicevox.py:57-93` の `__init__` を書き換え：

```python
    def __init__(
        self,
        base_url: str | None = None,
        speaker_id: int = _DEFAULT_SPEAKER_ID,
        speed_scale: float = 1.0,
        pitch_scale: float = 0.0,
        intonation_scale: float = 1.0,
        volume_scale: float = 1.0,
        request_timeout: float = 30.0,
    ) -> None:
        self.audio_subject: Subject = Subject()  # type: ignore[type-arg]
        self.text_subject: Subject = Subject()  # type: ignore[type-arg]
        self.subscription = None
        self.processing_thread: threading.Thread | None = None
        self.is_running = True
        self.text_queue: list[str] = []
        self.queue_lock = threading.Lock()

        # base_url stays env-aware (category B: deployment-dependent endpoint).
        self._base = (
            base_url or os.environ.get("DIMOS_VOICEVOX_URL", _DEFAULT_URL)
        ).rstrip("/")
        self._speaker_id = speaker_id
        self._speed_scale = speed_scale
        self._pitch_scale = pitch_scale
        self._intonation_scale = intonation_scale
        self._volume_scale = volume_scale
        self._timeout = request_timeout

        # Probe so we fail fast at start() rather than on first utterance.
        # First request can be slow while the engine warms up its models, so
        # retry a few times before giving up.
        probe_attempts = int(os.environ.get("DIMOS_VOICEVOX_PROBE_ATTEMPTS", "10"))
        probe_timeout = float(os.environ.get("DIMOS_VOICEVOX_PROBE_TIMEOUT", "10"))
        last_err: Exception | None = None
        for i in range(probe_attempts):
            try:
                r = requests.get(f"{self._base}/version", timeout=probe_timeout)
                r.raise_for_status()
                logger.info(
                    "VOICEVOX engine %s @ %s speaker_id=%d",
                    r.text.strip(),
                    self._base,
                    self._speaker_id,
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
                logger.info(
                    "VOICEVOX probe attempt %d/%d failed: %s", i + 1, probe_attempts, e
                )
                time.sleep(2.0)
        if last_err is not None:
            raise RuntimeError(
                f"Cannot reach VOICEVOX engine at {self._base} after "
                f"{probe_attempts} attempts: {last_err}. "
                "Start the engine (e.g. `voicevox_engine` or "
                "`docker run --rm -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu22.04-latest`) "
                "or set DIMOS_VOICEVOX_URL."
            ) from last_err

        # VOICEVOX default output is 24 kHz mono 16-bit. We read the actual
        # rate from each WAV header anyway, but expose 24000 up-front so the
        # downstream audio sink can be opened immediately.
        self.sample_rate = 24000
```

Update the module docstring at line 22-30 to reflect that `DIMOS_VOICEVOX_SPEAKER_ID` etc. are no longer read by this node:

```python
"""Neural Japanese TTS node backed by the VOICEVOX engine HTTP API.

Mirrors ``StyleBertVits2TTSNode``'s interface so call sites only need an
import swap. Talks to a VOICEVOX engine over HTTP (default
``http://127.0.0.1:50021``).

Synthesis params (speaker_id / *_scale) are passed in by the caller.
The Config seed for those values lives in
``AssistantSpeechNodeJaConfig.voicevox`` (see ``speak_skill_ja.py``).

Env vars read directly here (category B: deployment-dependent):

- ``DIMOS_VOICEVOX_URL``              base URL (default ``http://127.0.0.1:50021``)
- ``DIMOS_VOICEVOX_PROBE_ATTEMPTS``   probe retry count (default ``10``)
- ``DIMOS_VOICEVOX_PROBE_TIMEOUT``    per-probe timeout seconds (default ``10``)
"""
```

- [ ] **Step 4: 呼び出し側 `_make_tts_node` を更新**

`dimos/agents/skills/speak_skill_ja.py:109-111` の voicevox 分岐を書き換え：

```python
        if impl == "voicevox":
            from dimos.stream.audio.tts.node_voicevox import VoicevoxTTSNode
            vv = self.config.voicevox
            return VoicevoxTTSNode(
                speaker_id=vv.speaker_id,
                speed_scale=vv.speed_scale,
                pitch_scale=vv.pitch_scale,
                intonation_scale=vv.intonation_scale,
                volume_scale=vv.volume_scale,
            )
```

- [ ] **Step 5: 全テスト実行**

Run: `python -m pytest tests/agents/skills/test_voicevox_params_config.py tests/agents/skills/test_speak_skill_ja_impl_switch.py -v`
Expected: all pass (6 from new file + 7 from existing)

- [ ] **Step 6: Commit**

```bash
git add dimos/stream/audio/tts/node_voicevox.py dimos/agents/skills/speak_skill_ja.py tests/agents/skills/test_voicevox_params_config.py
git commit -m "refactor(voicevox): drop env reads from node; consume Config params

VoicevoxTTSNode.__init__ no longer reads DIMOS_VOICEVOX_SPEAKER_ID /
_*_SCALE. The Config layer (VoicevoxParamsConfig) is the single env
seed point. DIMOS_VOICEVOX_URL and probe vars stay env (category B)."
```

---

## Phase 3b: SBV2 を category A に migrate

### Task 3b.1: `Sbv2ParamsConfig` 追加 + Config テスト (TDD)

**Files:**
- Test: `tests/agents/skills/test_sbv2_params_config.py` (新規)
- Modify: `dimos/agents/skills/speak_skill_ja.py`

- [ ] **Step 1: テストを書く**

`tests/agents/skills/test_sbv2_params_config.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Verify Sbv2ParamsConfig honors `explicit > env seed > default`."""

from __future__ import annotations

import pytest

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJaConfig,
    Sbv2ParamsConfig,
)


def test_defaults():
    cfg = Sbv2ParamsConfig()
    assert cfg.speaker_id == 0
    assert cfg.style == "Neutral"
    assert cfg.style_weight == pytest.approx(1.0)
    assert cfg.sdp_ratio == pytest.approx(0.15)
    assert cfg.noise == pytest.approx(0.4)
    assert cfg.noise_w == pytest.approx(0.6)
    assert cfg.length == pytest.approx(1.1)
    assert cfg.pitch_scale == pytest.approx(1.08)
    assert cfg.intonation_scale == pytest.approx(0.85)


def test_env_seed(monkeypatch):
    monkeypatch.setenv("DIMOS_SBV2_SPEAKER_ID", "3")
    monkeypatch.setenv("DIMOS_SBV2_STYLE", "Angry")
    monkeypatch.setenv("DIMOS_SBV2_LENGTH", "1.5")
    cfg = Sbv2ParamsConfig()
    assert cfg.speaker_id == 3
    assert cfg.style == "Angry"
    assert cfg.length == pytest.approx(1.5)


def test_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("DIMOS_SBV2_SPEAKER_ID", "3")
    cfg = Sbv2ParamsConfig(speaker_id=7)
    assert cfg.speaker_id == 7


def test_nested_in_assistant_speech_config():
    cfg = AssistantSpeechNodeJaConfig(
        impl="sbv2",
        sbv2={"speaker_id": 2, "style": "Happy"},
    )
    assert cfg.sbv2.speaker_id == 2
    assert cfg.sbv2.style == "Happy"
    assert cfg.sbv2.length == pytest.approx(1.1)  # untouched default
```

- [ ] **Step 2: テスト実行して失敗を確認**

Run: `python -m pytest tests/agents/skills/test_sbv2_params_config.py -v`
Expected: ImportError on `Sbv2ParamsConfig`

- [ ] **Step 3: `Sbv2ParamsConfig` を実装**

`dimos/agents/skills/speak_skill_ja.py` の `VoicevoxParamsConfig` の直後に追加：

```python
class Sbv2ParamsConfig(ModuleConfig):
    """Style-Bert-VITS2 synthesis params (category A)."""

    speaker_id: int = Field(
        default_factory=lambda: int(os.environ.get("DIMOS_SBV2_SPEAKER_ID", "0"))
    )
    style: str = Field(
        default_factory=lambda: os.environ.get("DIMOS_SBV2_STYLE", "Neutral")
    )
    style_weight: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_STYLE_WEIGHT", "1.0"))
    )
    sdp_ratio: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_SDP_RATIO", "0.15"))
    )
    noise: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_NOISE", "0.4"))
    )
    noise_w: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_NOISE_W", "0.6"))
    )
    length: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_LENGTH", "1.1"))
    )
    pitch_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_PITCH_SCALE", "1.08"))
    )
    intonation_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_INTONATION_SCALE", "0.85"))
    )
```

Update `AssistantSpeechNodeJaConfig` to nest:

```python
class AssistantSpeechNodeJaConfig(ModuleConfig):
    """Config selecting the underlying TTS implementation."""

    impl: TtsImpl = Field(default_factory=_default_tts_impl)
    voicevox: VoicevoxParamsConfig = Field(default_factory=VoicevoxParamsConfig)
    sbv2: Sbv2ParamsConfig = Field(default_factory=Sbv2ParamsConfig)
    openai_voice: Voice = Voice.ECHO
    openai_model: str = "tts-1"
    idle_grace_s: float = 1.0
```

Update `__all__`:

```python
__all__ = [
    "AssistantSpeechNodeJa",
    "AssistantSpeechNodeJaConfig",
    "Sbv2ParamsConfig",
    "VoicevoxParamsConfig",
]
```

- [ ] **Step 4: テスト実行して通ることを確認**

Run: `python -m pytest tests/agents/skills/test_sbv2_params_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/skills/speak_skill_ja.py tests/agents/skills/test_sbv2_params_config.py
git commit -m "feat(speak_skill_ja): add Sbv2ParamsConfig (category A)"
```

---

### Task 3b.2: SBV2 node から env 読みを剥がす + `_make_tts_node` 更新

**Files:**
- Modify: `dimos/stream/audio/tts/node_style_bert_vits2.py:93-134` (env 直読みを削除)
- Modify: `dimos/agents/skills/speak_skill_ja.py:104-108` (`_make_tts_node` の sbv2 分岐)

- [ ] **Step 1: `StyleBertVits2TTSNode.__init__` を書き換え**

`dimos/stream/audio/tts/node_style_bert_vits2.py:93-134` を書き換え：

```python
    def __init__(
        self,
        device: str | None = None,
        speaker_id: int = 0,
        style: str = "Neutral",
        style_weight: float = 1.0,
        sdp_ratio: float = 0.15,
        noise: float = 0.4,
        noise_w: float = 0.6,
        length: float = 1.1,
        pitch_scale: float = 1.08,
        intonation_scale: float = 0.85,
    ) -> None:
        import torch
        from style_bert_vits2.constants import Languages
        from style_bert_vits2.nlp import bert_models
        from style_bert_vits2.tts_model import TTSModel

        self.audio_subject: Subject = Subject()  # type: ignore[type-arg]
        self.text_subject: Subject = Subject()  # type: ignore[type-arg]
        self.subscription = None
        self.processing_thread: threading.Thread | None = None
        self.is_running = True
        self.text_queue: list[str] = []
        self.queue_lock = threading.Lock()

        self._speaker_id = speaker_id
        self._style = style
        self._style_weight = style_weight
        self._sdp_ratio = sdp_ratio
        self._noise = noise
        self._noise_w = noise_w
        self._length = length
        self._pitch_scale = pitch_scale
        self._intonation_scale = intonation_scale

        bert_name = os.environ.get("DIMOS_SBV2_BERT_MODEL", _DEFAULT_BERT_MODEL)
        logger.info(f"Loading SBV2 BERT model: {bert_name}")
        bert_models.load_model(Languages.JP, bert_name)
        bert_models.load_tokenizer(Languages.JP, bert_name)

        model_path, config_path, style_path = _resolve_model_files()
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Loading SBV2 model {model_path.name} on device={device}")
        self._model = TTSModel(
            model_path=str(model_path),
            config_path=str(config_path),
            style_vec_path=str(style_path),
            device=device,
        )
        # Expose native sample rate so the audio sink can be opened to match.
        self.sample_rate = int(self._model.hyper_parameters.data.sampling_rate)
```

(Note: `DIMOS_SBV2_MODEL_PATH/_CONFIG_PATH/_STYLE_PATH/_BERT_MODEL/_HF_REPO/_MODEL_SUBDIR/_MODEL_FILE` are kept as env-only because they are deployment-dependent paths/identifiers, not synthesis params. Category B.)

- [ ] **Step 2: `_make_tts_node` の sbv2 分岐を更新**

`dimos/agents/skills/speak_skill_ja.py:104-108` を書き換え：

```python
        if impl == "sbv2":
            from dimos.stream.audio.tts.node_style_bert_vits2 import (
                StyleBertVits2TTSNode,
            )
            s = self.config.sbv2
            return StyleBertVits2TTSNode(
                speaker_id=s.speaker_id,
                style=s.style,
                style_weight=s.style_weight,
                sdp_ratio=s.sdp_ratio,
                noise=s.noise,
                noise_w=s.noise_w,
                length=s.length,
                pitch_scale=s.pitch_scale,
                intonation_scale=s.intonation_scale,
            )
```

- [ ] **Step 3: 既存テスト含めて全部実行**

Run: `python -m pytest tests/agents/skills/ -v`
Expected: all existing tests still pass (`test_speak_skill_ja_impl_switch` sbv2 sentinel test must still pass since it monkeypatches the class).

Note: `test_impl_sbv2_routes_to_sbv2_module` uses `monkeypatch.setattr(sbv2_mod, "StyleBertVits2TTSNode", lambda: sentinel)` — the lambda takes no args, but now `_make_tts_node` calls with kwargs. **This breaks the existing test.** Fix it:

Edit `tests/agents/skills/test_speak_skill_ja_impl_switch.py:60-66`:

```python
def test_impl_sbv2_routes_to_sbv2_module(monkeypatch):
    """Dispatch picks the sbv2 module without actually loading the model."""
    sentinel = object()
    import dimos.stream.audio.tts.node_style_bert_vits2 as sbv2_mod

    monkeypatch.setattr(sbv2_mod, "StyleBertVits2TTSNode", lambda **kw: sentinel)
    node = _build_node(impl="sbv2")
    assert node._make_tts_node() is sentinel
```

Same fix for `test_impl_voicevox_routes_to_voicevox_module` (line 69-76):

```python
def test_impl_voicevox_routes_to_voicevox_module(monkeypatch):
    """Dispatch picks the voicevox module without contacting the engine."""
    sentinel = object()
    import dimos.stream.audio.tts.node_voicevox as vv_mod

    monkeypatch.setattr(vv_mod, "VoicevoxTTSNode", lambda **kw: sentinel)
    node = _build_node(impl="voicevox")
    assert node._make_tts_node() is sentinel
```

- [ ] **Step 4: 全テスト再実行**

Run: `python -m pytest tests/agents/skills/ tests/robot/cli/test_profile_resolution.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add dimos/stream/audio/tts/node_style_bert_vits2.py dimos/agents/skills/speak_skill_ja.py tests/agents/skills/test_speak_skill_ja_impl_switch.py
git commit -m "refactor(sbv2): drop env reads from node; consume Config params

StyleBertVits2TTSNode.__init__ no longer reads DIMOS_SBV2_SPEAKER_ID /
_STYLE / _*_SCALE. Sbv2ParamsConfig is the single env seed point.
Model-path and BERT-model env vars stay env (category B)."
```

---

## Final verification

- [ ] **Step 1: 全テストスイート実行**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -30`
Expected: no failures introduced by this work.

- [ ] **Step 2: smoke run (任意、VOICEVOX engine が動いてれば)**

```bash
dimos run unitree-go2-agentic-local-tts --profile local-qwen-voicevox 2>&1 | head -20
```
Expected: blueprint が立ち上がる (engine が無ければ probe 失敗で停止する — 動作確認は engine ありで)。

- [ ] **Step 3: handoff の TODO 状態を更新**

`docs/misc/handoff_2026-05-19_env_vs_config.md` の「未完了タスク」セクションが解消されたことを示すため、ファイル末尾に1行追記：

```markdown

---

**2026-05-19 更新:** 本ハンドオフの未完了タスクは `docs/superpowers/specs/2026-05-19-profile-and-env-config-policy-design.md` の Phase 1〜3 で全て解消した。
```

- [ ] **Step 4: 最終 commit**

```bash
git add docs/misc/handoff_2026-05-19_env_vs_config.md
git commit -m "docs(handoff): mark 2026-05-19 env-vs-config items resolved"
```
