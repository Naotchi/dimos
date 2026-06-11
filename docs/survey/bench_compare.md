# STT/TTS ベンチマーク比較レポート

`unitree-go2-agentic-local-tts-detection` を `scripts/bench_llm.py` でリプレイ計測し、
**STT モデル（Whisper `large-v3` / `medium` / `small`）** と **実行デバイス（CPU / GPU）** の
レイテンシを比較した記録。TTS（VOICEVOX）・LLM（gpt-4o, cloud）は固定。

> [!summary] 結論（BLUF）
> - **CPU では STT が e2e レイテンシの律速**（`large-v3` は e2e 27.3 s のうち 24.5 s ≈ 90% が STT）。
> - **GPU 実行で STT は律速でなくなる**。`medium` STT 12.87 s → **0.41 s（~31×）**、`small` 3.85 s → **0.20 s（~19×）**。
> - **e2e first-audio**: `medium` 15.6 s →**3.75 s**、`small` 6.5 s →**3.62 s**。GPU では **`medium` ≈ `small`**（STT が誤差レベルになり、残りは LLM TTFT + TTS + overhead）。
>   → **GPU 環境なら、精度で有利な `medium` をレイテンシのペナルティほぼ無しで選べる**。
> - `large-v3` は 6 GB VRAM に収まらず **GPU 不可（CUDA OOM）**。GPU 化したい場合は `medium` 以下。
> - **TTS（VOICEVOX）は ~0.5–1.0 s**、LLM（gpt-4o）も TTFT ~1.1–1.4 s で、GPU STT 後はこの 2 つが e2e の主成分。
> - 精度（CER 等）は未評価（小モデルほど精度低下しうる点に留意）。

---

## 1. 計測条件

| 項目 | 値 |
|---|---|
| Blueprint | `unitree-go2-agentic-local-tts-detection` |
| Runner | `scripts/bench_llm.py`（fixture wav をマイクに inject するリプレイ計測） |
| Bench config | `scripts/bench_configs/agentic_ja.yaml`（`runs: 3`, `warmup: 1`, `turn_timeout: 30s`, `simulation: mujoco`, `headless: true`） |
| Fixtures | `tests/bench_fixtures/agentic_ja/fixtures.yaml`（日本語 10 種 `fx_01`–`fx_10`） |
| LLM | `openai:gpt-4o`（`endpoint: cloud` / Azure 経由）※全条件で固定 |
| TTS | VOICEVOX（`speaker_id=29`, streaming）※全条件で固定 |
| Sim | MuJoCo（headless, EGL レンダリング） |
| マシン | Dell Precision 3240 / NVIDIA Quadro RTX 3000 Mobile（**6 GB VRAM**, 空き ~5.1 GB）/ NVIDIA driver 595（CUDA 13.2） |

> [!note] デバイスの切り替え方
> - **CPU 実行**: `CUDA_VISIBLE_DEVICES="" .venv/bin/python scripts/bench_llm.py ...`（torch を CPU に固定）。
> - **GPU 実行**: 上記 env を付けない（torch が `cuda:0` を自動使用。ログに `VoxelGrid using device: CUDA:0`）。
> - `large-v3` は GPU だとロボットスタックの他 torch モジュール（VoxelGrid / Detection3D 等）と合わせて 6 GB を超過し OOM するため CPU 専用。`medium`/`small` は空き ~5.1 GB に収まる。

---

## 2. 比較対象（プロファイル × デバイス）

| プロファイル | STT モデル | TTS | CPU | GPU |
|---|---|---|---|---|
| `gpt4o` | `large-v3` | voicevox | ✅ | ❌ OOM |
| `gpt4o-stt-medium` | `medium` | voicevox | ✅ | ✅ |
| `gpt4o-stt-small` | `small` | voicevox | ✅ | ✅ |
| `gpt4o-tts-openai` | large-v3 | openai | ❌ 404（§6.1） | – |

プロファイル差分は STT モデル名（および TTS impl）のみ。

---

## 3. 結果サマリ（warmup 除外・非 warmup ターン平均, 単位=秒）

