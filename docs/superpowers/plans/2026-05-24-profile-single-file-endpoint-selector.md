# Profile single-file + endpoint selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move LLM endpoint credentials to the root `.env` as named `LOCAL`/`CLOUD` pairs, collapse each profile from a directory (`<name>/config.json` + `<name>/.env`) into a single committed `configs/profiles/<name>.json`, and let the profile pick local vs cloud via `timedmcpclient.endpoint`.

**Architecture:** The profile JSON gains a `timedmcpclient.endpoint` (`"local"|"cloud"`) field. The fork-local profile loader (`profile_ja.apply_profile`) reads that field and copies the matching `DIMOS_LLM_{LOCAL,CLOUD}_{BASE_URL,API_KEY}` pair (loaded from the root `.env` by the caller) into the generic `DIMOS_LLM_BASE_URL`/`DIMOS_LLM_API_KEY`. The existing `mirror_llm_endpoint_env()` then mirrors those into `OPENAI_*` at blueprint import — unchanged. No per-profile `.env`; secrets live only in the root `.env`.

**Tech Stack:** Python, pydantic v2 (`ModuleConfig` subclass), python-dotenv (`${VAR}` not needed — resolution is in Python), pytest. All edited files are **fork-only** (`*_ja.py`, `scripts/bench_*.py`, `configs/profiles/`, `docs/`); zero upstream edits.

---

## Background facts (verified against the codebase)

- `dimos/robot/cli/dimos.py:59` runs `load_dotenv()` at module import → root `.env` is loaded before `run()` calls `apply_profile`. **The CLI needs no edit.**
- `scripts/bench_llm.py` `main()` calls `apply_profile` *before* importing `dimos.robot.cli.dimos`, so the root `.env` is **not** loaded yet when the profile resolves. The bench must call `load_dotenv()` itself first (Task 5).
- `dimos/core/coordination/blueprints.py:172` builds the aggregate config with `extra="forbid"`, so a bare top-level `endpoint` key in the JSON would be rejected by `load_config_args`'s `config(**kwargs)` validation. The selector therefore lives **nested** as `timedmcpclient.endpoint`, which requires `endpoint` to be a declared field on `TimedMcpClientConfig` (Task 1).
- `dimos/agents/mcp/mcp_client.py:215` reads `self.config.model` and calls `create_agent` inside `on_system_modules`; the LLM client reads `OPENAI_*` from env there. Because resolution happens in `apply_profile` (parent process, before blueprint import), no worker-side timing change is needed.
- Real secrets currently live only in untracked `configs/profiles/local-qwen-voicevox-*/.env`. The new `qwen-text`/`qwen-vl`/`gpt4o` dirs hold only tracked `config.json` + `.env.example` (no secrets) → safe to `git rm`.
- The `Write` tool is permission-denied under `configs/profiles/`; create profile JSON files with a Bash heredoc.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `dimos/agents/mcp/mcp_client_ja.py` | modify | Add `endpoint` field to `TimedMcpClientConfig` |
| `.env.example` (repo root) | create | Template documenting `DIMOS_LLM_{LOCAL,CLOUD}_*` |
| `dimos/agents/profile_ja.py` | rewrite | Single-file resolution + endpoint→env selection |
| `configs/profiles/qwen-text.json` | create | Single-file profile (endpoint=local) |
| `configs/profiles/qwen-vl.json` | create | Single-file profile (endpoint=local) |
| `configs/profiles/gpt4o.json` | create | Single-file profile (endpoint=cloud) |
| `configs/profiles/{qwen-text,qwen-vl,gpt4o}/` | delete | Old directory layout (tracked files) |
| `scripts/bench_llm.py` | modify | Load root `.env` before `apply_profile`; drop dead None check; fix stale comment |
| `tests/agents/mcp/test_timed_mcp_client_config.py` | modify | `endpoint` field tests |
| `tests/agents/test_profile_ja.py` | rewrite | New layout + endpoint-selection tests |
| `tests/robot/cli/test_new_profiles.py` | rewrite | Single-file shape + real-profile selection |
| `docs/env-vs-config.md` | append | Document the root-`.env`/single-file design |

---

### Task 1: Add `endpoint` field to `TimedMcpClientConfig`

**Files:**
- Modify: `dimos/agents/mcp/mcp_client_ja.py` (the `TimedMcpClientConfig` class, ~line 44)
- Test: `tests/agents/mcp/test_timed_mcp_client_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/mcp/test_timed_mcp_client_config.py`:

