import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dimos.agents.voice_live.voice_live_node import AzureVoiceLiveNode


def test_node_constructor_stores_config():
    node = AzureVoiceLiveNode(
        endpoint="wss://example.azure.com/voice-live",
        api_key="test-key",
        model="gpt-4o-realtime",
        voice="ja-JP-NanamiNeural",
        instructions="日本語で話して",
        tools=[],
        on_tool_call=lambda call_id, name, args_json: None,
    )
    assert node.endpoint == "wss://example.azure.com/voice-live"
    assert node.api_key == "test-key"
    assert node.model == "gpt-4o-realtime"
    assert node.voice == "ja-JP-NanamiNeural"
    assert node.instructions == "日本語で話して"
    assert node.tools == []


@pytest.mark.asyncio
async def test_start_connects_and_sends_session_update():
    sent_messages: list[str] = []

    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)  # exit recv loop immediately
    mock_ws.close = AsyncMock()

    # async context manager
    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = AsyncMock(return_value=False)

    node = AzureVoiceLiveNode(
        endpoint="wss://example.azure.com/voice-live",
        api_key="test-key",
        model="gpt-4o-realtime",
        voice="ja-JP-NanamiNeural",
        instructions="日本語で話して",
        tools=[{"type": "function", "name": "move", "description": "move", "parameters": {}}],
        on_tool_call=lambda *a: None,
    )

    # Make async iteration of ws raise CancelledError immediately
    async def _aiter(self):
        raise asyncio.CancelledError
        yield  # unreachable; makes this an async generator
    mock_ws.__aiter__ = _aiter

    with patch("dimos.agents.voice_live.voice_live_node.websockets.connect", return_value=mock_ws):
        await node._run_once()  # one connection attempt

    # session.update should have been sent
    session_msgs = [json.loads(m) for m in sent_messages if json.loads(m).get("type") == "session.update"]
    assert len(session_msgs) == 1
    session = session_msgs[0]["session"]
    assert session["model"] == "gpt-4o-realtime"
    assert session["voice"] == "ja-JP-NanamiNeural"
    assert session["instructions"] == "日本語で話して"
    assert session["tools"] == [{"type": "function", "name": "move", "description": "move", "parameters": {}}]
