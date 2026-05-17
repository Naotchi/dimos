# unitree-go2-agentic-local-tts: 発話を assistant message に統合

## 背景

`unitree-go2-agentic-local-tts` は LLM に `speak(text: str)` tool を提供し、ユーザに伝えたい内容はその tool 呼び出しで話させている。一方で LLM が生成する assistant message の text content はユーザに届かない（テキスト UI を持たない）ため、`system_prompt_ja.py` で「テキストだけで返答するな、必ず `speak` を呼べ」と強制している。結果、

- assistant message text と `speak(text=...)` 引数が完全に重複し、トークン・レイテンシともに無駄。
- system prompt の半分が「speak を呼べ」のリマインダーに割かれている。
- 兄弟 blueprint `unitree-go2-agentic-voice-live`（Azure Voice Live）は assistant 応答をそのまま音声化するため、`local-tts` だけが二重構造になっている。

本 spec では `speak` tool を廃止し、assistant message の text content をそのまま OpenJTalk TTS にキューイングして再生する。結果として `local-tts` は voice-live と同じ「assistant 応答 = 発話」モデルになり、両者の差は実装軸（local pyopenjtalk + 自前 LLM step vs cloud Azure E2E）だけに整理される。

## ゴール

- LLM が応答 text を返したらそのまま発話される。
- LLM が tool だけ呼んで text を空で返したら無音。
- bench event 名 (`speak_invoke` / `first_audio_out` with `tool="speak"`) は維持し、既存 bench / replay スクリプトと既存 fixture (`logs/*-unitree-go2-agentic-local-tts*`) を破壊しない。
- upstream 由来ファイル（`speak_skill.py`、`mcp_client.py`、`system_prompt.py`、英語版 `unitree-go2-agentic` blueprint）は触らない。

## 非ゴール

- LLM token streaming レベルでの TTS 投入（将来別 spec）。
- 発話中の barge-in / 割り込み。
- 「次の tool 実行までに発話を終わらせる」同期保証（並列再生で割り切る）。
- `unitree-go2-agentic`（英語版）への波及。引き続き skill ベースの speak を使う。

## 設計判断（ブレストでの決定）

| 軸 | 決定 |
|---|---|
| TTS と次 tool の同期 | **並列 (fire-and-forget)**。assistant text を enqueue したら即座に次のステップへ。 |
| TTS への投入粒度 | **AIMessage 一括**。message 確定後に content 全体を一度に enqueue。文区切り分割はしない。 |
| TTS パイプライン配置 | **`speak_skill_ja.py` を全面改造**。新規ファイルは作らず、既存 fork-local ファイルの中身を差し替える。 |
| 旧 skill `speak` の扱い | **削除**。`JapaneseSpeakSkill` クラス自体を `AssistantSpeechNodeJa` に置き換える（fork-local なので rename 可）。 |
| bench event 名 | **流用**（`speak_invoke` / `first_audio_out` tool=`"speak"`）。bench/replay と既存 fixture との互換性を保つ。 |

## アーキテクチャ

```
LLM (TimedMcpClient)
   │  AIMessage (text content)
   ▼
mcp_client.agent : Out[BaseMessage]                   (既存)
   │  autoconnect は (name, type) 一致で wire
   ▼
AssistantSpeechNodeJa.agent : In[BaseMessage]         (新責務)
   │  AIMessage & non-empty str content だけ通す
   ▼
_text_subject : Subject[str]
   ▼
OpenJTalkTTSNode  ──emit_audio──▶  do_action(_on_audio_chunk)
                                          │
                                          ▼
                                SounddeviceAudioOutput (48 kHz)
```

`mcp_client.agent` は LLM step ごとの message を publish 済み (`mcp_client.py:331`, `mcp_client_ja.py:96`)。新規にフックポイントを切る必要はない。autoconnect の wiring は `(stream name, type)` ペアで照合される (`module_coordinator.py:539`) ため、subscriber 側の field 名も **`agent`** に揃える必要がある（`agent_message` 等の別名にすると別 topic 扱いになって繋がらない）。

## コンポーネント

