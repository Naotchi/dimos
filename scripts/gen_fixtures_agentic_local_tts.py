#!/usr/bin/env python
"""Generate 16 kHz mono WAV fixtures from fixtures.yaml using VOICEVOX.

Synthesizes each entry's ``text`` via a running VOICEVOX engine (HTTP), then
resamples to 16 kHz mono and writes the WAV. Idempotent: skips entries whose
target wav already exists. Run after editing `text` fields in fixtures.yaml.

Requires a reachable VOICEVOX engine, e.g.:
    docker run --rm -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu22.04-latest

Env:
    DIMOS_VOICEVOX_URL          base URL (default http://127.0.0.1:50021)
    DIMOS_VOICEVOX_SPEAKER_ID   speaker id (default 74)

Usage:
    python scripts/gen_fixtures_agentic_local_tts.py
"""

from __future__ import annotations

import io
import os
import sys
import wave
from math import gcd
from pathlib import Path

import numpy as np
import requests
import yaml
from scipy.signal import resample_poly

FIXTURE_DIR = Path("tests/bench_fixtures/agentic_ja")
TARGET_SR = 16000

VOICEVOX_URL = os.environ.get("DIMOS_VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/")
SPEAKER_ID = int(os.environ.get("DIMOS_VOICEVOX_SPEAKER_ID", "74"))
HTTP_TIMEOUT = 30.0


def _voicevox_wav(text: str) -> bytes:
    """Synthesize ``text`` via VOICEVOX (audio_query -> synthesis), return WAV bytes."""
    q = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": SPEAKER_ID},
        timeout=HTTP_TIMEOUT,
    )
    q.raise_for_status()
    s = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": SPEAKER_ID},
        json=q.json(),
        timeout=HTTP_TIMEOUT,
    )
    s.raise_for_status()
    return s.content


def synth_to_wav(text: str, out_path: Path) -> None:
    """Synthesize `text` with VOICEVOX, resample to 16kHz mono, write WAV."""
    with wave.open(io.BytesIO(_voicevox_wav(text))) as wf:
        src_sr = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        pcm = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        raise RuntimeError(f"VOICEVOX returned unexpected sample width: {sampwidth} bytes")

    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    if channels > 1:  # downmix to mono
        audio = audio.reshape(-1, channels).mean(axis=1)

    if src_sr != TARGET_SR:
        g = gcd(int(src_sr), TARGET_SR)
        audio = resample_poly(audio, TARGET_SR // g, int(src_sr) // g)

    audio = np.clip(audio, -32768.0, 32767.0).astype(np.int16)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_SR)
        w.writeframes(audio.tobytes())


def main(argv: list[str]) -> int:
    manifest_path = FIXTURE_DIR / "fixtures.yaml"
    if not manifest_path.exists():
        sys.exit(f"missing {manifest_path}")

    manifest = yaml.safe_load(manifest_path.read_text())
    fixtures = manifest.get("fixtures", [])

    n_generated = 0
    n_skipped = 0
    for fx in fixtures:
        wav_path = FIXTURE_DIR / fx["wav"]
        if wav_path.exists():
            n_skipped += 1
            continue
        print(f"generating {wav_path} ({fx['text']!r})")
        synth_to_wav(fx["text"], wav_path)
        n_generated += 1

    print(f"done: generated={n_generated}, skipped(existing)={n_skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
