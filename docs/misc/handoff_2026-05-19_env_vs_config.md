# Handoff: env vs config 責務分離

**日付:** 2026-05-19
**前セッションの成果物:**
- `dimos/agents/skills/speak_skill_ja.py` — `DIMOS_TTS_BACKEND` env を `AssistantSpeechNodeJaConfig.impl` の default seed として復活（分岐ロジックではなく `Field(default_factory=...)` 経由、explicit config が常に勝つ）
- `docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md` — 「env 廃止」の記述を「default seed としてのみ存続、explicit > env > default の優先順位」に書き換え
- `configs/local_qwen_voicevox.json` — `dimos run -c` 用のサンプル config（bench の `whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml` 相当）
- `tests/agents/skills/test_speak_skill_ja_impl_switch.py` — 6 件パス確認済み

## 確認済みの事実

- **`dimos run -c <path>`（JSON 設定）は upstream の機能** (`dimos/robot/cli/dimos.py:164-191`, commit `f39d61584` "Config options (#1543)")。fork 側で追加実装は不要。
- JSON の top-level key は **lowercase module name**（例: `assistantspeechnodeja`, `whisperhumaninputja`, `timedmcpclient`）。`ModuleCoordinator` の dispatch key と一致。
- bench (`scripts/bench_llm.py:63-104`) は friendlier YAML schema (`stt`/`llm`/`tts` グループ) を CLI 形式に翻訳して `coordinator.build(..., blueprint_args=...)` に渡している。`-c` JSON は raw 形式（翻訳なし）。

## 未完了タスク: env と config の責務分離

### 提案フレームワーク（3 カテゴリ）

| カテゴリ | 例 | 置き場所 | 理由 |
|---|---|---|---|
| **A. 振る舞いの選択** | backend impl, model 名, fp16, speaker_id, 速度 | **config field**（env は default seed のみ） | 再現性が要る。bench/CI で YAML/JSON に残す。 |
| **B. 秘匿情報 / デプロイ依存エンドポイント** | API key, private base URL | **env only** | secret/マシン依存値は config file に書けない。 |
| **C. プロセス境界の env** | `OPENAI_API_KEY`, `OPENAI_BASE_URL` | **env only**（外部 SDK が直接読む） | dimos の管理外。 |

ルール:
- A は env を「default seed」として許容するが、explicit config が常に勝つ（再現性と利便性の両立）。
- A の値を env だけでしか変えられない状況は anti-pattern（bench YAML に書けないと比較実験が記録に残らない）。
- B/C を config file に書ける状況も anti-pattern（誤って secret を commit するリスク）。

### 現状とのギャップ

| 項目 | 現状 | 分類 | 一致 |
|---|---|---|---|
| `assistantspeechnodeja.impl` (`DIMOS_TTS_BACKEND`) | config + env seed | A | ✅ 今セッション修正 |
| `whisperhumaninputja.model/fp16` (`DIMOS_WHISPER_*`) | config + env seed | A | ✅ |
| `timedmcpclient.model` (`DIMOS_LLM_MODEL`) | blueprint import 時に `_LLM_MODEL = resolve_llm_model()` で焼き込み | A | ⚠️ `-c` で override できるか要検証（次セッションで確認） |
| `DIMOS_VOICEVOX_SPEAKER_ID/_SPEED_SCALE/_PITCH_SCALE/_INTONATION_SCALE/_VOLUME_SCALE` | env only (`VoicevoxTTSNode.__init__` で直読み) | A | ❌ **要修正** |
| `DIMOS_VOICEVOX_URL/_PROBE_*` | env only | B 寄り | ✅ |
| `DIMOS_SBV2_*` | env only | A 寄り | ❌ **要修正** |
| `DIMOS_LLM_BASE_URL/_API_KEY` | env → `OPENAI_*` に side-effect | B | ✅ |

### 進め方の選択肢（前セッションでの user 提示分、未決）

- **A. ドキュメント先行**: `docs/env-vs-config.md`（or 既存 spec への追記）で責務分離を明文化、コード変更は後続 PR。
- **B. ドキュメント + VOICEVOX/SBV2 の config 昇格を同時に実施**: 一気通貫で整える。範囲広め。
- **C. ドキュメントだけで終わり**: 現状の env 直読みは「ファイン」と判定して維持。

**前セッションのおすすめ: A**（責務分離原則を先に固めてから、具体的なリファクタリング B を別 PR で実施）。

### 次セッションで最初に決めること

1. 方針 A / B / C のどれを採用するか。
2. （A or B を選んだ場合）ドキュメントの置き場所: `docs/env-vs-config.md` 新規作成 or `docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md` への追記。
3. （B を選んだ場合）昇格する config field のスキーマ案（`VoicevoxParamsConfig` ネスト案は前セッション末で提示済み）。

### 参考: VOICEVOX config 昇格のスキーマ案

```python
class VoicevoxParamsConfig(ModuleConfig):
    speaker_id: int = Field(default_factory=lambda: int(os.environ.get("DIMOS_VOICEVOX_SPEAKER_ID", "74")))
    speed_scale: float = Field(default_factory=lambda: float(os.environ.get("DIMOS_VOICEVOX_SPEED_SCALE", "1.0")))
    pitch_scale: float = ...
    intonation_scale: float = ...
    volume_scale: float = ...

class AssistantSpeechNodeJaConfig(ModuleConfig):
    impl: TtsImpl = Field(default_factory=_default_tts_impl)
    voicevox: VoicevoxParamsConfig = Field(default_factory=VoicevoxParamsConfig)
    sbv2: Sbv2ParamsConfig = ...
    ...
```

`VoicevoxTTSNode.__init__` の env 直読みは互換のため残しつつ、`AssistantSpeechNodeJa._make_tts_node` から `self.config.voicevox.speaker_id` 等を引数で渡す経路を追加する形（env と config 両方で動くが、explicit config が勝つ優先順位は維持）。

## 関連ファイル

- 実装: `dimos/agents/skills/speak_skill_ja.py`, `dimos/stream/audio/tts/node_voicevox.py`, `dimos/stream/audio/tts/node_style_bert_vits2.py`
- CLI: `dimos/robot/cli/dimos.py` (`load_config_args`)
- bench 参考: `scripts/bench_llm.py`, `scripts/bench_configs/*.yaml`
- LLM env contract: `dimos/agents/llm_env_ja.py`
- spec: `docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md`
- サンプル config: `configs/local_qwen_voicevox.json`

## 実行コマンド（参考）

```bash
DIMOS_LLM_BASE_URL=http://192.168.11.16:1234/v1 \
DIMOS_LLM_API_KEY=dummy \
dimos run unitree-go2-agentic-local-tts -c configs/local_qwen_voicevox.json
```

---

**2026-05-19 更新:** 本ハンドオフの未完了タスクは `docs/superpowers/specs/2026-05-19-profile-and-env-config-policy-design.md` の Phase 1〜3 で全て解消した（commits `25ad8988d`..`870005bec`）。新コマンド: `dimos run unitree-go2-agentic-local-tts --profile local-qwen-voicevox`。VOICEVOX/SBV2 の synthesis params は `AssistantSpeechNodeJaConfig.{voicevox,sbv2}` に昇格、node 側 env 直読みは削除。