### `dimos/agents/skills/speak_skill_ja.py`（全面差し替え）

旧 `JapaneseSpeakSkill(SpeakSkill)` を削除し、`AssistantSpeechNodeJa(Module)` に置き換える。

**フィールド**:
- `agent: In[BaseMessage]` — autoconnect が `McpClient.agent` から `(name="agent", type=BaseMessage)` 一致で繋ぐ。
- `_tts_node: OpenJTalkTTSNode`
- `_audio_output: SounddeviceAudioOutput`
- `_text_subject: Subject[str]` — TTS への投入チャネル。
- `_first_chunk_pending: bool`
- `_first_chunk_lock: threading.Lock`

**`start()`**:
1. `Module.start(self)` を呼ぶ（`SpeakSkill.start` 経由ではない）。
2. `_tts_node = OpenJTalkTTSNode()`
3. `_audio_output = SounddeviceAudioOutput(sample_rate=48000)`
4. `_text_subject = Subject[str]()`
5. `_tts_node.consume_text(self._text_subject)`
6. tapped = `_tts_node.emit_audio().pipe(ops.do_action(self._on_audio_chunk))`
7. `_audio_output.consume_audio(tapped)`
8. `self.register_disposable(Disposable(self.agent.subscribe(self._on_agent_message)))`

**`_on_agent_message(msg: BaseMessage)`**:
- `isinstance(msg, AIMessage)` でない → return。
- `isinstance(msg.content, str)` でない（list content の image など）→ return。
- `msg.content.strip() == ""` → return。
- 通った場合:
  - `log_bench_event("speak_invoke")`
  - `with _first_chunk_lock: _first_chunk_pending = True`
  - `_text_subject.on_next(msg.content)`

**`_on_audio_chunk(_chunk)`**:
- 現行 `JapaneseSpeakSkill._on_audio_chunk` と同一ロジック。`_first_chunk_pending` を 1 回だけ消化して `log_bench_event("first_audio_out", tool="speak")` を発火。

**`stop()`**:
- `_text_subject.on_completed()`（あれば）。
- `_tts_node.dispose()` / `_audio_output.stop()`。
- `Module.stop(self)`。

**`@skill` メソッドを定義しない**。MCP server が tool として登録しないので、LLM の tools list から `speak` が消える。

### `dimos/agents/system_prompt_ja.py`（fork-local、文面差し替え）

「# コミュニケーション」段落を以下に置換。

```
# コミュニケーション
ユーザはスピーカー経由であなたの声を聞きます。ユーザに伝えたいことは応答テキストとして
そのまま日本語で書いてください。発話は簡潔に 1〜2 文で。tool だけを実行して黙りたい時は
応答テキストを空にして tool_calls だけを返してください。
```

「# 振る舞い」配下の `speak` 言及（デリバリーとピックアップ）も、`speak("…")` → 「応答テキストで…と伝える」に書き換える。

### `dimos/robot/unitree/go2/blueprints/agentic/_common_agentic_ja.py`（fork-local）

- import: `from dimos.agents.skills.speak_skill_ja import JapaneseSpeakSkill` → `from dimos.agents.skills.speak_skill_ja import AssistantSpeechNodeJa`。
- blueprint list: `JapaneseSpeakSkill.blueprint()` → `AssistantSpeechNodeJa.blueprint()`。

### `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts.py`

無変更。`_common_agentic_ja` 経由で透過的に切り替わる。

### 触らないもの（upstream 由来 or 別系統）

- `dimos/agents/skills/speak_skill.py` — upstream、英語 blueprint で利用継続。
- `dimos/agents/skills/speak_skill_spec.py` — 同上。
- `dimos/agents/mcp/mcp_client.py` — upstream、`agent: Out[BaseMessage]` を既に提供。
- `dimos/agents/mcp/mcp_client_ja.py` — fork-local だが今回は変更不要。
- `dimos/agents/system_prompt.py` — upstream、英語版で利用継続。
- `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py`（英語）/ `unitree_go2_agentic_voice_live.py`（Azure）— 影響なし。

## データフロー

### 通常ターン（発話あり）

