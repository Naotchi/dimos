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

"""Speak assistant messages directly via Japanese TTS.

Subscribes to ``McpClient.agent: Out[BaseMessage]`` (autoconnect wires by
``(name, type)``) and feeds the text content of each ``AIMessage`` into a
TTS node selected by ``impl`` (default ``sbv2``). The audio sink is opened
at the node's native sample rate so the speak path never resamples.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Literal, get_args

import reactivex.operators as ops
from langchain_core.messages import AIMessage
from langchain_core.messages.base import BaseMessage
from pydantic import Field
from reactivex import Subject
from reactivex.disposable import Disposable

from dimos.agents.bench_ja import log_bench_event
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# Static sample rates for nodes that don't expose `self.sample_rate`.
# OpenJTalk's module-level SAMPLE_RATE constant is 48000; OpenAI TTS
# returns 24 kHz audio (see node_openai.py's __main__ example).
_STATIC_SAMPLE_RATE: dict[str, int] = {
    "open_jtalk": 48000,
    "openai": 24000,
}


TtsImpl = Literal["open_jtalk", "sbv2", "voicevox", "openai"]

# DIMOS_TTS_BACKEND seeds the `impl` default for interactive runs. Bench /
# YAML / explicit `AssistantSpeechNodeJaConfig(impl=...)` always wins — the
# env is only consulted when no caller specified `impl`.
def _default_tts_impl() -> TtsImpl:
    raw = os.environ.get("DIMOS_TTS_BACKEND")
    if raw is None:
        return "sbv2"
    valid = get_args(TtsImpl)
    if raw not in valid:
        raise ValueError(
            f"DIMOS_TTS_BACKEND={raw!r} is not one of {valid}"
        )
    return raw  # type: ignore[return-value]


# DIMOS_TTS_STREAMING seeds the `streaming` default for interactive runs.
# Explicit config / YAML / bench always wins (category A behavior toggle).
def _default_tts_streaming() -> bool:
    raw = os.environ.get("DIMOS_TTS_STREAMING")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class VoicevoxParamsConfig(ModuleConfig):
    """VOICEVOX synthesis params (category A; env vars are default seeds only)."""

    speaker_id: int = Field(
        default_factory=lambda: int(os.environ.get("DIMOS_VOICEVOX_SPEAKER_ID", "74"))
    )
    speed_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_SPEED_SCALE", "1.0"))
    )
    pitch_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_PITCH_SCALE", "0.0"))
    )
    intonation_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_INTONATION_SCALE", "1.0"))
    )
    volume_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_VOLUME_SCALE", "1.0"))
    )


class Sbv2ParamsConfig(ModuleConfig):
    """Style-Bert-VITS2 synthesis params (category A)."""

    speaker_id: int = Field(
        default_factory=lambda: int(os.environ.get("DIMOS_SBV2_SPEAKER_ID", "0"))
    )
    style: str = Field(
        default_factory=lambda: os.environ.get("DIMOS_SBV2_STYLE", "Neutral")
    )
    style_weight: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_STYLE_WEIGHT", "1.0"))
    )
    sdp_ratio: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_SDP_RATIO", "0.15"))
    )
    noise: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_NOISE", "0.4"))
    )
    noise_w: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_NOISE_W", "0.6"))
    )
    length: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_LENGTH", "1.1"))
    )
    pitch_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_PITCH_SCALE", "1.08"))
    )
    intonation_scale: float = Field(
        default_factory=lambda: float(os.environ.get("DIMOS_SBV2_INTONATION_SCALE", "0.85"))
    )


class AssistantSpeechNodeJaConfig(ModuleConfig):
    """Config selecting the underlying TTS implementation."""

    impl: TtsImpl = Field(default_factory=_default_tts_impl)
    voicevox: VoicevoxParamsConfig = Field(default_factory=VoicevoxParamsConfig)
    sbv2: Sbv2ParamsConfig = Field(default_factory=Sbv2ParamsConfig)
    openai_voice: Voice = Voice.ECHO  # used when impl == "openai"
    openai_model: str = "tts-1"  # used when impl == "openai"
    idle_grace_s: float = 1.0  # silence-watchdog tail after last chunk's playback end
    streaming: bool = Field(default_factory=_default_tts_streaming)


class AssistantSpeechNodeJa(Module):
    """Speak assistant message text via a configurable Japanese TTS node."""

    agent: In[BaseMessage]
    agent_text: In[str]
    tts_idle: Out[bool]
    config: AssistantSpeechNodeJaConfig

    def _make_tts_node(self):
        """Construct the TTS node for this run's impl.

        Heavy backends (sbv2/voicevox) are imported lazily so a run that
        only targets open_jtalk/openai doesn't pay their import cost.
        """
        impl = self.config.impl
        if impl == "open_jtalk":
            return OpenJTalkTTSNode()
        if impl == "openai":
            return OpenAITTSNode(
                voice=self.config.openai_voice,
                model=self.config.openai_model,
            )
        if impl == "sbv2":
            from dimos.stream.audio.tts.node_style_bert_vits2 import (
                StyleBertVits2TTSNode,
            )
            s = self.config.sbv2
            return StyleBertVits2TTSNode(
                speaker_id=s.speaker_id,
                style=s.style,
                style_weight=s.style_weight,
                sdp_ratio=s.sdp_ratio,
                noise=s.noise,
                noise_w=s.noise_w,
                length=s.length,
                pitch_scale=s.pitch_scale,
                intonation_scale=s.intonation_scale,
            )
        if impl == "voicevox":
            from dimos.stream.audio.tts.node_voicevox import VoicevoxTTSNode
            vv = self.config.voicevox
            return VoicevoxTTSNode(
                speaker_id=vv.speaker_id,
                speed_scale=vv.speed_scale,
                pitch_scale=vv.pitch_scale,
                intonation_scale=vv.intonation_scale,
                volume_scale=vv.volume_scale,
            )
        raise ValueError(f"Unknown AssistantSpeechNodeJa impl: {impl!r}")

    def _sample_rate_for(self, node: Any) -> int:
        """Resolve the playback sample rate for ``node``.

        sbv2/voicevox set ``self.sample_rate``; open_jtalk/openai don't,
        so fall back to a static per-impl rate.
        """
        sr = getattr(node, "sample_rate", None)
        if sr is not None:
            return int(sr)
        return _STATIC_SAMPLE_RATE[self.config.impl]

    def _select_input(self):
        """Pick (stream, callback) for this run based on ``config.streaming``.

        Streaming feeds pre-segmented sentences from the producer's
        ``agent_text`` port; non-streaming consumes whole ``AIMessage``s
        from ``agent`` (legacy behavior). Only one is ever subscribed, so
        no double-speak even though autoconnect wires both ports.
        """
        if self.config.streaming:
            return self.agent_text, self._on_agent_text
        return self.agent, self._on_agent_message

    @rpc
    def start(self) -> None:
        super().start()

        self._first_chunk_pending = False
        self._first_chunk_lock = threading.Lock()

        self._tts_node = self._make_tts_node()
        self._playback_sample_rate = self._sample_rate_for(self._tts_node)
        self._audio_output = SounddeviceAudioOutput(
            sample_rate=self._playback_sample_rate
        )

        self._idle_lock = threading.Lock()
        self._play_end_t = 0.0
        self._idle_timer: threading.Timer | None = None
        self._is_idle = True
        self.tts_idle.publish(True)

        self._text_subject = Subject()
        self._tts_node.consume_text(self._text_subject)

        tapped = self._tts_node.emit_audio().pipe(ops.do_action(self._on_audio_chunk))
        self._audio_output.consume_audio(tapped)

        stream, callback = self._select_input()
        self.register_disposable(Disposable(stream.subscribe(callback)))

    @rpc
    def stop(self) -> None:
        idle_lock = getattr(self, "_idle_lock", None)
        if idle_lock is not None:
            with idle_lock:
                if self._idle_timer is not None:
                    self._idle_timer.cancel()
                    self._idle_timer = None
        if getattr(self, "_text_subject", None) is not None:
            self._text_subject.on_completed()
            self._text_subject = None
        if getattr(self, "_tts_node", None) is not None:
            self._tts_node.dispose()
            self._tts_node = None
        if getattr(self, "_audio_output", None) is not None:
            self._audio_output.stop()
            self._audio_output = None
        super().stop()

    def _on_agent_message(self, msg: BaseMessage) -> None:
        if not isinstance(msg, AIMessage):
            return
        content = msg.content
        if not isinstance(content, str):
            return
        self._speak(content)

    def _on_agent_text(self, text: str) -> None:
        self._speak(text)

    def _speak(self, text: str) -> None:
        """Feed one text unit into TTS, firing utterance-start once per idle edge.

        ``speak_invoke`` / ``first_audio_out`` anchor on the idle->busy
        transition, so a streaming turn that submits many sentences logs a
        single utterance start (matching the bench ``speak_tts_s`` metric,
        which uses ``speak_invokes[0]``).
        """
        if text.strip() == "":
            return
        if self._text_subject is None:
            logger.warning(
                "AssistantSpeechNodeJa received agent message after stop(); dropping."
            )
            return

        with self._idle_lock:
            starting = self._is_idle
            if starting:
                self._is_idle = False
                if self._idle_timer is not None:
                    self._idle_timer.cancel()
                    self._idle_timer = None
                self.tts_idle.publish(False)
                log_bench_event("tts_idle", idle=False)
        if starting:
            log_bench_event("speak_invoke")
            with self._first_chunk_lock:
                self._first_chunk_pending = True
        self._text_subject.on_next(text)

    def _on_audio_chunk(self, chunk: Any) -> None:
        """Fire ``first_audio_out`` once per utterance and extend the idle watchdog.

        The watchdog tracks the last chunk's *playback* end time
        (``now`` advanced by ``samples / sample_rate``) and fires
        ``idle_grace_s`` seconds after it. New chunks push the timer
        forward, so a streaming TTS that yields many chunks per
        utterance stays "busy" until playback actually drains.
        """
        with self._first_chunk_lock:
            fire_first = self._first_chunk_pending
            if fire_first:
                self._first_chunk_pending = False
        if fire_first:
            log_bench_event("first_audio_out", tool="speak")

        sample_rate = getattr(chunk, "sample_rate", self._playback_sample_rate)
        channels = getattr(chunk, "channels", 1) or 1
        data = getattr(chunk, "data", None)
        if data is None or sample_rate <= 0:
            return
        frames = len(data) // max(channels, 1)
        chunk_dur = frames / float(sample_rate)
        if chunk_dur <= 0:
            return

        now = time.monotonic()
        grace = float(self.config.idle_grace_s)
        with self._idle_lock:
            self._play_end_t = max(now, self._play_end_t) + chunk_dur
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            delay = max(0.0, self._play_end_t + grace - now)
            self._idle_timer = threading.Timer(delay, self._on_idle_fire)
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _on_idle_fire(self) -> None:
        with self._idle_lock:
            self._idle_timer = None
            if self._is_idle:
                return
            self._is_idle = True
        self.tts_idle.publish(True)
        log_bench_event("tts_idle", idle=True)


__all__ = [
    "AssistantSpeechNodeJa",
    "AssistantSpeechNodeJaConfig",
    "Sbv2ParamsConfig",
    "VoicevoxParamsConfig",
]
