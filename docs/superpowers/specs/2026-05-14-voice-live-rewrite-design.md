# Voice Live エージェント書き直し設計

- 作成日: 2026-05-14
- ブランチ: `feat/voice-live`
- 旧 spec: `2026-05-14-go2-agentic-azure-voice-live-design.md`（廃止予定）

## 背景と動機

`feat/voice-live` ブランチで先行実装した `dimos/agents/voice_live/`
（`AzureVoiceLiveAgent` + `AzureVoiceLiveNode`）は、生 WebSocket を直接叩く
最小実装として作られた。foundry 生成の `voice-live-playground.py` を後追いで
比較したところ、現実装は以下の構造的問題を抱えていることが分かった:

- `session.update` に SDK 仕様外フィールド（`input/output_audio_sample_rate_hz`）
  を送っている。
- `turn_detection`（ServerVad）を送っていないため、サーバ側 VAD が走らない可能性。
- `error` イベントを処理していないため、サーバが拒否しても気付けない。
- バージインがない（AI 発話中にユーザが割り込めない）。
- `unitree-go2-agentic` の頭脳である `McpClient` の I/F（`human_input`、
  `agent`、`agent_idle`、`add_message`、`dispatch_continuation`、tool stream、
  画像ツール結果）に対応していない。`SpeakSkill` / `WebInput` を外した特殊
  blueprint としてだけ機能している。

「Voice Live バリアントを最低限動かす」のではなく、`unitree-go2-agentic` の
**頭脳ごと差し替えるドロップイン**として作り直し、ツール呼び出し・バージイン・
trigger continuation を含む全機能を備える。

## スコープ

### 含むもの

- `McpClient` の I/F 完全互換（`human_input` / `agent` / `agent_idle` /
  `add_message` / `dispatch_continuation`）
- Voice Live が STT + LLM + TTS + 関数呼び出しを 1 WS セッションで担当
- MCP `tools/list` から取得した tools を Voice Live function 形式に変換
- バージイン（`SPEECH_STARTED` → `response.cancel` + 再生キュー破棄）
- WebInput からのテキスト入力を音声会話と同じ session に流す
- `agent` Out への AIMessage emit（Web UI 表示用）
- `agent_idle` Out（`RESPONSE_CREATED`/`RESPONSE_DONE` で切り替え）
- system_prompt / model / voice を env / `ModuleConfig` で可変
- `azure.ai.voicelive` SDK 利用（生 WebSocket 撤廃）

### 含まないもの（明示）

- 画像ツール結果の Voice Live への注入（テキスト部分のみ抽出して返す。画像が
  あった場合は結果末尾に `[image omitted]` を付記）
- 単体テストの本実装（MVP 動作確認後に追加。スタブファイルのみ用意）
- WS 再接続バックオフ（SDK 既定挙動に委ね、失敗時は例外で停止）
- 連続会話品質チューニング（`ServerVad` は playground と同じ既定値）

## アーキテクチャ

### blueprint 構成

```python
unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),     # McpClient + SpeakSkill を置換
    WebInput.blueprint(),                # テキスト並行入力
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)
```

`McpClient` / `SpeakSkill` が外れる。`McpServer` と skill container 群はそのまま。

### モジュール構成

```
dimos/agents/realtime/
  __init__.py
  azure_voice_live.py        # AzureVoiceLiveAgent + 内蔵 _VoicePlayback
  prompts/
    __init__.py
    japanese.py              # 既定 system prompt
  test_azure_voice_live.py   # MVP 後に書き直し（スタブのみ）

dimos/agents/mcp/
  mcp_http.py                # McpHttpClient（McpClient と voice_live が共用）
  test_mcp_http.py
  mcp_client.py              # McpHttpClient を使うよう書き直し
```

`dimos/agents/realtime/` という namespace を新設する理由:
- McpClient は MCP プロトコルクライアント、Voice Live は realtime 双方向
  streaming で動く LLM エージェント — 構造的に別物
- 将来 OpenAI Realtime / Gemini Live など同類が増えても自然に並ぶ

### スレッディング

| スレッド | 役割 |
|---|---|
| Module メイン | ライフサイクル、`@rpc` 受信、Subject の `on_next` |
| WS worker | `asyncio.run()` で event loop を保持、SDK の `connect()` を `async with` で開きっぱなしにし、`async for event in conn` を回す |
| Tool worker (ThreadPoolExecutor) | MCP `tools/call`（httpx 同期）を WS worker から分離して実行 |
| sounddevice 内部 | mic capture / speaker callback |

Sounddevice からのコールバックは Subject 経由で WS worker の loop に
`asyncio.run_coroutine_threadsafe` でブリッジ。

