"""Forced-speech state machine tests for AzureVoiceLiveAgent.

The Voice Live WS is mocked. We drive `_handle_event` directly with fake
event objects, then assert on `mock_conn.response.create.call_args_list`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from azure.ai.voicelive.models import ServerEventType

from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent


@dataclass
class _FakeEvent:
    type: Any
    delta: str | None = None
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None
    transcript: str | None = None
    session: Any = None
    error: Any = None


def _make_agent() -> AzureVoiceLiveAgent:
    """Construct an agent with WS-side dependencies replaced by mocks."""
    agent = AzureVoiceLiveAgent(
        endpoint="https://fake",
        api_key="fake",
        report_after_tools={"observe", "current_time"},
    )

    # Mock the Voice Live connection.
    conn = MagicMock()
    conn.response.create = AsyncMock()
    conn.response.cancel = AsyncMock()
    conn.conversation.item.create = AsyncMock()
    agent._conn = conn

    # Mock MCP adapter.
    mcp = MagicMock()
    mcp.call_tool = MagicMock(
        return_value={"content": [{"type": "text", "text": "ok"}]}
    )
    agent._mcp = mcp

    # Mock executor + loop wiring for run_in_executor.
    loop = asyncio.get_event_loop()
    agent._loop = loop
    # Replace _tool_pool with an inline executor stub.
    class _InlineExec:
        def submit(self, fn, *a, **kw):
            raise NotImplementedError
    agent._tool_pool = _InlineExec()
    # Override run_in_executor to run synchronously in the same loop.
    async def _run_in_executor(_pool, fn, *args):
        return fn(*args)
    loop.run_in_executor = _run_in_executor  # type: ignore[assignment]

    # Mock publishers we don't care about.
    agent.agent = MagicMock()
    agent.agent_idle = MagicMock()
    return agent


async def _emit(agent: AzureVoiceLiveAgent, *events: _FakeEvent) -> None:
    for ev in events:
        await agent._handle_event(ev)


async def _drain_tasks() -> None:
    """Yield until all currently-scheduled tasks finish."""
    for _ in range(50):
        pending = [t for t in asyncio.all_tasks() if not t.done()]
        pending = [t for t in pending if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.sleep(0)
    raise AssertionError("tasks did not settle")


@pytest.mark.asyncio
async def test_no_preface_when_audio_present_no_tool():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_DELTA, delta=b"\x00\x01"),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA, delta="hi"),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await _drain_tasks()
    assert agent._conn.response.create.call_count == 0


@pytest.mark.asyncio
async def test_audio_present_then_silent_action_tool():
    agent = _make_agent()
    await _emit(
        agent,
        _FakeEvent(type=ServerEventType.RESPONSE_CREATED),
        _FakeEvent(type=ServerEventType.RESPONSE_AUDIO_DELTA, delta=b"\x01"),
        _FakeEvent(
            type=ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE,
            call_id="c1",
            name="relative_move",
            arguments='{"forward": 1.0}',
        ),
        _FakeEvent(type=ServerEventType.RESPONSE_DONE),
    )
    await _drain_tasks()
    # function_call_output sent but NO response.create (silent action).
    assert agent._conn.conversation.item.create.await_count == 1
    assert agent._conn.response.create.call_count == 0
    agent._mcp.call_tool.assert_called_once_with("relative_move", {"forward": 1.0})
