# unitree-go2-agentic: Azure Voice Live 統合バリアント

- Date: 2026-05-14
- Target: 新規 blueprint `unitree_go2_agentic_voice_live`
- Scope: 新規 blueprint、新規 Agent モジュール `AzureVoiceLiveAgent`、新規 Node `AzureVoiceLiveNode`、`pyproject.toml`

## 目的

`unitree-go2-agentic` の STT + LLM + TTS を **Azure Voice Live API**（リアルタイム音声 WebSocket）1本に置き換えた新バリアント blueprint を提供する。

- DimOS PC のローカルマイク → Voice Live → ローカルスピーカーで完結する音声会話ループ
- 既存 MCP サーバの skill 群（navigation、unitree skill、person follow など）を Voice Live の function calling 経由で実行
- 既存 blueprint (`unitree_go2_agentic`) はそのまま残し、Azure 版は共存する別 blueprint として導入

起動コマンド: `uv run dimos run unitree-go2-agentic-voice-live`

## 現状

- 既存 `unitree_go2_agentic` (blueprints/agentic/unitree_go2_agentic.py) の音声/LLM 経路:
  - STT: `WebInput` (`dimos/agents/web_human_input.py`) がブラウザマイクを受け、`WhisperNode` でテキスト化し LCM `/human_input` に publish
  - LLM: `McpClient` (`dimos/agents/mcp/mcp_client.py`) が `/human_input` を購読、LangChain `create_agent(model="gpt-4o")` で MCP tool を呼ぶ
  - TTS: `SpeakSkill` (`dimos/agents/skills/speak_skill.py`) が `OpenAITTSNode` + `SounddeviceAudioOutput` で発話
- MCP サーバ (`dimos/agents/mcp/mcp_server.py`) は port 9990 で skill を Model Context Protocol で公開。`McpClient` 以外に外部 Claude Code 等からも利用される。
- 既存資産:
  - `dimos/stream/audio/node_microphone.py` の `SounddeviceAudioSource`（PC ローカルマイク）
  - `dimos/stream/audio/node_output.py` の `SounddeviceAudioOutput`（PC ローカルスピーカー）
  - `dimos/agents/mcp/mcp_adapter.py` の `McpAdapter`（langchain 非依存の MCP HTTP クライアント、`initialize` / `list_tools` / `call_tool_text` / `wait_for_ready`）

## アーキテクチャ

```
[DimOS PC マイク]
   │ SounddeviceAudioSource (PCM chunks)
   ▼
[AzureVoiceLiveNode] ── function_call ──→ [AzureVoiceLiveAgent]
   │                                          │
   │                                          ▼ McpAdapter.call_tool_text
   │                  ←── function_output ── [MCP server :9990] → skill 実行
   │ TTS audio chunks
   ▼
[SounddeviceAudioOutput]
   │
   ▼
[DimOS PC スピーカー]
```

blueprint:

```python
unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
    # WebInput は含めない
    # SpeakSkill は含めない（Voice Live 応答音声が代わり）
)
```

## 変更内容

### 1. 新規ファイル: `dimos/agents/voice_live/voice_live_node.py`

`AzureVoiceLiveNode` クラス（Module 派生ではなく、Module 内で抱える純粋なノード）。責務:

- Azure Voice Live への WebSocket 接続管理（接続、`session.update` 送信、切断時の指数バックオフ再接続、最大3回まで自動再試行）
- マイク PCM (`consume_audio`) を `input_audio_buffer.append` として WS に送信
- WS から受信した `response.audio.delta` を `AudioEvent` に組み立てて `emit_audio()` で出力
- WS から受信した `response.function_call_arguments.done` を `on_tool_call(name, args)` コールバックで通知
- 上位（Agent）から渡される `send_function_output(call_id, result_text)` で `conversation.item.create` (function_call_output) + `response.create` を送信

実装メモ:
- 内部は `asyncio` で 1 ループ。`reactivex` の Subject と `asyncio` の橋渡しは既存の pattern（`OpenAITTSNode` 等）に倣う
- `session.update` は接続成功直後に 1 回送る:
  - `model`: env から
  - `voice`: env から
  - `instructions`: 日本語 system prompt
  - `tools`: Agent から渡された function 定義リスト
