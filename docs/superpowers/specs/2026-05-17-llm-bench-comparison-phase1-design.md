# LLM 比較ベンチ Phase 1 設計（latency + cost）

- 日付: 2026-05-17
- 対象 blueprint: `unitree_go2_agentic_local_tts`
- 目的: env 切替えだけで Azure gpt-4o とローカル vLLM Qwen3-30B-A3B を A/B 比較できる最小構成を整える。
- 関連調査: `docs/survey/unitree-go2-agentic-local-tts-llm-2026-05.md`
- 関連先（Phase 2）: tool-call 正答性、日本語生成品質スコア（本 spec の対象外）

## 1. 背景

`scripts/replay_agentic_local_tts.py` と `scripts/bench_agentic_local_tts.py` で latency 計測パイプラインは既に動いているが、LLM 比較には次の不足がある:

1. **モデル識別が log に無い** — run-dir 名にも main.jsonl にも `DIMOS_LLM_MODEL` / `DIMOS_LLM_BASE_URL` が残らない。
2. **token usage が log に無い** — `llm_step` event は duration のみ。cost 比較ができない。
3. **TTFT が取れない** — `llm_step` は LangGraph node の完了時に emit されるため、prefix cache の効き（gpt-4o ~0.5–1s vs vLLM ~0.12s）が見えない。
4. **per-step latency が集計で潰れている** — bench.py は `llm_total_s` (sum) のみ。tool-calling agent では step 0（planning）が最も LLM 性能依存だが、後段の step に紛れて見えない。
5. **tool-less turn で headline が NaN** — chit-chat turn は `first_tool_call` が無く `agent_first_call_s` が空になる。

LLM 切替は env 3 つ（`DIMOS_LLM_MODEL` / `DIMOS_LLM_BASE_URL` / `DIMOS_LLM_API_KEY`）で完結する仕組みが既に `dimos/agents/llm_env_ja.py` にある。Phase 1 では blueprint コードは触らず、replay / bench instrumentation の追加で上記 5 点を埋める。

## 2. 非目標（Phase 2 以降）

- Cost ($) の自動換算。token 数を raw で残し、価格表は skill doc の表で人手参照。
- 複数 run を 1 コマンドで回す orchestrator。env を手で切替えて 2 回 replay する前提。
- fixture の expected_tools / ground truth による正答性スコア。
- LLM-judge による日本語生成品質スコア。

## 3. 変更ファイル

| ファイル | 種別 | 変更概要 |
|---|---|---|
| `scripts/replay_agentic_local_tts.py` | fork 固有 | `--label` 追加、起動時に `run_meta` bench event を emit |
| `dimos/agents/mcp/mcp_client_ja.py` | upstream 由来（既に bench instrumentation 入り） | TTFT callback、token usage、`llm_first_token` event |
| `scripts/bench_agentic_local_tts.py` | fork 固有 | `run_meta` 表示、step 別 latency / TTFT / usage 集計 |
| skill doc（後続 plan で配置決定） | 新規 | 切替手順、jq snippet、注意点 |

`mcp_client_ja.py` は upstream 由来だが、本ファイルは fork が bench instrumentation を載せている既存延長線で、ロジック改変ではなく event 追加のみ。CLAUDE.md の編集方針には抵触しない範囲。

## 4. 設計詳細

### 4.1 `run_meta` event（replay.py）

`new_turn()` より前、log_dir 確定直後に 1 回だけ emit:

```python
log_bench_event(
    "run_meta",
    label=args.label,                 # 例: "azure-gpt-4o" / "qwen3-30b-a3b"
    model=os.getenv("DIMOS_LLM_MODEL"),
    base_url=os.getenv("DIMOS_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
    api_key_source=("DIMOS_LLM_API_KEY" if os.getenv("DIMOS_LLM_API_KEY") else "OPENAI_API_KEY"),
    started_at=datetime.now().isoformat(),
)
```

- `--label` は CLI 引数（既定: model 名 or "unlabeled"）。
- API key そのものは log しない（source 名のみ）。
- bench.py 起動時に main.jsonl 先頭をスキャンして見つけ、headline 行に表示。

### 4.2 TTFT 計測（mcp_client_ja.py）

LangChain `BaseChatModel` は `on_llm_new_token` callback を持つ。`TimedMcpClient._process_message` で LangGraph 実行時に turn 単位の `BaseCallbackHandler` を `RunnableConfig.callbacks` 経由で注入する。

```python
class _TtftHandler(BaseCallbackHandler):
    def __init__(self):
        self.first_token_t: float | None = None
    def on_llm_new_token(self, token, **kwargs):
        if self.first_token_t is None:
            self.first_token_t = time.perf_counter()
```

