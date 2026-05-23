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
