# Voice Live 強制発話設計

`unitree-go2-agentic-voice-live` で発話保証をコード側に組み込み、LLM 任せでは出ないことが多い preface 発話と tool 実行後報告を確実に出す。

## 背景と問題

現状の `AzureVoiceLiveAgent` (`dimos/agents/realtime/azure_voice_live.py`) は、Voice Live が返す `response.audio.delta` をそのまま playback queue に流すだけで、何を喋るかは完全に LLM (gpt-realtime) 任せ。

実運用で観測される問題:

- ユーザ発話に対して **tool 呼び出しだけが入り音声応答が無い** response がしばしば生成される。
- system prompt に「ツールを呼ぶ時も必ず短い音声応答を伴う」と書いても、Voice Live / gpt-realtime は同 response 内で audio を省略することがある。
- `_send_function_output` の `response.create()` は引数なしで呼ばれているため、tool 実行後の発話も「するかしないか」も含めて LLM 任せ。

## 要件

1. **Preface 常時保証**: ユーザ発話起点の response は必ず音声を1回以上含んで終わる（tool 有無を問わず）。
2. **Tool 別 post-execution 発話**: `report_after_tools` に列挙された tool の実行後は音声で結果報告する。それ以外の tool 実行後は完全無音とする。
3. **発話順序の保証**: tool 呼び出しがある場合、`発話 → tool 実行 → (必要なら) 発話` の順序を守る。Preface が落ちていれば tool 実行**前**に補完する。
4. **無限ループ防止**: 補完目的で発火した response が audio 無しで終わっても再強制しない。

## 非要件

- アクション系 tool の完了 ack（「完了しました」程度の発話）。要件 2 の「無音」で扱う。
- ローカル TTS による preface フォールバック。LLM 再発話で吸収する。
- Skill / MCP tool 側に発話メタ情報（`voice_report_after_call` 等）を持たせること。`AzureVoiceLiveConfig.report_after_tools` のリストで一元管理する。

## アーキテクチャ

`AzureVoiceLiveAgent` 内に「response 単位の state machine」を追加し、Voice Live event stream を `_on_response_done` でルーティングする。新規モジュール / ファイルは作らず `azure_voice_live.py` 内で完結させる。

```
[Voice Live WS event stream]
        │
        ▼
 _handle_event
   ├─ RESPONSE_CREATED      → per-response state reset
   ├─ RESPONSE_AUDIO_DELTA  → _resp_had_audio = True, playback enqueue
   ├─ RESPONSE_*_TRANSCRIPT_DELTA → text buf に追加
   ├─ RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE
   │     → _resp_pending_calls.append((call_id, name, args))  (dispatch しない)
   └─ RESPONSE_DONE         → _on_response_done() を asyncio.create_task で起動
        │
        ▼
 _on_response_done
   ├─ user turn かつ audio 無し → _force_preface(pending_calls) を await
   └─ pending_calls を順に _dispatch_and_wait()
        │
        ▼
 _dispatch_and_wait
   ├─ MCP 呼び出し（executor）
   ├─ function_call_output 送信
   └─ name in report_after_tools
        ├─ True  → response.create(audio + 結果報告 instructions) を await
        └─ False → 何もしない（次の user 入力まで session は idle）
```

### 不変条件

1. ユーザ発話起点の response は audio を1回以上含んで終わる。
2. `report_after_tools` に登録された tool の実行後は音声報告 response が必ず1回流れる。
3. それ以外の tool 実行後は session が idle に戻り、次の user 入力を待つ。

## State 設計

`AzureVoiceLiveAgent` instance に以下を追加（すべて 1 response のライフサイクル分のみ）。

```python
self._resp_had_audio: bool = False
self._resp_pending_calls: list[tuple[str, str, str]] = []  # (call_id, name, args_json)
self._resp_text_buf: list[str] = []   # 既存 _response_text_buf を改名
self._resp_trigger: str = "user"      # "user" | "tool_result" | "preface_forced"
self._next_trigger: str | None = None # 次の RESPONSE_CREATED 用予約
self._resp_done_event: asyncio.Event | None = None  # 直列化用
```

`RESPONSE_CREATED` で `_resp_had_audio = False`, `_resp_pending_calls = []`, `_resp_text_buf = []` をリセットし、`_resp_trigger = self._next_trigger or "user"` を確定、`_next_trigger = None`。

### `_resp_trigger` の決定

`response.create` を呼ぶ直前に `_next_trigger` をセットし、`RESPONSE_CREATED` ハンドラで `_resp_trigger = self._next_trigger or "user"` と確定する。`_next_trigger` は消費したら `None` にリセット。

