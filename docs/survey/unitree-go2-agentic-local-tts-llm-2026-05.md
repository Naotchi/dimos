# unitree-go2-agentic-local-tts 向け LLM 選定調査（2026-05）

## 0. 対象と前提

- 対象 blueprint: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py`
- 現状: `DIMOS_LLM_MODEL=gpt-4o`（Azure OpenAI）を `TimedMcpClient` 経由で MCP tool calling に使用
- 音声系: STT = ローカル ja-tuned Whisper / TTS = ローカル Style-Bert-VITS2（**固定**）
- ローカル推論ランタイム: **LM Studio**（llama.cpp / MLX バックエンド、OpenAI 互換サーバー内蔵）on DGX Spark（128GB unified memory、Blackwell GB10）
- 量子化形式: **GGUF**（Q4_K_M / Q5_K_M / Q6_K / Q8_0）に統一

vLLM / NVFP4 / SGLang 等の他ランタイムは本書のスコープ外。Blackwell ネイティブの理論性能を引き出したい場合の選択肢として §10 で言及するに留める。

## 1. 要件

| カテゴリ | 要件 | 重要度 |
|---|---|---|
| API | OpenAI 互換 ChatCompletion（`TimedMcpClient.blueprint(model=...)` 無改修） | 必須 |
| ランタイム | LM Studio（llama.cpp / MLX） | 必須 |
| モデル形式 | GGUF（tool calling テンプレート同梱） | 必須 |
| tool calling | MCP 経由のロボット制御、JSON Schema 強制（GBNF）動作 | 必須 |
| 出力形式 | **non-thinking**（`<think>` ブロックは Style-Bert-VITS2 に流入するため致命的） | 必須 |
| 日本語 | TTS 入力としての自然さ、敬体/常体ぶれの少なさ | 重要 |
| latency | TTFT < 500ms、TPOT < 50ms（≒ 20 tok/s 以上、TTS 律速ライン） | 重要 |
| メモリ | DGX Spark 128GB に余裕、ロード時 unified memory ~40GB 以下が望ましい | 重要 |
| ライセンス | Apache 2.0 / MIT 相当が望ましい、NVIDIA / Gemma 系は商用配布時要確認 | 任意 |
| オフライン | 完全断ち可能（クラウド fallback は任意） | 任意 |

## 2. 結論サマリ

| 層 | モデル | 量子化 | 採用理由 |
|---|---|---|---|
| **本命（ローカル default）** | **Qwen3-30B-A3B-Instruct-2507** | Q5_K_M / Q6_K | non-thinking 専用、262K context、MCP / tool calling native、標準 Transformer + MoE で llama.cpp 安定対応、GGUF 多数公開、日本語自然 |
| **対抗（A/B 必須）** | **NVIDIA Nemotron 3 Nano 30B-A3B** | Q5_K_M / Q6_K（公開状況確認） | DGX Spark 製造元純正、日本語が公式サポート言語、qwen3_coder tool parser 互換、`reasoning_budget` で TPOT 制御可能 |
| **対抗（公式デモ機）** | **gpt-oss-120b** | MXFP4 native | DGX Spark 公式デモモデル、Apache 2.0、native function calling、`reasoning_effort=low` で voice 化可能。**日本語自然さは Qwen に劣る見込み** |
| **次期候補** | Qwen3.5-35B-A3B | Q5_K_M | 標準 Transformer + MoE で llama.cpp 安定、知性スコア向上 |
| **軽量 fallback** | Qwen3.5-9B（dense）/ Qwen3-14B（dense）/ gpt-oss-20b | Q6_K / Q8_0 | voice 最低遅延優先時 |
| **クラウド fallback** | gpt-4o-mini / Gemini 2.5 Flash | – | ローカル失敗時の保険、tool calling 同等 |
| **クラウド最高精度** | GPT-5 / Claude Sonnet 4.6 | – | tool 計画が複雑化した時の上振れ |

## 3. 評価軸

| 軸 | 重要度 | 評価基準 |
|---|---|---|
| non-thinking | ★★★ | `<think>` を出さない、もしくは抑止オプションで完全に消せる |
| TTFT / TPOT | ★★★ | TTFT < 500ms、TPOT < 50ms（llama.cpp 実測） |
| tool calling | ★★★ | OpenAI 互換 function calling 動作、JSON Schema 強制、MCP パイプライン互換 |
| 日本語 | ★★★ | Style-Bert-VITS2 入力としての自然さ |
| LM Studio 適合 | ★★★ | GGUF 公開済、chat_template に tools フィールド同梱、llama.cpp アーキ対応 |
| DGX Spark 適合 | ★★ | Q5_K_M 級で 40GB 以下、active param が小さい MoE 優先 |
| ライセンス | ★ | Apache 2.0 / MIT を優先、商用配布制約があれば明記 |

## 4. 候補ファミリ詳細

### 4.1 Qwen 系（Alibaba）

| モデル | 種別 | active/total | 適性 | 備考 |
|---|---|---|---|---|
| **Qwen3-30B-A3B-Instruct-2507** | MoE | 3B / 30B | **◎ 本命** | non-thinking 専用、262K context、tool calling 強化、Qwen-Agent 公式推奨。GGUF: `lmstudio-community` / `unsloth` / `bartowski` から複数版 |
| Qwen3-30B-A3B-Thinking-2507 | MoE | 3B / 30B | ✕ | `<think>` で voice 不可 |
| Qwen3-32B（dense） | dense | 32B | ○ | GGUF 安定、dense で挙動が読みやすい。MoE より TPOT 不利 |
| Qwen3-14B / Qwen3.5-9B（dense） | dense | 同 | ○（軽量） | TPOT 最優先時の fallback |
| Qwen3.5-35B-A3B | MoE | 3B / 35B | ○ 次期 | 標準アーキで llama.cpp 安定、知性スコア向上、GGUF 公開済 |
| Qwen3.6-35B-A3B | MoE | 3B / 35B | △ 保留 | Gated DeltaNet + Gated Attention で **llama.cpp 対応不安定**。安定化次第で次期候補に昇格 |
| Qwen3-Next-80B-A3B | MoE+SSM | 3B / 80B | △ 保留 | hybrid SSM、llama.cpp 対応 PR 進行中 |
| Qwen3-Coder-30B-A3B-Instruct | MoE | 3B / 30B | △ | コード分布訓練、日本語自然さで Instruct-2507 に劣る |
| Qwen3-VL-* | – | – | ✕ | LM Studio multimodal 限定、vision encoder の memory / TTFT が無駄 |
| Qwen3-Omni-30B-A3B-Instruct | MoE | 3B / 30B | ✕（別 blueprint） | S2S 一体、Style-Bert-VITS2 固定方針と矛盾。LM Studio の audio I/O 対応も未確認 |
| Qwen3-235B / Qwen3.5-122B-A10B 以上 | – | – | ✕ | DGX Spark 不可、ないし Q4 必須でギリ |

### 4.2 NVIDIA Nemotron 3 系

| モデル | アーキ | active/total | 適性 | 備考 |
|---|---|---|---|---|
| **Nemotron 3 Nano 30B-A3B** | Mamba-2 + Transformer + MoE hybrid | 3.2B / 31.6B | **◎ 対抗** | DGX Spark 純正、日本語公式サポート（en/de/es/fr/it/ja）、qwen3_coder tool parser 互換、`reasoning_budget` で thinking 長制御 |
| Nemotron 3 Super 120B | hybrid + latent MoE + MTP | 12B / 120B | △ | DGX Spark でギリ、量子化必須 |
| Nemotron 3 Nano Omni 30B-A3B | hybrid + audio/vision encoder | 3B / 30B | ✕（別 blueprint） | Parakeet-TDT-0.6B-v2 audio encoder 内蔵、S2S 候補。本 blueprint の固定 TTS 方針と不整合 |
| Nemotron 3 Ultra | – | – | ✕ | DGX Spark 不可 |

**重要な留保**: Nemotron 3 Nano は Mamba-2 hybrid のため llama.cpp / LM Studio での **GGUF 公開状況と動作安定性を Step 2 直前に必ず確認**する。NVIDIA Open Model License は Apache 2.0 ではないため、配布形態によっては商用利用条件を要確認。

### 4.3 OpenAI gpt-oss 系

| モデル | active/total | アーキ | 適性 | 備考 |
|---|---|---|---|---|
| **gpt-oss-120b** | 5.1B / 120B | MXFP4 native | **◎ 対抗** | DGX Spark 公式デモ機、Apache 2.0、native function calling、harmony フォーマット、`reasoning_effort=low/medium/high` 切替可（voice では `low` 固定）。日本語は英語特化のため Qwen 劣後想定 |
| gpt-oss-20b | 3.6B / 20B | MXFP4 | ○（軽量） | 16GB 級で動く voice 軽量枠、DGX Spark のメモリは余す |

留意点: `reasoning_effort=low` でも先頭に簡易な reasoning が出る場合がある。**TTS 流入対策にチャンク投入側で `<analysis>` 等のメタ区間を捨てる処理が必要かを実機確認**する。

### 4.4 Z.ai / Zhipu GLM 系

| モデル | active/total | ライセンス | 適性 | 備考 |
|---|---|---|---|---|
| **GLM-4.5-Air** | 12B / 106B | MIT | △ | BFCL v3 で Gemini 2.5 Pro 超、tool 強い。**hybrid thinking なので thinking OFF 運用必須**。DGX Spark でメモリ・TPOT ともギリ |
| GLM-4.5 / 4.6 / 5 / 5.1 | 22B〜40B / 355B〜744B | MIT | ✕ | DGX Spark 不可 |
| GLM-4.6V-Flash | 9B dense | MIT | ○（vision 込） | voice 専用なら vision 分が無駄 |

### 4.5 その他

- **DeepSeek V3.2 / V4 系**: DGX Spark 不可、または Q4 必須。V4 の hallucination 率が高い報告（知らない事柄も自信を持って答える傾向）。voice loop で誤情報を発話するリスクが許容できないため候補から外す。
- **Mistral Small 3.2 (24B dense)**: 関数呼び出し改善、Apache 2.0。日本語が弱いため不採用。
- **Llama 3.3 70B / Llama 4 Scout**: ライセンス（700M MAU 制限）と日本語品質で Qwen / Nemotron 劣後。
- **Gemma 4 (9B / 26B-MoE)**: native function calling、audio 系も対応するが Gemma ライセンスは fine-tune 配布で曖昧、日本語は Qwen 劣後。
- **Kimi K2 系**: 1T 級、DGX Spark 不可。
- **MiniMax M2 / Hunyuan 3 / SmolLM3 / Phi-4-mini**: スケールまたは能力面で要件外。
- **Llama 3.1 Swallow（東工大）**: 日本語特化だが base が古い（Llama 3.1）。新世代の Qwen / Nemotron で代替可能。

## 5. 総合評価

**注意**: tok/s 表記は llama.cpp 実測 / 推定の混在で参考値。LM Studio (llama.cpp) は vLLM (NVFP4) より 30–50% 遅い傾向のため、**Step 2 で必ず実機 A/B 計測**する。

### S 評価（即採用候補）

| モデル | TPOT 想定 | tool | 日本語 | LM Studio | 総合 |
|---|---|---|---|---|---|
| Qwen3-30B-A3B-Instruct-2507 | 30–45 tok/s | ◎ | ◎ | ◎ GGUF 多数 | **default 最適解** |
| Nemotron 3 Nano 30B-A3B | 想定 35–50 tok/s | ○ Qwen 互換 | ◎ 公式 | △ GGUF / 動作要確認 | **対抗、要 A/B** |
| gpt-oss-120b | 30–40 tok/s | ◎ harmony | △ 英語特化 | ◎ DGX Spark 公式 | **対抗、日本語次第** |

### A 評価（次期 / 軽量候補）

| モデル | 強み | 弱み |
|---|---|---|
| Qwen3.5-35B-A3B | 知性向上、llama.cpp 安定 | TPOT 微低 |
| Qwen3-32B（dense） | dense で挙動安定、GGUF 安定 | TPOT 不利 |
| gpt-oss-20b | 軽量、Apache 2.0 | DGX Spark メモリ余る、日本語劣後 |
| Qwen3.5-9B / Qwen3-14B（dense） | TPOT 最速、低遅延 voice 向き | 複雑な tool 計画では精度不足 |
| GLM-4.5-Air | tool 強い、MIT | thinking OFF 必須、メモリギリ |

### B 評価（保留・将来枠）

| モデル | 用途 |
|---|---|
| Qwen3.6-35B-A3B | llama.cpp 対応安定化を待って復帰 |
| Qwen3-Next-80B-A3B | hybrid SSM の llama.cpp 対応待ち |
| Nemotron 3 Nano Omni / Qwen3-Omni | **別 blueprint（S2S 系）で評価**、本 blueprint には乗せない |

### C 評価（不適）

- 全 `*-Thinking-*` / hybrid thinking 系の thinking ON 運用（`<think>` で voice TTFT 破壊）
- Qwen3-VL-* / Qwen3-Coder-*（vision encoder ロス / 日本語劣後）
- DeepSeek V3.2 / V4 Pro（DGX Spark 不可 / hallucination リスク）
- Kimi K2 系、Llama 3.3 70B、Mistral Small（規模・日本語）
- Qwen3-235B / 397B / 480B 系（DGX Spark 不可）

## 6. 本命モデル採用理由（Qwen3-30B-A3B-Instruct-2507）

- **MCP ネイティブ**: 現行 `TimedMcpClient` 構成に無改修で乗る。Qwen-Agent 公式推奨の tool calling テンプレートを GGUF に同梱
- **non-thinking 専用設計**: `-Thinking-2507` と分離されており、`<think>` 出力が構造的に混入しない
- **LM Studio / llama.cpp 安定対応**: 標準 Transformer + MoE、量子化が成熟。`lmstudio-community` / `unsloth` / `bartowski` から Q5_K_M / Q6_K / Q8_0 GGUF が公開済
- **agentic / function calling**: BFCL v3 で Qwen3 32B が上位。Instruct-2507 はこの線上で tool calling を更に強化
- **DGX Spark 相性**: active 3B MoE、Q5_K_M で ~20GB、Q8_0 でも ~33GB。128GB unified memory に余裕
- **日本語**: Style-Bert-VITS2 入力として安定。敬体/常体のぶれが少なく、TTS 側で読み崩しが起きにくい
- **context**: 262K native、長尺 MCP 会話に余裕
- **TPOT**: llama.cpp で 30 tok/s 程度に落ちても TTS 律速ライン（20 tok/s）を超える

## 7. エンドポイント切替（実装済み）

`dimos/agents/llm_env_ja.py` の `resolve_llm_model()` が以下 3 env を読み、内部で `OPENAI_BASE_URL` / `OPENAI_API_KEY` を設定。blueprint コードは無変更で **OpenAI 互換エンドポイントなら全て切替可能**。

| env | 用途 |
|---|---|
| `DIMOS_LLM_MODEL` | model 名（Azure は deployment 名、LM Studio は model identifier） |
| `DIMOS_LLM_BASE_URL` | OpenAI 互換 `/v1` URL |
| `DIMOS_LLM_API_KEY` | bearer token / API key（LM Studio は任意の値で OK） |

未設定の env は既存 `OPENAI_*` を温存する（破壊しない）。

## 8. 移行手順

### Step 0（推奨）: Azure を v1 互換エンドポイントに移行

Azure OpenAI は 2025-06 GA で OpenAI 互換 `/v1` API を提供。Bearer 認証・`api-version` 不要、素の `OpenAI` SDK で動く。これに切替えれば以降の手順が「base_url 差し替えだけ」で済む。

```bash
DIMOS_LLM_MODEL=<azure-deployment-name>          # 例: gpt-4o-deploy
DIMOS_LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1
DIMOS_LLM_API_KEY=<azure-key>
```

注意:
- `model` には Azure の **deployment 名** を渡す（汎用名ではない）
- AAD / Entra ID 認証環境では Bearer token 生成が別途必要

### Step 1（即効）: クラウドモデル差し替え

```bash
DIMOS_LLM_MODEL=gpt-4o-mini
DIMOS_LLM_BASE_URL=https://api.openai.com/v1
DIMOS_LLM_API_KEY=sk-...
```

gpt-4o → mini で latency / $ を即改善し、品質劣化が許容範囲か判定。

### Step 2: DGX Spark で LM Studio + ローカル LLM

#### 2.1 LM Studio 側

1. LM Studio を DGX Spark にインストール（Linux x64、CUDA バックエンド有効化）
2. GUI または `lms` CLI で本命 GGUF をダウンロード:
   ```bash
   lms get lmstudio-community/Qwen3-30B-A3B-Instruct-2507-GGUF \
     --quantization Q5_K_M
   ```
   候補リポジトリ:
   - `lmstudio-community/Qwen3-30B-A3B-Instruct-2507-GGUF`
   - `unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF`
   - `bartowski/Qwen3-30B-A3B-Instruct-2507-GGUF`
3. OpenAI 互換サーバー起動:
   ```bash
   lms server start --port 1234
   ```
4. モデルロード:
   ```bash
   lms load qwen/qwen3-30b-a3b-instruct-2507 \
     --context-length 16384 \
     --gpu-offload max
   ```
5. Settings:
   - `Enable Tool Use` ON（OpenAI 互換 function calling 有効化）
   - `Flash Attention` ON（CUDA 対応版）
   - `Reuse KV Cache` ON（system prompt prefix 再利用で TTFT 改善）
   - `KV Cache Quantization` は任意（Q8_0 推奨、メモリ余裕あり）

#### 2.2 クライアント側

```bash
DIMOS_LLM_MODEL=qwen/qwen3-30b-a3b-instruct-2507       # LM Studio が表示する identifier
DIMOS_LLM_BASE_URL=http://<dgx-spark>:1234/v1
DIMOS_LLM_API_KEY=lm-studio                            # 任意の文字列で OK
```

blueprint コード変更不要。A/B 比較で tool-call 成功率・日本語自然さ・TTFT / TPOT を計測し、Azure gpt-4o を default から落とすか判断。

### Step 3: 対抗候補の A/B

`DIMOS_LLM_MODEL` を切り替えるだけで以下を順次評価。最低限、以下 3 モデルは実機で比較する。

1. Qwen3-30B-A3B-Instruct-2507（本命）
2. Nemotron 3 Nano 30B-A3B（**事前に GGUF 公開状況と llama.cpp 動作確認**）
3. gpt-oss-120b（MXFP4、`reasoning_effort=low` 固定、日本語自然さの確認が要点）

計測指標:
- TTFT / TPOT（実発話で句単位）
- tool-call 成功率（JSON Schema バリデーション通過率、引数のフォーマット崩れ率）
- 日本語自然さ（句読点、敬体/常体ぶれ、TTS 読み崩し）
- 長文（>4K tokens 会話履歴）での品質劣化

### Step 4（任意）: fallback / ハイブリッド

- ローカル失敗時のクラウド fallback として `gpt-4o-mini` or `Claude Haiku 4.5` を保持
- 完全オフライン要件なら Qwen3.5-35B-A3B（GGUF）をローカル 2nd choice

## 9. LM Studio 運用時の注意点

1. **量子化選択**
   - Q5_K_M を default 推奨（30B-A3B で ~20GB、品質劣化ほぼ無し）
   - Q6_K: 品質重視、~25GB
   - Q8_0: ほぼ FP16 同等、~33GB（DGX Spark で余裕）
   - Q4_K_M: 容量最優先、わずかに品質低下
   - **Q3 以下は避ける**（tool calling の JSON 精度低下報告多数）
2. **prompt caching**: 会話履歴の頭（system prompt + tool schema）を固定しておけば prefix KV cache が効き TTFT 改善。Settings → Server → `Reuse KV Cache` ON
3. **Flash Attention**: CUDA 対応版を必ず ON。OFF だと TTFT が 2–3 倍遅くなる
4. **GPU offload**: DGX Spark の unified memory 構成では `--gpu-offload max` で全層 GPU 配置（MoE active expert routing 含む）
5. **tool calling**: chat_template が tool calling 対応か確認（GGUF メタデータの `tools` フィールド）。Qwen3-30B-A3B-Instruct-2507 の `lmstudio-community` 版は同梱
6. **モデル名は変種まで明示**: `Qwen3-30B-A3B` だけでは曖昧。`-Instruct-2507` を必ず付ける。`-Thinking-2507` は voice では絶対に使わない
7. **JSON Schema 強制（GBNF）**: LM Studio は llama.cpp の grammar-based constrained decoding を持つ。MCP tool schema を JSON Schema で渡す既存パイプラインが動作するか Step 2 で確認
8. **`<think>` / 推論プレフィックス対策**: 本命 Qwen3-Instruct-2507 は構造的に出ないが、gpt-oss / Nemotron / GLM-Air を A/B する場合は `reasoning_effort=low` / `reasoning_budget=0` / thinking OFF を必ず設定し、TTS 投入前に冒頭メタ区間（`<analysis>` 等）を除去するチャンク化フィルタの必要性を実機確認する

## 10. 棄却した選択肢

- **gpt-realtime / Gemini Live API**: S2S 一体型。本 blueprint は TTS を Style-Bert-VITS2 固定で設計しているため不適合。`unitree_go2_agentic_voice_live.py` 側で別途検討
- **vLLM / SGLang (NVFP4)**: スループットと Blackwell ネイティブ性能では LM Studio を上回るが、本プロジェクトは LM Studio 採用方針。将来 Blackwell の理論性能をフル活用したくなった時の選択肢
- **Ollama**: LM Studio と類似だが tool calling 対応が後発・荒い、GUI 管理性で LM Studio に劣る
- **Qwen3-Coder-* / Qwen3-VL-***: 日本語生成の自然さ / vision 不要のためロス
- **Qwen3-*-Thinking-* / hybrid thinking 系 ON**: `<think>` 出力で voice TTFT 破壊
- **Qwen3-Omni / Nemotron 3 Nano Omni**: 本 blueprint の Style-Bert-VITS2 固定方針と矛盾。S2S 評価は別 blueprint で
- **Qwen3-Next-80B / Qwen3.6-35B-A3B**: llama.cpp の新アーキ対応待ち。安定化次第で復帰評価
- **Qwen3-235B / 397B / 480B / GLM-4.5 以上 / DeepSeek V3.2・V4 Pro / Kimi K2**: DGX Spark に乗らない、または乗っても TPOT が voice 要件を満たさない
- **DeepSeek V4 系**: hallucination リスクが voice loop で許容しにくい
- **Mistral Small / Llama 3.3 / Gemma 4**: 日本語品質またはライセンスで Qwen / Nemotron 劣後
- **Llama 3.1 Swallow**: base が古く、新世代モデルで代替可能
- **Hermes 4（Nous Research）**: self-improving agent として有望だが、まずは Qwen3 素直構成で十分。観察継続

## 11. 補足: TTFT / TPOT の目安

### TTFT（Time To First Token）
ユーザー発話終了から最初の token が出るまでの時間。低いほど会話が自然。voice では 500ms 以下が目安。

### TPOT（Time Per Output Token）

**TPOT < 50ms（= 20 tok/s 以上）なら日本語 TTS は律速しない。** 余裕を見て 30 tok/s、快適なら 50 tok/s。それ以上は TTS 側がボトルネックになり投資効率が落ちる。

#### TTS の消費速度

| 項目 | 値 |
|---|---|
| 日本語の自然な発話速度 | 約 6–8 mora/秒（≒ 5–7 文字/秒） |
| Style-Bert-VITS2 RTF | 約 0.1–0.3 |
| → TTS が消費するテキスト速度 | 約 **5–7 文字/秒** |

#### LLM 供給速度（tok/s ↔ 文字/秒）

Qwen トークナイザーの日本語効率: 漢字は約 1 token / 1 文字、ひらがな・カタカナは約 1 token / 1–2 文字 → 平均 **1 token ≒ 0.7–1.0 文字**。

| TPOT | tok/s | 文字/秒 | 評価 |
|---|---|---|---|
| 100ms | 10 | 7–10 | ギリギリ追いつく |
| **50ms** | **20** | **14–20** | 発話の 2–3 倍、律速しない |
| 30ms | 33 | 23–33 | 余裕、ストリーム平滑 |
| 20ms | 50 | 35–50 | 完全に TTS 律速 |

#### チャンク化の影響

実際の voice パイプラインは句読点単位で TTS にチャンク投入。1 句 ≒ 10–20 文字 ≒ 15–30 tokens。TPOT 50ms なら 1 句生成に 0.75–1.5 秒。これより遅いと句と句の間に沈黙が出る。**TPOT 30ms 以下になると句切れ沈黙がほぼ消える**。

#### LM Studio 上での見込み

vLLM 計測の Qwen3-30B-A3B-Instruct-2507 が 64 tok/s（TPOT 15.6ms）に対し、LM Studio (llama.cpp) は 30–50% 遅い → **TPOT 25–35ms（30–45 tok/s）見込み**。律速しない実用ラインに収まる。

## 12. 参考

- LM Studio Docs — https://lmstudio.ai/docs
- LM Studio CLI (lms) — https://lmstudio.ai/docs/cli
- Practical local LLM examples on DGX Spark — https://medium.com/sparktastic/practical-local-llm-examples-on-dgx-spark-2f8ba384a9d7
- Building Local + Hybrid LLMs on DGX Spark — https://forums.developer.nvidia.com/t/building-local-hybrid-llms-on-dgx-spark-that-outperform-top-cloud-models/359569
- Choosing an Inference Engine on DGX Spark — https://medium.com/sparktastic/choosing-an-inference-engine-on-dgx-spark-8a312dfcaac6
- BFCL v3 Leaderboard — https://pricepertoken.com/leaderboards/benchmark/bfcl-v3
- BFCL V4（Berkeley） — https://gorilla.cs.berkeley.edu/leaderboard.html
- Hermes Self-Improving Agents on RTX/DGX Spark — https://blogs.nvidia.com/blog/rtx-ai-garage-hermes-agent-dgx-spark/
- DGX Spark Inference Performance 2026 — https://dev.to/mrjhsn/dgx-spark-inference-performance-local-llm-vs-cloud-benchmarks-2026-59pe
- Qwen3-30B-A3B-Instruct-2507 — https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507
- Qwen3-30B-A3B-Instruct-2507-GGUF (lmstudio-community) — https://huggingface.co/lmstudio-community/Qwen3-30B-A3B-Instruct-2507-GGUF
- Qwen3-30B-A3B-Instruct-2507-GGUF (unsloth) — https://huggingface.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF
- Qwen3-30B-A3B-Instruct-2507-GGUF (bartowski) — https://huggingface.co/bartowski/Qwen3-30B-A3B-Instruct-2507-GGUF
- NVIDIA Nemotron 3 Nano — https://huggingface.co/nvidia
- gpt-oss-120b / 20b — https://huggingface.co/openai
- GLM-4.5-Air — https://huggingface.co/zai-org
