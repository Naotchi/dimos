# Design: bench を profile 参照型に統一

- 日付: 2026-05-21
- 起点: `docs/misc/handoff_2026-05-21_unify_bench_and_profile_config.md`
- 方針整合: `docs/superpowers/plans/2026-05-19-profile-and-env-config-policy.md`、CLAUDE.md「env と config の責務分離」「upstream との衝突を避ける編集方針」

## 1. 背景と問題

TTS streaming を main にマージした際、`streaming` を speak node の config field として追加したが、`bench_llm.py` は `streaming` を YAML から渡さず env (`DIMOS_TTS_STREAMING`) のみで切替えていた。2026-05-21 の分析（`docs/misc/bench_analysis_2026-05-21_qwen3-30b-a3b-2507-streaming.md`）で、ON/OFF を別 run・別日で比較したため LLM backend 状態が交絡し clean な A/B にならなかった。

根本原因は **bench と通常実行（`dimos run --profile`）が独立した 2 系統の config を持ち、module パラメータがドリフトする / 設定が run 記録に残らない**こと。

### 現状の 2 系統（事実）

| | bench | 通常実行 |
|---|---|---|
| エントリ | `python scripts/bench_llm.py --config <yaml>` | `dimos run <bp> --profile <name>` |
| config 形式 | friendly YAML（`simulation`/`stt`/`llm`/`tts` セクション） | `configs/profiles/<name>/config.json`（**= blueprint_args そのもの**、lowercase module 名キー） |
| blueprint への変換 | `build_blueprint_args()` が翻訳（`scripts/bench_llm.py:63-106`） | `load_config_args()` がそのまま渡す（`dimos/robot/cli/dimos.py:204-231`、**upstream 由来**） |
| LLM endpoint | YAML `llm.base_url`/`api_key` → `apply_llm_env()` が `OPENAI_*` に直接 set | profile `.env` の `DIMOS_LLM_BASE_URL`/`API_KEY` → blueprint import 時 `resolve_llm_model()` が `OPENAI_*` にミラー |
| 共通の終端 | 双方とも `ModuleCoordinator.build(blueprint, <module-keyed dict>)` に収束 | 同左 |

**鍵となる発見**: profile `config.json` は既に `ModuleCoordinator.build` に渡る canonical な blueprint_args 形式そのもの。bench YAML だけがその上に friendly schema の翻訳層を被せている。

`resolve_llm_model()` は blueprint モジュールの **import 時**に発火する（`unitree_go2_agentic_local_tts.py:50` の `_LLM_MODEL = resolve_llm_model()`）。`dimos run` は `_apply_profile`（.env ロード）→ その後 blueprint を遅延 import するため `DIMOS_LLM_*` 経路が正しく効く。`bench_llm.py` は blueprint をファイル冒頭で eager import するため config 適用前に発火してしまい、`apply_llm_env()` で `OPENAI_*` を後から直接 set する hack が必要だった。これが env 名 2 系統（`DIMOS_LLM_*` vs `OPENAI_*`）の原因。

## 2. ゴール

- module パラメータ（category A=振る舞い）の **single source を profile に置く**。bench は profile を参照する。
- bench を「profile loader 再利用 + blueprint 遅延 import」に変え、env 解決経路を `dimos run --profile` と完全一致させる。`build_blueprint_args` / `apply_llm_env` を**撤去**。
- 各 bench run の記録に「実際に何が走ったか」を resolved な形で残す。
- `streaming` を含む category A は config field に集約。`DIMOS_TTS_STREAMING` env は**削除**。

非ゴール（YAGNI）: profile の継承/extends 機構、bench 側の per-run override 機構、旧 friendly YAML との両対応。

## 3. 層の分離

| 層 | 内容 | 置き場所 |
|---|---|---|
| 共有 A（振る舞い） | `g.simulation`, stt `model`/`fp16`, llm `model`, tts `impl`/`streaming`/speaker 等, `rerunbridgemodule.memory_limit` | **profile `config.json`** |
| 共有 B/C（secret/endpoint） | `DIMOS_LLM_BASE_URL` / `DIMOS_LLM_API_KEY` | **profile `.env`**（commit しない） |
| bench 専用 orchestration | `name`, `profile`, `fixtures`, `runs`, `warmup`, `shuffle`, `turn_timeout`, `tts_drain_timeout` | **bench YAML** |
| 実行時 process | `headless`（xvfb/`MUJOCO_GL` 警告用） | bench YAML + env |

