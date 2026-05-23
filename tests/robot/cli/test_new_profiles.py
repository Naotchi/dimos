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
