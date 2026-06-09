# STT/TTS ベンチマーク比較レポート

`unitree-go2-agentic-local-tts-detection` を `scripts/bench_llm.py` でリプレイ計測し、
**STT モデル（Whisper `large-v3` / `medium` / `small`）** のレイテンシを比較した記録。
TTS（VOICEVOX）・LLM（gpt-4o, cloud）は固定。

> [!summary] 結論（BLUF）
> - **STT が e2e レイテンシの律速**。`large-v3`（CPU）では 1 ターンの「マイク発話終了 → 最初の音声出力」**27.3 s** のうち **24.5 s（約 90%）が STT**。
> - STT モデルを小さくすると STT・e2e が大きく短縮（いずれも CPU 実行）:
>   | STT | STT 時間 | e2e first-audio |
>   |---|---:|---:|
>   | `large-v3` | 24.5 s | 27.3 s |
>   | `medium` | 12.9 s（−47%） | 15.6 s（−43%） |
>   | `small` | 3.9 s（−84%） | 6.5 s（−76%） |
> - **TTS（VOICEVOX）は ~0.5–0.7 s で律速ではない**。LLM（gpt-4o）も TTFT ~1.1–1.3 s、全体 ~2.0–2.5 s と十分速い。
> - `large-v3` のみ `fx_08`（逐次 move+speak）で idle 30 s ゲートに 3 回掛かった。`medium`/`small` は 0 件。
> - **本計測は STT の「速度」のみ**。精度（CER 等）は ground-truth を取っていないため未評価（小モデルほど精度低下しうる点に留意）。

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
| マシン | Dell Precision 3240 / NVIDIA Quadro RTX 3000 Mobile（6 GB VRAM）/ NVIDIA driver 595（CUDA） |
| torch 実行デバイス | **CPU**（`CUDA_VISIBLE_DEVICES=""`）※下記注記参照 |

> [!note] デバイスについて
> `large-v3` は 6 GB VRAM にロボットスタック一式と同居できず CUDA OOM になるため CPU 実行。`medium`/`small` の計測も STT が数〜十数秒かかっており、GPU 実行（数秒未満が期待値）ではなく **CPU 実行**だったと判断できる。したがって本レポートの 3 条件は **いずれも torch=CPU** での比較である。GPU STT のレイテンシは別途未計測（→ §6）。

---

## 2. 比較対象（プロファイル）

| プロファイル | STT モデル | fp16 | TTS | 状態 |
|---|---|---|---|---|
| `gpt4o` | `large-v3` | true | voicevox | ✅ 計測済み |
| `gpt4o-stt-medium` | `medium` | true | voicevox | ✅ 計測済み |
| `gpt4o-stt-small` | `small` | true | voicevox | ✅ 計測済み |
| `gpt4o-tts-openai` | large-v3 | true | openai | ❌ 失敗（Azure に TTS デプロイ無し / 404）→ §6.1 |

差分は STT モデル名のみ（他フィールドは `gpt4o.json` と同一）。

---

## 3. 結果サマリ（warmup 除外・非 warmup ターン平均, 単位=秒）

| 指標 | `large-v3`（CPU） | `medium`（CPU） | `small`（CPU） |
|---|---:|---:|---:|
| STT（Whisper 文字起こし） | **24.51** | **12.87** | **3.85** |
| LLM TTFT（STT完了→初トークン） | 1.28 | 1.15 | 1.03 |
| LLM 全体（`turn_done.llm_s`） | 2.51 | 2.09 | 1.98 |
| TTS synth（speak→初音声, VOICEVOX） | 0.47 | 0.68 | 0.72 |
| **e2e first-audio（発話終了→初音声）** | **27.31** | **15.61** | **6.52** |
| ターン全体（`turn_done`） | 2.55 | 2.10 | 1.99 |
| turn_timeout 件数 | 3（全 `fx_08`） | 0 | 0 |

> e2e first-audio の差はほぼ STT の差に一致（`large-v3`→`medium` で e2e −11.7 s / STT −11.6 s、`medium`→`small` で e2e −9.1 s / STT −9.0 s）。**STT がそのまま体感レイテンシに乗る**ことを示す。

---

## 4. fixture 別内訳

### 4.1 `large-v3`（profile `gpt4o`）

ログ: `logs/2026-06-09-100406-unitree-go2-agentic-local-tts-detection-gpt4o`

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

timeout: `fx_08` run0/1/2（3 件）

### 4.2 `medium`（profile `gpt4o-stt-medium`）

ログ: `logs/2026-06-09-132935-unitree-go2-agentic-local-tts-detection-gpt4o-stt-medium`（別 run `131307` も STT 12.98 / e2e 15.62 とほぼ一致）