- `tools` 定義は変換済みの形（次節）で受け取る

### 2. 新規ファイル: `dimos/agents/voice_live/voice_live_agent.py`

`AzureVoiceLiveAgent`（`Module` 派生）。`AzureVoiceLiveConfig` を持つ。

```python
class AzureVoiceLiveConfig(ModuleConfig):
    endpoint: str           # DIMOS_AZURE_VOICE_LIVE_ENDPOINT
    api_key: str            # DIMOS_AZURE_VOICE_LIVE_API_KEY
    model: str              # DIMOS_AZURE_VOICE_LIVE_MODEL
    voice: str              # DIMOS_AZURE_VOICE_LIVE_VOICE
    mcp_server_url: str = "http://localhost:9990/mcp"   # DIMOS_AZURE_VOICE_LIVE_MCP_URL
    mic_device_index: int | None = None                  # DIMOS_AZURE_VOICE_LIVE_MIC_DEVICE
    speaker_device_index: int | None = None              # DIMOS_AZURE_VOICE_LIVE_SPEAKER_DEVICE
```

env 値は `ModuleConfig` のデフォルトで `os.environ.get(...)` を使って読み、必須項目が未設定の場合は `start()` 内で `ValueError` を投げて即座にフェイル。

`start()` シーケンス:

1. 必須 env チェック → 不足時 `ValueError`
2. `self._mcp = McpAdapter(url=config.mcp_server_url)` + `wait_for_ready(timeout=30)`
3. `mcp_tools = self._mcp.list_tools()` → Voice Live 形式に変換（次節）
4. `SounddeviceAudioSource(device_index=mic_device_index, sample_rate=24000)` 構築
5. `SounddeviceAudioOutput(device_index=speaker_device_index, sample_rate=24000)` 構築
6. `AzureVoiceLiveNode(endpoint, api_key, model, voice, instructions=JAPANESE_SYSTEM_PROMPT, tools=converted_tools, on_tool_call=self._handle_tool_call)` 構築
7. `node.consume_audio(mic.emit_audio())`、`speaker.consume_audio(node.emit_audio())`
8. `node.start()`（WS 接続）

`_handle_tool_call(call_id, name, args_json)`:

- 別スレッド（`ThreadPoolExecutor`）で実行（WS 受信ループをブロックしない）
- `args = json.loads(args_json)` → `self._mcp.call_tool_text(name, args)`
- 成功時もエラー時も結果テキストを `self._node.send_function_output(call_id, text)` で返す
- 例外が起きたら例外メッセージを result として返す（LLM が「すみません、…」と発話して回復可能にする）

MCP → Voice Live 形式変換:

```python
voice_live_tools = [
    {
        "type": "function",
        "name": t["name"],
        "description": t.get("description", ""),
        "parameters": t["inputSchema"],
    }
    for t in mcp_tools
]
```

### 3. 新規ファイル: `dimos/agents/voice_live/japanese_prompt.py`

日本語 system prompt の定数 `JAPANESE_SYSTEM_PROMPT`。既存 `dimos/agents/system_prompt.py` の `SYSTEM_PROMPT` をベースに日本語化（Unitree Go2 のキャラクター・skill 利用方針）。

### 4. 新規ファイル: `dimos/agents/voice_live/__init__.py`

`AzureVoiceLiveAgent` を re-export。

### 5. 新規ファイル: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`

```python
from dimos.agents.voice_live import AzureVoiceLiveAgent
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)

__all__ = ["unitree_go2_agentic_voice_live"]
```

`dimos/robot/all_blueprints.py` に登録。

### 6. `pyproject.toml`

`agents` グループに `websockets>=13` を追加（現状未登録、Azure Voice Live への WS 接続に使用）。HTTP は既存の `httpx`（mcp 経由で既に入っている）を使用するため追加不要。

## 設定（環境変数まとめ）

