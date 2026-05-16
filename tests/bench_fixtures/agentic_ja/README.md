# agentic_ja bench fixtures

WAV fixtures for `scripts/replay_agentic_ja.py`.

- 16 kHz mono PCM WAV, synthesized from the `text` field of `fixtures.yaml` via pyopenjtalk.
- Regenerate with `python scripts/gen_fixtures_agentic_ja.py`.
- 10 prompts chosen for varied tool coverage (`speak` only, `sport + speak`, `current_time`, `relative_move`, `sport` only); default replay is 3 runs per fixture.
- Caveat: pyopenjtalk synthesis is what `JapaneseSpeakSkill` also uses, so Whisper may transcribe these unrealistically well versus human speech. Acceptable for in-stack regression bench; not a substitute for human-recorded fixtures when comparing STT providers.