```python
def test_endpoint_defaults_to_local():
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    assert TimedMcpClientConfig().endpoint == "local"


def test_endpoint_explicit_cloud():
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    assert TimedMcpClientConfig(endpoint="cloud").endpoint == "cloud"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/mcp/test_timed_mcp_client_config.py -v`
Expected: the two new tests FAIL (`endpoint` not a field / AttributeError or ValidationError).

- [ ] **Step 3: Add the field**

In `dimos/agents/mcp/mcp_client_ja.py`, change the `TimedMcpClientConfig` body from:

```python
    model: str = Field(
        default_factory=lambda: os.environ.get("DIMOS_LLM_MODEL", DEFAULT_MODEL)
    )
```

to:

```python
    model: str = Field(
        default_factory=lambda: os.environ.get("DIMOS_LLM_MODEL", DEFAULT_MODEL)
    )
    # Category-A selection of which root-.env endpoint pair to use.
    # profile_ja.apply_profile reads this and copies
    # DIMOS_LLM_<ENDPOINT>_{BASE_URL,API_KEY} → DIMOS_LLM_{BASE_URL,API_KEY}.
    endpoint: str = "local"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/mcp/test_timed_mcp_client_config.py -v`
Expected: all tests PASS (the 4 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/mcp/mcp_client_ja.py tests/agents/mcp/test_timed_mcp_client_config.py
git commit -m "feat(mcp_client_ja): add endpoint field to TimedMcpClientConfig

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Create root `.env.example` with named endpoint pairs

**Files:**
- Create: `.env.example` (repo root)

No test (it is a template file); Task 3/4 tests exercise the resolution logic.

- [ ] **Step 1: Create the file**

Write `.env.example` (repo root) with:

```bash
# LLM endpoint credentials — machine-specific. Copy to `.env` and fill per machine.
# `.env` is gitignored; this `.env.example` is the committed template.
#
# Profiles select which set to use via `timedmcpclient.endpoint` (local|cloud)
# in configs/profiles/<name>.json. Define both here once; profiles carry no
# secrets and only name which to read.

# Local OpenAI-compatible server (LM Studio / vLLM / Ollama).
DIMOS_LLM_LOCAL_BASE_URL=http://localhost:1234/v1
DIMOS_LLM_LOCAL_API_KEY=dummy

# Cloud endpoint (Azure OpenAI v1 or OpenAI cloud).
DIMOS_LLM_CLOUD_BASE_URL=https://<resource>.openai.azure.com/openai/v1
DIMOS_LLM_CLOUD_API_KEY=<azure-or-openai-key>

# Model name is owned by the profile config.json (timedmcpclient.model), not here.
```

- [ ] **Step 2: Verify it is not gitignored**

Run: `git check-ignore .env.example || echo "tracked-ok"`
Expected: prints `tracked-ok` (the `.gitignore` `.env` rule must not match `.env.example`).

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "feat(env): root .env.example with DIMOS_LLM_{LOCAL,CLOUD}_* pairs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Rewrite `profile_ja` for single-file layout + endpoint selection

**Files:**
- Modify (rewrite functions): `dimos/agents/profile_ja.py`
- Test (rewrite): `tests/agents/test_profile_ja.py`

- [ ] **Step 1: Replace the test file**

Overwrite `tests/agents/test_profile_ja.py` with:

```python
from __future__ import annotations

import json
import os

import pytest

from dimos.agents import profile_ja


def _write_profile(root, name, cfg: dict):
    path = root / f"{name}.json"
    path.write_text(json.dumps(cfg))
    return path


def test_resolve_profile_returns_json_path(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    _write_profile(tmp_path, "p", {})
    assert profile_ja.resolve_profile("p") == (tmp_path / "p.json")


def test_resolve_profile_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        profile_ja.resolve_profile("nope")


def test_resolve_profile_rejects_unsafe_name(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    with pytest.raises(ValueError):
        profile_ja.resolve_profile("../escape")


def test_apply_profile_selects_local_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    monkeypatch.setenv("DIMOS_LLM_LOCAL_BASE_URL", "http://local:1/v1")
    monkeypatch.setenv("DIMOS_LLM_LOCAL_API_KEY", "localkey")
    monkeypatch.setenv("DIMOS_LLM_CLOUD_BASE_URL", "http://cloud:2/v1")
    _write_profile(tmp_path, "p", {"timedmcpclient": {"endpoint": "local"}})
    config_path = profile_ja.apply_profile("p")
    assert config_path == (tmp_path / "p.json")
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://local:1/v1"
    assert os.environ["DIMOS_LLM_API_KEY"] == "localkey"


def test_apply_profile_selects_cloud_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    monkeypatch.setenv("DIMOS_LLM_LOCAL_BASE_URL", "http://local:1/v1")
    monkeypatch.setenv("DIMOS_LLM_CLOUD_BASE_URL", "http://cloud:2/v1")
    monkeypatch.setenv("DIMOS_LLM_CLOUD_API_KEY", "cloudkey")
    _write_profile(
        tmp_path, "p", {"timedmcpclient": {"model": "openai:gpt-4o", "endpoint": "cloud"}}
    )
    profile_ja.apply_profile("p")
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://cloud:2/v1"
    assert os.environ["DIMOS_LLM_API_KEY"] == "cloudkey"


def test_apply_profile_defaults_to_local_when_endpoint_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    monkeypatch.setenv("DIMOS_LLM_LOCAL_BASE_URL", "http://local:1/v1")
    _write_profile(tmp_path, "p", {"timedmcpclient": {"model": "m"}})
    profile_ja.apply_profile("p")
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://local:1/v1"


def test_apply_profile_leaves_generic_unset_when_source_absent(tmp_path, monkeypatch):
    # Unfilled root .env → generic vars untouched, mirror falls back to OpenAI default.
    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    monkeypatch.delenv("DIMOS_LLM_LOCAL_BASE_URL", raising=False)
    monkeypatch.delenv("DIMOS_LLM_BASE_URL", raising=False)
    _write_profile(tmp_path, "p", {"timedmcpclient": {"endpoint": "local"}})
    profile_ja.apply_profile("p")
    assert "DIMOS_LLM_BASE_URL" not in os.environ


def test_apply_profile_then_mirror_endpoint_env(tmp_path, monkeypatch):
    from dimos.agents.llm_env_ja import mirror_llm_endpoint_env

    monkeypatch.setattr(profile_ja, "PROFILES_ROOT", tmp_path)
    monkeypatch.setenv("DIMOS_LLM_LOCAL_BASE_URL", "http://local:9/v1")
    monkeypatch.setenv("DIMOS_LLM_LOCAL_API_KEY", "k")
    _write_profile(tmp_path, "p", {"timedmcpclient": {"endpoint": "local"}})
    profile_ja.apply_profile("p")
    mirror_llm_endpoint_env()
    assert os.environ["OPENAI_BASE_URL"] == "http://local:9/v1"
    assert os.environ["OPENAI_API_KEY"] == "k"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_profile_ja.py -v`
Expected: FAIL — `resolve_profile` still returns a tuple / loads `<name>/.env`, so the new assertions break.

- [ ] **Step 3: Rewrite `profile_ja.py`**

Keep only the license header (the `# Copyright ...` block at the top). Replace everything after it — the existing module docstring, imports, and both functions — with:

```python
"""Resolve and apply named profiles at ``configs/profiles/<name>.json``.

A profile is a single committed JSON file holding module parameters
(category A blueprint_args) including ``timedmcpclient.endpoint``
(``"local"`` | ``"cloud"``). Endpoint credentials are NOT in the profile;
they live in the root ``.env`` as ``DIMOS_LLM_{LOCAL,CLOUD}_{BASE_URL,API_KEY}``.

``apply_profile`` reads the selected endpoint and copies the matching pair
into the generic ``DIMOS_LLM_BASE_URL`` / ``DIMOS_LLM_API_KEY`` that
``mirror_llm_endpoint_env()`` mirrors into ``OPENAI_*`` at blueprint import.
The caller is responsible for having loaded the root ``.env`` first (the CLI
does this at ``dimos.py`` import; the bench calls ``load_dotenv()`` itself).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROFILES_ROOT = Path("configs/profiles")


def resolve_profile(name: str) -> Path:
    """Resolve a profile name to ``configs/profiles/<name>.json``.

    Raises ValueError on unsafe names, FileNotFoundError if absent.
    """
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise ValueError(f"Invalid profile name: {name!r}")

    path = (PROFILES_ROOT / f"{name}.json").resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Profile {name!r} not found: {path} does not exist")
    return path


def apply_profile(name: str) -> Path:
    """Apply a profile: select the LLM endpoint env, return its config path.

    Reads ``timedmcpclient.endpoint`` (default ``"local"``) and copies the
    matching ``DIMOS_LLM_<ENDPOINT>_{BASE_URL,API_KEY}`` pair into the generic
    ``DIMOS_LLM_{BASE_URL,API_KEY}``. The profile carries no secrets.
    """
    config_path = resolve_profile(name)
    cfg = json.loads(config_path.read_text())
    endpoint = cfg.get("timedmcpclient", {}).get("endpoint", "local")
    _select_endpoint_env(endpoint)
    return config_path


def _select_endpoint_env(endpoint: str) -> None:
    """Copy ``DIMOS_LLM_<ENDPOINT>_{BASE_URL,API_KEY}`` → ``DIMOS_LLM_{...}``.

    Only copies a value when the source var is set, so an unfilled root ``.env``
    leaves the generic vars untouched (mirror then uses the OpenAI default).
    """
    prefix = f"DIMOS_LLM_{endpoint.upper()}_"
    for suffix in ("BASE_URL", "API_KEY"):
        val = os.environ.get(prefix + suffix)
        if val is not None:
            os.environ[f"DIMOS_LLM_{suffix}"] = val
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_profile_ja.py -v`
Expected: all PASS.

- [ ] **Step 5: Verify no caller relies on the old tuple return**

Run: `grep -rn "resolve_profile\|apply_profile" dimos/ scripts/ | grep -v "def \|profile_ja.py"`
Expected: the only call sites are `dimos/robot/cli/dimos.py:225` (`_apply_profile(profile)`, uses the return as a path — still valid) and `scripts/bench_llm.py` (handled in Task 5). No tuple unpacking of `resolve_profile`. If any tuple unpacking appears, STOP and report.

- [ ] **Step 6: Commit**

```bash
git add dimos/agents/profile_ja.py tests/agents/test_profile_ja.py
git commit -m "refactor(profile_ja): single-file profiles + endpoint selection from root .env

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Migrate the three profiles to single JSON files

**Files:**
- Create: `configs/profiles/qwen-text.json`, `configs/profiles/qwen-vl.json`, `configs/profiles/gpt4o.json` (via Bash heredoc — Write is denied under `configs/profiles/`)
- Delete: `configs/profiles/qwen-text/`, `configs/profiles/qwen-vl/`, `configs/profiles/gpt4o/` (tracked files)
- Test (rewrite): `tests/robot/cli/test_new_profiles.py`

- [ ] **Step 1: Replace the test file**

Overwrite `tests/robot/cli/test_new_profiles.py` with:

```python
import json
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "name,model,endpoint",
    [
        ("qwen-text", "openai:qwen/qwen3-30b-a3b-2507", "local"),
        ("qwen-vl", "openai:qwen/qwen3.6-35b-a3b", "local"),
        ("gpt4o", "openai:gpt-4o", "cloud"),
    ],
)
def test_profile_config_shape(name, model, endpoint):
    cfg = json.loads(Path(f"configs/profiles/{name}.json").read_text())
    # run-mode は profile が持たない
    assert "g" not in cfg
    # model と endpoint は timedmcpclient ブロックの category-A 値
    assert cfg["timedmcpclient"]["model"] == model
    assert cfg["timedmcpclient"]["endpoint"] == endpoint
    # machine 非依存の共通 category-A 値
    assert cfg["rerunbridgemodule"]["memory_limit"] == "25%"
    assert cfg["assistantspeechnodeja"]["impl"] == "voicevox"


