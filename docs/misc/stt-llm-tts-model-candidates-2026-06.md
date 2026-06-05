# STT / LLM / TTS モデル候補まとめ（2026-06）

## STT（音声認識）

**現行:** Whisper（local, large-v3）

| 候補・代替 | 場所 |
|---|---|
| **Azure Speech Recognition** | `scripts/bench_stt_compare.py` — whisper vs Azure の A/B 比較ハーネス |
| **Azure Voice Live**（統合型 S2S） | `dimos/agents/realtime/azure_voice_live.py` |
| Whisper サイズ比較（base/medium/large-v3） | `configs/profiles/local-qwen-voicevox-mac/config.json` など |

設計ドキュメント: `docs/superpowers/specs/2026-05-17-stt-compare-harness-design.md`

---

## LLM

**現行:** Qwen3-30B-A3B-Instruct-2507（LM Studio, local）

詳細な選定サーベイ: `docs/survey/unitree-go2-agentic-local-tts-llm-2026-05.md`

| ティア | 候補 |
|---|---|
| **S（採用候補）** | NVIDIA Nemotron 3 Nano 30B-A3B、gpt-oss-120b |
| **A（次世代）** | Qwen3.5-35B-A3B、Qwen3-32B、Qwen3-14B、gpt-oss-20b、GLM-4.5-Air |
| **△（保留）** | Qwen3.6-35B-A3B（llama.cpp 不安定）、Qwen3-Next-80B-A3B |
| **✕（不採用）** | Thinking 系列全般（`<think>` ブロックが TTS を破壊）、DeepSeek V3/V4、大型モデル群（128GB超） |
| **クラウド fallback** | gpt-4o-mini、Gemini 2.5 Flash、Claude Sonnet / GPT-5 |

ベンチ結果: `docs/misc/bench_analysis_2026-05-19_qwen3-30b-a3b-2507.md`

---

## TTS（音声合成）

**現行:** VOICEVOX（HTTP API）/ Style-Bert-VITS2（local-tts blueprint 固定）

| 候補・代替 | 場所 |
|---|---|
| **OpenAI TTS**（tts-1/tts-1-hd） | `dimos/stream/audio/tts/node_openai.py`、旧 bench config |
| **pyttsx3**（英語 fallback） | `dimos/stream/audio/tts/node_pytts.py` |
| **OpenJTalk**（旧世代） | 削除済み bench config に痕跡のみ |
| **Azure Voice Live** | 統合 S2S として別 blueprint |

impl の切り替え: `assistantspeechnodeja.impl` フィールド（`voicevox` / `sbv2` / `openai`）
テスト: `tests/agents/skills/test_speak_skill_ja_impl_switch.py`
