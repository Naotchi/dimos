import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import reactivex as rx

from dimos.agents.voice_live.voice_live_node import AzureVoiceLiveNode
from dimos.stream.audio.base import AudioEvent


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


@pytest.mark.asyncio
async def test_audio_in_chunks_forwarded_as_input_audio_buffer_append():
    sent_messages: list[str] = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
    )
    node._ws = mock_ws
    node._loop = asyncio.get_event_loop()

    audio = AudioEvent(
        data=np.array([0, 100, -100], dtype=np.int16),
        sample_rate=24000,
        timestamp=0.0,
    )
    # consume_audio should subscribe to source and forward chunks
    subject: rx.subject.Subject[AudioEvent] = rx.subject.Subject()
    node.consume_audio(subject)
    node._activate_audio_input()  # bridge subject to WS sender

    subject.on_next(audio)
    await asyncio.sleep(0.05)  # let async task run

    appends = [json.loads(m) for m in sent_messages if json.loads(m).get("type") == "input_audio_buffer.append"]
    assert len(appends) == 1
    decoded = base64.b64decode(appends[0]["audio"])
    assert len(decoded) == 6  # 3 int16 samples = 6 bytes


@pytest.mark.asyncio
async def test_response_audio_delta_emits_audio_event():
    received: list[AudioEvent] = []
    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
    )
    node.emit_audio().subscribe(on_next=received.append)

    pcm = np.array([0, 256, -256], dtype=np.int16).tobytes()
    raw = json.dumps({
        "type": "response.audio.delta",
        "delta": base64.b64encode(pcm).decode("ascii"),
    })
    await node._handle_message(raw)

    assert len(received) == 1
    assert received[0].sample_rate == 24000
    assert received[0].data.dtype == np.int16
    assert received[0].data.tolist() == [0, 256, -256]


@pytest.mark.asyncio
async def test_function_call_arguments_done_invokes_handler():
    calls: list[tuple[str, str, str]] = []

    def handler(call_id: str, name: str, args_json: str) -> None:
        calls.append((call_id, name, args_json))

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=handler,
    )

    raw = json.dumps({
        "type": "response.function_call_arguments.done",
        "call_id": "call_123",
        "name": "relative_move",
        "arguments": '{"x": 1.0, "y": 0}',
    })
    await node._handle_message(raw)

    assert calls == [("call_123", "relative_move", '{"x": 1.0, "y": 0}')]


@pytest.mark.asyncio
async def test_send_function_output_emits_correct_messages():
    sent: list[str] = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda m: sent.append(m))

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
    )
    node._ws = mock_ws
    node._loop = asyncio.get_event_loop()

    node.send_function_output("call_42", "Moved 1m forward")
    await asyncio.sleep(0.05)

    types = [json.loads(s)["type"] for s in sent]
    assert types == ["conversation.item.create", "response.create"]
    item = json.loads(sent[0])["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_42"
    assert item["output"] == "Moved 1m forward"


@pytest.mark.asyncio
async def test_start_retries_on_connect_failure_then_gives_up():
    attempts: list[int] = []

    async def fake_connect(*args, **kwargs):
        attempts.append(len(attempts) + 1)
        raise ConnectionError("nope")

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
        max_retries=3, backoff_base=0.0,
    )
    with patch("dimos.agents.voice_live.voice_live_node.websockets.connect", side_effect=fake_connect):
        with pytest.raises(ConnectionError):
            await node._run()

    assert len(attempts) == 3