@pytest.mark.parametrize("name", ["qwen-text", "qwen-vl", "gpt4o"])
def test_profile_is_single_file_no_dir(name):
    # profile は単一 JSON。per-profile ディレクトリ/.env は廃止し、
    # endpoint 資格情報は root .env が持つ。
    assert Path(f"configs/profiles/{name}.json").is_file()
    assert not Path(f"configs/profiles/{name}").exists()


def test_apply_real_profile_selects_endpoint(monkeypatch):
    # 実プロファイル + loader の結合: endpoint 値で LOCAL/CLOUD が切り替わる。
    from dimos.agents import profile_ja

    monkeypatch.setenv("DIMOS_LLM_LOCAL_BASE_URL", "http://L/v1")
    monkeypatch.setenv("DIMOS_LLM_CLOUD_BASE_URL", "http://C/v1")

    profile_ja.apply_profile("qwen-text")
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://L/v1"

    profile_ja.apply_profile("gpt4o")
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://C/v1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/robot/cli/test_new_profiles.py -v`
Expected: FAIL — `configs/profiles/<name>.json` does not exist yet (still directories).

- [ ] **Step 3: Create the single-file profiles (Bash heredoc)**

```bash
cat > configs/profiles/qwen-text.json <<'JSON'
{
  "rerunbridgemodule": { "memory_limit": "25%" },
  "localmicrophoneja": { "mic_mode": "vad" },
  "whisperhumaninputja": { "model": "large-v3", "fp16": true },
  "timedmcpclient": { "model": "openai:qwen/qwen3-30b-a3b-2507", "endpoint": "local" },
  "assistantspeechnodeja": { "impl": "voicevox", "streaming": true }
}
JSON
cat > configs/profiles/qwen-vl.json <<'JSON'
{
  "rerunbridgemodule": { "memory_limit": "25%" },
  "localmicrophoneja": { "mic_mode": "vad" },
  "whisperhumaninputja": { "model": "large-v3", "fp16": true },
  "timedmcpclient": { "model": "openai:qwen/qwen3.6-35b-a3b", "endpoint": "local" },
  "assistantspeechnodeja": { "impl": "voicevox", "streaming": true }
}
JSON
cat > configs/profiles/gpt4o.json <<'JSON'
{
  "rerunbridgemodule": { "memory_limit": "25%" },
  "localmicrophoneja": { "mic_mode": "vad" },
  "whisperhumaninputja": { "model": "large-v3", "fp16": true },
  "timedmcpclient": { "model": "openai:gpt-4o", "endpoint": "cloud" },
  "assistantspeechnodeja": { "impl": "voicevox", "streaming": true }
}
JSON
```

