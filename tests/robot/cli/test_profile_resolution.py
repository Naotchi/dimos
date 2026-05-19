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
