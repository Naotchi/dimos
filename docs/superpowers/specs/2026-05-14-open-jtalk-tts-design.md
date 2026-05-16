# Design: `DIMOS_TTS=open_jtalk` TTS Backend

Status: Draft
Date: 2026-05-14
Branch: feat/go2-agentic-local-tts-llm-env

## Background

Current `SpeakSkill` supports two TTS backends selected via `DIMOS_TTS`:

- `openai` — `OpenAITTSNode` emits an audio Observable consumed by `SounddeviceAudioOutput`.
- `pyttsx3` — `PyTTSNode` synthesizes and plays back internally.

Japanese support via `pyttsx3` depends on OS-level voice installation (e.g. espeak-ng voices on Linux, system voices on macOS), which is brittle. We want a self-contained Japanese TTS path. `pyopenjtalk` bundles the Mei HTS voice and synthesizes Japanese without OS voice setup.

## Goals

- Add a new backend `DIMOS_TTS=open_jtalk` to `SpeakSkill`.
- Reuse the existing audio-emit pipeline pattern (same architecture as `openai`).
- No new configuration surface: defaults only.

## Non-Goals

- Custom HTS voice selection.
- Speed/pitch tuning.
- Replacing `pyttsx3` as the default backend.

## Architecture

### New node

File: `dimos/stream/audio/tts/node_open_jtalk.py`

Class: `OpenJTalkTTSNode(AbstractTextConsumer, AbstractAudioEmitter, AbstractTextEmitter)`

Mirrors `OpenAITTSNode`:

- Background thread + queue (`text_queue`, `queue_lock`, `processing_thread`, `is_running`).
- `consume_text(observable)` subscribes and starts the worker.
- Worker pops text, calls `pyopenjtalk.tts(text)` → `(waveform, sample_rate)`.
- Emits `AudioEvent(data=waveform, sample_rate=48000, timestamp=time.time(), channels=1)` on `audio_subject`.
- Emits the spoken text on `text_subject` (before audio, same ordering as `OpenAITTSNode`).
- `dispose()` stops the worker, clears the queue, completes both subjects.

`pyopenjtalk` returns a `float64` numpy array roughly in int16 range and `sample_rate=48000`. We pass the waveform through unchanged; `SounddeviceAudioOutput` handles playback.

### Dispatch in SpeakSkill

`dimos/agents/skills/speak_skill.py` `start()`:

```python
elif backend == "open_jtalk":
    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode
    self._tts_node = OpenJTalkTTSNode()
    self._audio_output = SounddeviceAudioOutput(sample_rate=48000)
    self._audio_output.consume_audio(self._tts_node.emit_audio())
```

- `pyopenjtalk` import is deferred to this branch (so missing install does not break other backends).
- Error message in the `else` branch becomes `"DIMOS_TTS must be 'openai', 'pyttsx3', or 'open_jtalk', got: {backend!r}"`.
- Type annotation `_tts_node` updated to include `OpenJTalkTTSNode`.

### Parameters

- `speed`: fixed at 1.0 (no constructor arg).
- `sample_rate`: fixed at 48000.
- voice: bundled Mei (pyopenjtalk default).

## Data Flow

```
text_subject ──▶ OpenJTalkTTSNode._queue_text
                       │  (background thread)
                       ▼
                pyopenjtalk.tts(text)
                       │
                       ▼
              AudioEvent(48000Hz, mono)
                       │
                       ▼
              SounddeviceAudioOutput
```

## Error Handling

- `pyopenjtalk` not installed → `ImportError` propagates from `SpeakSkill.start()`. No silent fallback (matches `openai` behavior).
- Synthesis failure inside the worker → `logger.error(...)`, worker continues (matches `OpenAITTSNode._synthesize_speech`).

## Testing

New: `dimos/stream/audio/tts/tests/test_node_open_jtalk.py`

- Mock `pyopenjtalk.tts` to return a small fixed waveform and `48000`.
- Feed a text Subject; assert one `AudioEvent` is emitted with `sample_rate == 48000`, `channels == 1`, and that the waveform matches.
- Assert `emit_text()` re-emits the input text.
- Assert `dispose()` stops the worker thread (joins within timeout) and completes the subjects.

Extend: `dimos/agents/skills/tests/test_speak_skill_env.py`

- Add a case for `DIMOS_TTS=open_jtalk`: mock `OpenJTalkTTSNode` and `SounddeviceAudioOutput`, assert the open_jtalk branch is taken and an audio output is wired up.
- Add a case asserting that an unknown backend raises `ValueError` with the updated message including `'open_jtalk'`.

## Documentation

`README` Japanese voice setup section: add `open_jtalk` as a recommended backend for Japanese, noting that it requires no OS-level voice installation (`pip install pyopenjtalk` is sufficient).

## Out of Scope / Future Work

- Custom HTS voice via env var or config.
- Speed/pitch tuning.
- Streaming synthesis (pyopenjtalk is synchronous one-shot).