| fixture | 発話内容 | audio | STT | LLM_tot | TTSsyn | e2eAud | tools |
|---|---|---:|---:|---:|---:|---:|---:|
| fx_01 | おはよう | 1.16 | 13.11 | 1.40 | 0.40 | 14.87 | 0 |
| fx_02 | 自己紹介して | 2.08 | 12.42 | 1.65 | 0.44 | 14.48 | 0 |
| fx_03 | ありがとう | 1.30 | 13.11 | 1.38 | 1.05 | 15.49 | 0 |
| fx_04 | 立ち上がって挨拶 | 2.50 | 13.17 | 2.33 | 0.53 | 15.98 | 2 |
| fx_05 | お座りしてよろしく | 2.27 | 13.06 | 2.26 | 0.99 | 16.38 | 2 |
| fx_06 | 踊って感想 | 2.85 | 12.85 | 2.90 | 0.36 | 16.07 | 1 |
| fx_07 | 今何時 | 1.75 | 12.34 | 1.94 | 0.57 | 14.80 | 1 |
| fx_08 | 1m前進して報告 | 3.75 | 13.25 | 2.33 | 0.75 | 16.29 | 1 |
| fx_09 | 伏せて | 0.99 | 12.26 | 2.56 | 0.40 | 15.18 | 1 |
| fx_10 | 予定3つ提案 | 2.33 | 13.15 | 2.13 | 1.28 | 16.52 | 0 |
| **平均** | | 2.10 | **12.87** | 2.09 | 0.68 | **15.61** | |

timeout: なし

### 4.3 `small`（profile `gpt4o-stt-small`）

ログ: `logs/2026-06-09-134649-unitree-go2-agentic-local-tts-detection-gpt4o-stt-small`

| fixture | 発話内容 | audio | STT | LLM_tot | TTSsyn | e2eAud | tools |
|---|---|---:|---:|---:|---:|---:|---:|
| fx_01 | おはよう | 1.16 | 3.72 | 1.37 | 0.41 | 5.45 | 0 |
| fx_02 | 自己紹介して | 2.08 | 3.78 | 1.85 | 0.47 | 6.04 | 0 |
| fx_03 | ありがとう | 1.30 | 3.65 | 0.96 | 1.61 | 6.19 | 0 |
| fx_04 | 立ち上がって挨拶 | 2.50 | 3.99 | 2.38 | 0.55 | 6.89 | 1 |
| fx_05 | お座りしてよろしく | 2.27 | 3.88 | 1.72 | 0.53 | 6.09 | 1 |
| fx_06 | 踊って感想 | 2.85 | 3.99 | 3.18 | 0.47 | 7.61 | 2 |
| fx_07 | 今何時 | 1.75 | 3.77 | 1.79 | 0.84 | 6.35 | 1 |
| fx_08 | 1m前進して報告 | 3.75 | 4.12 | 2.06 | 0.46 | 6.61 | 1 |
| fx_09 | 伏せて | 0.99 | 3.66 | 2.47 | 0.86 | 6.96 | 2 |
| fx_10 | 予定3つ提案 | 2.33 | 3.95 | 2.05 | 1.02 | 6.98 | 0 |
| **平均** | | 2.10 | **3.85** | 1.98 | 0.72 | **6.52** | |

timeout: なし

### 列の定義

- `audio` — fixture wav の長さ
- `STT` — Whisper 文字起こし時間
- `LLM_tot` — LLM フェーズ全体（`turn_done.llm_s`）
- `TTSsyn` — `speak_invoke` → 最初の音声出力（VOICEVOX synth レイテンシ）
- `e2eAud` — マイク発話終了 → 最初の音声出力（体感レイテンシ）
- `tools` — そのターンの tool 呼び出し回数

---

## 5. 考察

1. **STT が支配的**。STT は発話長（0.99–3.75 s）にほぼ依存せず一定（`large-v3` ~24.5 s / `medium` ~12.9 s / `small` ~3.9 s）で、CPU 実行の推論固定コストが効いている。e2e first-audio の差は STT の差にほぼ等しい。
2. **モデルを下げるほど STT 律速が改善**。`large-v3`→`medium` で STT −47%、`medium`→`small` でさらに −70%（`large-v3` 比 −84%）。`small` では e2e first-audio が 6.5 s まで下がり、実用的な体感速度に近づく。
3. **TTS（VOICEVOX）は ~0.5–0.7 s** で常に非律速。小モデルほど TTSsyn がわずかに大きく見えるが、ターン依存のばらつき範囲で有意ではない。
4. **LLM は全条件で同等**（cloud gpt-4o, TTFT ~1.1–1.3 s）。STT モデルを変えても LLM は不変。
5. **`large-v3` の `fx_08` timeout**。STT ~25.5 s + 逐次 move→report の応答で bench の idle 30 s ゲートを超過（3 run とも）。`medium`/`small` では STT 短縮により発生せず。計測上のアーティファクトであり、エージェントの失敗ではない。
6. **速度↔精度トレードオフ（要追加計測）**。本ベンチは速度のみで、小モデルの日本語認識精度低下は未評価。速度だけ見れば `small` が圧倒的だが、誤認識が tool 選択を誤らせる可能性があり、精度比較（§6）が判断に必須。

