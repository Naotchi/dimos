# go2-agentic 日本語化 設計

## 目的

`unitree_go2_agentic` ブループリント経由で起動した Go2 エージェントを、入出力ともに日本語で動作させる。具体的には:

- ユーザの日本語音声が「human message」として日本語のまま転記される（現状は英語に変換されてしまう）
- LLM が日本語で応答する
- TTS が日本語ボイスで読み上げる

## スコープ

- 対象は `unitree_go2_agentic` ブループリントとその直接の依存（`_common_agentic`）のみ
- `dimos/stream/audio/stt/node_whisper.py`、`dimos/stream/audio/tts/node_pytts.py`、`dimos/agents/web_human_input.py`、`dimos/agents/skills/speak_skill.py` のライブラリ層は **英語デフォルトのまま** とし、optional パラメータで日本語化できるよう拡張する
- 他ブループリント（drone、g1、manipulation 等）への影響なし

## 現状の問題点

1. **STT (Whisper)** — `WhisperNode.__init__` の `modelopts` 既定が `{"language": "en", "fp16": False}` (`dimos/stream/audio/stt/node_whisper.py:62`)。`WebInput.start()` が引数なしで `WhisperNode()` を生成するため (`dimos/agents/web_human_input.py:59`)、日本語音声も英語として転記される
2. **System prompt** — `dimos/agents/system_prompt.py` の `SYSTEM_PROMPT` が全文英語。`unitree_go2_agentic.py` は `McpClient.blueprint(model=_LLM_MODEL)` で起動しており system_prompt を明示しないため英語の既定が使われ、LLM が英語応答へ誘導される
3. **TTS (pyttsx3)** — `PyTTSNode` は OS の既定ボイスを使うため、日本語テキストを英語ボイスで読み上げる（既定 backend は `DIMOS_TTS=pyttsx3`）

## 設計

### 1. STT: WebInput への `whisper_language` パラメータ追加

`dimos/agents/web_human_input.py`:

- `WebInput` クラスに `whisper_language: str = "en"` 属性を追加
- `start()` 内の `WhisperNode()` 呼び出しを `WhisperNode(modelopts={"language": self.whisper_language, "fp16": False})` に変更

`dimos/robot/unitree/go2/blueprints/agentic/_common_agentic.py`:

- `WebInput.blueprint(whisper_language="ja")` に変更

### 2. System prompt: 日本語版を新規作成

新規ファイル `dimos/agents/system_prompt_ja.py`:

- 既存 `SYSTEM_PROMPT` を日本語訳した `SYSTEM_PROMPT_JA` を定義
- "Daneel" の名乗りや SAFETY/IDENTITY/COMMUNICATION/SKILL COORDINATION/BEHAVIOR セクションを日本語化
- "daniel" 等の英語誤認識ゆれは「ダニエル」「だにえる」など日本語表記のゆれに置換
- スキル名（`navigate_with_text`、`tag_location`、`speak` など）は **英語のまま残す**（実装シンボルなので翻訳不可）

`dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py`:

- `from dimos.agents.system_prompt_ja import SYSTEM_PROMPT_JA` を追加
- `McpClient.blueprint(model=_LLM_MODEL, system_prompt=SYSTEM_PROMPT_JA)` に変更

### 3. TTS: PyTTSNode に `voice_lang` パラメータ追加

`dimos/stream/audio/tts/node_pytts.py`:

- `PyTTSNode.__init__` に `voice_lang: str | None = None` を追加
- 指定時は `self.engine.getProperty('voices')` を走査し、`voice.languages` のいずれかが指定言語コードで始まる、または `voice.id` / `voice.name` に当該コードを含む最初のボイスを選んで `engine.setProperty('voice', matched.id)`
- 該当ボイスが見つからない場合は `logger.warning` を出し、既定ボイスのまま動作続行（OS にボイスが入っていない場合の防衛）
- `OpenAITTSNode` 側の経路は変更しない（OpenAI TTS はテキスト判定で多言語対応するため）

`dimos/agents/skills/speak_skill.py`:

- `SpeakSkill` に `voice_lang: str | None = None` 属性を追加
- `start()` 内 `pyttsx3` ブランチで `PyTTSNode(voice_lang=self.voice_lang)` を渡す
- `openai` ブランチは現状維持

`dimos/robot/unitree/go2/blueprints/agentic/_common_agentic.py`:

- `SpeakSkill.blueprint(voice_lang="ja")` に変更

### 4. ドキュメント

`README.md`（go2-agentic セクション付近）:

- 日本語動作時の注意を1〜2行追記
  - 例: `pyttsx3` の日本語ボイスは OS 側で必要（Linux なら `espeak-ng` + `mbrola-mb-jp1` 等）
  - 高品質を求めるなら `DIMOS_TTS=openai` を推奨

## 影響範囲

| ファイル | 変更内容 |
| --- | --- |
| `dimos/agents/web_human_input.py` | `whisper_language` 属性追加、`WhisperNode` 生成に反映 |
| `dimos/agents/system_prompt_ja.py` | 新規。`SYSTEM_PROMPT_JA` を定義 |
| `dimos/stream/audio/tts/node_pytts.py` | `voice_lang` パラメータとボイス選択ロジック追加 |
| `dimos/agents/skills/speak_skill.py` | `voice_lang` 属性追加、`PyTTSNode` へ伝搬 |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py` | `SYSTEM_PROMPT_JA` を `McpClient.blueprint` に渡す |
| `dimos/robot/unitree/go2/blueprints/agentic/_common_agentic.py` | `WebInput.blueprint(whisper_language="ja")` と `SpeakSkill.blueprint(voice_lang="ja")` |
| `README.md` | 日本語ボイス導入の注意書きを追記 |

## テスト

- 既存 `dimos/agents/skills/tests/test_speak_skill_env.py` を壊さないこと（`voice_lang` は optional、デフォルト `None` で旧挙動と同等）
- 新規単体テスト:
  - `PyTTSNode(voice_lang="ja")` がボイス一覧から日本語ボイスを選んで `setProperty('voice', ...)` を呼ぶ（`pyttsx3.init` をモック）
  - 該当ボイスがない場合は警告ログを出して `setProperty('voice', ...)` を呼ばない
- 手動確認:
  - go2-agentic 起動 → ブラウザから日本語音声を発話 → human message が日本語で記録される
  - LLM が日本語で応答する
  - スピーカーから日本語ボイスで読み上げられる（pyttsx3 日本語ボイス導入済みの環境で）

## 非対象（YAGNI）

- 他言語（中国語、韓国語等）への一般化 — 必要になった時点で対応
- `DIMOS_LANG` のような言語切替 env var — go2-agentic ハードコードでよい
- `OpenAITTSNode` への `voice` 言語切替 — 日本語テキストをそのまま渡せば動作するため不要
- `system_prompt.py`(英語) の改変 — 他ブループリントの既定として残す
