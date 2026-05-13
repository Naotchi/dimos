# Copyright 2025-2026 Dimensional Inc.
"""WebInput config tests (whisper language)."""

from __future__ import annotations


def test_default_whisper_language_is_english() -> None:
    from dimos.agents.web_human_input import WebInput

    wi = WebInput()
    try:
        assert wi.config.whisper_language == "en"
    finally:
        wi._close_module()


def test_whisper_language_can_be_overridden() -> None:
    from dimos.agents.web_human_input import WebInput

    wi = WebInput(whisper_language="ja")
    try:
        assert wi.config.whisper_language == "ja"
    finally:
        wi._close_module()
