---
name: llm-bench-compare
description: Use when the user wants to compare LLMs (e.g. Azure gpt-4o vs local vLLM Qwen3) on the unitree_go2_agentic_local_tts blueprint. Runs replay twice with different DIMOS_LLM_* env vars, aggregates with bench_agentic_local_tts.py, and pulls token / TTFT / latency numbers via jq for manual side-by-side comparison.
---

# LLM A/B 比較ベンチ（unitree-go2-agentic-local-tts）

## Overview

`unitree_go2_agentic_local_tts` blueprint の LLM を env だけ切替えて A/B 比較する。
blueprint コードは一切触らず、`DIMOS_LLM_MODEL` / `DIMOS_LLM_BASE_URL` / `DIMOS_LLM_API_KEY` の 3 env で OpenAI 互換エンドポイントなら何でも比較可能。

取れる指標:
- **latency**: `ttft_s` / `agent_first_call_s` / `speak_tts_s` / `llm_step_0_s` / `llm_step_last_s` / `e2e_first_audio_s`
- **コスト用 raw**: `prompt_tokens` / `completion_tokens` per llm_step（$ 換算は手動）
- 既存: `stt_s` / `llm_total_s` / `tools_total_s` / `turn_total_s`

スコープ外（Phase 2 以降）: tool-call 正答性、日本語生成品質スコア、$ 自動換算、orchestrator 自動化。

## 前提

- worktree（推奨）or main で `.venv` source 済み。
- replay は実機 Unitree が無くても動く（`192.168.11.10` への接続エラーは無視可）。
- 比較対象 LLM が **OpenAI 互換 `/v1`** を持つこと。Azure OpenAI v1（2025-06 GA）、OpenAI 本家、vLLM、Ollama などはこれを満たす。

## 手順

### 1. fixture を選ぶ / 用意する

既定: `tests/bench_fixtures/agentic_ja/fixtures.yaml`（9 個の wav）。
新規 fixture を作りたい場合は `scripts/gen_fixtures_agentic_local_tts.py` を参照。

### 2. 1 個目のモデルで replay

```bash
export DIMOS_LLM_MODEL=gpt-4o
export DIMOS_LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1
export DIMOS_LLM_API_KEY=<azure-key>

python scripts/replay_agentic_local_tts.py \
  --fixtures tests/bench_fixtures/agentic_ja/fixtures.yaml \
  --runs 3 --warmup 1 \
  --label azure-gpt-4o
```

- `--label` は run を後で識別するための free-form 文字列。省略時は `$DIMOS_LLM_MODEL`。
- 出力: `logs/<ts>-bench-agentic-local-tts/main.jsonl`。

### 3. 2 個目のモデルで replay

ローカル vLLM の例（DGX Spark など別マシンで vLLM 起動済み前提）:

```bash
export DIMOS_LLM_MODEL=Qwen/Qwen3-30B-A3B
export DIMOS_LLM_BASE_URL=http://<dgx-spark>:8000/v1
export DIMOS_LLM_API_KEY=dummy

python scripts/replay_agentic_local_tts.py \
  --fixtures tests/bench_fixtures/agentic_ja/fixtures.yaml \
  --runs 3 --warmup 2 \
  --label qwen3-30b-a3b
```

ローカル系は初回 prefix cache cold。`--warmup 2` 以上推奨。

### 4. run-dir を識別

```bash
for d in logs/*-bench-agentic-local-tts; do
  echo "=== $d ==="
  jq -r 'select(.event_kind=="run_meta") | "\(.label)  \(.model)"' "$d/main.jsonl"
done
```

`label` が空の run は Phase 1 より前の log なので除外。

### 5. 各 run を bench analyzer に通す

```bash
python scripts/bench_agentic_local_tts.py logs/<azure-run>
python scripts/bench_agentic_local_tts.py logs/<qwen-run>
```

冒頭に `label: ... model: ... base_url: ...` が表示され、続いて headline と aggregate table が出る。

### 6. token を jq で抜く（コスト計算用）