### データフロー

| 入力 / イベント | 経路 |
|---|---|
| マイク音声 | Mic → AudioEvent → Subject → WS worker → `conn.input_audio_buffer.append(audio=base64)` |
| WebInput テキスト | `human_input` In → `conversation.item.create(role=user, input_text)` + `response.create` |
| `add_message` RPC | 同上（role はメッセージ型から判定） |
| Voice Live 音声出力 | `RESPONSE_AUDIO_DELTA (delta=bytes)` → `_VoicePlayback.enqueue(pcm)` |
| Voice Live テキスト | `RESPONSE_TEXT_DELTA` / `RESPONSE_AUDIO_TRANSCRIPT_DELTA` を累積、`RESPONSE_DONE` で `AIMessage` を `agent` Out に emit |
| function call | `RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE` → Tool worker → MCP `tools/call` → `conversation.item.create(function_call_output)` + `response.create` |
| バージイン | `INPUT_AUDIO_BUFFER_SPEECH_STARTED` → `_VoicePlayback.skip_pending()` + `conn.response.cancel()`（active 時のみ）|
| `agent_idle` | `RESPONSE_CREATED` で False、`RESPONSE_DONE` で True |
| `dispatch_continuation` RPC | LLM をバイパスし MCP を直接呼ぶ。結果は `conversation.item.create(role=user, "[continuation:foo] ...")` として注入（`response.create` は呼ばない）+ `agent` Out に `HumanMessage` emit |
| Tool stream（progress） | `conversation.item.create(role=user, "[tool:foo] ...")` で注入（`response.create` は呼ばない）+ `agent` Out emit |

### 設定 (`AzureVoiceLiveConfig`)

```python
endpoint: str            # DIMOS_AZURE_VOICE_LIVE_ENDPOINT
api_key: str             # DIMOS_AZURE_VOICE_LIVE_API_KEY
model: str = "gpt-realtime"
voice: str = "ja-JP-NanamiNeural"
system_prompt: str       # 未設定なら JAPANESE_SYSTEM_PROMPT
mcp_server_url: str = "http://localhost:9990/mcp"
mic_device_index: int | None = None
speaker_device_index: int | None = None
sample_rate: int = 24000
```

## 個別設計

### `_VoicePlayback`（azure_voice_live.py 内 private クラス）

playground の `AudioProcessor` 再生半分を移植。`SounddeviceAudioOutput` を
拡張せず voice_live 専用で持つ理由は、SpeakSkill 等の他利用者が flush 系
API を必要としないため。

```python
class _VoicePlayback:
    """Callback-driven sounddevice output with cancellable queue.

    - sd.OutputStream(callback=...) でカーネル要求量だけ pop
    - playback_queue: queue.Queue[_Packet(seq, bytes)]
    - skip_pending(): playback_base を進め、それ未満は破棄
    - enqueue(pcm_bytes): SDK の audio.delta を流し込む
    """
    def __init__(self, sample_rate: int, device_index: int | None): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def enqueue(self, pcm: bytes) -> None: ...
    def skip_pending(self) -> None: ...
```

### Mic 起動 gating

`SESSION_UPDATED` 受信までは mic からの AudioEvent を破棄。
`_mic_active = threading.Event()` を立て、subscriber 側で
`if not self._mic_active.is_set(): return`。これも playground 準拠。

### `McpHttpClient`（mcp_http.py に抽出）

`McpClient._mcp_request` / `_mcp_tool_call` / `_try_fetch_tools` から純粋な
HTTP 部分を抽出した共通クラス:

```python
class McpHttpClient:
    def __init__(self, url: str, timeout: float = 120.0): ...
    def wait_for_ready(self, timeout: float, interval: float = 1.0) -> bool: ...
    def list_tools(self) -> list[dict]: ...
    def call_tool(self, name: str, args: dict,
                  progress_token: str | None = None) -> dict: ...
    def close(self) -> None: ...
```

`McpClient` も `McpHttpClient` に委譲するよう書き直す。`StructuredTool`
ラップは `McpClient` 固有なのでそちらに残す。テキスト抽出ヘルパ
`extract_tool_text(result) -> str` も `mcp_http.py` に共有関数として置く
（画像があれば `[image omitted]` を末尾に付記）。

### `_mcp_to_voice_function`

```python
def _mcp_to_voice_function(mcp_tool: dict) -> Any:
    """MCP tool dict → Voice Live function tool（SDK の型を優先）.
    SDK の RequestFunctionTool（または同等の dataclass）がある場合はそれを
    使い、無ければ dict 構造で送る。
    """
```

