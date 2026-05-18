# Profile CLI + env vs config 責務分離 政策

**日付:** 2026-05-19
**前提:** `docs/misc/handoff_2026-05-19_env_vs_config.md`、`docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md`

## 動機

`local-qwen + voicevox` ↔ `azure-gpt4o + sbv2` のような切替を1コマンドで完結させたい。現状は config (JSON) と env (endpoint, API key) を別々に切替える必要があり、2手かかる。

切替の単位を **名前付きの env+config バンドル** (= profile) として扱い、`dimos run --profile NAME` で世界ごとスワップする。あわせて、env と config の責務分離ルールを明文化し、現状で env-only になっている category A の値（VOICEVOX/SBV2 のパラメータ）を config field に昇格する。

## スコープ (3 フェーズ)

| Phase | 内容 | 成果物 |
|---|---|---|
| 1 | profile 機構 (CLI flag, dir layout, dotenv override) + サンプル migration | `dimos/robot/cli/dimos.py` 拡張、`configs/profiles/local-qwen-voicevox/` |
| 2 | env vs config 政策ドキュメント化 | `docs/env-vs-config.md` |
| 3 | VOICEVOX/SBV2 を category A に migrate (env-only → config field、node 側 env 直読み剥がし) | `dimos/agents/skills/speak_skill_ja.py`, `dimos/stream/audio/tts/node_voicevox.py`, `node_style_bert_vits2.py` |

Phase はそれぞれ独立した commit にして main へ順次 merge。

---

## Phase 1: profile 機構

### CLI

新フラグ: `dimos run <blueprint> --profile NAME`

- `--profile` と既存 `-c <path>` は **排他**（同時指定でエラー）
- profile 解決パスは `configs/profiles/NAME/` のみ（CWD 相対）
- 名前に `/` `..` を含む場合はエラー（path traversal 防止）

### ディレクトリ構成

```
configs/profiles/
├── local-qwen-voicevox/
│   ├── config.json       # 既存 -c 形式そのまま (upstream互換)
│   ├── .env              # gitignore (secret/endpoint)
│   └── .env.example      # commit (テンプレート)
└── azure-gpt4o-sbv2/
    ├── config.json
    ├── .env
    └── .env.example
```

`configs/profiles/*/\.env` を `.gitignore` に追加（`.env.example` は commit 対象から外れない）。

### 起動時の処理順

`--profile foo` を受け取ったとき、`dimos run` の早い段階で次を実行：

1. profile dir `configs/profiles/foo/` を解決。存在しなければエラー
2. `configs/profiles/foo/.env` があれば `dotenv.load_dotenv(path, override=True)` で process env に流し込む（profile が shell env を **黙って上書き**）
3. `configs/profiles/foo/config.json` を、既存の `load_config_args` 経路に `-c configs/profiles/foo/config.json` 相当として渡す
4. 以降は upstream の `-c` と完全に同じ動作（dispatch key は lowercase module name）

`.env` 不在 or `config.json` 不在は許容（片方のみの profile も可）。両方不在ならエラー。

### patch 範囲

- `dimos/robot/cli/dimos.py`:
  - `dimos run` の argparser に `--profile NAME` を追加
  - 既存 `--config-args/-c` との排他チェック
  - profile 解決ヘルパ `_resolve_profile(name) -> tuple[Path|None, Path|None]` を追加
  - `--profile` 指定時は `load_config_args` 呼び出し前に `.env` を override load し、`-c` パスを差し替え
- `pyproject.toml`: `python-dotenv` を依存に追加（既に入っていなければ）
- `.gitignore`: `configs/profiles/*/.env` を追加

`load_config_args` 本体には触らない。profile レイヤは `-c` の前段に薄く乗るだけ。

### サンプル migration

既存 `configs/local_qwen_voicevox.json` を `configs/profiles/local-qwen-voicevox/config.json` に移動。同 dir に `.env.example` を作成：

```bash
# configs/profiles/local-qwen-voicevox/.env.example
DIMOS_LLM_BASE_URL=http://192.168.11.16:1234/v1
DIMOS_LLM_API_KEY=dummy
```

ユーザは `cp .env.example .env` してから値を埋める運用。

### 不採用案

- **`--profile` と `-c` の併用 (overlay)**: 「base profile + 差分 overlay」は便利だが、deep merge ルールの仕様化と検証コストが見合わない。YAGNI。
- **`--env KEY=VAL` フラグでの ad-hoc override**: profile 完全勝利の単純モデルを保つ。必要になったら追加。
- **`~/.dimos/profiles/` も探索**: 探索ルールが増える。当面 repo 内のみ。
- **TOML 形式**: 形式へのこだわりよりも切替体験が優先。upstream 互換の JSON のまま。
- **shell wrapper (zsh function)**: CLI に組み込んだ方が discoverability・ドキュメンタビリティが上。

---

## Phase 2: env vs config 政策ドキュメント

`docs/env-vs-config.md` を新規作成。内容：

### 3 カテゴリ

