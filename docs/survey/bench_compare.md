# STT/TTS ベンチマーク比較レポート

`unitree-go2-agentic-local-tts-detection` を `scripts/bench_llm.py` でリプレイ計測し、
**STT モデル（Whisper `large-v3` vs `medium`）** のレイテンシを比較した記録。
TTS（VOICEVOX）・LLM（gpt-4o, cloud）は固定。

> [!summary] 結論（BLUF）
> - **STT が e2e レイテンシの律速**。`large-v3`（CPU）では 1 ターンの「マイク発話終了 → 最初の音声出力」**27.3 s** のうち **24.5 s（約 90%）が STT**。
> - STT を **`large-v3` → `medium`** に下げると **STT 24.5 s → 13.0 s（約 47% 減）**、e2e first-audio も **27.3 s → 15.6 s** に短縮。
> - **TTS（VOICEVOX）は ~0.5 s で律速ではない**。LLM（gpt-4o）も TTFT ~1.3 s、全体 ~2.5 s と十分速い。
> - `large-v3` 側は `fx_08`（逐次 move+speak）で idle 30 s ゲートに 3 回掛かった。`medium` 側は 0 件。
> - **本計測は STT の「速度」のみ**。精度（CER 等）は ground-truth を取っていないため未評価。

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
> `large-v3` は 6 GB VRAM にロボットスタック一式と同居できず CUDA OOM になるため CPU 実行。`medium` の計測も STT が ~13 s かかっており、GPU 実行（数秒未満が期待値）ではなく **CPU 実行**だったと判断できる。したがって本レポートの 2 条件は **いずれも torch=CPU** での比較である。GPU STT のレイテンシは別途未計測（→ §6）。

---

## 2. 比較対象（プロファイル）

| プロファイル | STT モデル | fp16 | TTS | 状態 |
|---|---|---|---|---|
| `gpt4o` | `large-v3` | true | voicevox | ✅ 計測済み |
| `gpt4o-stt-medium` | `medium` | true | voicevox | ✅ 計測済み |
| `gpt4o-stt-small` | `small` | true | voicevox | ⬜ 未計測 |
| （TTS `openai`） | – | – | openai | ⬜ 未計測 |

差分は STT モデル名のみ（他フィールドは `gpt4o.json` と同一）。

---

## 3. 結果サマリ（warmup 除外・非 warmup ターン平均, 単位=秒）

| 指標 | `large-v3`（CPU） | `medium`（CPU） | 差分 |
|---|---:|---:|---:|
| STT（Whisper 文字起こし） | **24.51** | **12.98** | **−47%** |
| LLM TTFT（STT完了→初トークン） | 1.28 | 1.22 | ≒ |
| LLM 全体（`turn_done.llm_s`） | 2.51 | 2.04 | −19% |
| TTS synth（speak→初音声, VOICEVOX） | 0.47 | 0.63 | +0.16 |
| **e2e first-audio（発話終了→初音声）** | **27.31** | **15.62** | **−43%** |
| ターン全体（`turn_done`） | 2.55 | 2.05 | −20% |
| turn_timeout 件数 | 3（全 `fx_08`） | 0 | −3 |

> e2e first-audio の差（−11.7 s）はほぼ STT の差（−11.5 s）に一致。**STT がそのまま体感レイテンシに乗る**ことを示す。

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

ログ: `logs/2026-06-09-131307-unitree-go2-agentic-local-tts-detection-gpt4o-stt-medium`

| fixture | 発話内容 | audio | STT | LLM_tot | TTSsyn | e2eAud | tools |
|---|---|---:|---:|---:|---:|---:|---:|
| fx_01 | おはよう | 1.16 | 12.46 | 1.50 | 0.40 | 14.33 | 0 |
| fx_02 | 自己紹介して | 2.08 | 12.77 | 1.48 | 0.43 | 14.66 | 0 |
| fx_03 | ありがとう | 1.30 | 12.59 | 1.42 | 0.46 | 14.42 | 0 |
| fx_04 | 立ち上がって挨拶 | 2.50 | 13.30 | 2.58 | 0.54 | 16.37 | 2 |
| fx_05 | お座りしてよろしく | 2.27 | 13.05 | 2.58 | 0.86 | 16.45 | 2 |
| fx_06 | 踊って感想 | 2.85 | 13.35 | 2.05 | 0.44 | 15.80 | 1 |
| fx_07 | 今何時 | 1.75 | 13.03 | 2.30 | 0.80 | 16.12 | 1 |
| fx_08 | 1m前進して報告 | 3.75 | 13.63 | 2.52 | 0.98 | 17.10 | 1 |
| fx_09 | 伏せて | 0.99 | 12.38 | 2.19 | 0.39 | 14.93 | 1 |
| fx_10 | 予定3つ提案 | 2.33 | 13.24 | 1.79 | 1.00 | 15.99 | 0 |
| **平均** | | 2.10 | **12.98** | 2.04 | 0.63 | **15.62** | |

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