```bash
RUN=logs/<run>

# turn × step 単位
jq -r 'select(.event_kind=="llm_step")
       | [.turn_id, .step_idx, .input_tokens, .output_tokens] | @tsv' \
  "$RUN/main.jsonl"

# turn 単位 sum
jq -r 'select(.event_kind=="llm_step")
       | "\(.turn_id)\t\(.input_tokens // 0)\t\(.output_tokens // 0)"' \
  "$RUN/main.jsonl" \
  | awk '{ in_t[$1]+=$2; out_t[$1]+=$3 }
         END { for (k in in_t) print k, in_t[k], out_t[k] }'

# run 全体 sum
jq -r 'select(.event_kind=="llm_step")
       | "\(.input_tokens // 0)\t\(.output_tokens // 0)"' \
  "$RUN/main.jsonl" \
  | awk '{ in_t+=$1; out_t+=$2 } END { print in_t, out_t }'
```

$ 換算は手動（後述）。

### 7. TTFT / step 別 latency を抜く

```bash
jq -r 'select(.event_kind=="llm_first_token")
       | [.turn_id, .step_idx, .t] | @tsv' "$RUN/main.jsonl"

jq -r 'select(.event_kind=="llm_step")
       | [.turn_id, .step_idx, .duration_s] | @tsv' "$RUN/main.jsonl"
```

`ttft_s` 集計値は bench.py の aggregate table を見るのが速い。

### 8. 横並び比較

bench.py の出力（特に `p50` / `p95`）を 2 run 並べる。Markdown 表 or スプレッドシートに転記。

## $/turn の手計算（参考）

2026 年代表値:

| モデル | $/1M input | $/1M output |
|---|---|---|
| Azure gpt-4o | 2.50 | 10.00 |
| OpenAI gpt-4o-mini | 0.15 | 0.60 |
| Claude Sonnet 4.6 | 3.00 | 15.00 |
| ローカル vLLM | 電気代のみ | — |

`$_per_turn = (input/1e6)*$IN + (output/1e6)*$OUT`

## 注意点

1. **ローカル LLM の prefix cache**: 初回呼び出し cold。`--warmup` を増やすか、initial 1 turn を捨てる。
2. **`--enable-chunked-prefill` は付けない**: SSM+MoE hybrid で 9× throughput 劣化が報告。
3. **vLLM 推奨**: Ollama だと TTFT 2-4s で voice 用途破綻。vLLM の prefix caching で 0.12s。
4. **量子化**: DGX Spark なら NVFP4。FP8 → NVFP4 で 52 → 64 tok/s。
5. **同一 fixture / 同一 `--runs` で揃える**: 比較の前提条件。fixture 順序が違うと cache hit 率が変わって不公平。
6. **`run_meta` event**: replay 起動直後に main.jsonl 先頭に 1 行出る。後追いで「どの run がどのモデルか」を識別する基盤。

## トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| `llm_first_token` が main.jsonl に出ない | LangGraph dual-mode stream が streaming を起こしていない | `mcp_client_ja.py` の `stream_mode=["updates","messages"]` 引数確認、langchain/langgraph バージョン確認 |
| `input_tokens` / `output_tokens` が常に `null` | LLM クライアントが usage_metadata を返していない | langchain-openai バージョン更新、Azure 側 API バージョン確認 |
| `wait for turn` がタイムアウト | tool 実行で詰まり or LLM が応答不能 | `--turn-timeout` 引数を上げる、log で stt_done / llm_step が出ているか確認 |
| `192.168.11.10` connection refused | ロボット未接続（想定内） | 無視。STT/LLM/TTS のソフト経路は別経路で動く |
| 出力が完全に空 | replay が boot 段階で hang | `pkill -f replay_agentic_local_tts` → log の最後の event を確認、blueprint 側の問題 |

## 関連 ドキュメント

- 調査: `docs/survey/unitree-go2-agentic-local-tts-llm-2026-05.md`（モデル選定理由 / BFCL ランキング / DGX Spark セットアップ）
- 設計: `docs/superpowers/specs/2026-05-17-llm-bench-comparison-phase1-design.md`
- 実装プラン: `docs/superpowers/plans/2026-05-17-llm-bench-comparison-phase1.md`
