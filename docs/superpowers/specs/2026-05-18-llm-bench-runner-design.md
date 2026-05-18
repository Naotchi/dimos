# LLM Bench Runner — Config-Driven Eval Harness

**Status:** Design
**Date:** 2026-05-18
**Owner:** Naotchi

## Goal

`unitree_go2_agentic_local_tts` blueprint を使った LLM/STT/TTS の bench を、以下の条件で回せるようにする:

1. **GUI なし**（MuJoCo viewer off）でヘッドレス実行できる。
2. CLI 引数を増やさず、**設定ファイル（YAML）で構成を一括指定**できる。
3. STT / LLM / TTS の各実装・モデルを **config で切り替え可能**。
4. **計測結果と config が 1:1 で対応**する形で保存する。

個別評価（STT 単体 / LLM 単体 / TTS 単体）は新規 harness を用意せず、フル通しの bench イベントを analyzer 側で切り出して算出する。

## Non-Goals

- 新規 blueprint の追加（既存 `unitree_go2_agentic_local_tts` をそのまま使う）。
- analyzer (`summary.json` の生成側) の実装。本 spec では bench events を JSONL に残すところまで。
- 横断インデックス（`bench_index.csv` 等）の実装。
- 既存 `unitree_go2_agentic_local_tts` の prod 動作の挙動変更（bench からのパラメータ注入経路だけ追加）。

## Approach

### blueprint には触らない

`unitree_go2_agentic_local_tts` は **定数 Blueprint のまま**。bench は dimos 標準の `ModuleCoordinator.build(blueprint, blueprint_args=...)` を使って各 Module の config を注入する。

```python
ModuleCoordinator.build(
    unitree_go2_agentic_local_tts,
    blueprint_args={
        "WhisperHumanInputJa":   {"model": "small", "fp16": True},
        "TimedMcpClient":        {"model": "gpt-4o-mini"},
        "AssistantSpeechNodeJa": {"impl": "openai", "voice": "alloy"},
        "g":                     {"simulation": True},  # global_config
    },
)
```

- LLM model / base_url / system_prompt: `blueprint_args["TimedMcpClient"]`
- STT model size / fp16: `blueprint_args["WhisperHumanInputJa"]`
- simulation flag: `blueprint_args["g"]`

### TTS impl 切替のために `AssistantSpeechNodeJa` を改修

現状 `AssistantSpeechNodeJa` は OpenJTalk hardcoded。`blueprint_args` は同一クラスの config 上書きしかできず、別クラス（`node_openai`, `node_pytts`）への差し替えはできない。

そこで `AssistantSpeechNodeJa` を「内部で impl を選択する wrapper」に改修する:

- config に `impl: "open_jtalk" | "openai" | "pytts"`（default は現状の OpenJTalk）
- impl ごとの追加 param（voice, speed 等）も同 config に
- blueprint 構造は変わらない、`blueprint_args["AssistantSpeechNodeJa"]` で切替

このファイルは fork-local（`speak_skill_ja.py`）なので、CLAUDE.md の編集ルールに抵触しない。

### Bench runner: 既存スクリプトを改修

新規スクリプトを作らず、`scripts/replay_agentic_local_tts.py` を `scripts/bench.py` 相当に改修する（rename する）。新名称: `scripts/bench_llm.py`（既存 `replay_agentic_local_tts.py` は削除）。

- 引数は `--config <path>` の 1 つに絞る（debug 用に `--dry-run` 等は付けても良い）
- 既存の CLI 引数（`--runs`, `--warmup`, ...）は全部 config 内に移す
- 既存の replay ループ（fixture 反復, `inject_utterance`, idle 同期）はそのまま流用

### Config schema (YAML)

```yaml
# bench_configs/whisper-base-gpt4o-openjtalk.yaml
name: whisper-base-gpt4o-openjtalk     # run dir 名に使う、必須
fixtures: tests/bench_fixtures/agentic_ja/fixtures.yaml
runs: 3
warmup: 1
shuffle: false
turn_timeout: 30.0

simulation:
  enabled: true
  headless: true                       # MuJoCo viewer 抑制

stt:
  # WhisperHumanInputJa に渡る
  model: base                          # tiny / base / small / medium / large
  fp16: false

llm:
  # TimedMcpClient に渡る
  model: gpt-4o
  base_url: null                       # null なら env (DIMOS_LLM_BASE_URL / OPENAI_BASE_URL)
  system_prompt: ja_default            # ja_default / minimal / <ファイルパス>

tts:
  # AssistantSpeechNodeJa に渡る（改修後）
  impl: open_jtalk                     # open_jtalk / openai / pytts
  # 以下は impl 依存。openai なら voice/model、open_jtalk なら voice_dir/speed など
```

`bench_configs/` ディレクトリを `scripts/bench_configs/` 配下に置く（fork-local）。