1. **STT が支配的**。STT は発話長（0.99–3.75 s）にほぼ依存せず一定（`large-v3` ~24.5 s / `medium` ~13.0 s）で、CPU 実行の推論固定コストが効いている。e2e first-audio の差は STT の差にほぼ等しい。
2. **`medium` で STT が約半減**。精度要件が許すなら STT 律速の改善効果が最大。
3. **TTS（VOICEVOX）は ~0.5 s** で常に非律速。`medium` 側で TTSsyn がわずかに大きい（0.63 vs 0.47）が、ターン依存のばらつき範囲で有意ではない。
4. **LLM は両条件で同等**（cloud gpt-4o, TTFT ~1.3 s）。STT モデルを変えても当然 LLM は不変。
5. **`large-v3` の `fx_08` timeout**。STT ~25.5 s + 逐次 move→report の応答で bench の idle 30 s ゲートを超過（3 run とも）。`medium` では STT 短縮により発生せず。計測上のアーティファクトであり、エージェントの失敗ではない。

---

## 6. 制約・未計測事項（今後）

- **精度（CER）未評価**: 本計測は速度のみ。fixtures には期待 `text` があるが、bench ログは `text_len` のみで文字起こし全文を保存しないため、accuracy 比較は別途ハーネスが必要。
- **GPU STT 未計測**: 6 GB VRAM の制約で `large-v3` は CPU 固定。`medium`/`small` は GPU に載る（GPU 実行なら STT は数秒未満が期待される）が、本計測は CPU。GPU 実行での再計測が STT 高速化の本命。
- **TTS バックエンド比較未実施**: TTS impl は `voicevox` / `openai` の 2 択（`speak_skill_ja.py`）。本レポートは voicevox 固定。`openai` TTS は OpenAI 互換 TTS エンドポイントの資格情報が必要で未検証。
- **`small` モデル未計測**: `gpt4o-stt-small.json` は用意済みだが未実行。
- **サンプル数**: 各条件 1 run（30 ターン, warmup 除く）。run 間ばらつきは未評価。

---

## 7. 再現手順

```bash
# VOICEVOX エンジンを起動（gpt4o 系プロファイルは :50021 必須）
sudo docker run --rm -d -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu22.04-latest

# large-v3（CPU 固定。GPU だと 6GB で OOM する）
CUDA_VISIBLE_DEVICES="" .venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o --bench scripts/bench_configs/agentic_ja.yaml

# medium
CUDA_VISIBLE_DEVICES="" .venv/bin/python scripts/bench_llm.py \
    unitree-go2-agentic-local-tts-detection \
    --profile gpt4o-stt-medium --bench scripts/bench_configs/agentic_ja.yaml
```

プロファイル（`configs/profiles/`）の差分は STT モデル名のみ:

```jsonc
// gpt4o-stt-medium.json（gpt4o.json との差分）
"whisperhumaninputja": { "model": "medium", "fp16": true }
```

---

## 8. 参照ログ

| 条件 | run ディレクトリ |
|---|---|
| `large-v3` / voicevox / CPU | `logs/2026-06-09-100406-unitree-go2-agentic-local-tts-detection-gpt4o` |
| `medium` / voicevox / CPU | `logs/2026-06-09-131307-unitree-go2-agentic-local-tts-detection-gpt4o-stt-medium` |

各 run ディレクトリに `bench.yaml` / `profile_config.json` / `resolved_config.json` / `main.jsonl`（イベントログ）が同梱される。集計は `main.jsonl` の `t`（単調時刻）差分から算出。
