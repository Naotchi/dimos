---
title: 棚監視パイプライン 前段 VLM 棚段 crop → dense 検出（blueprint 拡張）設計
created: 2026-06-07
status: draft
branch: shelf/row-vlm-crop-detection
related:
  - docs/misc/棚監視AI 2026年5月最新版.md
  - docs/superpowers/specs/2026-06-07-shelf-dense-detection-design.md
---

# 前段「VLM 棚段 crop → dense 検出」設計

棚監視 SOTA パイプラインの **dense 検出（Stage ②, RT-DETRv4）の前段**として、
カメラ画像から **棚段（各シェルフの段）の領域を VLM grounding で抽出 → 段ごとに crop →
各 crop に RT-DETRv4 dense 検出 → 全画像座標へ remap** するステージを実装する。

実装形態は **既存の物体検出 blueprint（`unitree_go2_agentic_local_tts_detection`）の拡張**とし、
合成 `Detector` を `Detection3DModule` に差し替える**新 blueprint** を fork 固有ファイルとして追加する。

## 1. 背景 / 動機

- 棚監視は「状態推定」であり **realtime は本質要件ではない**（什器は秒単位で変わらない）。
  → VLM grounding のネットワーク往復レイテンシは許容できる。
- 精度を上げる王道は「背景（床・通路・他什器・人）を除き、対象領域を高解像度で dense 検出に渡す」こと。
  段ごとの crop はこの「背景除去＋実効解像度向上」を担う。
- 棚段の既製学習済み検出器は存在せず、RT-DETRv4 の fine-tune 用学習データも現状用意できない。
  一方 **Spark の LM Studio に grounding 可能な VLM が既にホスト済み**で、agentic blueprint が
  それを agent LLM として既に使っている。これを段検出にも再利用する。

## 2. ゴール / 非ゴール

### ゴール
- カメラ画像 → 棚段 bbox（VLM grounding）→ 段 crop → RT-DETRv4 → **全画像座標の `ImageDetections2D`**。
- 既存 `Detection2DModule.Config.detector` 注入点に差し込める **`Detector` 準拠の合成検出器**として実装。
- 既存 agentic + `Detection3DModule`（3D 投影 / LCM / rerun 可視化）配線を**丸ごと再利用**する新 blueprint。
- VLM 呼び出しは**低頻度（既定: 初回1回 + 明示再取得）**で、dense 検出は毎フレーム高速に回す。
- 単体テスト（VLM はモック）で「単独で動く」ことを満たす。

### 非ゴール（後続に分離）
- SKU 同定（③ 埋め込み + k-NN）、欠品判定（④）、棚割整合（⑤）。
- RT-DETRv4 の product/price-tag fine-tune。
- VLM 段抽出が不安定だった場合の「什器→幾何分割」ハイブリッド（フォールバック方針のみ本 spec に記載、実装は後続）。

## 3. 検証済みの前提 / 既存資産

### 3.1 実機エンドポイントで確認したこと
`http://localhost:1234/v1`（LM Studio, OpenAI 互換）の `qwen/qwen3.6-35b-a3b` に画像 + プロンプトを投げ、以下を確認済み：

