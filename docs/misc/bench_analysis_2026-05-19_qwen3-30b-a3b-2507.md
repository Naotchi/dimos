# ベンチマーク分析: qwen3-30b-a3b-2507 + whisper-largev3 + voicevox

- 日付: 2026-05-19
- run: `logs/2026-05-19-025122-whisper-largev3-qwen3-30b-a3b-2507-voicevox`
- 構成:
  - STT: `whisper large-v3` (faster-whisper backend, fp16, CUDA)
  - LLM: `openai:qwen/qwen3-30b-a3b-2507` @ LM Studio `http://192.168.11.16:1234/v1`
  - TTS: `voicevox` (HTTP 一発取得型、非 streaming)
- fixture: `tests/bench_fixtures/agentic_ja/fixtures.yaml` × runs=3 (warmup=1)
- non-warmup turns: 20
- 実行場所: shibahara desktop pc (LLMのみDGX Spark)

## 全体サマリ (p50 / p95)

| metric | p50 | p95 |
|---|---:|---:|
| `stt_s` | 0.22s | 0.25s |
| `ttft_s` | 1.30s | 1.45s |
| `llm_total_s` | 3.10s | 5.32s |
| `speak_tts_s` | 1.04s | 1.47s |
| **`e2e_first_audio_s`** | **3.91s** | 11.30s |
| `turn_total_s` | 3.10s | 9.91s |

## Turn の種類別内訳 (p50)

| cohort | n | STT | LLM TTFT | LLM total | tools | speak_tts | **e2e** | turn_total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **no_tool** (即発話) | 6 | 0.18 | 1.13 | 1.58 | — | 1.08 | **2.85** | 1.58 |
| **with_tool** (tool→発話) | 12 | 0.22 | 1.33 | 3.38 | 0.003 | 0.96 | **4.52** | 3.39 |
| **with_wait** (`wait(5s)` 含む) | 2 | 0.23 | 1.27 | 5.11 | 5.007 | 1.32 | **11.68** | 10.12 |

## 時間予算 (e2e 構成式)

`e2e_first_audio_s ≈ stt_s + llm_total_s + speak_tts_s`

| cohort | 計算 | 実測 e2e |
|---|---|---:|
| no_tool | 0.18 + 1.58 + 1.08 = **2.84s** | 2.85s ✓ |
| with_tool | 0.22 + 3.38 + 0.96 = **4.56s** | 4.52s ✓ |

数式が ±0.1s で合うので計測パイプラインの整合性は確認済み。

## ボトルネック

1. **LLM が支配的**: no_tool で 1.58s/2.85s = **56%**、with_tool で 3.38/4.52 = **75%**
2. **TTFT 1.13s が硬い下限** — LM Studio + qwen3-30b @ 192.168.11.16 の network + decode 立ち上がり。これを縮めない限り e2e は 2s を切れない
3. **speak_tts ~1.0s** — voicevox 合成 + 再生バッファ立ち上がり。HTTP 一発取得型で応答長に比例。streaming voicevox なら 0.2-0.3s 圏に縮む余地あり
4. **tool 経由は LLM 二回分** — step0 (1.7s) + step_last (1.6s) ≈ 3.3s。tool 実行自体は ms 級でオーバーヘッドは LLM 往復のみ (= 設計通り、tool 利用で不利になっていない)
5. **STT 0.2s** — large-v3 でも faster-whisper のおかげで完全に誤差

## Tail latency

`turn_total_s` p95=9.9s は `wait(5s)` を含む 2 turn が引っ張っているだけ。`wait` 除外の no_tool/with_tool 合算 (n=18) なら p95 は 3-5s 圏で安定。

## 改善余地

| 対策 | no_tool e2e | with_tool e2e | コスト |
|---|---:|---:|---|
| **TTS streaming 化** (voicevox) | -0.7s → 2.1s | -0.7s → 3.8s | 中（実装必要） |
| **より速い LLM** (Sonnet 4.6 / GPT-4o, TTFT ~0.5s) | -0.6s → 2.2s | -1.2s → 3.3s | API 料金 |
| **コンテキスト圧縮** (system_prompt 短縮) | -0.2s → 2.6s | -0.4s → 4.1s | 機能制限 |
| **STT 軽量化** (large-v3 → medium) | -0.05s | -0.05s | 認識率トレードオフ |

**TTS streaming が最も費用対効果が高い**: ローカル変更だけで no_tool 2.1s、with_tool 3.8s まで圧縮可能。次の最適化候補としては最有力。

## 既知の制約

- `prompt_tokens` / `completion_tokens` が n=0 — LM Studio の OpenAI 互換レスポンスに `usage` が乗っていない。LM Studio 設定で「Return token usage」相当を有効化すれば集計可能。
- harness 修正 (`tts_drain_timeout=300s`、`tts_was_busy` ラッチ、aggregator の turn boundary 修正) を経て、過去 run と比べて以下が確認済:
  - `speak_tts_s` 全 turn 正の値（race 起因の負値ゼロ）
  - `turn_timeout` イベント 0 件
  - 数字が 3 run 連続で安定 (ttft 1.3s / llm_total 3.0s / e2e 3.6-3.9s)

## 関連ファイル

- 計測ポイントと metric 定義: `docs/misc/bench_metrics_reference.md`
- 設定ファイル: `scripts/bench_configs/whisper-largev3-qwen3-30b-a3b-2507-voicevox.yaml`
- 集計スクリプト: `scripts/bench_agentic_local_tts.py`
- ベンチランナー: `scripts/bench_llm.py`