`streaming` は profile の `assistantspeechnodeja.streaming` に書くだけで bench に反映される（特別なマッピング不要）。

## 4. コンポーネント設計

### 4.1 profile loader の切り出し（CLAUDE.md upstream 方針への配慮）

- `load_config_args` は **upstream 由来**（`dimos/robot/cli/dimos.py`）。bench からは **import して再利用**（編集しない）。
- profile 解決 `_resolve_profile` / `_apply_profile` は **fork 追加コード**（upstream file 内の fork 編集として存在）。これを **新規 fork 固有モジュール `dimos/agents/profile_ja.py`** に切り出し、`resolve_profile()` / `apply_profile()` として公開する。
- CLI 側（`dimos/robot/cli/dimos.py`）の既存 `_resolve_profile`/`_apply_profile` は新モジュールへ委譲する薄い形に置換する。これは upstream file への編集だが、ロジックを fork モジュールへ移すことで **upstream file 内の fork 差分が純減**し、CLAUDE.md の「upstream 由来ファイルの編集は最小差分に」に合致する（新規ファイル登録のための最小差分）。
- bench（`scripts/bench_llm.py`、fork 固有）は `dimos.agents.profile_ja.apply_profile` と `dimos.robot.cli.dimos.load_config_args` を import して使う。

### 4.2 bench_llm.py の改修

`build_blueprint_args` と `apply_llm_env` を**削除**し、`main()` を次の順序にする:

1. bench YAML をロード（`name`/`profile`/`fixtures`/`runs`/... のみ。`profile` は必須）。
2. `apply_profile(cfg["profile"])` → profile の `.env` を `override=True` でロード。
3. blueprint を**遅延 import**（関数内 import）→ `resolve_llm_model()` が `DIMOS_LLM_*` を拾い `OPENAI_*` をミラー。
4. `config_path = resolve_profile(cfg["profile"])` で profile の `config.json` パスを得る。
5. `kwargs = load_config_args(blueprint.config(), [], config_path)` → resolved blueprint_args。
6. `ModuleCoordinator.build(blueprint, kwargs)`。
7. 以降の fixture 注入・idle/drain gate・イベントログは現状維持。

`profile` キーが無い bench YAML は**エラー**にする（フォールバックなし）。

### 4.3 `streaming` の config 化と env 削除

`dimos/agents/skills/speak_skill_ja.py`（fork 固有）:
- `_default_tts_streaming()` 関数と `DIMOS_TTS_STREAMING` コメント（74-80 行）を削除。
- `streaming: bool = Field(default_factory=_default_tts_streaming)` → `streaming: bool = True`（現行 default = env 未設定時 True を保持）。
- `import os` が他で未使用になれば除去。

`tests/agents/skills/test_speak_skill_ja_streaming.py`（fork 固有）:
- `monkeypatch.delenv/setenv("DIMOS_TTS_STREAMING")` を使うテストを、config field を直接指定する形（`streaming=True`/`False`）に書き換える。env による default seed のテストは削除。

## 5. データフロー

```
bench YAML (name/profile/fixtures/runs/...)
        │  profile name
        ▼
apply_profile(name)  ──load .env(override)──▶ DIMOS_LLM_* env
        │
        ▼  (遅延 import)
blueprint module import ──resolve_llm_model()──▶ OPENAI_BASE_URL/API_KEY
        │
        ▼
resolve_profile(name) ─▶ config.json path
        │
        ▼
load_config_args(blueprint.config(), [], config.json)
        │  resolved kwargs (= module-keyed blueprint_args)
        ▼
ModuleCoordinator.build(blueprint, kwargs)
        │
        ▼
fixture 注入 / idle・drain gate / bench イベントログ（現状維持）
```

`dimos run --profile <name>` は手順 2→3→5→6 と同一経路を通る（bench は前後に orchestration を足すだけ）。

