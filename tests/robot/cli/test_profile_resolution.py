# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for `_resolve_profile` / `_apply_profile` helpers used by `dimos run --profile NAME`.

Each profile is now a single JSON file at ``configs/profiles/<name>.json``; there
is no per-profile ``.env``.  Endpoint credentials live in the root ``.env`` as
``DIMOS_LLM_{LOCAL,CLOUD}_{BASE_URL,API_KEY}``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dimos.robot.cli.dimos import _resolve_profile


def test_resolve_existing_profile(tmp_path, monkeypatch):
    profiles_root = tmp_path / "configs" / "profiles"
    profiles_root.mkdir(parents=True)
    profile_file = profiles_root / "local-qwen-voicevox.json"
    profile_file.write_text("{}")

    monkeypatch.chdir(tmp_path)
    result = _resolve_profile("local-qwen-voicevox")
    assert result == (tmp_path / "configs" / "profiles" / "local-qwen-voicevox.json").resolve()


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


def test_apply_profile_selects_endpoint(tmp_path, monkeypatch):
    """apply_profile copies the cloud endpoint vars into the generic DIMOS_LLM_* vars."""
    from dimos.robot.cli.dimos import _apply_profile

    profiles_root = tmp_path / "configs" / "profiles"
    profiles_root.mkdir(parents=True)
    profile_file = profiles_root / "p2.json"
    profile_file.write_text('{"timedmcpclient": {"endpoint": "cloud"}}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DIMOS_LLM_CLOUD_BASE_URL", "http://cloud/v1")
    monkeypatch.setenv("DIMOS_LLM_CLOUD_API_KEY", "ck")

    returned_path = _apply_profile("p2")
    assert returned_path == (tmp_path / "configs" / "profiles" / "p2.json").resolve()
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://cloud/v1"
    assert os.environ["DIMOS_LLM_API_KEY"] == "ck"