### Headless 実装

`simulation.headless: true` のとき bench スクリプト冒頭で `os.environ["MUJOCO_GL"] = "egl"` をセット（MuJoCo 側で viewer を立てないように）。加えて MuJoCo backend 側に viewer 起動 flag がある場合は `blueprint_args["g"]` 経由で渡す。具体的な flag 名・場所は実装計画フェーズで特定する。

### 出力 (Approach B: 1 run = 1 ディレクトリ)

```
logs/{YYYY-MM-DD-HHMMSS}-{config.name}/
  config.yaml          # 使った config の完全コピー
  main.jsonl           # 既存 bench events + 拡張 run_meta
  summary.json         # 将来 analyzer が書く（本 spec の対象外）
```

`run_meta` event 拡張:

```json
{
  "event": "run_meta",
  "ts": "...",
  "config_name": "whisper-base-gpt4o-openjtalk",
  "config_hash": "ab12cd34",            // config dict の正規化 JSON の sha256 先頭 8 文字
  "config": { ...full config dict... }, // 安全な範囲で全部埋め込む。api_key は値ではなく env 変数名のみ
  "started_at": "..."
}
```

これにより `main.jsonl` の 1 行目を見ればこの run が何の構成だったか完全に再現できる。

## Components

### `scripts/bench_llm.py` (改修 / rename)

- 役割: config 読み、`MUJOCO_GL` 設定、blueprint_args 組立、ModuleCoordinator 起動、replay ループ実行。
- 依存: `scripts/replay_agentic_local_tts.py` の既存ロジック（fixture 反復、`inject_utterance`、idle 同期、bench event 発行）をそのまま移植。
- I/F: `python scripts/bench_llm.py --config bench_configs/<name>.yaml`

### `dimos/agents/skills/speak_skill_ja.py` (改修)

- `AssistantSpeechNodeJa.Config` に `impl` フィールド追加（default: `"open_jtalk"`）。
- 起動時に impl 文字列を見て対応する TTS node を内部に構築・委譲する。
- 既存呼出し側（`_common_agentic_ja`）は引数なし `.blueprint()` のままで動く（default が open_jtalk なので破壊的変更なし）。

### `scripts/bench_configs/*.yaml` (新規)

- 評価対象ごとに 1 ファイル。例:
  - `whisper-base-gpt4o-openjtalk.yaml` (baseline)
  - `whisper-small-gpt4o-openai-tts.yaml`
  - `whisper-base-gpt4o-mini-openjtalk.yaml`
- `name` フィールドが run dir 名になる。

### `dimos/agents/bench_ja/__init__.py` 周辺 (微修正)

- `run_meta` event に `config_name` / `config_hash` / `config` を埋め込めるよう、`log_bench_event("run_meta", ...)` の呼び出し側（= bench runner）で payload を渡す。`bench_ja` 側は schema 自由なので変更不要の見込み。

## Data Flow

```
config.yaml
    ↓ (bench_llm.py)
parse + validate
    ↓
{ MUJOCO_GL set, blueprint_args 組立, run dir 作成, config.yaml copy }
    ↓
ModuleCoordinator.build(unitree_go2_agentic_local_tts, blueprint_args=...)
    ↓
log_bench_event("run_meta", config_name=..., config_hash=..., config=...)
    ↓
fixture loop:
    new_turn() → user_audio_end → inject_utterance(wav)
    → (STT → LLM → TTS の通常パイプライン経由で bench events 発行)
    → idle wait → 次の fixture
    ↓
coordinator.stop()
```

## Error Handling

- config が不正（必須 field 欠落、型不一致）: スクリプト冒頭で例外を投げて即終了。run dir も作らない。
- `impl` が未知の値: `AssistantSpeechNodeJa` が起動時に明示的にエラー（typo を早期検出）。
- 既存 `inject_failed` / `turn_timeout` イベントの仕組みはそのまま流用。

## Testing

- TTS impl 切替の単体テスト: `AssistantSpeechNodeJa` に impl=open_jtalk / impl=openai のテストを 1 件ずつ追加（実 TTS は呼ばずに impl 選択ロジックだけ確認）。
- bench スクリプト smoke test: 既存 fixtures + 最小 config（runs=1 warmup=0、模擬 LLM endpoint）で 1 周回ることを確認する CI 用テストを 1 本追加。
  - 既存テストの構成が分からない場合は実装計画フェーズで確認。

## Open Questions

1. MuJoCo viewer 抑制が `MUJOCO_GL=egl` だけで足りるか、それとも simulation backend 側に明示的な viewer flag があるか。実装計画フェーズで `dimos/simulation` 配下のコードを調査して確定する。
2. `system_prompt` の `ja_default` / `minimal` のスイッチは新規実装か、既存に該当する名前付きプロンプトがあるか。