| STT / デバイス | STT | LLM_ttft | LLM_tot | TTSsyn | **e2e first-audio** | timeout |
|---|---:|---:|---:|---:|---:|---:|
| `large-v3` / CPU | 24.51 | 1.28 | 2.51 | 0.47 | **27.31** | 3 |
| `medium` / CPU | 12.87 | 1.15 | 2.09 | 0.68 | **15.61** | 0 |
| `medium` / **GPU** | **0.41** | 1.38 | 2.45 | 0.96 | **3.75** | 0 |
| `small` / CPU | 3.85 | 1.03 | 1.98 | 0.72 | **6.52** | 0 |
| `small` / **GPU** | **0.20** | 1.38 | 2.70 | 0.78 | **3.62** | 0 |

**GPU 効果（STT / e2e）**: `medium` 12.87→0.41 s（−97%）/ 15.61→3.75 s（−76%）、`small` 3.85→0.20 s（−95%）/ 6.52→3.62 s（−44%）。

> GPU 化後は e2e に占める STT が誤差レベル（0.2–0.4 s）になり、`medium`(3.75) と `small`(3.62) の e2e 差はほぼ消える。e2e の残りは LLM TTFT(~1.4 s) + TTS synth(~0.8–1.0 s) + 音声処理 overhead。

---

## 4. fixture 別内訳

### CPU 実行

#### 4.1 `large-v3` / CPU（profile `gpt4o`）— `logs/2026-06-09-100406-…-gpt4o`

| fixture | 発話内容 | audio | STT | LLM_tot | TTSsyn | e2eAud | tools |
|---|---|---:|---:|---:|---:|---:|---:|
| fx_01 | おはよう | 1.16 | 23.52 | 1.38 | 0.34 | 25.20 | 0 |
| fx_02 | 自己紹介して | 2.08 | 24.12 | 1.57 | 0.46 | 26.11 | 0 |
| fx_03 | ありがとう | 1.30 | 23.83 | 1.49 | 0.38 | 25.66 | 0 |
| fx_04 | 立ち上がって挨拶 | 2.50 | 24.98 | 2.84 | 0.48 | 28.27 | 2 |
| fx_05 | お座りしてよろしく | 2.27 | 24.48 | 2.65 | 0.30 | 27.37 | 1 |
| fx_06 | 踊って感想 | 2.85 | 25.12 | 3.78 | 0.41 | 29.28 | 2 |
| fx_07 | 今何時 | 1.75 | 24.21 | 2.61 | 0.77 | 27.57 | 1 |
| fx_08 | 1m前進して報告 | 3.75 | 25.50 | 4.31 | 0.71 | 28.97 | 2 |
| fx_09 | 伏せて | 0.99 | 24.39 | 2.41 | 0.28 | 27.05 | 1 |
| fx_10 | 予定3つ提案 | 2.33 | 24.99 | 2.12 | 0.57 | 27.63 | 0 |
| **平均** | | 2.10 | **24.51** | 2.51 | 0.47 | **27.31** | |

timeout: `fx_08` run0/1/2（3 件。STT ~25.5 s + 逐次応答で idle 30 s ゲート超過）

#### 4.2 `medium` / CPU（profile `gpt4o-stt-medium`）— `logs/2026-06-09-132935-…`

平均: STT **12.87** / LLM_tot 2.09 / TTSsyn 0.68 / e2eAud **15.61**（timeout 0）。fixture 別は STT が 12.3–13.4 s でほぼ一定。

#### 4.3 `small` / CPU（profile `gpt4o-stt-small`）— `logs/2026-06-09-134649-…`

平均: STT **3.85** / LLM_tot 1.98 / TTSsyn 0.72 / e2eAud **6.52**（timeout 0）。fixture 別は STT が 3.65–4.12 s。

### GPU 実行

#### 4.4 `medium` / GPU（profile `gpt4o-stt-medium`）— `logs/2026-06-11-105144-…`