| 環境変数 | 内容 | デフォルト | 必須 |
|---|---|---|---|
| `DIMOS_AZURE_VOICE_LIVE_ENDPOINT` | Voice Live WS エンドポイント | なし | ✅ |
| `DIMOS_AZURE_VOICE_LIVE_API_KEY` | API キー | なし | ✅ |
| `DIMOS_AZURE_VOICE_LIVE_MODEL` | モデルデプロイ名 | なし | ✅ |
| `DIMOS_AZURE_VOICE_LIVE_VOICE` | TTS ボイス名（例 `ja-JP-NanamiNeural`） | なし | ✅ |
| `DIMOS_AZURE_VOICE_LIVE_MCP_URL` | MCP サーバ URL | `http://localhost:9990/mcp` | ✕ |
| `DIMOS_AZURE_VOICE_LIVE_MIC_DEVICE` | sounddevice 入力デバイス index | OS デフォルト | ✕ |
| `DIMOS_AZURE_VOICE_LIVE_SPEAKER_DEVICE` | sounddevice 出力デバイス index | OS デフォルト | ✕ |

## エラー処理

| 事象 | 動作 |
|---|---|
| 必須 env 欠落 | `start()` で `ValueError` |
| MCP サーバ未起動 | `wait_for_ready(30s)` タイムアウトで起動失敗 |
| WS 接続失敗 | 指数バックオフで最大3回再試行 → なお失敗で起動失敗 |
| 実行中の WS 切断 | 指数バックオフで自動再接続、3 回連続失敗で `stop()` 自身を呼びログ通知（音声通知はしない） |
| function call の例外 | 例外メッセージを `function_call_output` の text として返し、LLM に説明させる |
| sounddevice デバイスエラー | sounddevice 例外を補足、利用可能デバイス一覧をログ、起動失敗 |

## 停止シーケンス

`AzureVoiceLiveAgent.stop()`:

1. `node.stop()` で WS クローズ + asyncio ループ停止
2. mic / speaker を `stop()` / `dispose()`
3. 進行中の tool call スレッドを `join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)`
4. `McpAdapter` の HTTP クライアントを close
5. `super().stop()`

## テスト戦略

### 単体テスト

- `dimos/agents/voice_live/test_voice_live_node.py`: WS をモックして送受信メッセージを検証
  - `session.update` の構造（model / voice / instructions / tools）
  - `response.audio.delta` → `emit_audio()` の AudioEvent
  - `response.function_call_arguments.done` → `on_tool_call` 呼び出し
  - `send_function_output` の WS メッセージ形式
  - 再接続ロジック（バックオフと最大試行回数）
- `dimos/agents/voice_live/test_voice_live_agent.py`: `McpAdapter` をモック
  - tool 変換（inputSchema → parameters）
  - `_handle_tool_call` が MCP に正しい引数で転送
  - env バリデーション

### 統合テスト（オフライン）

- 実 `McpServer` + モック WS で起動、function_call を流して skill が実行されるエンドツーエンドを確認

### 手動 E2E（Azure 実接続）

CI には含めず、`docs/` に手順:
1. 必要 env 設定し `uv run dimos run unitree-go2-agentic-voice-live` 起動
2. スモーク項目:
   - 「こんにちは」→ 日本語応答
   - 「1メートル前進して」→ Go2 が動き完了報告
   - 会話中の割り込み挙動
   - WS 強制切断 → 自動再接続

### 既存テストへの影響

- 既存 `unitree_go2_agentic` blueprint は変更しないため、既存 e2e_tests は影響なし。

## スコープ外（非ゴール）

- Go2 onboard マイク/スピーカー対応（WebRTC 経由のオーディオ）。今回は DimOS PC のローカルデバイスのみ。
- Voice Live が使えない場合のテキスト/ローカル STT/TTS へのフォールバック。
- 英語/多言語切替。今回は日本語固定。
- Web UI（テキスト入力、ログ表示）。Voice Live バリアントでは削除。
- 既存 `unitree_go2_agentic` blueprint の挙動変更。
