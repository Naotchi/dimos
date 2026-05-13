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


import time


def test_handle_tool_call_forwards_to_mcp_and_returns_result():
    agent = AzureVoiceLiveAgent()
    try:
        agent._mcp = MagicMock()
        agent._mcp.call_tool_text.return_value = "moved successfully"
        agent._node = MagicMock()
        from concurrent.futures import ThreadPoolExecutor
        agent._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="VoiceLiveToolTest")

        agent._handle_tool_call("call_1", "relative_move", '{"x": 1.0}')

        for _ in range(50):
            if agent._node.send_function_output.called:
                break
            time.sleep(0.02)

        agent._mcp.call_tool_text.assert_called_once_with("relative_move", {"x": 1.0})
        agent._node.send_function_output.assert_called_once_with("call_1", "moved successfully")
    finally:
        agent.stop()


def test_handle_tool_call_returns_error_text_on_exception():
    agent = AzureVoiceLiveAgent()
    try:
        agent._mcp = MagicMock()
        agent._mcp.call_tool_text.side_effect = RuntimeError("boom")
        agent._node = MagicMock()
        from concurrent.futures import ThreadPoolExecutor
        agent._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="VoiceLiveToolTest")

        agent._handle_tool_call("call_2", "broken_tool", "{}")

        for _ in range(50):
            if agent._node.send_function_output.called:
                break
            time.sleep(0.02)

        args, _ = agent._node.send_function_output.call_args
        assert args[0] == "call_2"
        assert "boom" in args[1]
    finally:
        agent.stop()