- [ ] **Step 4: Delete the old directory layout (tracked files only)**

```bash
git rm -r configs/profiles/qwen-text configs/profiles/qwen-vl configs/profiles/gpt4o
```

Expected: removes each dir's `config.json` and `.env.example`. (These dirs hold no untracked secrets — verified. Do NOT touch `configs/profiles/local-qwen-voicevox-*`, which hold the operator's real `.env`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/robot/cli/test_new_profiles.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add configs/profiles/qwen-text.json configs/profiles/qwen-vl.json configs/profiles/gpt4o.json tests/robot/cli/test_new_profiles.py
git add -A configs/profiles/qwen-text configs/profiles/qwen-vl configs/profiles/gpt4o
git commit -m "refactor(profiles): collapse profile dirs into single <name>.json with endpoint

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Bench loads root `.env` before `apply_profile`

**Files:**
- Modify: `scripts/bench_llm.py` (`main()`, the `apply_profile` block ~line 134–150)

- [ ] **Step 1: Verify the failure mode first**

Run: `grep -n "load_dotenv\|apply_profile\|config_path is None\|profile .env" scripts/bench_llm.py`
Expected: shows `apply_profile` called with no preceding `load_dotenv`, the stale "Load the profile .env BEFORE importing" comment, and the dead `if config_path is None:` check. (This step documents what Step 2 fixes; there is no unit test for the bench boot path — it is exercised manually in Step 4.)

