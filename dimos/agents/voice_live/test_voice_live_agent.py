import os
from unittest.mock import MagicMock, patch

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


def test_start_wires_mcp_node_and_audio(monkeypatch):
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", "wss://e")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_API_KEY", "k")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_MODEL", "m")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_VOICE", "v")

    mock_mcp = MagicMock()
    mock_mcp.wait_for_ready.return_value = True
    mock_mcp.list_tools.return_value = [
        {"name": "x", "description": "d", "inputSchema": {"type": "object"}},
    ]

    mock_node = MagicMock()
    mock_mic = MagicMock()
    mock_speaker = MagicMock()

    with patch("dimos.agents.voice_live.voice_live_agent.McpAdapter", return_value=mock_mcp), \
         patch("dimos.agents.voice_live.voice_live_agent.AzureVoiceLiveNode", return_value=mock_node), \
         patch("dimos.agents.voice_live.voice_live_agent.SounddeviceAudioSource", return_value=mock_mic), \
         patch("dimos.agents.voice_live.voice_live_agent.SounddeviceAudioOutput", return_value=mock_speaker):
        agent = AzureVoiceLiveAgent()
        try:
            agent.start()
            mock_mcp.wait_for_ready.assert_called_once()
            mock_mcp.list_tools.assert_called_once()
            mock_node.consume_audio.assert_called_once()
            mock_speaker.consume_audio.assert_called_once_with(mock_node.emit_audio.return_value)
            mock_node.start.assert_called_once()
        finally:
            agent.stop()
