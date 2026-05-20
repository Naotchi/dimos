# ローカルマイクの VAD / hold モード切り替え設計

- 日付: 2026-05-20
- 対象 blueprint: `unitree-go2-agentic-local-tts`
- ステータス: 設計レビュー中 → 実装計画へ

## 背景と目的

現在の `unitree-go2-agentic-local-tts` は **push-to-talk (PTT)**: `PttKeyboard`
が F9 押下中だけ `mic_gate=True` を publish し、`LocalMicrophoneJa` が
gate の立ち下がりで発話を確定する（hold 方式）。

要望は「F9 を押しっぱなしにせず喋れるようにしたい」。検討の結果、
マイクを開けっぱなしにするには**発話の終端を判定する仕組み（VAD）** が
必須であることを確認した（gate の立ち下がりが現状の唯一の発話区切り）。

最終的に「**F9 押下中だけ (hold) と VAD を切り替えられる**」ことをゴールとする。
切り替えはプロファイル `config.json` から行う（後述の通り env には入れない）。

ハードウェアは Anker PowerConf スピーカーフォンを想定。ハードウェア側で
AEC（エコーキャンセル）+ ノイズ抑制を行うため、VAD の主目的は雑音除去では
なく**発話の終端検出（エンドポインティング）**。VAD エンジンは silero-vad を採用
（計算リソースは十分）。ストリーミング入力には silero 公式の **`VADIterator`** を使う。

## 用語

- **hold モード**: 現状の PTT。F9 押下中だけ録音、離した瞬間に発話確定。
- **vad モード**: 常時聴取。発話開始 → 一定無音で自動的に発話確定。キー操作不要。

## 既存アーキテクチャ（確認済み）

```
PttKeyboard.mic_gate (Out[bool])
   └─(autoconnect: mic_gate)→ LocalMicrophoneJa
                                  ├ _on_gate: gate 立ち上がりで録音開始 / 立ち下がりで _flush
                                  ├ _on_audio: 録音中フレームを _buffer に蓄積
                                  └ mic_utterance (Out[AudioEvent])
                                       └→ WhisperHumanInputJa → /human_input
```

- 関係ファイルはすべて **fork 固有**（`local_microphone_ja.py`,
  `ptt_keyboard.py`, `whisper_human_input_ja.py`）。自由に編集してよい。
- `node_key_recorder.py` は upstream 由来（触らない）。
- `pyproject.toml` は upstream 由来。依存追加は**1 行の最小差分**に留める。
- マイク既定: 16kHz mono int16、`block_size=1024`（=64ms）。silero v5 の
  512 サンプル(32ms)チャンクにちょうど 2 分割できる。

## 設計

### モード切り替えの仕組み

`LocalMicrophoneJaConfig` に振る舞いフィールドを追加する。

**env seed の方針**: 既存フィールド（`device_index` / `sample_rate` /
`block_size` / `max_utterance_seconds`）は**マシン・実行場所に依存する**ため
`DIMOS_LOCAL_MIC_*` の env seed を維持する。一方、本設計で追加する `mic_mode`
と VAD パラメータは**実行場所に依存しない振る舞い設定**なので env seed を持たせず、
**プロファイル `config.json` の `localmicrophoneja` キーのみ**で設定する
（env vs config 責務分離: 実行場所非依存の振る舞いは config 専管）。

| フィールド | 型 | 既定 | 用途 | VADIterator への対応 |
|---|---|---|---|---|
| `mic_mode` | `"hold" \| "vad"` | `"hold"` | モード選択。既定は現状維持 | — |
| `vad_threshold` | `float` | `0.5` | 発話確率の閾値 | `threshold=` |
| `vad_min_silence_ms` | `int` | `700` | この長さの連続無音で発話確定 | `min_silence_duration_ms=` |
| `vad_speech_pad_ms` | `int` | `300` | 発話前後に付けるパディング（頭/末尾切れ防止） | `speech_pad_ms=` + 自前プリロール |
| `vad_min_speech_ms` | `int` | `200` | これ未満の発話は破棄（誤検出除去） | アダプタ側でフィルタ |

- 既定 `mic_mode="hold"` により、設定しなければ**現状の挙動を完全維持**。
- vad モードは profile で `"localmicrophoneja": {"mic_mode": "vad"}` と書いて有効化。
- `PttKeyboard` は**変更せずバンドルに常駐**。vad モードでは `LocalMicrophoneJa`
  が `mic_gate` を購読しないだけで、blueprint の構成は静的なまま。

### コンポーネント