---

## 6. 制約・未計測事項（今後）

- **精度（CER）未評価**: 本計測は速度のみ。fixtures には期待 `text` があるが、bench ログは `text_len` のみで文字起こし全文を保存しないため、accuracy 比較は別途ハーネスが必要。小モデルほど速いが精度は要確認。
- **GPU STT 未計測**: 6 GB VRAM の制約で `large-v3` は CPU 固定。`medium`/`small` は GPU に載る（GPU 実行なら STT は数秒未満が期待される）が、本計測は CPU。GPU 実行での再計測が STT 高速化の本命。
- **TTS バックエンド比較は `openai` 側が実行不可（§6.1）**: TTS impl は `voicevox` / `openai` の 2 択（`speak_skill_ja.py`）。本レポートのレイテンシ計測はすべて voicevox。`openai` は下記理由で音声が出ず、計測できなかった。
- **サンプル数**: 各条件 1 run（30 ターン, warmup 除く）。`medium` のみ 2 run あり、STT/e2e はほぼ一致（run 間ばらつきは小さい）。

### 6.1 OpenAI TTS 実行不可（負の結果）

`gpt4o-tts-openai`（`impl: openai`, `openai_model: tts-1`, `openai_voice: echo`）を実行したが、**音声は一切出力されなかった**。

- `OpenAITTSNode`（`dimos/stream/audio/tts/node_openai.py:78`）は `OpenAI(api_key=None)` で base_url を渡さないため、openai SDK が env の `OPENAI_API_KEY`/`OPENAI_BASE_URL` を参照する。これは bench の `endpoint: cloud` ミラーにより **gpt-4o LLM と同じ Azure エンドポイント**を指す。
- その Azure リソースには chat デプロイメントはあるが **TTS/speech デプロイメントが無い**ため、`speak` のたびに `404 DeploymentNotFound` が返る（最新 run で 84 回）。
- bench は per-`speak` の TTS 例外を握りつぶして継続するため、**30 ターン完走・exit 0 になるが `first_audio_out` は 0 件**（音声なし）。「完走」≠「TTS 成功」である点に注意。

**計測するには**: (a) Azure リソースに TTS デプロイ（`tts-1` / `gpt-4o-mini-tts` 等）を追加し `openai_model` をそのデプロイ名にする、または (b) fork ファイル `speak_skill_ja.py` の `_make_tts_node` を改修し、LLM とは別の api_key/base_url（例 `DIMOS_TTS_OPENAI_*`）を `OpenAITTSNode` に渡して本家 OpenAI を指す。いずれか未対応の間は TTS 比較は VOICEVOX 内（`voicevox.speaker_id` 等）に限られる。

---

## 7. 再現手順

```bash
# VOICEVOX エンジンを起動（gpt4o 系プロファイルは :50021 必須）
sudo docker run --rm -d -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu22.04-latest

# large-v3（CPU 固定。GPU だと 6GB で OOM する）
CUDA_VISIBLE_DEVICES="" .venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o --bench scripts/bench_configs/agentic_ja.yaml

# medium / small は --profile を差し替えるだけ
CUDA_VISIBLE_DEVICES="" .venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o-stt-medium --bench scripts/bench_configs/agentic_ja.yaml

CUDA_VISIBLE_DEVICES="" .venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o-stt-small --bench scripts/bench_configs/agentic_ja.yaml
```

プロファイル（`configs/profiles/`）の差分は STT モデル名のみ:

```jsonc
// gpt4o-stt-medium.json / gpt4o-stt-small.json（gpt4o.json との差分）
"whisperhumaninputja": { "model": "medium", "fp16": true }   // または "small"
```

---

## 8. 参照ログ

| 条件 | run ディレクトリ |
|---|---|
| `large-v3` / voicevox / CPU | `logs/2026-06-09-100406-unitree-go2-agentic-local-tts-detection-gpt4o` |
| `medium` / voicevox / CPU | `logs/2026-06-09-132935-unitree-go2-agentic-local-tts-detection-gpt4o-stt-medium` |
| `small` / voicevox / CPU | `logs/2026-06-09-134649-unitree-go2-agentic-local-tts-detection-gpt4o-stt-small` |
| `openai` TTS（❌ 404, 音声なし） | `logs/2026-06-10-031540-unitree-go2-agentic-local-tts-detection-gpt4o-tts-openai` |

各 run ディレクトリに `bench.yaml` / `profile_config.json` / `resolved_config.json` / `main.jsonl`（イベントログ）が同梱される。集計は `main.jsonl` の `t`（単調時刻）差分から算出。