| カテゴリ | 例 | 置き場所 | 理由 |
|---|---|---|---|
| **A. 振る舞いの選択** | backend impl, model 名, fp16, speaker_id, 速度 | **config field**（env は default seed のみ） | 再現性が要る。bench/CI で YAML/JSON に残る。 |
| **B. 秘匿情報 / デプロイ依存エンドポイント** | API key, private base URL | **env only** | secret/マシン依存値は config file に書けない。 |
| **C. プロセス境界の env** | `OPENAI_API_KEY`, `OPENAI_BASE_URL` | **env only**（外部 SDK が直接読む） | dimos の管理外。 |

### 優先順位

`explicit config field > env (seed) > Field default`

A の env は `Field(default_factory=lambda: os.environ.get(...))` で読み取り、explicit な config が常に勝つ構造を保つ。

### Anti-pattern

- A の値を env だけでしか変えられない（bench YAML/profile config.json に書けないため、比較実験が記録に残らない）
- B/C を config file に書く（誤って secret を commit するリスク）

### profile との関係

profile はカテゴリ横断のバンドル名を提供するだけで、各値の置き場ルールは変えない。category A は `config.json`、B/C は `.env`。

---

## Phase 3: VOICEVOX/SBV2 を category A に migrate

### 現状

- `DIMOS_VOICEVOX_SPEAKER_ID/_SPEED_SCALE/_PITCH_SCALE/_INTONATION_SCALE/_VOLUME_SCALE`: `VoicevoxTTSNode.__init__` で env 直読み。config 不在
- `DIMOS_SBV2_*`: 同様

→ profile/.env でしか切替できない category A → anti-pattern。

### ターゲットスキーマ

```python
# dimos/agents/skills/speak_skill_ja.py (or 適切な config モジュール)

class VoicevoxParamsConfig(ModuleConfig):
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
    # 既存 DIMOS_SBV2_* env を同様に Field default に
    ...

class AssistantSpeechNodeJaConfig(ModuleConfig):
    impl: TtsImpl = Field(default_factory=_default_tts_impl)
    voicevox: VoicevoxParamsConfig = Field(default_factory=VoicevoxParamsConfig)
    sbv2: Sbv2ParamsConfig = Field(default_factory=Sbv2ParamsConfig)
    # ... 既存 field
```

### node 側 env 読みの扱い: 剥がす

`VoicevoxTTSNode.__init__` と `StyleBertVITS2TTSNode.__init__` の env 直読みは **完全に削除**。explicit 引数のみ受ける。

理由: シードは1箇所（`*ParamsConfig` の Field default）に集約することで、優先順位ルール `explicit > env > default` が自明になる。node 側にも env fallback があると2 重シードで混乱する。

呼び出し側 (`AssistantSpeechNodeJa._make_tts_node` 等) は `self.config.voicevox.speaker_id` 等を引数で渡す形に書き換え。

### config.json での記述例

```json
{
  "assistantspeechnodeja": {
    "impl": "voicevox",
    "voicevox": {
      "speaker_id": 1,
      "speed_scale": 1.2
    }
  }
}
```

profile の `.env` には `DIMOS_VOICEVOX_*` を **書かない**（書けば動くが、profile の `config.json` に書くのが正しい置き場）。`.env.example` から `DIMOS_VOICEVOX_*` を削除し、B/C のみ残す。

### テスト

- 既存 `tests/agents/skills/test_speak_skill_ja_impl_switch.py` 相当の構造で、`voicevox` / `sbv2` ネスト config の override テストを追加（explicit config が env seed に勝つこと、env 不在時 Field default が効くこと）

---

## commit 順序

1. **Phase 1**: profile 機構 + サンプル migration + `python-dotenv` 依存追加
2. **Phase 2**: `docs/env-vs-config.md` 追加
3. **Phase 3a**: VOICEVOX config 昇格 + node 側 env 読み剥がし + テスト
4. **Phase 3b**: SBV2 同様

Phase 1 と 2 は小さいので1 commit にまとめてもよい。Phase 3a/3b は独立 commit。

## upstream divergence

- Phase 1: `dimos/robot/cli/dimos.py` に追加（〜30行）。`load_config_args` 本体には触らない。
- Phase 3: `dimos/agents/skills/speak_skill_ja.py`, `node_voicevox.py`, `node_style_bert_vits2.py` を fork で持つ。これらは既に fork-side ファイルなので divergence は増えない。

## 関連ファイル

- 実装: `dimos/robot/cli/dimos.py`, `dimos/agents/skills/speak_skill_ja.py`, `dimos/stream/audio/tts/node_voicevox.py`, `dimos/stream/audio/tts/node_style_bert_vits2.py`
- 既存 spec: `docs/superpowers/specs/2026-05-18-llm-bench-runner-design.md`
- ハンドオフ: `docs/misc/handoff_2026-05-19_env_vs_config.md`
- LLM env contract: `dimos/agents/llm_env_ja.py`
- bench 参考: `scripts/bench_llm.py`, `scripts/bench_configs/*.yaml`