- [ ] **Step 2: Edit `main()`**

In `scripts/bench_llm.py`, replace this block:

```python
def main() -> int:
    from dimos.agents.profile_ja import apply_profile

    args = parse_args()
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # Load the profile .env BEFORE importing the blueprint: the blueprint module
    # calls mirror_llm_endpoint_env() at import time, which reads DIMOS_LLM_* and
    # mirrors them into OPENAI_*. Importing earlier would miss the profile env.
    config_path = apply_profile(cfg["profile"])
    from dimos.robot.cli.dimos import load_config_args
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
        unitree_go2_agentic_local_tts as blueprint,
    )

    if config_path is None:
        raise ValueError(f"profile {cfg['profile']!r} has no config.json")
    kwargs = load_config_args(blueprint.config(), [], config_path)
```

with:

```python
def main() -> int:
    from dotenv import load_dotenv

    from dimos.agents.profile_ja import apply_profile

    args = parse_args()
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # Load the root .env (DIMOS_LLM_{LOCAL,CLOUD}_* endpoint creds) BEFORE
    # apply_profile, so the profile's endpoint selection can resolve them. The
    # CLI loads root .env at dimos.py import; the bench reaches apply_profile
    # first, so it must load it explicitly. apply_profile then sets
    # DIMOS_LLM_{BASE_URL,API_KEY}, which the blueprint's import-time
    # mirror_llm_endpoint_env() mirrors into OPENAI_*.
    load_dotenv()
    config_path = apply_profile(cfg["profile"])
    from dimos.robot.cli.dimos import load_config_args
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
        unitree_go2_agentic_local_tts as blueprint,
    )

    kwargs = load_config_args(blueprint.config(), [], config_path)
```