#### 1. `dimos/agents/vad_segmenter_ja.py`（新規・fork 固有）

**`VadStreamSegmenter`**: silero `VADIterator` をラップし、ストリーミング
音声フレームから発話を切り出す。

- 構築（DI）: silero モデルと `VADIterator` を生成する factory をコンストラクタ
  引数で受け取る（既定は本物の silero、テストではモックを注入）。これにより
  音声モデル無しで単体テスト可能。
- `VADIterator` 設定: `threshold=vad_threshold`,
  `min_silence_duration_ms=vad_min_silence_ms`, `speech_pad_ms=vad_speech_pad_ms`,
  `sampling_rate=16000`。
- `feed(frame: AudioEvent) -> AudioEvent | None`:
  1. 入力フレームを 512 サンプル@16kHz チャンクへ再チャンク（端数はキャリー
     バッファで次フレームへ持ち越し → 任意 `block_size` に対応）。
  2. 直近 `vad_speech_pad_ms` 分のサンプルをプリロール用リングバッファに保持。
  3. 各チャンクを `VADIterator` に food。返り値:
     - `{'start': ...}` → 録音状態に入る。発話バッファをプリロールで初期化。
     - `{'end': ...}` → 発話確定。長さが `vad_min_speech_ms` 未満なら破棄、
       それ以上なら結合済み `AudioEvent` を返す。`VADIterator.reset_states()`。
     - `None` → 録音中なら発話バッファへ追加。
- `max_utterance_seconds` 超過時の強制確定もここで扱う（保険）。

silero モデルは pip パッケージ同梱（`from silero_vad import load_silero_vad,
VADIterator`、ネットワーク不要）。エンジン交換が必要になった場合は本クラスを
差し替える（インターフェースは `feed()` のみ）。

#### 2. `LocalMicrophoneJa` 拡張（fork 固有）

- `tts_idle` 等のハーフデュプレックス制御は**入れない**（PowerConf の AEC に任せる。
  必要が生じた場合の対処は vad モード側で検討する）。新 In ポートは追加しない。
- `start()`:
  - `mic_mode=="vad"`: `VadStreamSegmenter` を構築（silero ロード）。`mic_gate` は
    購読しない。
  - `mic_mode=="hold"`: 現状通り `mic_gate` を購読、VAD は構築しない。
- `_on_audio(event)`:
  - hold モード: 現状のまま（`_recording` 中だけ `_buffer` へ）。
  - vad モード: `segmenter.feed(event)` が `AudioEvent` を返したら
    `mic_utterance.publish(...)`。返り値が None の間は何もしない。
- `inject_utterance`（bench replay 用）は**全経路を迂回**、変更なし。

#### 3. 依存追加

`pyproject.toml` の audio / whisper extra グループに `silero-vad` を 1 行追加。
追加後は `uv sync --extra all` で解決（`--all-extras` は不可）。

### エラー処理

- vad モードで silero のインポート / モデルロードに失敗 → `start()` で**明示的に
  例外**を投げ、インストール手順（`uv sync --extra all`）を案内。無設定で黙って
  ホットマイク化・無反応化させない（現行 `LocalMicrophoneJa` の「fail loud」方針を踏襲）。
- `max_utterance_seconds`(60s) は vad モードでも保険として有効。連続騒音等で
  VAD が閉じない場合に発話を強制確定。

## テスト戦略（TDD）

すべて `python -m pytest`（worktree 方針）で実行。

1. **`VadStreamSegmenter`（VADIterator モック注入）**:
   - `{'start'}` → `None`×N → `{'end'}` の系列で発話が組み立てられ、プリロールが
     先頭に含まれる
   - `vad_min_speech_ms` 未満の発話が破棄される
   - `{'end'}` で `reset_states()` が呼ばれ、次の発話を独立に切り出せる
   - `max_utterance_seconds` 超過で強制確定
2. **再チャンク**: 64ms フレームが 512 サンプルチャンクへ正しく分割される /
   block_size が 512 の倍数でない場合に端数がキャリーされる。
3. **`LocalMicrophoneJa` vad モード**: 偽 segmenter（任意のタイミングで
   `AudioEvent` を返す）で `mic_utterance` 発火を検証。
4. **hold モード回帰**: 既存テストが緑のまま（挙動完全維持）。

## スコープ外（YAGNI）

- silero 以外のエンジン実装（`feed()` を差し替えれば交換可能、だが本実装はしない）。
- ハーフデュプレックス / バージイン制御（tts_idle 連携）。
- WebUI / GUI からのモード切り替え（profile `config.json` のみ）。
- 複数同時話者の分離。