1. `human_input` → `_message_queue` → `TimedMcpClient._process_message`。
2. LangGraph stream の各 step で msg を `self.agent.publish(msg)`（既存）。
3. `AssistantSpeechNodeJa._on_agent_message(msg)` が走る。
4. `AIMessage` & 非空 `str` content のみフィルタ通過。
5. `log_bench_event("speak_invoke")` → `_first_chunk_pending=True` → `_text_subject.on_next(text)`。
6. OpenJTalk が合成 → SounddeviceAudioOutput に流れ、最初の chunk で `first_audio_out` 発火。
7. **再生中も MCP client は次の step に進む**（並列方針）。

### tool 呼び出しのみのターン

- assistant message の content が `""` または list / tool_calls だけ → フィルタで弾く → 無音。
- LLM 側に追加制御は不要（system prompt で「黙りたければ text を空に」と指示するだけ）。

### multi-step 応答（tool → assistant text → tool → assistant text）

- 各 AIMessage step ごとに上記が走り、TTS キューに直列で積まれる。
- `OpenJTalkTTSNode` + `Subject` が自然にシリアライズするので追加の lock は不要。

### HumanMessage / ToolMessage

- `agent` ストリームに publish はされる（既存挙動）。
- `AssistantSpeechNodeJa` 側で `AIMessage` 判定により弾かれる。

## エラー処理

| ケース | 挙動 |
|---|---|
| TTS 例外 (`on_next` 経由) | LLM ループは止めない。`_text_subject` に `on_error` を subscribe してログのみ。 |
| `start()` 完了前の message | autoconnect は `start` 完了後に subscribe するので通常発生しない。防御的に `_text_subject is None` ならドロップして warn。 |
| 音声デバイス unavailable | 現行と同じく `SounddeviceAudioOutput` の例外で `start` 失敗 = blueprint 起動失敗。挙動変更なし。 |
| 長文 / 再生途中で次ターン | `OpenJTalkTTSNode` のキューに後続文が積まれる。割り込みは行わない（barge-in は非ゴール）。 |
| 旧 `_speak_blocking` のタイムアウト | **廃止**。fire-and-forget なのでタイムアウト概念なし。 |

## テスト

### 既存テスト（破壊しないこと）

- `tests/scripts/test_bench_agentic_local_tts_analyzer.py` — `speak_invoke` / `first_audio_out` event 名を流用するため変更不要。CI で pass を確認。
- `scripts/replay_agentic_local_tts.py` — 既存 fixture は「speak tool call が含まれる古い trace」だが、bench event 名が同じなので analyzer 側は壊れない。新 trace を取り直したい場合のみ `scripts/gen_fixtures_agentic_local_tts.py` で再生成。

### 新規テスト

`tests/agents/skills/test_assistant_speech_node_ja.py`（fork 固有・新規可）。`OpenJTalkTTSNode` と `SounddeviceAudioOutput` はモック。

検証項目:
1. `AIMessage(content="こんにちは")` → `_text_subject.on_next("こんにちは")` が 1 回呼ばれる。
2. `AIMessage(content="", tool_calls=[…])` → `on_next` は呼ばれない。
3. `AIMessage(content="   \n  ")`（空白のみ）→ 呼ばれない。
4. `HumanMessage("…")` / `ToolMessage(...)` → 呼ばれない。
5. `AIMessage(content=[{"type": "text", ...}, {"type": "image", ...}])`（list content）→ 呼ばれない。
6. `speak_invoke` bench event が 1 message につき 1 回 emit される。
7. `first_audio_out` が最初の audio chunk 受信時に 1 回だけ emit され、2 chunk 目では emit されない（次の message が来るまで）。

### 手動確認

`unitree-go2-agentic-local-tts` blueprint を起動して:
- 音声入力 → 応答が喋られる。
- tool 呼び出しのみのターンが無音。
- 「navigate して報告して」のように tool → 発話 → tool が連続する応答も自然に喋れる。

## 段階移行

- skill 削除と node 追加は同一 PR で行う（中途半端な状態を残さない）。
- 既存 `logs/` 配下の trace は古い形式のままで構わない（analyzer 互換）。