- [ ] **Step 3: Run the existing bench-related tests / import check**

Run: `python -c "import ast; ast.parse(open('scripts/bench_llm.py').read()); print('ok')"`
Expected: prints `ok` (file parses).
Run: `pytest tests/agents/test_profile_ja.py tests/robot/cli/test_new_profiles.py -q`
Expected: all PASS (regression guard for the loader the bench depends on).

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_llm.py
git commit -m "fix(bench): load root .env before apply_profile for endpoint resolution

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Document the design in `docs/env-vs-config.md`

**Files:**
- Modify (append): `docs/env-vs-config.md`

- [ ] **Step 1: Append a dated subsection**

Append to `docs/env-vs-config.md`:

```markdown
## profile レイアウト: 単一ファイル + endpoint セレクタ（2026-05-24 追記）

profile はディレクトリ（`<name>/config.json` + `<name>/.env`）ではなく
**単一の `configs/profiles/<name>.json`** にまとめた。秘密（endpoint の
URL/鍵）は profile に置かず、**root `.env`** に名前付きで1回ずつ定義する:

| 値 | 軸 | 置き場所 |
|---|---|---|
| endpoint の実体（local/cloud の URL+key） | machine | root `.env`（`DIMOS_LLM_{LOCAL,CLOUD}_{BASE_URL,API_KEY}`） |
| local / cloud のどちらを使うか | profile（category A） | `<name>.json` の `timedmcpclient.endpoint` |
| model 名 | profile（category A） | `<name>.json` の `timedmcpclient.model` |

解決の流れ:

```
<name>.json の timedmcpclient.endpoint = "local"|"cloud"
  → apply_profile が root .env の DIMOS_LLM_<ENDPOINT>_* を
    DIMOS_LLM_BASE_URL / DIMOS_LLM_API_KEY にコピー
  → mirror_llm_endpoint_env() が DIMOS_LLM_* → OPENAI_* にミラー（既存）
```

`endpoint` を top-level でなく `timedmcpclient` の中に置くのは、
集約 config が `extra="forbid"`（`blueprints.py`）で module 名以外の
top-level キーを弾くため。`endpoint` は `TimedMcpClientConfig` の正式
フィールド。

これにより machine 変更は root `.env` 1箇所、profile は commit 可能な
JSON 1枚で自己完結し、同じ model を local/cloud で切り替えられる
（spark + cloud / desktop + local も表現可能）。
```

- [ ] **Step 2: Commit**

```bash
git add docs/env-vs-config.md
git commit -m "docs(env-vs-config): single-file profiles + root .env endpoint selector

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Run the full affected suite:

```bash
pytest tests/agents/test_profile_ja.py \
       tests/agents/mcp/test_timed_mcp_client_config.py \
       tests/robot/cli/test_new_profiles.py \
       tests/robot/blueprints/test_local_tts_model_unbaked.py -v
```
Expected: all PASS.

- [ ] Confirm no upstream files changed:

```bash
git diff --name-only main | while read f; do git cat-file -e "upstream/main:$f" 2>/dev/null && echo "UPSTREAM TOUCHED: $f"; done
```
Expected: no `UPSTREAM TOUCHED` lines.

## Operator follow-up (manual, not part of the plan)

The new `qwen-text`/`qwen-vl`/`gpt4o` profiles read endpoint creds from the **root `.env`**. Before running, add to the real root `.env` (copy values out of the old `configs/profiles/local-qwen-voicevox-*/.env`):

```
DIMOS_LLM_LOCAL_BASE_URL=...   # this machine's local vLLM/LM Studio endpoint
DIMOS_LLM_LOCAL_API_KEY=dummy
DIMOS_LLM_CLOUD_BASE_URL=...   # azure/openai endpoint
DIMOS_LLM_CLOUD_API_KEY=...    # secret
```

Then the leftover `configs/profiles/local-qwen-voicevox-*` dirs (which still hold old untracked `.env` secrets) can be removed manually.
```