SDK のバージョンによって型名・受理形式が変わりうるため、実装時に SDK 内部
を確認して具体化。

### `session.update`（最終形）

```python
RequestSession(
    modalities=[Modality.TEXT, Modality.AUDIO],
    instructions=cfg.system_prompt,
    voice=_voice_config(cfg.voice),       # ja-JP-* → AzureStandardVoice
    input_audio_format=InputAudioFormat.PCM16,
    output_audio_format=OutputAudioFormat.PCM16,
    turn_detection=ServerVad(
        threshold=0.5,
        prefix_padding_ms=300,
        silence_duration_ms=500,
    ),
    input_audio_echo_cancellation=AudioEchoCancellation(),
    input_audio_noise_reduction=AudioNoiseReduction(
        type="azure_deep_noise_suppression"
    ),
    tools=voice_tools,
)
```

### 起動順序（`McpServer` との同期）

`McpClient` と同じく `on_system_modules` RPC フックで WS worker を起動:

```python
@rpc
def on_system_modules(self, _modules: list[RPCClient]) -> None:
    if not self._thread.is_alive():
        self._thread.start()
```

`_thread` は内部で:
1. `mcp_http.wait_for_ready(timeout=60)`
2. `mcp_http.list_tools()`
3. `asyncio.run(self._async_run())` で SDK `connect()` を開く

### Tool worker による function call 処理

```
SDK event: RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE { call_id, name, arguments }
   ↓
WS worker:  self._tool_pool.submit(_run_tool_call, call_id, name, arguments)
   ↓
Tool worker:
  args = json.loads(arguments) or {}
  try:
      result = self._mcp_http.call_tool(name, args, progress_token=uuid())
      text = extract_tool_text(result)
  except Exception as exc:
      text = f"Error: {exc}"
  asyncio.run_coroutine_threadsafe(
      self._send_function_output(call_id, text), self._loop
  )
```

`_send_function_output` は `conversation.item.create(function_call_output)`
+ `response.create` の 2 段送信。

### `dispatch_continuation` セマンティクス

McpClient 互換: `$`-prefixed テンプレ変数を `continuation_context` から
解決。LLM をバイパスして MCP を直接呼ぶ。結果は `user` メッセージとして
session に注入し、`response.create` は呼ばない（次のユーザターンで文脈
として効く形）。同時に `agent` Out にも `HumanMessage` を流して Web UI
で見えるようにする。

### `agent` Out 構築

`RESPONSE_CREATED` → 累積バッファクリア、`agent_idle.emit(False)`。
`RESPONSE_TEXT_DELTA` または `RESPONSE_AUDIO_TRANSCRIPT_DELTA` を累積。
`RESPONSE_DONE` で `AIMessage(content=累積テキスト)` を `agent` Out に emit、
`agent_idle.emit(True)`。

`RESPONSE_TEXT_DELTA` が来ないモデルの場合は transcript のみで構築する。

### バージイン

```
INPUT_AUDIO_BUFFER_SPEECH_STARTED:
    1) _playback.skip_pending()
    2) if response_active and not response_done:
           try: await conn.response.cancel()
           except <"no active response"> エラーは黙殺
    3) agent_idle.emit(False)
```

「`no active response`」エラーは playground と同じ文字列マッチで黙殺。

## 変更ファイル一覧

### 削除

```
dimos/agents/voice_live/__init__.py
dimos/agents/voice_live/voice_live_agent.py
dimos/agents/voice_live/voice_live_node.py
dimos/agents/voice_live/japanese_prompt.py
dimos/agents/voice_live/test_voice_live_agent.py
dimos/agents/voice_live/test_voice_live_node.py
voice-live-playground.py   # 検証完了後に削除
```

### 新規

```
dimos/agents/realtime/__init__.py
dimos/agents/realtime/azure_voice_live.py
dimos/agents/realtime/prompts/__init__.py
dimos/agents/realtime/prompts/japanese.py
dimos/agents/realtime/test_azure_voice_live.py   # スタブ
dimos/agents/mcp/mcp_http.py
dimos/agents/mcp/test_mcp_http.py
```

### 編集