## 6. 再現性記録

`run_meta` イベントに記録:
- `profile`: profile 名
- `resolved_config`: `load_config_args` が返す kwargs 全体（実際に build に渡った module-keyed dict）
- `resolved_endpoint`: `OPENAI_BASE_URL` と model（**`api_key` は redact**）
- `config_hash`: resolved kwargs の sha256（先頭 8 桁）

`logs/<run>/` に:
- `resolved_config.json`（resolved kwargs のダンプ）
- bench YAML のコピー + profile `config.json` のコピー（`.env` はコピーしない＝secret を logs に残さない。endpoint は `resolved_endpoint` で redact 済み記録）

現状の `config.yaml` 単一コピーはこの構成に置換する。

## 7. A/B 比較（profile 複製）

`streaming` ON/OFF のような clean な A/B は、**当該 field 以外バイト同一の profile を 2 つ**用意して実現する（例 `local-qwen-voicevox-sim` と派生 `*-sim-stream`）。bench config も profile 名だけ違う 2 本。bench 側に override 機構は設けない（YAGNI）。

## 8. 移行スコープ

- 機構（4.1〜4.3）を実装。
- **active な qwen-voicevox 系 bench config 1 本**を profile 参照型に書き換え:
  - `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml` → `profile: local-qwen-voicevox-sim` 参照に。`simulation`/`stt`/`llm`/`tts` セクションを削除し、`name`/`profile`/`fixtures`/`runs`/`warmup`/`shuffle`/`turn_timeout`/`headless` のみ残す。
  - profile `configs/profiles/local-qwen-voicevox-sim/config.json` に `assistantspeechnodeja.streaming` を追記（A/B 用に必要なら派生 profile も）。
- **他 5 本の bench YAML（gpt4o 系）は削除**（移行せずに削除。フォールバックは作らない）:
  - `whisper-base-gpt4o-openai-tts.yaml` / `whisper-base-gpt4o-openjtalk.yaml` / `whisper-base-gpt4o-sbv2.yaml` / `whisper-base-gpt4o-voicevox.yaml` / `whisper-small-gpt4o-openjtalk.yaml`
  - 将来必要になれば profile を作って bench YAML を新規追加する。

## 9. テスト

fork 固有ファイルなので `pytest`（worktree 時は `python -m pytest`）。

1. **profile 解決経路（unit）**: `apply_profile`+`resolve_profile`+`load_config_args` で profile 名 → resolved kwargs が期待 module キー（`g`/`whisperhumaninputja`/`timedmcpclient`/`assistantspeechnodeja`）を含むこと。
2. **import 順序（unit/integration）**: `apply_profile` 後に blueprint を import すると `OPENAI_BASE_URL` が profile `.env` の `DIMOS_LLM_BASE_URL` 値になること（順序依存の回帰防止）。
3. **profile 欠落エラー**: `profile` キーの無い bench YAML が明示的にエラーになること。
4. **streaming config field**: `streaming=True/False` で購読先（`agent_text` vs `agent`）が切替わること。env による seed テストは削除（`DIMOS_TTS_STREAMING` 廃止に伴い）。
5. **CLI 委譲の回帰**: `dimos run --profile` 既存テスト（`dimos/robot/cli/test_*`）が、loader 切り出し後も通ること。

## 10. 関連ファイル

- 改修: `scripts/bench_llm.py`（fork）、`dimos/agents/skills/speak_skill_ja.py`（fork）、`tests/agents/skills/test_speak_skill_ja_streaming.py`（fork）
- 新規: `dimos/agents/profile_ja.py`（fork）、その test
- 最小編集（upstream 由来、委譲化で fork 差分純減）: `dimos/robot/cli/dimos.py`
- 削除: `scripts/bench_configs/whisper-base-gpt4o-*.yaml`（4 本）、`whisper-small-gpt4o-openjtalk.yaml`
- 移行: `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml`、`configs/profiles/local-qwen-voicevox-sim/config.json`
- 参照のみ: `dimos/agents/llm_env_ja.py`（`resolve_llm_model`）、`dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py:50`
