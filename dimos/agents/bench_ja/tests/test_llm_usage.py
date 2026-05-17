"""Tests for the AIMessage usage-metadata extractor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dimos.agents.bench_ja.llm_usage import extract_usage


@dataclass
class _FakeMsg:
    """Minimal AIMessage stand-in: only the usage_metadata attribute matters."""
    usage_metadata: dict[str, Any] | None


def test_empty_list_returns_zero_with_none_flag():
    out = extract_usage([])
    assert out == {"input_tokens": None, "output_tokens": None, "available": False}


def test_single_aimessage_with_usage():
    msgs = [_FakeMsg(usage_metadata={"input_tokens": 120, "output_tokens": 17, "total_tokens": 137})]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 120, "output_tokens": 17, "available": True}


def test_sums_across_multiple_aimessages():
    msgs = [
        _FakeMsg(usage_metadata={"input_tokens": 100, "output_tokens": 10}),
        _FakeMsg(usage_metadata={"input_tokens": 50,  "output_tokens": 3}),
    ]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 150, "output_tokens": 13, "available": True}


def test_skips_messages_without_usage_metadata():
    class _NoUsage:
        pass
    msgs = [_FakeMsg(usage_metadata={"input_tokens": 7, "output_tokens": 2}), _NoUsage()]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 7, "output_tokens": 2, "available": True}


def test_handles_none_usage_metadata():
    msgs = [_FakeMsg(usage_metadata=None), _FakeMsg(usage_metadata={"input_tokens": 5, "output_tokens": 1})]
    out = extract_usage(msgs)
    assert out == {"input_tokens": 5, "output_tokens": 1, "available": True}