| ファイル | 変更 |
|---|---|
| `dimos/agents/mcp/mcp_client.py` | HTTP 部分を `McpHttpClient` に委譲 |
| `dimos/agents/mcp/__init__.py` | `McpHttpClient` を re-export（任意） |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py` | import path を新モジュールへ、blueprint 構成を再記述 |
| `pyproject.toml` | `azure-ai-voicelive>=1,<2` と `azure-identity>=1.15` を追加 |
| `README.md` | env 例と起動手順を更新 |

## 依存

追加:
- `azure-ai-voicelive` >= 1, < 2
- `azure-identity` >= 1.15

削除:
- なし。`websockets` は rerun viewer で利用中のため残す。

## 環境変数（最終形）

必須:
- `DIMOS_AZURE_VOICE_LIVE_ENDPOINT`
  - 例: `wss://<resource>.cognitiveservices.azure.com/` または HTTPS endpoint
    （SDK が WS へ変換する版を使う）
- `DIMOS_AZURE_VOICE_LIVE_API_KEY`

任意:
- `DIMOS_AZURE_VOICE_LIVE_MODEL` (既定: `gpt-realtime`)
- `DIMOS_AZURE_VOICE_LIVE_VOICE` (既定: `ja-JP-NanamiNeural`)
- `DIMOS_AZURE_VOICE_LIVE_SYSTEM_PROMPT` (未設定なら `JAPANESE_SYSTEM_PROMPT`)
- `DIMOS_AZURE_VOICE_LIVE_MCP_URL` (既定: `http://localhost:9990/mcp`)
- `DIMOS_AZURE_VOICE_LIVE_MIC_DEVICE`
- `DIMOS_AZURE_VOICE_LIVE_SPEAKER_DEVICE`

## テスト戦略

MVP 実装中は単体テストを書かず、手動 E2E で検証。実装完了後にスタブ:

- `test_azure_voice_live.py`
  - `azure.ai.voicelive.aio.connect` を AsyncMock で差し替え
  - スモーク: session.update が呼ばれる、`RESPONSE_AUDIO_DELTA` で
    `_VoicePlayback.enqueue` が呼ばれる、function call → MCP call →
    `function_call_output` 送信、`SPEECH_STARTED` → `response.cancel`
    + `skip_pending`
- `test_mcp_http.py`
  - `httpx.MockTransport` で initialize / tools/list / tools/call の正常系
    とエラー系

## 手動 E2E 検証手順

1. **playground.py で known-good 確立**
   ```bash
   uv pip install azure-ai-voicelive azure-identity python-dotenv pyaudio
   # .env に AZURE_VOICELIVE_ENDPOINT / API_KEY / MODEL / VOICE
   python voice-live-playground.py
   ```
   マイク入力 → 音声応答を確認。

2. **dimos 単体で sanity check**
   - playground と同じ env 値を `DIMOS_AZURE_VOICE_LIVE_*` に転記
   - `McpServer` だけ起動した状態で `AzureVoiceLiveAgent` を単独起動できる
     ことを最小スクリプトで確認

3. **blueprint 統合**
   ```bash
   uv run dimos run unitree-go2-agentic-voice-live
   ```
   - マイクで「立って」「歩いて」等の指示 → MCP ツール
     (`stand_up`, `walk_forward` 等) が叩かれることをログで確認
   - Web UI でテキスト入力 → 音声応答が返る
   - AI 発話中にマイクで割り込み → 即座に停止しユーザ発話に反応
   - person follow trigger を仕込んだ状態で人を映す →
     `dispatch_continuation` 経由で follow が起動

4. **playground スクリプトを削除**してコミット。

## リスク

- **`azure-ai-voicelive` の API surface 不安定**: バージョンによって
  `RequestSession` のフィールド名・関数 tool 形式が変わる可能性。pyproject
  でバージョン範囲を慎重に固定する。
- **`RESPONSE_TEXT_DELTA` の発火条件**: モデル設定によって TEXT modality が
  emit されない場合は transcript のみで fallback。
- **画像ツール結果**: 完全に捨てると LLM が困惑するので
  `[image omitted]` を結果末尾に付記する。
- **`McpClient` の HTTP 抽出による回帰**: `McpHttpClient` への置き換えで
  `McpClient` の既存テストが影響を受ける。リファクタを最小限に保ち、外部
  挙動は変えない。

## 完了基準

- [ ] `uv run dimos run unitree-go2-agentic-voice-live` がエラーなく起動
- [ ] マイクからの音声入力で AI が音声応答
- [ ] WebInput からのテキスト入力で AI が音声応答
- [ ] MCP ツール（`stand_up` 等）が音声指示で叩ける
- [ ] AI 発話中の割り込みで即座に停止
- [ ] trigger tool が `dispatch_continuation` 経由で発火する
- [ ] `agent` Out に AIMessage が emit され Web UI で見える
- [ ] 旧 `dimos/agents/voice_live/` ディレクトリと `voice-live-playground.py`
  が削除済み
- [ ] `McpClient` の既存テストが回帰なくグリーン
