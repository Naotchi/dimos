# shelf — 棚監視パイプライン（fork-local）

`docs/misc/棚監視AI 2026年5月最新版.md` の推奨パイプラインを段階実装する fork 固有パッケージ。

## Stage ② Dense Detection (RT-DETRv4)

棚画像 → 高密度 bbox（クラス非依存, COCO 学習済み重み）。

### 依存

RT-DETRv4 は `Naotchi/rt-detrv4`（upstream `RT-DETRs/RT-DETRv4` の fork + hatchling pyproject で
`engine` パッケージと `configs/` を同梱）として導入済み:

```bash
uv add "rt-detrv4 @ git+https://github.com/Naotchi/rt-detrv4@86b20b0a68d73a93b8ee23372cb2f6c12f0dd341" gdown
```

install 後、`engine` と `configs/` が site-packages に兄弟配置され、`weights.resolve_config()` が
`site-packages/configs/rtv4/rtv4_hgnetv2_{size}_coco.yml` を解決する。

### 使い方（CLI）

```bash
python scripts/shelf_detect.py path/to/shelf.jpg --model-size l --out out/
# out/shelf_annotated.jpg と out/shelf.json が生成される
# 初回は重みを Google Drive から自動ダウンロード（~/.cache/dimos/rtdetrv4、L は約505MB）
```

引数: `images...`, `--model-size {s,m,l,x}`, `--weights <path>`, `--device {cuda,cpu}`, `--conf`, `--out`。
`DIMOS_RTDETRV4_DIR` で重みキャッシュ先を変更可能。

### 使い方（ライブ USB カメラ）

GPU（CUDA）で約 30 FPS。`cv2.imshow` は使えない（OpenCV GUI 非搭載）ため、表示は rerun か mp4 で行う。

```bash
# rerun web ビューアでライブ表示（ログに出る "?url=..." 付き URL をブラウザで開く。
# Spark 上のブラウザなら http://localhost:9090/?url=... が直接開ける。
# デスクトップセッションがあれば --open-browser で自動起動も可）
python scripts/shelf_detect_live.py --camera 0 --serve

# 注釈付き mp4 と rerun 録画(.rrd)を保存（後で `rerun shelf_live.rrd` で再生）
python scripts/shelf_detect_live.py --camera 0 --out shelf_live.mp4 --rrd shelf_live.rrd
```

引数: `--camera <idx>`, `--backend {rtdetrv4,yolo}`, `--weights`, `--model-size`, `--device`, `--conf`, `--serve`, `--rrd <path>`, `--out <path.mp4>`, `--max-frames N`（0=Ctrl-C まで）。

### バックエンド（COCO汎用 / 既製の棚商品検出器）

- `--backend rtdetrv4`（既定）: RT-DETRv4 + COCO 重み。GPU で約 30 FPS。**汎用 COCO クラス**（人/椅子等）で、棚の「商品」は商品として認識しない。
- `--backend yolo --weights <local.pt | HFリポID>`: 任意の ultralytics YOLO を読む。**学習なしで棚商品検出**したい場合、コミュニティ既製モデルを指定:

```bash
# 既製の「商品/空き棚」検出器（foduucom, クラス: empty / product）を webカメラに
python scripts/shelf_detect_live.py --camera 0 --backend yolo \
  --weights foduucom/product-detection-in-shelf-yolov8 --conf 0.25 --serve
```

注意:
- 既製 YOLO 重みは**非公式・ライセンス未記載・精度自己申告**。本番採用前に要確認。SKU-110K の**公式**学習済み重みは存在しない（公式は COCO のみ）。
- この環境は **torchvision の CUDA NMS が無い**ため、YOLO バックエンドは自動で **CPU 実行**にフォールバックする（~7-10 FPS）。RT-DETRv4 は NMS 不要なので GPU のまま。

### 使い方（Python）

```python
from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.shelf.detection.rtdetrv4_detector import RTDetrv4Detector

detector = RTDetrv4Detector(model_size="l")
detections = detector.process_image(Image.from_file("shelf.jpg"))
for d in detections:
    print(d.to_repr_dict())
```

`RTDetrv4Detector` は dimos の `Detector` 抽象（`process_image(Image) -> ImageDetections2D`）に
準拠するため、後段で `Detection2DModule(Config.detector=RTDetrv4Detector)` として stream 層に
差し込める。

### 構成

| ファイル | 役割 |
|---|---|
| `detection/rtdetrv4_detector.py` | `RTDetrv4Detector` 本体 + 純関数（`build_image_detections` / `_preprocess` / `_to_numpy`） |
| `detection/weights.py` | config / ckpt の解決と gdown ダウンロード |
| `detection/cli.py` | CLI ロジック（`run` / `main`） |
| `scripts/shelf_detect.py` | 薄い CLI エントリポイント |

### テスト

```bash
pytest dimos/perception/shelf/
```

純関数・変換・前処理・CLI は重み無しで実行（`test_real_inference_smoke` は engine/重みが
揃った環境でのみ実推論を検証、無ければ skip）。

### 範囲外（後続デリバラブル）

- product / price-tag への fine-tune（公開重みは COCO 80 クラスのみ）。
- ③ SKU 埋め込み + k-NN retrieval（DINOv3 / ArcFace / FAISS）以降の段。
- dimos の Module / stream(LCM) 層への統合。
