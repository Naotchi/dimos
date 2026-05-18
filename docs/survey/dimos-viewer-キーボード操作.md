# dimos-viewer キーボード操作リファレンス

`dimos-viewer` (デスクトップ Rust バイナリ。`bridge.py` から `--connect <grpc> --ws-url <ws>` 付きで spawn される) のキーボード挙動まとめ。

ソース確認: upstream `dimensionalOS/dimos-viewer` の以下を読んだ結果。

- `dimos/src/interaction/keyboard.rs` — fork 独自の WASD HUD
- `dimos/src/viewer.rs` / `dimos/src/lib.rs` / `dimos/src/interaction/{mod,handle,protocol,ws}.rs` — fork が足してるのは「click-to-navigate」と「WASD teleop HUD」だけ
- `crates/viewer/re_ui/src/command.rs` — stock Rerun viewer のショートカット定義

つまり **dimos-viewer が独自に握っているキーは WASD HUD だけ**、それ以外は全部 stock Rerun viewer の挙動。「関節の表示が時間方向に動く」のは Rerun のタイムライン再生機能。

---

## 1. dimos-viewer 独自: WASD Teleop HUD

画面左下 (タイムラインバーの少し上) に出る半透明のオーバーレイ。

### Engage / Disengage

- **クリックで engage** (枠が緑に変わる) しないとキーは捕捉されない。3D ビューポート操作と競合させないための明示的なゲート。
- HUD 外をどこかクリックするか、HUD を再クリックすると disengage。
- disengage 時は必ず `{"type":"stop"}` を WebSocket に送る (取りこぼし防止)。

### キーマップ (engage 中のみ有効)

定数: `BASE_LINEAR_SPEED = 0.5 m/s` / `BASE_ANGULAR_SPEED = 0.8 rad/s` / `FAST_MULTIPLIER = 2.0`

| キー | 動作 | Twist 成分 |
|------|------|-----------|
| `W` / `↑` | 前進 | `linear_x = +0.5` |
| `S` / `↓` | 後退 | `linear_x = -0.5` |
| `A` / `←` | 左旋回 | `angular_z = +0.8` |
| `D` / `→` | 右旋回 | `angular_z = -0.8` |
| `Q` | 左ストレイフ | `linear_y = +0.5` |
| `E` | 右ストレイフ | `linear_y = -0.5` |
| `Shift` (押下中) | 倍速モディファイア | 上記全てを ×2 |
| `Space` | **緊急停止** (ワンショット) | 全 0 Twist を publish + HUD が赤フラッシュ |

- 同時押し OK: `W+A` で「前進しながら左旋回」。
- 相反キー (`W+S` / `A+D` / `Q+E`) は打ち消し合って 0 になる。
- キーを全部離した瞬間に stop が 1 回飛ぶ (惰性走行しない設計)。
- engage 中の `Space` は **再生切替ではなく e-stop** に置き換わる。

### Python 側の受け口

`dimos/visualization/rerun/websocket_server.py` の `RerunWebSocketServer`:

- `{"type":"twist", linear_x, ..., angular_z}` → `tele_cmd_vel` (`Twist`) に publish
- `{"type":"stop"}` → `Twist.zero()` を publish
- `{"type":"click", x, y, z, entity_path, timestamp_ms}` → `clicked_point` (`PointStamped`) に publish
- `{"type":"heartbeat"}` → 何もしない (接続維持用)

### デバッグ

`DIMOS_DEBUG=1` を付けて起動すると `[DIMOS_DEBUG] Published twist: ...` / `Published stop command` が stderr に出る。WebSocket 接続先は `DIMOS_VIEWER_WS_URL` 環境変数 or `--ws-url` CLI フラグで上書き可 (既定 `ws://127.0.0.1:3030/ws`)。

---

## 2. stock Rerun viewer ショートカット

HUD 非 engage 時は全キーが Rerun に素通りする。**関節アニメーションが動いて見えるのは下表のタイムライン操作が走っているから**。

### タイムライン再生 (joint state を時刻方向にリプレイ)