- **画像入力に対応した VLM**である（text-only ではない）。
- grounding bbox を返す。**座標系は 0–1000 正規化**（`px = coord / 1000 * (W or H)`）。
- 出力は本文中の ```json ブロックで、要素は `{"bbox_2d": [x1,y1,x2,y2], "label": "..."}`。
- 320×240 のテスト画像で上段/下段の2段をほぼ正確に分割 → **段直接抽出は実用上機能する**。

### 3.2 既存資産（再利用する／しない）
- **再利用する**: 基底 `dimos/models/vl/base.py` の `VlModel.query_detections(image, query) -> ImageDetections2D[Detection2DBBox]`
  （`base.py:266`）。VLM grounding → `ImageDetections2D` 変換を、JSON パース・`query_json` の
  リトライ・`auto_resize` スケーリング込みで実装済み。**この機構を丸ごと使う**（独自 HTTP クライアントは作らない）。
- **そのままは使えない**: `dimos/models/vl/qwen.py` の `QwenVlModel` は **base_url が Alibaba dashscope
  クラウドにハードコード**（`qwen.py:45`, `ALIBABA_API_KEY`, model `qwen2.5-vl-72b-instruct`）で、
  **localhost:1234 を指していない**。base_url は config 化されておらず差し替え不可。
  → 現状 `Detection3DModule.ask_vlm`/`nav_vlm` はローカル Spark ではなく**クラウド Alibaba を叩いている**。
- **新規に作る**: ローカル LM Studio エンドポイントを指す **fork-local の `VlModel` サブクラス**（§4 コンポーネント1）。

### 3.3 座標規約リスク（実装時に検証）
基底 `query_detections` のプロンプトは `["label", x1, y1, x2, y2]` を**ピクセルで**要求するが、
実機テストではローカル Qwen は **0–1000 正規化の `bbox_2d`** を返した（プロンプト指定を無視気味）。
このまま流すと**約1000倍スケールの誤った crop**になりうる。
→ 実装時に「ローカル Qwen が基底プロンプトのピクセル形式に従うか」を検証し、従わなければ
fork-local サブクラス側で**プロンプト or パースを override** して 0–1000 → px 換算を入れる。

## 4. アーキテクチャ

```
camera.color_image
   └─> Detection3DModule(detector = ShelfRowDetector)        # 新 blueprint で差し替え
          └─ ShelfRowDetector.process_image(image)            # 合成 Detector（fork 固有・新規）
                ├─ (低頻度) LocalQwenVlModel.query_detections(image, "each horizontal shelf row")
                │            # 基底 VlModel.query_detections を再利用 → 段 bbox[]（キャッシュ）
                ├─ crop[]  = 各段 bbox で image を切り出し
                ├─ RTDetrv4Detector.process_image(crop)        # 段ごとに dense 検出（既存・fork 固有）
                └─ remap: crop 座標 → 全画像座標（原点 offset, row_id 付与）→ ImageDetections2D
          └─ 既存どおり 3D 投影 / LCM transport / rerun 可視化