| fixture | 発話内容 | audio | STT | LLM_tot | TTSsyn | e2eAud | tools |
|---|---|---:|---:|---:|---:|---:|---:|
| fx_01 | おはよう | 1.16 | 0.31 | 1.88 | 2.14 | 4.28 | 0 |
| fx_02 | 自己紹介して | 2.08 | 0.37 | 1.77 | 0.42 | 2.31 | 0 |
| fx_03 | ありがとう | 1.30 | 0.28 | 1.61 | 2.58 | 4.42 | 0 |
| fx_04 | 立ち上がって挨拶 | 2.50 | 0.49 | 2.88 | 0.55 | 3.89 | 2 |
| fx_05 | お座りしてよろしく | 2.27 | 0.44 | 3.07 | 0.32 | 3.89 | 2 |
| fx_06 | 踊って感想 | 2.85 | 0.48 | 2.77 | 0.38 | 3.59 | 1 |
| fx_07 | 今何時 | 1.75 | 0.36 | 3.09 | 0.67 | 4.09 | 1 |
| fx_08 | 1m前進して報告 | 3.75 | 0.59 | 2.26 | 1.09 | 3.91 | 1 |
| fx_09 | 伏せて | 0.99 | 0.30 | 2.77 | 0.50 | 3.52 | 1 |
| fx_10 | 予定3つ提案 | 2.33 | 0.48 | 2.36 | 0.98 | 3.56 | 0 |
| **平均** | | 2.10 | **0.41** | 2.45 | 0.96 | **3.75** | |

timeout: なし（TTSsyn 平均が CPU より高めなのは fx_01/fx_03 の初回 VOICEVOX 合成 2.1–2.6 s の外れ値による）

#### 4.5 `small` / GPU（profile `gpt4o-stt-small`）— `logs/2026-06-11-110125-…`

| fixture | 発話内容 | audio | STT | LLM_tot | TTSsyn | e2eAud | tools |
|---|---|---:|---:|---:|---:|---:|---:|
| fx_01 | おはよう | 1.16 | 0.17 | 1.83 | 0.39 | 2.34 | 0 |
| fx_02 | 自己紹介して | 2.08 | 0.16 | 1.44 | 0.77 | 2.34 | 0 |
| fx_03 | ありがとう | 1.30 | 0.14 | 1.56 | 3.09 | 4.73 | 0 |
| fx_04 | 立ち上がって挨拶 | 2.50 | 0.26 | 3.91 | 0.41 | 4.33 | 2 |
| fx_05 | お座りしてよろしく | 2.27 | 0.22 | 2.80 | 0.31 | 3.30 | 1 |
| fx_06 | 踊って感想 | 2.85 | 0.25 | 4.20 | 0.34 | 4.76 | 2 |
| fx_07 | 今何時 | 1.75 | 0.16 | 2.83 | 0.47 | 3.43 | 1 |
| fx_08 | 1m前進して報告 | 3.75 | 0.30 | 2.91 | 0.73 | 3.88 | 1 |
| fx_09 | 伏せて | 0.99 | 0.13 | 2.86 | 0.51 | 3.47 | 1 |
| fx_10 | 予定3つ提案 | 2.33 | 0.25 | 2.64 | 0.80 | 3.64 | 0 |
| **平均** | | 2.10 | **0.20** | 2.70 | 0.78 | **3.62** | |

timeout: なし

### 列の定義

- `audio` — fixture wav の長さ / `STT` — Whisper 文字起こし時間
- `LLM_tot` — LLM フェーズ全体（`turn_done.llm_s`） / `TTSsyn` — `speak_invoke`→最初の音声出力（VOICEVOX synth）
- `e2eAud` — マイク発話終了→最初の音声出力（体感レイテンシ） / `tools` — そのターンの tool 呼び出し回数

---

## 5. 考察

1. **CPU では STT が支配的**。発話長にほぼ依存せず一定（`large-v3` ~24.5 / `medium` ~12.9 / `small` ~3.9 s）で、e2e の差は STT の差にほぼ等しい。
2. **GPU で STT が劇的に高速化**し、律速から外れる（`medium` 0.41 s / `small` 0.20 s）。これにより e2e first-audio は `medium` 3.75 s / `small` 3.62 s まで短縮。
3. **GPU では model サイズが e2e にほぼ効かない**。STT が誤差レベルになるため `medium`(3.75) と `small`(3.62) の e2e はほぼ同等。**精度で有利な `medium` を latency 無犠牲で採れる**のが実務上の結論。
4. **GPU STT 後の e2e 主成分は LLM TTFT(~1.4 s) と TTS synth(~0.8–1.0 s)**。さらなる短縮はこの 2 つ（ローカル LLM 化 / TTS streaming）が対象。
5. **TTS（VOICEVOX）の TTSsyn は ~0.5–1.0 s**。GPU 計測でターン先頭（fx_01/fx_03）の初回合成が 2–3 s に跳ねる外れ値があり、平均をやや押し上げている（ウォームアップ的挙動）。
6. **timeout は CPU `large-v3` のみ**（STT 律速で idle 30 s ゲート超過）。GPU 化・小モデル化で解消。
7. **速度↔精度トレードオフ（要追加計測）**。GPU なら `medium` が latency 的に最適だが、`small` との認識精度差は未評価（§6）。

