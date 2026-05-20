# dimos/stream/audio/tts/sentence_stream.py
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

"""Stateful sentence segmentation for streaming TTS input.

Accumulates LLM token deltas and yields complete sentences as soon as a
sentence-final boundary is seen, so a TTS node can start synthesizing the
first sentence before the full response finishes generating. Pure (no I/O,
no threads) so it is unit-testable in isolation — mirrors the design of
``dimos/agents/bench_ja/stream_tracker.py``.
"""

from __future__ import annotations

import re

# A run of non-terminator chars followed by one-or-more sentence-final
# terminators (JA + ASCII) or newlines. Consecutive terminators stay with
# the sentence they close (e.g. "本当？！").
_SENTENCE_RE = re.compile(r"[^。！？!?\n]*[。！？!?\n]+")


class SentenceAccumulator:
    """Buffer token deltas and emit complete sentences on boundaries."""

    def __init__(self) -> None:
        self._buf = ""

    def push(self, delta: str) -> list[str]:
        """Append ``delta`` and return any sentences now complete."""
        if not delta:
            return []
        self._buf += delta
        sentences: list[str] = []
        last_end = 0
        for m in _SENTENCE_RE.finditer(self._buf):
            sentence = m.group().strip()
            if sentence:
                sentences.append(sentence)
            last_end = m.end()
        self._buf = self._buf[last_end:]
        return sentences

    def flush(self) -> str | None:
        """Return the trailing partial sentence (if any) and clear the buffer."""
        rest = self._buf.strip()
        self._buf = ""
        return rest or None


__all__ = ["SentenceAccumulator"]