| キー | コマンド | 効果 |
|------|---------|------|
| `Space` | `PlaybackTogglePlayPause` | 再生/一時停止 |
| `←` / `→` | `PlaybackBack` / `PlaybackForward` | 1 フレーム戻る/進む |
| `Shift+←` / `Shift+→` | `PlaybackBackFast` / `PlaybackForwardFast` | 高速送り |
| `Cmd/Ctrl+←` / `Cmd/Ctrl+→` | `PlaybackStepBack` / `PlaybackStepForward` | ステップ |
| `Alt+→` | `PlaybackFollow` | 最新に追従 |
| `Alt+←` | `PlaybackRestart` | 先頭にリセット |
| `Home` / `End` | `PlaybackBeginning` / `PlaybackEnd` | 先頭/末尾へ |

仕組み: タイムラインカーソルが動くと、その時刻に対応する `pose` / `joint_positions` などが Rerun の TimeSeries から引かれて 3D ビューが再描画される。dimos-viewer 側で関節をいじるコードは **存在しない**。

### パネル / UI 開閉

| キー | 効果 |
|------|------|
| `Cmd/Ctrl+P` | コマンドパレット (全ショートカット一覧もここで見られる) |
| `Ctrl+Shift+B` | Blueprint パネル開閉 |
| `Ctrl+Shift+S` | Selection パネル開閉 |
| `Ctrl+Shift+T` | Time パネル開閉 |
| `Ctrl+Shift+M` | Memory パネル |
| `Ctrl+Shift+R` | Viewer リセット |
| `Ctrl+Shift+I` | Blueprint inspection パネル |
| `Ctrl+Shift+U` | egui debug パネル |
| `Ctrl+Shift+P` | Profiler |
| `Ctrl+Shift+D` | Chunk store browser |
| `F11` | フルスクリーン |
| `Cmd/Ctrl+,` | Settings |

### ファイル / Recording

| キー | 効果 |
|------|------|
| `Cmd/Ctrl+S` | 保存 |
| `Cmd/Ctrl+Alt+S` | 選択範囲のみ保存 |
| `Cmd/Ctrl+O` | Open |
| `Cmd/Ctrl+Shift+O` | Import |
| `Cmd/Ctrl+Shift+L` | Open URL |
| `Cmd/Ctrl+L` | Share |
| `Cmd+Alt+↑` / `Cmd+Alt+↓` | 前/次の recording へ |
| `Cmd/Ctrl+[` / `Cmd/Ctrl+]` | Navigate back / forward |
| `Cmd/Ctrl+Z` / `Cmd/Ctrl+Shift+Z` (or `Cmd/Ctrl+Y`) | Undo / Redo (Blueprint 編集) |
| `Ctrl+Shift+E` | Entity hierarchy をコピー |

### 3D ビュー内マウス操作 (参考)

stock Rerun そのまま。

- 左ドラッグ = orbit
- 右ドラッグ (or middle ドラッグ) = pan
- ホイール = zoom
- エンティティクリック = Selection 更新 → **3D 位置を持つエンティティなら fork の click-to-navigate がトリガし、`clicked_point` に publish される** (デバウンス 100ms)

---

## 3. engage 中のキー競合まとめ

| キー | HUD 非 engage | HUD engage |
|------|--------------|-----------|
| `W` `A` `S` `D` `Q` `E` | Rerun に素通り (未割当) | teleop |
| `↑` `↓` `←` `→` | タイムライン送り | teleop (`↑↓` は前後、`←→` は旋回) |
| `Shift+←/→` | 高速送り | (`Shift` は倍速モディファイアになる) |
| `Space` | 再生/一時停止 | **緊急停止** |
| `Home` `End` `Cmd+矢印` `Alt+矢印` | タイムライン操作 | そのまま生きる (HUD は奪わない) |
| `Cmd/Ctrl+各種` | パネル操作等 | そのまま生きる |

迷ったら **コマンドパレット (`Cmd/Ctrl+P`)** を開けば stock Rerun の全コマンドとショートカットが一覧できる。これが Rerun 側の一次情報。

---

## 4. 出典 (一次情報)

- WASD HUD: `dimensionalOS/dimos-viewer` `dimos/src/interaction/keyboard.rs`
- click-to-navigate: 同 `dimos/src/viewer.rs` の `on_event` ハンドラ
- WebSocket プロトコル: 同 `docs/websockets.md`
- stock Rerun ショートカット: 同 `crates/viewer/re_ui/src/command.rs` の `kb_shortcuts()`
- spawn 経路 (Python 側): `dimos/visualization/rerun/bridge.py` (`dimos-viewer --connect ... --ws-url ...`)
- WS 受信 (Python 側): `dimos/visualization/rerun/websocket_server.py`
