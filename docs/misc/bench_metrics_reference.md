# Bench イベントと Metric 定義リファレンス

agentic-local-tts ベンチ (`scripts/bench_llm.py` → `scripts/bench_agentic_local_tts.py`) の計測ポイントと、そこから計算する metric の定義。

## 使い方

2 段構成: **① 計測ラン**（`bench_llm.py` が `main.jsonl` にイベントを吐く）→ **② 集計**（`bench_agentic_local_tts.py` が読んで metric を算出）。

### ① 計測ラン

CLI は実機の `dimos run <blueprint> --profile <name>` に合わせてある（blueprint は位置引数、`--profile` 共通、bench 固有ノブだけ `--bench` YAML）。

```bash
python scripts/bench_llm.py unitree-go2-agentic-local-tts-detection \
    --profile qwen-vl \
    --bench scripts/bench_configs/agentic_ja.yaml

# headless MuJoCo（Linux）の場合は xvfb 下で
xvfb-run -a python scripts/bench_llm.py <blueprint> --profile <name> --bench scripts/bench_configs/agentic_ja.yaml
```

- 出力先は `logs/{ts}-{blueprint}-{profile}/main.jsonl`（`--bench` YAML の `label:` で上書き可）。
- blueprint は `dimos run` と同じく名前解決されるので、fork-local の `-detection` 系も計測できる。fixture wav を `LocalMicrophoneJa.inject_utterance` で注入してイベントを記録する。
- `.env` と `configs/profiles/<name>.json` から LLM エンドポイント (`DIMOS_LLM_*` → `OPENAI_*`) が解決される（`--profile` のローダーは `dimos run` と共通）。
- `--bench` YAML（`scripts/bench_configs/agentic_ja.yaml`）が持つのは bench 固有ノブのみ: `fixtures` / `runs` / `warmup` / `shuffle` / `turn_timeout` / `headless` / `simulation` / 任意の `label`。

### ② 集計

```bash
# 引数なし = 最新の logs/*agentic-local-tts*/ を自動選択
python scripts/bench_agentic_local_tts.py

# run-dir を明示 / JSON 出力
python scripts/bench_agentic_local_tts.py logs/<run-dir> --json out.json
```

- per-turn の e2e レイテンシ・ステージ内訳・`mcp_tool:*` 個別サマリを表示。
- **warmup turn は集計から除外**され、残りの live turn を 1 プールで集約する。
- `--config {auto,agentic-local-tts,voice-live}` で analyzer モードを切替（既定 `auto` は run-dir 名から推定）。

以下は `main.jsonl` に出るイベントと、②が計算する metric の定義。

## 計測イベント (= 時刻マーカー)

すべて `dimos/agents/bench_ja/log_bench_event` 経由で `main.jsonl` に出力。各イベントには `t` (perf_counter) と `turn_id` が付く。

| event_kind | 発火元 | 発火タイミング | 付帯フィールド |
|---|---|---|---|
| `user_audio_end` | `scripts/bench_llm.py:280` | fixture wav を inject する**直前** = ユーザー発話終了 | `audio_seconds`, `fixture_id`, `run_idx`, `warmup` |
| `stt_done` | `whisper_human_input_ja.py:86` | Whisper が text を emit した瞬間 | `duration_s`, `audio_seconds`, `text_len` |
| `llm_first_token` | `mcp_client_ja.py:133` | LLM の `AIMessageChunk` 1 個目を観測（**step 毎に 1 回**） | `step_idx` |
| `first_tool_call` | `mcp_client_ja.py:177` | LLM が最初に tool_call を発行した瞬間 | `tool` |
| `llm_step` | `mcp_client_ja.py:181` (kind 決定は `:159`) | langgraph の `agent`/`model` node 完了時 | `node`, `duration_s`, `step_idx`, `n_messages`, `input_tokens`, `output_tokens` |
| `tools_step` | `mcp_client_ja.py:181` (kind 決定は `:159`) | langgraph の `tools` node 完了時。kind は `f"{node_name}_step"` で動的生成 | `node`, `duration_s`, `step_idx`, `n_messages` |
| `speak_invoke` | `speak_skill_ja.py:242` | AIMessage を受信し TTS に text を流した瞬間 | — |
| `first_audio_out` | `speak_skill_ja.py:261` | TTS から最初の AudioEvent chunk が出力デバイスに届いた瞬間 | `tool="speak"` |
| `tts_idle` | `speak_skill_ja.py:240,291` | TTS busy 突入 / 再生 drain 完了 | `idle` (bool) |
| `MCP tool done` | `mcp_server.py:131,134` (structlog → log Tee) | 個別 MCP tool 実行完了 | `tool`, `duration` |
| `turn_done` | `mcp_client_ja.py:201` | 最終 AIMessage を publish したエージェントループ終了 | `duration_s`, `llm_s`, `n_steps`, `n_tool_calls` |
| `turn_timeout` | `bench_llm.py:303` | `idle_event.wait()` が turn_timeout に達した | `fixture_id`, `run_idx` |

---

## Metric 定義 (`scripts/bench_agentic_local_tts.py:compute_per_turn_metrics`)

t0 = `user_audio_end.t` とする。

### エンドツーエンド系

| metric | 計算式 | 意味 |
|---|---|---|
| `agent_first_call_s` | `first_tool_call.t − t0` | 発話終了 → エージェントが最初に tool を呼ぶまで |
| `speak_tts_s` | `first_audio_out.t − speak_invokes[0].t` | TTS に text を流してから最初の音が出るまで |
| `e2e_first_audio_s` | `first_audio_out.t − t0` | **STT + LLM + TTS 込み**、発話終了 → 最初の音まで |
| `turn_total_s` | `turn_done.duration_s` | エージェントループ全体の所要時間（TTS 再生は含まない） |

### ステージ内訳

| metric | 計算式 | 意味 |
|---|---|---|
| `stt_s` | `stt_done.duration_s` | Whisper 推論時間 |
| `ttft_s` | `llm_first_tokens[0].t − t0 − stt_s` | **LLM 単体 TTFT** (step 0 の最初のトークン) |
| `llm_step_0_s` | `llm_steps[0].duration_s` | 1 回目の LLM step duration |
| `llm_step_last_s` | `llm_steps[-1].duration_s` | 最後の LLM step duration (= 発話を吐くステップ) |
| `llm_total_s` | `Σ llm_steps[i].duration_s` | 全 LLM step duration の和 |
| `tools_total_s` | `Σ tools_steps[i].duration_s` | 全 langgraph `tools` node duration の和（個別 MCP tool ではなく tools ノード全体）|

### トークン

| metric | 計算式 |
|---|---|
| `prompt_tokens` | `Σ llm_steps[i].input_tokens` |
| `completion_tokens` | `Σ llm_steps[i].output_tokens` |

### 個別ツール

| metric | 計算式 |
|---|---|
| `mcp_tool:<name>` | 該当 tool の `duration` の n / mean / p50 / p95 / max / min |

---

## イベントの帰属 (turn boundary)

- aggregator は `user_audio_end` で `current = turn_id` を**開く**
- `turn_done` は **turn を閉じない**（TTS 関連イベントが async に後着するため）
- 次の `user_audio_end` が来ると新しい turn_id で再オープン → 暗黙的に前 turn が確定
- `turn_timeout` は明示的に閉じる

これで `first_audio_out` / `tts_idle` のような後着イベントも正しい turn に紐付きます。