```

### コンポーネント（各々単独でテスト可能な単位）

1. **`LocalQwenVlModel(QwenVlModel)`** — `dimos/perception/shelf/regions/local_qwen_vl.py`（新規, fork 固有）
   - 既存 `QwenVlModel` を継承し、`_client`（OpenAI クライアント）だけ override して
     **`base_url` / `api_key` / `model_name` を env から取得**し localhost:1234 を指す。
   - 設定は env: `SHELF_VLM_BASE_URL` / `SHELF_VLM_MODEL` / `SHELF_VLM_API_KEY`
     （未設定なら `DIMOS_LLM_BASE_URL` / `DIMOS_LLM_MODEL` / `DIMOS_LLM_API_KEY` にフォールバック
     = agent と同じローカル Qwen を共有）。判断① 参照。
   - grounding 本体（プロンプト・JSON パース・スケーリング）は**基底 `VlModel.query_detections` を再利用**。
   - §3.3 の座標規約検証の結果、ローカル Qwen がピクセル形式に従わない場合のみ、
     本サブクラスで `query_detections`（プロンプト/パース）を override し 0–1000 → px 換算を入れる。

2. **`ShelfRowDetector(Detector)`** — `dimos/perception/shelf/regions/shelf_row_detector.py`（新規）
   - `Detector` インターフェース `process_image(image) -> ImageDetections2D` を実装。
   - 内部に `LocalQwenVlModel` と `RTDetrv4Detector` を保持。
   - 段抽出は `self.vlm.query_detections(image, "each horizontal shelf row")` を呼ぶだけ。
   - grounding 結果（段 bbox）を**キャッシュ**し、既定では初回のみ VLM を呼ぶ。判断② 参照。
   - 各段 crop に RT-DETRv4 を適用し、crop 原点 offset で全画像座標へ remap、`row_id` を付与してマージ。
   - 画像外 bbox はクランプ。

3. **新 blueprint** — `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts_shelf.py`（新規）
   - 既存 `unitree_go2_agentic_local_tts_detection.py` の変種。差分は実質1点：
     `Detection3DModule.blueprint(camera_info=..., detector=ShelfRowDetector)`。
   - 3D remapping / LCM transports / `disabled_modules(SecurityModule)` は既存どおり。
   - `all_blueprints` への登録（upstream 由来ファイルへの**最小差分** = エントリ1行追加のみ許容）。

4. **CLI（任意・補助）** — `scripts/shelf_rows.py`（新規, 後続でも可）
   - 静止画 → 段 bbox + 段内検出を rerun/注釈画像で可視化。デバッグ用。

## 5. データフロー / 座標

- VLM 座標 `[0,1000]` → ピクセル: `x_px = x/1000 * W`, `y_px = y/1000 * H`（W,H は元画像サイズ）。
- crop: `crop = image[y1:y2, x1:x2]`、原点 `(x1, y1)` を保持。
- remap: RT-DETRv4 が返す crop 内 bbox `(bx1,by1,bx2,by2)` → `(bx1+x1, by1+y1, bx2+x1, by2+y1)`。
- `ImageDetections2D` に既存フィールドで載せ、段識別は `row_id`（メタ/ラベル）で表現。

## 6. 設計判断

### 判断① VLM エンドポイントの共有（採用: 共有デフォルト + 上書き可）
- 既定で agent と同じ `DIMOS_LLM_*` を再利用（= localhost:1234 の Qwen を共有、設定一箇所）。
- agent LLM をクラウドへ差し替えても段 grounding をローカル Qwen に固定できるよう、
  `SHELF_VLM_BASE_URL` / `SHELF_VLM_MODEL` で**上書き可能**にする。

### 判断② VLM grounding の頻度（採用: 低頻度キャッシュ）
- `Detection3DModule` はカメラを**ストリーム処理**するが、VLM grounding は数秒かかる。
  毎フレーム叩くとパイプラインが詰まる。
- よって **grounding は低頻度**：v1 は「初回1回 + 明示再取得（メソッド/スキル呼び出し）」。
  取得した段レイアウトをキャッシュし、**RT-DETRv4 は毎フレームキャッシュ領域内で高速実行**。
- 運用: ロボットが棚前に停止 → 段レイアウトを1回取得 → そのまま dense 検出。
- 後続拡張余地: 周期再取得 / ロボット姿勢の大変化で再取得。

### 判断③ 段抽出方式（採用: VLM 直接 + フォールバック）
- 段を VLM に直接返させる（検証済みで機能）。
- 段が 0 件 / タイムアウト / パース失敗時は **画像全体を1段とみなして** dense 検出（パイプラインは止めない）。
- 将来、段分割が不安定なら「什器→幾何分割」ハイブリッドへ差し替え（本 spec 非ゴール）。

## 7. エラー処理 / フォールバック

| 事象 | 挙動 |
|---|---|
| VLM 0 段 / タイムアウト / JSON パース失敗 | 画像全体を1段としてフォールバック、warning ログ |
| bbox が画像外 | 画像範囲にクランプ |
| エンドポイント未設定（env 無し） | 明示エラー（起動時に分かるように） |
| crop が空（面積0） | その段をスキップ、ログ |

## 8. テスト

- `LocalQwenVlModel`: `_client`（OpenAI 呼び出し）をモックし、env からの base_url/model/key 解決と
  フォールバック順（`SHELF_VLM_*` → `DIMOS_LLM_*`）、および（override する場合）0–1000 → px 換算を検証。
  **実エンドポイントは叩かない**。
- `ShelfRowDetector`: `LocalQwenVlModel.query_detections` をモックし、(a) crop 原点 remap が決定的に正しいこと、
  (b) 0 段フォールバック、(c) grounding キャッシュ（2回目は VLM を呼ばない）を検証。
- 実エンドポイント結合テスト: env がある時のみ実行、無ければ skip。
- blueprint: 既存 blueprint 生成テストに倣い、新 blueprint が組み立つ（autoconnect/transports）ことを検証。

## 9. fork 方針との整合（CLAUDE.md）

- `LocalQwenVlModel` / `ShelfRowDetector` / 新 blueprint / CLI は**すべて新規 fork 固有ファイル**。
- upstream 由来ファイルは**編集しない**：
  - `module2D.py` / `module3D.py`（`Detection3DModule`）は注入点 `Config.detector` をそのまま利用。
  - `dimos/models/vl/base.py`（`VlModel.query_detections`）/ `qwen.py`（`QwenVlModel`）は
    **継承して再利用**（base_url の override はサブクラス側で行い、upstream は触らない）。
- `all_blueprints` への**エントリ1行追加のみ**を最小差分として許容（ロジック改変なし）。
