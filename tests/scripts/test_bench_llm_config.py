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


def test_setup_run_dir_writes_self_describing_record(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bench_llm, "set_run_log_dir", lambda *_a, **_k: None)
    cfg_path = tmp_path / "bench.yaml"
    cfg_path.write_text("name: t\nprofile: p\n")
    profile_cfg = tmp_path / "config.json"
    profile_cfg.write_text('{"assistantspeechnodeja": {"impl": "voicevox"}}')
    cfg = {"name": "t", "profile": "p"}
    kwargs = {"g": {"simulation": True}, "assistantspeechnodeja": {"impl": "voicevox"}}

    out_dir = bench_llm.setup_run_dir(cfg, cfg_path, profile_cfg, kwargs)

    assert (out_dir / "bench.yaml").exists()
    assert (out_dir / "profile_config.json").exists()
    import json as _json
    loaded = _json.loads((out_dir / "resolved_config.json").read_text())
    assert loaded == kwargs
    assert out_dir.resolve().is_relative_to(tmp_path / "logs")