---

## 6. 制約・未計測事項（今後）

- **精度（CER）未評価**: 本計測は速度のみ。bench ログは `text_len` のみで文字起こし全文を保存しないため、accuracy 比較は別ハーネスが必要。GPU で `medium` と `small` の latency 差が消えた今、**選定軸は精度に移る**。
- **`large-v3` の GPU 計測は不可**: 6 GB VRAM 制約で OOM。より大きい VRAM の GPU があれば測定価値あり。
- **サンプル数**: 各条件 1 run（30 ターン, warmup 除く）。run 間ばらつきは未評価（`medium`/CPU のみ 2 run でほぼ一致を確認）。

### 6.1 OpenAI TTS 実行不可（負の結果）

`gpt4o-tts-openai`（`impl: openai`, `openai_model: tts-1`）を実行したが **音声は一切出力されなかった**。`OpenAITTSNode`（`node_openai.py:78`）は base_url を渡さず env の `OPENAI_*` を参照するため、bench の `endpoint: cloud` ミラーにより **gpt-4o LLM と同じ Azure エンドポイント**を指す。そのリソースに TTS デプロイが無く `speak` ごとに `404 DeploymentNotFound`（最新 run で 84 回）。bench は per-`speak` 例外を握りつぶすため **30 ターン完走・exit 0 だが `first_audio_out` 0 件**（音声なし）。**「完走」≠「TTS 成功」**。計測には (a) Azure に TTS デプロイ追加 + `openai_model` をデプロイ名に、または (b) `speak_skill_ja.py` を改修し別 env（`DIMOS_TTS_OPENAI_*`）で本家 OpenAI を指す、が必要。

---

## 7. 再現手順

```bash
# VOICEVOX エンジン起動（gpt4o 系プロファイルは :50021 必須）
sudo docker run --rm -d -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu22.04-latest

# --- CPU 実行（large-v3 は CPU 専用。GPU だと 6GB OOM）---
CUDA_VISIBLE_DEVICES="" .venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o --bench scripts/bench_configs/agentic_ja.yaml

# --- GPU 実行（env を付けない。medium / small は 6GB に収まる）---
.venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o-stt-medium --bench scripts/bench_configs/agentic_ja.yaml

.venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o-stt-small --bench scripts/bench_configs/agentic_ja.yaml
```

集計: `python /tmp/bench_summarize.py <run_dir>`（`main.jsonl` の `t` 差分から算出）。

---

## 8. 参照ログ

| 条件 | run ディレクトリ |
|---|---|
| `large-v3` / voicevox / CPU | `logs/2026-06-09-100406-unitree-go2-agentic-local-tts-detection-gpt4o` |
| `medium` / voicevox / CPU | `logs/2026-06-09-132935-unitree-go2-agentic-local-tts-detection-gpt4o-stt-medium` |
| `medium` / voicevox / **GPU** | `logs/2026-06-11-105144-unitree-go2-agentic-local-tts-detection-gpt4o-stt-medium` |
| `small` / voicevox / CPU | `logs/2026-06-09-134649-unitree-go2-agentic-local-tts-detection-gpt4o-stt-small` |
| `small` / voicevox / **GPU** | `logs/2026-06-11-110125-unitree-go2-agentic-local-tts-detection-gpt4o-stt-small` |
| `openai` TTS（❌ 404, 音声なし） | `logs/2026-06-10-031540-unitree-go2-agentic-local-tts-detection-gpt4o-tts-openai` |

各 run ディレクトリに `bench.yaml` / `profile_config.json` / `resolved_config.json` / `main.jsonl` が同梱される。