- 1 turn = 複数 LLM step を含むため、handler は **step 単位**で reset（step_t0 の直後に新規 handler を作って config に差し込む）。
- 最初の token を受けた時点で `llm_first_token` event を emit（`step_idx`, `t`, `delta_from_step_start_s`）。
- 「streaming に対応しない LLM」（usage_metadata だけ返してくる種類）では callback が呼ばれない可能性があり、その場合 `llm_first_token` は出さない。Azure OpenAI / OpenAI / vLLM はいずれも token streaming 対応なので Phase 1 範囲では問題なし。

### 4.3 Token usage（mcp_client_ja.py）

各 LLM step の `msgs` を走査し、AIMessage の `usage_metadata`（LangChain 標準フィールド）を集計:

```python
input_tokens = sum(m.usage_metadata.get("input_tokens", 0)
                   for m in msgs if hasattr(m, "usage_metadata") and m.usage_metadata)
output_tokens = sum(m.usage_metadata.get("output_tokens", 0)
                    for m in msgs if hasattr(m, "usage_metadata") and m.usage_metadata)
```

`llm_step` event の payload に `input_tokens` / `output_tokens` を追加。値が取れなかったときは `None`（key を出さないと bench.py 側で None と "未対応" の区別がつかないので、常に key を出す）。

vLLM は OpenAI 互換レスポンスで usage を返し、LangChain `ChatOpenAI` がそれを `usage_metadata` に詰めるので、エンドポイント側 hardcode なしで両対応。

### 4.4 `llm_first_token` を headline の primary signal に

bench.py の `agent_first_call_s` を以下のように再定義:

- まず `llm_first_token` を探す（存在すれば「LLM が応答開始した瞬間」）。
- 無ければ既存の `first_tool_call` にフォールバック。
- 両方無ければ None。

これで chit-chat turn でも headline が埋まる。**既存 `first_tool_call` based の値は別 metric として残す**（`first_tool_call_s`）ことで、回帰検出可能。

### 4.5 bench.py の集計追加

per-turn metric に以下を追加:

- `ttft_s`: `llm_first_token.t - user_audio_end.t - stt_s`（STT 分を引いた純 LLM TTFT）
- `llm_step_0_s`: 最初の llm_step の duration
- `llm_step_last_s`: 最後の llm_step の duration
- `prompt_tokens`: 全 llm_step の input_tokens 合計
- `completion_tokens`: 全 llm_step の output_tokens 合計

aggregate keys に追加し、`_AGENTIC_LOCAL_TTS_METRIC_KEYS` を更新。headline に `ttft_s` を加える。

### 4.6 fixture の warmup ガイダンス

local モデルは初回 prefix cache cold。replay.py の `--warmup` 既定値は据置（1）だが、skill doc に「local モデルなら `--warmup 2-3` 推奨」と記載。

## 5. 比較ワークフロー（skill doc 想定）

```bash
# Azure gpt-4o 側
export DIMOS_LLM_MODEL=gpt-4o
export DIMOS_LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1
export DIMOS_LLM_API_KEY=...
python scripts/replay_agentic_local_tts.py --label azure-gpt-4o --runs 3 --warmup 1

# ローカル vLLM 側
export DIMOS_LLM_MODEL=Qwen/Qwen3-30B-A3B
export DIMOS_LLM_BASE_URL=http://dgx-spark:8000/v1
export DIMOS_LLM_API_KEY=dummy
python scripts/replay_agentic_local_tts.py --label qwen3-30b-a3b --runs 3 --warmup 2

# 比較
python scripts/bench_agentic_local_tts.py logs/<azure-run>
python scripts/bench_agentic_local_tts.py logs/<qwen-run>

# token usage を jq で抜くサンプル
jq -r 'select(.event_kind=="llm_step") | [.turn_id, .input_tokens, .output_tokens] | @tsv' \
  logs/<run>/main.jsonl
```

## 6. リスク / 検証ポイント

1. **TTFT callback が想定通り発火するか** — Azure OpenAI / vLLM の両方で実機検証。発火しない場合は spec 側で None として扱える設計にしてあるが、片側だけ取れる状況は比較不能。Plan 段階で smoke test を含める。
2. **`usage_metadata` の availability** — LangChain バージョン依存。`pyproject.toml` の固定版で動作確認。
3. **step 単位 callback のスレッド安全** — LangGraph stream は同一スレッドで走るが、念のため handler は per-step 生成（共有しない）。
4. **`llm_first_token` 再定義による既存ダッシュボード影響** — 現状ダッシュボード等は無く main.jsonl 直読みなので影響なし。

## 7. 完了条件

- replay.py が `--label` を受け、main.jsonl 先頭に `run_meta` を吐く。
- main.jsonl に `llm_first_token` event と、`llm_step` の `input_tokens` / `output_tokens` フィールドが乗る。
- bench.py が `ttft_s` / `llm_step_0_s` / `llm_step_last_s` / `prompt_tokens` / `completion_tokens` を per-turn / aggregate に表示し、`run_meta` の label・model を headline に出す。
- Azure gpt-4o とローカル vLLM の両方で smoke 実行が成功し、両 run の bench.py 出力を並べて p50/p95/token 数の差が見える。
