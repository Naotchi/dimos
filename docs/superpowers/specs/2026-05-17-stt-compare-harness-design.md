# STT 精度比較ハーネス 設計

実施日: 2026-05-17
関連: [voice-live-rewrite-design](2026-05-14-voice-live-rewrite-design.md), `docs/private/速度計測_比較_2026-05-16.md`

## 背景と動機

`unitree-go2-agentic-voice-live` を採用したそもそもの理由は「managed な
Azure Voice Live のほうが STT の速度・精度で勝るだろう」という想定だった。
しかし `docs/private/速度計測_比較_2026-05-16.md` の実測では:

- local faster-whisper の `stt_s` p95 は 0.12s で、既に十分速い
- Voice Live が e2e first-audio で速いのは streaming response（LLM/TTS
  並走）の効果であって、STT 速度そのものではない

つまり「STT 速度」の前提は崩れた。残る採用根拠は **STT 精度**だが、これは
未計測。精度差が小さければ voice_live blueprint を廃止して local cascade
（`unitree-go2-agentic-local-tts`）に一本化する判断もありうる。

本ドキュメントは、その判断材料となる **STT 単独の主観比較ハーネス**を設計する。

## Goal / Non-Goals

### Goal

同一マイク入力に対する次 2 種類の transcript を、即座に並べて stdout に
表示する CLI を提供する:

- local faster-whisper（既存 `dimos/stream/audio/stt/node_whisper.py` と
  同じモデル・パラメータ）
- Azure Voice Live STT を transcription-only モードで叩いたもの

ユーザーが何ターンか喋り、「voice_live の STT 精度が local より明らかに
優れているか」を主観で判断する材料にする。

### Non-Goals

- CER の自動計算（ground truth を取らないため）
- セッション保存・wav 永続化
- LLM / tool / TTS との結合
- 既存 blueprint や agent との統合
- pytest による自動テスト（マイク必須のため）

## アーキテクチャ

3 つの独立コンポーネント + asyncio main loop。

```
       ┌──────────────────┐
       │  MicCapture (PTT)│  ← SPACE 押下中だけ録音
       └─────────┬────────┘
                 │ bytes (16-bit PCM, 同一 buffer)
        ┌────────┴────────┐
        ▼                 ▼
 ┌──────────────┐  ┌──────────────────────┐
 │ LocalStt     │  │ VoiceLiveStt         │
 │ faster-whisper│  │ transcription-only ws│
 └──────┬───────┘  └──────────┬───────────┘
        │                     │
        └──────────┬──────────┘
                   ▼
            stdout 比較表示
```

### 配置

`scripts/bench_stt_compare.py` 単一ファイル。`dimos/` 配下のソースは変更
しない（fork-local 検証用 / CLAUDE.md の「新規ファイル追加」方針に準拠）。

## コンポーネント詳細

### MicCapture

`sounddevice.RawInputStream` を薄くラップし、SPACE 押下〜離すまでの 16-bit
PCM を `bytearray` に append、release で凍結して返す。キー監視は
`pynput.keyboard.Listener`（既存 `PttKeyboard` と同じライブラリ）。

サンプルレートは Voice Live の入力フォーマットに合わせる（24 kHz 想定。
既存 `dimos/agents/realtime/azure_voice_live.py` の設定値を踏襲）。local
側に渡すときは faster-whisper が期待する 16 kHz へ `scipy.signal.resample_poly`
でダウンサンプル。

### LocalStt

`faster_whisper.WhisperModel` をプロセス常駐させ、`transcribe(audio_buffer)`
を呼ぶ。モデル名・`language="ja"`・`vad_filter` 等のパラメータは
`dimos/stream/audio/stt/node_whisper.py` と揃える（モデルのウォームアップも
起動時に 1 度行う）。

戻り値は `(text, latency_seconds)`。

### VoiceLiveStt

起動時に `azure.ai.voicelive.aio.connect` で session を 1 本張り、ターンごとに
使い回す。`RequestSession` は次のように構成する:

- `modalities=[]` 相当（テキスト応答も音声応答も生成させない）
- `input_audio_transcription` を有効化（言語: `ja`）
- VAD は OFF にして、こちらから `input_audio_buffer.commit` を明示送信

ターンごとの処理:

1. `input_audio_buffer.append`（PCM を base64 化）
2. `input_audio_buffer.commit`
3. `conversation.item.input_audio_transcription.completed` イベントを await
4. transcript を返す

戻り値は `(text, latency_seconds)`。`latency_seconds` は append 完了から
transcription.completed までを測る（ローカルの buffer 凍結時刻基準では
ない。ネットワーク往復を含む値として記録）。

環境変数は既存 `AzureVoiceLiveAgent` と同じ `DIMOS_AZURE_VOICE_LIVE_*` を
再利用する（endpoint / key / deployment 名）。

### 司令塔（main loop）

```
init: モデル warmup、VL session 接続、key listener 起動
loop:
  await SPACE down → 録音開始
  await SPACE up   → 録音停止、buffer 凍結
  local_text, local_ms, vl_text, vl_ms = await asyncio.gather(
      LocalStt.transcribe(buf),
      VoiceLiveStt.transcribe(buf),
  )
  pretty_print(local_text, local_ms, vl_text, vl_ms)
  「q」押下で終了
```

## 表示フォーマット

```
─── turn 3 ─────────────────────────────────────
Local Whisper :  立ち上がって挨拶してください          (1.23s)
Voice Live    :  立ち上がって愛さくしてください        (0.85s)
diff           :       ●●差分文字をハイライト●●

[SPACE] 録音 / [q] 終了
```

差分箇所は `difflib.ndiff` で抽出し、ANSI カラーで強調する。両者が完全
一致した場合は `match` の 1 行だけにする。

## エラー処理

- 起動時 VL session 接続失敗 → fail-fast でプロセス終了、原因を stderr に。
- ターン中の VL 切断 → 当該ターンを「[VL: 接続切れ]」と表示して継続、
  次ターンで再接続を試みる。
- local STT 例外 → 当該ターンを「[local: error <msg>]」と表示して継続。
- 片方だけ取れたターンも比較として残す（プロセスは止めない）。

## 環境・依存

- 追加 PyPI 依存なし。faster-whisper / azure-ai-voicelive / sounddevice
  / pynput / scipy はすべて既に `pyproject.toml` 経由で導入済。
- 起動: `python scripts/bench_stt_compare.py`（`.venv` 前提、CLAUDE.md
  の Python 実行ルール準拠）。
- 必須環境変数: `DIMOS_AZURE_VOICE_LIVE_ENDPOINT` 等（既存と同じ）。

## テスト方針

スクリプト本体は **手動実行のみ**。CI には載せない（マイク必須・Azure
課金発生のため）。各コンポーネントは `transcribe(bytes) -> (text, float)`
というシンプルな境界で書くので、必要が出てきたら後から個別 pytest 化は
可能。初版では unit test は書かない（YAGNI）。

## 本ハーネスの結果がもたらす判断

判断は以下のロジックで行う想定:

| 観測結果 | 推奨アクション |
|---|---|
| VL の方が明確に正確（実用上の誤認識が VL でのみ起きない） | voice_live 残し、graph 拡張は sidecar/tool 路線で吸収 |
| 同等 / 差が分からない | voice_live 廃止し、local cascade に一本化。拡張性を取る |
| local の方が正確 | 同上、迷わず local 一本化 |

判断そのものはこのハーネスのスコープ外。
