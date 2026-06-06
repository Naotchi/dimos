---
title: 棚監視パイプライン Stage ② Dense Detection（単独モジュール）設計
created: 2026-06-07
status: approved
branch: shelf/dense-detection
related: docs/misc/棚監視AI 2026年5月最新版.md
---

# Stage ② Dense Detection — 単独モジュール設計

棚監視AIレポート（`docs/misc/棚監視AI 2026年5月最新版.md`）の「推奨SOTAパイプライン」を
dimos 上で段階実装する取り組みの **第1段（Dense Detection）** の設計。

各段は **dimos 本体（Module / stream / LCM 層）に繋ぐ前に、単独で動くモジュールとして順番に実装する**方針。
本 spec はその最初の段である Dense Detection を対象とする。

## 1. ゴール / 非ゴール

### ゴール
- RT-DETRv4（公開 COCO 重み、既定 `-L`）を **dimos とは独立に走らせ、棚画像 → 高密度 bbox を出す**。
- 出力はクラス非依存の dense detection（COCO ラベルは載るが意味には依存しない）。
- CLI + 可視化 + テストで「単独で動く」ことを満たす。

### 非ゴール（後続デリバラブルに分離）
- SKU-110K / price-tag データでの fine-tune、本番クラス（`product` / `price-tag`）。
- dimos の Module / stream(LCM) / coordinator 層への接続。
- ③ SKU埋め込み 以降のパイプライン段。

## 2. モデルと依存の取り込み方針（A2）

- 検出モデル：**RT-DETRv4**（arXiv:2510.25257, Apache-2.0, PyTorch）。既定サイズ `-L`（COCO 55.4 AP）。
  推論には学生重みのみで足り、**DINOv3 教師（蒸留用）は不要**。
- 取り込み：`RT-DETRs/RT-DETRv4` を **`Naotchi/rt-detrv4` に fork** し、
  **最小 `pyproject.toml` を1枚追加**して `engine` を import 可能なパッケージにする（pin した commit）。
  - upstream の RT-DETRv4 リポには **setup.py / pyproject.toml が無い**ため、そのままでは
    `uv add git+...` / `pip install git+...` で入らない。packaging 差分は fork 側に閉じる。
- dimos への追加：**`uv add "git+https://github.com/Naotchi/rt-detrv4@<sha>"`**。
- 環境的副作用：RT-DETRv4 の依存は `torch / torchvision / scipy / PyYAML / faster-coco-eval / transformers`
  程度で、**`ultralytics` 名前空間を上書きしない**（YOLOv13 と異なり既存 `Yolo2DDetector` /
  `Yoloe2DDetector` を壊さない）。よって **dimos 共有 `.venv` にそのまま同居可能**、専用 venv 不要。

## 3. ファイル構成

すべて **fork 固有・新規ファイル**。upstream 由来ファイルは無改変（CLAUDE.md fork方針に準拠）。
新規パッケージ `dimos/perception/shelf/` に以降の段も集約する（upstream 衝突ゼロ）。

| パス | 役割 |
|---|---|
| `dimos/perception/shelf/__init__.py` | パッケージ初期化 |
| `dimos/perception/shelf/detection/__init__.py` | パッケージ初期化 |
| `dimos/perception/shelf/detection/rtdetrv4_detector.py` | `RTDetrv4Detector(Detector)` 本体 |
| `dimos/perception/shelf/detection/weights.py` | ckpt / config の取得（`gdown`）と解決 |
| `dimos/perception/shelf/detection/test_rtdetrv4_detector.py` | テスト（dimos の colocate 流儀） |
| `scripts/shelf_detect.py` | 単独 CLI（既存 `scripts/bench_*.py` 流儀） |

## 4. コンポーネントとインターフェース

### `RTDetrv4Detector(Detector)`
dimos の `Detector` 抽象（`dimos/perception/detection/detectors/base.py`）に準拠：
`process_image(image: Image) -> ImageDetections2D`。
これにより後段の接続時、`Detection2DModule(Config.detector=RTDetrv4Detector)` として**そのまま差し込める**。

- `__init__(model_size="l", weights=None, config=None, device=None, conf=0.4)`
  - engine の YAML config（`configs/rtv4/...`）からモデルを構築し、ckpt を load。
  - `device` 自動判定（CUDA 無ければ CPU フォールバック、遅い旨 warn）。
- `process_image(image)`
  1. 前処理：640×640 リサイズ、`/255`、CHW、batch 次元付与。
  2. engine model 推論。
  3. 出力 `{boxes_xyxy, scores, labels}` を元画像座標へスケールバック。
  4. `conf` 閾値でフィルタ。
  5. 各検出を `Detection2DBBox`（`bbox / track_id / class_id / confidence / name / ts / image`）に変換し
     `ImageDetections2D` を構築。`track_id` は単発推論なので `-1` 等の既定。

### `weights.py`
- ckpt（Google Drive 上）を **`gdown` でダウンロード + キャッシュ**。
  保存先は既存 `dimos.utils.data` のデータディレクトリ規約に寄せる。
- `--weights <path>` で上書き可。未取得時は **DL コマンドを示す actionable なエラー**。
- size→（config パス, Google Drive file id）の対応表を持つ。

### `scripts/shelf_detect.py`
- 入力：画像ファイル（1枚 or 複数）。
- 出力：`annotated_image()` を保存（bbox 描画済み）＋ 検出結果の JSON ダンプ。
- 引数：`--model-size`, `--weights`, `--conf`, `--device`, `--out`。

## 5. データフロー

```
画像ファイル → dimos Image → RTDetrv4Detector.process_image
  → preprocess(640, /255, CHW) → engine model
  → {boxes_xyxy(scaled), scores, labels} → conf filter
  → [Detection2DBBox, ...] → ImageDetections2D
CLI: → annotated_image() を保存 + 検出結果を JSON 出力
```

## 6. エラー処理

- 重み未配置 / config 不在 → DL コマンド付き actionable error。
- CUDA 無し → CPU フォールバック（遅い旨 warn）。
- 検出ゼロ → 正常系（空の `ImageDetections2D`）として扱う。

## 7. テスト方針（TDD）

- **純粋な変換ロジック**（生テンソル → `ImageDetections2D`、座標スケール、conf フィルタ、
  クラス非依存ラベリング）は **engine model をフェイク出力で注入して単体テスト**。
  重み無しで回り、実装より先に書く。
- **実重みの推論**は **opt-in / 重み無ければ `skip`** の smoke テスト。
  サンプル棚画像で「検出が非空」「全 bbox が画像内」を assert。
- CLI の smoke テスト（小画像で例外なく JSON / 画像が出る）。

## 8. 後続デリバラブル（参考・本 spec 範囲外）

- ②-train：SKU-110K(product) + price-tag データで fine-tune し本番クラス化。
- ③ SKU 埋め込み + k-NN retrieval（DINOv3 / ArcFace / FAISS）。
- ④ 欠品検知、⑤ 棚割整合（Needleman-Wunsch）、⑥ VLM エージェント層、⑦ エッジ配信。
- dimos Module / stream 層への統合（`Detection2DModule` 差し込み）。
