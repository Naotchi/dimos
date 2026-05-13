import os
from unittest.mock import patch

import pytest

from dimos.agents.voice_live.voice_live_agent import AzureVoiceLiveAgent, AzureVoiceLiveConfig


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", "wss://e")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_API_KEY", "k")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_MODEL", "m")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_VOICE", "ja-JP-NanamiNeural")

    cfg = AzureVoiceLiveConfig()
    assert cfg.endpoint == "wss://e"
    assert cfg.api_key == "k"
    assert cfg.model == "m"
    assert cfg.voice == "ja-JP-NanamiNeural"
    assert cfg.mcp_server_url == "http://localhost:9990/mcp"


def test_missing_required_env_raises(monkeypatch):
    monkeypatch.delenv("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", raising=False)
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_API_KEY", "k")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_MODEL", "m")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_VOICE", "v")

    agent = AzureVoiceLiveAgent()
    try:
        with pytest.raises(ValueError, match="DIMOS_AZURE_VOICE_LIVE_ENDPOINT"):
            agent.start()
    finally:
        agent.stop()


def test_convert_mcp_tools_to_voice_live_format():
    mcp_tools = [
        {
            "name": "relative_move",
            "description": "Move robot by relative delta",
            "inputSchema": {"type": "object", "properties": {"x": {"type": "number"}}},
        },
        {
            "name": "speak",  # description 欠落のケース
            "inputSchema": {"type": "object"},
        },
    ]
    result = AzureVoiceLiveAgent._convert_tools(mcp_tools)
    assert result == [
        {
            "type": "function",
            "name": "relative_move",
            "description": "Move robot by relative delta",
            "parameters": {"type": "object", "properties": {"x": {"type": "number"}}},
        },
        {
            "type": "function",
            "name": "speak",
            "description": "",
            "parameters": {"type": "object"},
        },
    ]
