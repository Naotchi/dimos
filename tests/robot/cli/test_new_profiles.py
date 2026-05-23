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

    monkeypatch.delenv("DIMOS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DIMOS_LLM_API_KEY", raising=False)

    monkeypatch.setenv("DIMOS_LLM_LOCAL_BASE_URL", "http://L/v1")
    monkeypatch.setenv("DIMOS_LLM_CLOUD_BASE_URL", "http://C/v1")

    profile_ja.apply_profile("qwen-text")
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://L/v1"

    profile_ja.apply_profile("gpt4o")
    assert os.environ["DIMOS_LLM_BASE_URL"] == "http://C/v1"
