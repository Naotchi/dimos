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