| Response 発生元 | `_resp_trigger` |
|---|---|
| ユーザ音声入力（VAD 自動 / `_send_user_text(prompt_response=True)` / `add_message` / `dispatch_continuation` の経路） | `"user"` |
| `_dispatch_and_wait` 内の `response.create` | `"tool_result"` |
| `_force_preface` 内の `response.create` | `"preface_forced"` |

### Preface 強制の発火条件

`_on_response_done` 時点で **両方** 満たす場合のみ。

- `_resp_trigger == "user"`
- `_resp_had_audio == False`

`"tool_result"` および `"preface_forced"` の response が audio 無しで終わっても再強制しない（無限ループ防止）。

## フロー詳細

### `_on_response_done`

スナップショットは `_handle_event` 側で行い、`_on_response_done` には不変な dataclass として渡す。

```python
@dataclass
class _ResponseSnapshot:
    trigger: str
    had_audio: bool
    pending_calls: list[tuple[str, str, str]]
    text: str  # response_text_buf を結合した値

async def _on_response_done(self, snap: _ResponseSnapshot) -> None:
    self._finalize_response_text(snap)  # AIMessage publish / agent_idle.publish(True)

    if snap.trigger == "user" and not snap.had_audio:
        await self._force_preface(snap.pending_calls)

    for call_id, name, args in snap.pending_calls:
        await self._dispatch_and_wait(call_id, name, args)
```

### `_force_preface`

```python
async def _force_preface(self, pending_calls) -> None:
    if pending_calls:
        names = ", ".join(n for _, n, _ in pending_calls)
        prompt = (
            f"これから {names} を実行することを、"
            f"日本語で1〜2語の短い音声で伝えてください。ツールは呼ばない。"
        )
    else:
        prompt = "ユーザに日本語で短く一言返事をしてください。"

    self._next_trigger = "preface_forced"
    self._resp_done_event = asyncio.Event()
    await self._conn.response.create(response={
        "modalities": ["audio", "text"],
        "instructions": prompt,
    })
    await self._resp_done_event.wait()
    self._resp_done_event = None
```

### Event loop と直列化の責務分離

`_handle_event` 内で `_on_response_done` を直接 `await` してはいけない。`_event_loop` の `async for event in self._conn` が止まり、preface 強制 response の `RESPONSE_DONE` を受信できずデッドロックする。

正しい構成:

```python
# _handle_event 内
elif et == ServerEventType.RESPONSE_DONE:
    # event から必要な値をスナップショットして渡す
    snapshot = self._snapshot_response_state()
    self._reset_response_state()
    if self._resp_done_event is not None:
        self._resp_done_event.set()  # _force_preface / _dispatch_and_wait の await を解放
    asyncio.create_task(self._on_response_done(snapshot))
```

`_on_response_done` のタスクが `_force_preface` 内で `await self._resp_done_event.wait()` してブロックしている間も、`_event_loop` は次のイベントを受信し続け、preface response の `RESPONSE_DONE` で `_resp_done_event.set()` を呼ぶ。

`_on_response_done` の同時実行は起きない: user turn 終端で 1 タスク生成 → 内部で preface/tool 結果の RESPONSE_DONE が来ると `set()` で先行タスクが進むだけ。新しいタスクは生成しない（snapshot.trigger == "user" の場合のみ pending を処理するロジックではあるが、より明確にするため `_on_response_done` 起動は trigger="user" のときに限定する）。

```python
elif et == ServerEventType.RESPONSE_DONE:
    trigger = self._resp_trigger
    snapshot = self._snapshot_response_state()
    self._reset_response_state()
    if self._resp_done_event is not None:
        self._resp_done_event.set()
    if trigger == "user":
        asyncio.create_task(self._on_response_done(snapshot))
    else:
        # preface_forced / tool_result の終端処理（text publish 等）
        self._finalize_response_text(snapshot)
```

### `_dispatch_and_wait`

```python
async def _dispatch_and_wait(self, call_id, name, args_json) -> None:
    output = await self._loop.run_in_executor(
        self._tool_pool, self._invoke_mcp, name, args_json
    )

    await self._conn.conversation.item.create(item={
        "type": "function_call_output",
        "call_id": call_id,
        "output": output,
    })

    if name in self.config.report_after_tools:
        self._next_trigger = "tool_result"
        self._resp_done_event = asyncio.Event()
        await self._conn.response.create(response={
            "modalities": ["audio", "text"],
            "instructions": "直前のツール結果を日本語で1文に要約して音声で報告してください。",
        })
        await self._resp_done_event.wait()
        self._resp_done_event = None
    # silent パス: response.create を呼ばない
```

`_invoke_mcp` は既存の `_run_function_call` から MCP 呼び出し部分を切り出した同期関数。

### バージイン対応

`INPUT_AUDIO_BUFFER_SPEECH_STARTED` ハンドラで、既存の `playback.skip_pending()` + `response.cancel()` に加え、`_resp_done_event` がセットされていれば `set()` を呼んで await を解放する。`_resp_pending_calls` は破棄（次の `RESPONSE_CREATED` で reset されるが、明示的に空にしておく）。

