# tests/stream/audio/tts/test_sentence_stream.py
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the streaming sentence segmenter."""

from __future__ import annotations

from dimos.stream.audio.tts.sentence_stream import SentenceAccumulator


def test_no_boundary_buffers_and_emits_nothing():
    acc = SentenceAccumulator()
    assert acc.push("こんにちは") == []
    # buffered text is returned only on flush
    assert acc.flush() == "こんにちは"


def test_single_sentence_emitted_on_terminator():
    acc = SentenceAccumulator()
    assert acc.push("こんにちは。") == ["こんにちは。"]
    assert acc.flush() is None


def test_terminator_split_across_pushes():
    acc = SentenceAccumulator()
    assert acc.push("こんに") == []
    assert acc.push("ちは。残りは") == ["こんにちは。"]
    assert acc.flush() == "残りは"


def test_multiple_sentences_in_one_push():
    acc = SentenceAccumulator()
    assert acc.push("はい。いいえ？本当！") == ["はい。", "いいえ？", "本当！"]


def test_ascii_and_newline_boundaries():
    acc = SentenceAccumulator()
    assert acc.push("OK?\nNext line\n") == ["OK?", "Next line"]


def test_consecutive_terminators_kept_together():
    acc = SentenceAccumulator()
    assert acc.push("本当？！ さて") == ["本当？！"]
    assert acc.flush() == "さて"


def test_empty_push_returns_nothing():
    acc = SentenceAccumulator()
    assert acc.push("") == []
    assert acc.flush() is None
