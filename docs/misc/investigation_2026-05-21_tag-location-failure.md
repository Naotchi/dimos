# 調査メモ: 位置のタグ付け (`tag_location`) 失敗の原因

調査日: 2026-05-21
ステータス: **原因特定済み・対処は未着手（あとで判断）**

## 症状

「位置のタグ付けに失敗」。ユーザーには `"Error: Failed to store '<name>' in the spatial memory"` だけが返り、ログに真因が残らない。

## 失敗の経路

1. `dimos/agents/skills/navigation.py:80` `Navigate.tag_location()` → `RobotLocation` を作成
2. `dimos/perception/spatial_perception.py:562` `SpatialMemory.tag_location()` → `vector_db.tag_location()` を呼ぶ
3. `dimos/agents_deprecated/memory/spatial_vector_db.py:296` → `self.location_collection.add(ids=[...], documents=[location.name], metadatas=[...])`

## 根本原因

**`location_collection` を埋め込み関数を指定せずに作っている**（`spatial_vector_db.py:94`）:

```python
self.location_collection = self.client.get_or_create_collection(
    name=location_collection_name, metadata={"hnsw:space": "cosine"}
)
```

embedding_function 未指定なので Chroma がデフォルトの `ONNXMiniLM_L6_V2` を使い、
**最初の `.add()`/`.query()` 時に 79.3MB の ONNX モデルを `~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/` へ遅延ダウンロード**する。
ネット不通・タイムアウト・aarch64 (Spark) の onnxruntime 問題などで DL に失敗すると `.add()` が例外を投げる。

### 再現確認 (2026-05-21, このデスクトップ)

`SpatialVectorDB().tag_location(...)` を直接呼ぶと、`onnx.tar.gz` (79.3M) のダウンロードが走り、
ネットがあったため ~28 秒かけて成功した。オフライン/低速/DL失敗時はここで例外になる。
（onnxruntime の GPU device discovery 警告も出るが、これ自体は致命的ではない）

## 真因がログに残らない理由

例外が **`spatial_perception.py:562-567` で握り潰されて `False` を返すだけ**:

```python
def tag_location(self, robot_location: RobotLocation) -> bool:
    try:
        self.vector_db.tag_location(robot_location)
    except Exception:        # ← 原因がここで消える（ログも出ない）
        return False
    return True
```

→ `navigation.py:108` の `"Error: Failed to store '...' in the spatial memory"` しか見えない。

## 設計上のギャップ

`SpatialVectorDB` コンストラクタは `embedding_provider` を受け取り保持しているが、
`location_collection` ではそれを使っておらず、設定済みプロバイダではなく Chroma デフォルト ONNX が使われる。

## 対処の選択肢（未決）

- **(A) 最小差分**: `spatial_perception.py:565` の `except Exception:` に `logger.exception(...)` を足し、次回失敗時に実際の例外（DLタイムアウト/onnxruntime/権限など）をログ化する。
- **(B) 恒久対策**: `location_collection` に明示的な embedding_function を渡す（オフラインならローカルにモデル配置 or 既存 `embedding_provider` を流用）。Spark をオフライン運用するなら 79MB モデルの事前キャッシュも必要。
- **(C) 調査のみ**: コードは触らない。

> 注: `spatial_vector_db.py` / `spatial_perception.py` は **upstream 由来ファイル**。CLAUDE.md の方針に従い、編集する場合は最小差分に留める（理想は派生ファイル追加で切り替え）。

## 未確認の宿題

- 実機 (Spark) で失敗した時のログ。
- Spark 側に `~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/` が存在するか（無い／DL不可の可能性が高いと推測）。