## 設定とプロンプト

### `AzureVoiceLiveConfig` への追加

```python
report_after_tools: set[str] = Field(
    default_factory=lambda: _parse_tool_set(
        f"{_ENV_PREFIX}REPORT_AFTER_TOOLS",
        {"observe", "current_time"},
    )
)
```

ヘルパー:

```python
def _parse_tool_set(env_name: str, default: set[str]) -> set[str]:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    return {s.strip() for s in raw.split(",") if s.strip()}
```

env で `DIMOS_AZURE_VOICE_LIVE_REPORT_AFTER_TOOLS=observe,current_time,describe_scene` のように上書き可能。空文字列は「全 tool silent」と解釈する。

### デフォルトの分類

現在の `unitree_go2_agentic_voice_live` blueprint が公開する skill から分類:

| Tool | カテゴリ | Default in `report_after_tools` |
|---|---|---|
| `observe` | 情報取得 | ✓ |
| `current_time` | 情報取得 | ✓ |
| `relative_move` | アクション | ✗ |
| `wait` | アクション | ✗ |
| `execute_sport_command` | アクション | ✗ |
| `follow_person` | アクション | ✗ |
| `stop_following` | アクション | ✗ |
| `tag_location` | アクション | ✗ |
| `navigate_with_text` | アクション | ✗ |
| `stop_navigation` | アクション | ✗ |

### `prompts/japanese.py`

```python
JAPANESE_SYSTEM_PROMPT = """\
あなたは Unitree Go2 という四足歩行ロボットに搭載された日本語音声アシスタントです。

行動原則:
- ユーザの発話には簡潔で自然な日本語の音声で応答する。
- ロボットの動作や情報取得を指示されたら、提供されているツールを呼び出して実行する。
- ツールを呼ぶ前に、必ず短い一言（例:「はい、進みます」「確認します」）を音声で発してから呼び出す。
- 必要に応じてカメラやセンサーのツールを使って状況を確認してから動く。
- ツール結果に「エラー」と書かれていた場合は、内容を要約してユーザに伝える。
"""
```

「実行後に報告するかどうか」はプロンプトに書かない。コード側の `report_after_tools` と response-level instructions で制御するため、プロンプトに書くと LLM が二重判断して挙動が不安定になる。

### `_send_session_update`

変更なし。session レベルの `modalities=[TEXT, AUDIO]` 設定はそのまま。すべての強制動作は per-response の `response.create` の `instructions` / `modalities` で行う。

## テストと検証

### ユニットテスト

`tests/agents/realtime/test_azure_voice_live_forced_speech.py`（新規）。Voice Live WS は実接続せず、`_conn` と `_loop` をモックして `_handle_event` / `_on_response_done` を直接駆動する。MCP も `McpAdapter` をモック。

| # | シナリオ | 期待 |
|---|---|---|
| 1 | user audio → response (audio あり、tool 無し) | preface 強制 `response.create` が呼ばれない |
| 2 | user audio → response (audio 無し、tool 無し) | `instructions="ユーザに日本語で短く一言..."` で `response.create` が呼ばれる |
| 3 | user audio → response (audio あり、`relative_move` call あり) | preface 強制なし、`relative_move` dispatch、その後 `response.create` が呼ばれない |
| 4 | user audio → response (audio あり、`observe` call あり) | dispatch 後 `instructions="直前のツール結果を..."` で `response.create` |
| 5 | user audio → response (audio 無し、`relative_move` call あり) | preface 強制（`これから relative_move を...`）→ dispatch → silent |
| 6 | preface 強制 response が audio 無しで終わる | 再強制しない |
| 7 | tool_result response が audio 無しで終わる | 再強制しない |
| 8 | dispatch 中に `SPEECH_STARTED` | `_resp_done_event.set()`、pending 破棄、cancel 経路に乗る |

各シナリオで `mock_conn.response.create.call_args_list` を assert する。

### bench / replay 互換性

`reset_bench_turn` / `_first_audio_emitted` / `_first_tool_call_emitted` は触らない。preface 強制 response も `RESPONSE_AUDIO_DELTA` を出すので `first_audio_out` の bench metric は壊れない（むしろ安定する）。

### 手動テスト

実機 / マイク経由で以下を確認:

- 「前に進んで」 → 「はい、進みます」音声 → `relative_move` 実行 → 無音
- 「周りを見て」 → 「確認します」音声 → `observe` 実行 → 「正面に〇〇があります」音声報告
- 「こんにちは」 → 「こんにちは、ご用件は？」のような音声のみ（tool 呼ばない）
- LLM がたまたま preface を出した場合に追加 response が発火しないこと（重複発話しない）
