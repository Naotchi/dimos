#!/usr/bin/env python
"""Generate 16 kHz mono WAV fixtures from fixtures.yaml using pyopenjtalk.

Idempotent: skips entries whose target wav already exists. Run after editing
`text` fields in fixtures.yaml.

Usage:
    python scripts/gen_fixtures_agentic_ja.py
"""

from __future__ import annotations

import sys
import wave
from math import gcd
from pathlib import Path

import numpy as np
import pyopenjtalk
import yaml
from scipy.signal import resample_poly

FIXTURE_DIR = Path("tests/bench_fixtures/agentic_ja")
TARGET_SR = 16000


def synth_to_wav(text: str, out_path: Path) -> None:
    """Synthesize `text` with pyopenjtalk, resample to 16kHz mono, write WAV."""
    audio, src_sr = pyopenjtalk.tts(text)
    audio = np.asarray(audio, dtype=np.float64)

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
