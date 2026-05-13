# unitree-go2-agentic: LLM env 化 + ローカル TTS 切替

- Date: 2026-05-13
- Target: `unitree-go2-agentic` blueprint
- Scope: 単一 blueprint (`unitree_go2_agentic.py`) と `SpeakSkill`、`pyproject.toml`

## 目的

`unitree-go2-agentic` blueprint を以下の構成で起動可能にする：

- LLM モデルを環境変数で指定（プロバイダ切替を含む）
- TTS を環境変数でローカル (pyttsx3) と OpenAI から選択、デフォルトはローカル

オフライン（OpenAI API キー無し）で `DIMOS_LLM_MODEL=ollama:...` + `DIMOS_TTS=pyttsx3` の組み合わせで完走できることをゴールとする。

## 現状

- LLM: `McpClientConfig.model: str = "gpt-4o"` (mcp_client.py:46)。値は LangChain `create_agent(model=...)` にそのまま渡され、`openai:` / `ollama:` / `anthropic:` などの prefix で provider 判定される。blueprint 側は `McpClient.blueprint(model="...")` で上書き可能（`_ollama` 版が `"ollama:qwen3:8b"` を渡している）。
- TTS: `SpeakSkill.start()` で `OpenAITTSNode(speed=1.2, voice=Voice.ONYX)` と `SounddeviceAudioOutput(sample_rate=24000)` を直に new し、`_audio_output.consume_audio(tts_node.emit_audio())` でチェーン (speak_skill.py:39-43)。`_speak_blocking` は `emit_text()` の完了で再生完了を判定 (speak_skill.py:111-114)。
- ローカル TTS の素材: `dimos/stream/audio/tts/node_pytts.py` が pyttsx3 を `engine.runAndWait()` で内部再生し、完了時に `emit_text` で text をパススルー。PCM は emit しないため `SounddeviceAudioOutput` チェーンには載らない。
- STT: `WhisperNode` が openai-whisper / faster-whisper でローカル実行。env 化対象外。
- `pyttsx3` は `pyproject.toml` に未記載。Linux 実機では `espeak` 系のシステムパッケージが別途必要。

## 変更内容

### 1. `unitree_go2_agentic.py` — LLM を env から

```python
import os
...
model = os.environ.get("DIMOS_LLM_MODEL", "gpt-4o")
unitree_go2_agentic = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    McpClient.blueprint(model=model),
    _common_agentic,
)
```

- 仕様: env 未設定で `"gpt-4o"`（現状互換）。設定時は LangChain が prefix で provider 判定。
- 影響範囲: `unitree_go2_agentic` のみ。`_ollama` / `_huggingface` 派生 blueprint には手を入れない（既に専用モデルを直接渡しているため）。

### 2. `SpeakSkill` — TTS を env から切替

`dimos/agents/skills/speak_skill.py`：

- `start()` で `os.environ.get("DIMOS_TTS", "pyttsx3")` を読む
- `"pyttsx3"`:
  - `PyTTSNode(rate=..., volume=...)` を生成し、フィールド `_tts_node` に格納
  - `SounddeviceAudioOutput` は生成しない（`_audio_output` は `None` のまま）
- `"openai"`:
  - 既存どおり `OpenAITTSNode` + `SounddeviceAudioOutput`
- `_speak_blocking`: 変更なし（両ノードとも `consume_text` / `emit_text` を実装しているため、現行の subscribe による完了待ちロジックがそのまま機能）
- `stop()`: `_audio_output` が `None` の場合は停止しないようガード（既に `if self._audio_output:` ガードあり、追加変更不要）

型: `_tts_node` の型注釈を `OpenAITTSNode | PyTTSNode | None`、または両者の共通基底（`AbstractTextConsumer` 系）に緩める。

未知の `DIMOS_TTS` 値の扱い: `ValueError` で起動失敗（fail-fast）。

### 3. `pyproject.toml` — `pyttsx3` を追加

メイン依存に `pyttsx3` を追加。デフォルトが pyttsx3 のため、optional extras にすると標準動作が import エラーで落ちる。

### 4. ドキュメント

`README.md` の起動例（`dimos run unitree-go2-agentic`）に環境変数の説明を 1 ブロック追記：

```
DIMOS_LLM_MODEL  LangChain モデル文字列 (例: gpt-4o, ollama:qwen3:8b, anthropic:claude-...)
                 デフォルト gpt-4o
DIMOS_TTS        openai | pyttsx3 (デフォルト pyttsx3)
                 pyttsx3 は Linux で espeak/libespeak1 のインストールが必要
```

## 起動例

```sh
# 全部ローカル（OpenAI キー不要）
DIMOS_LLM_MODEL=ollama:qwen3:8b dimos run unitree-go2-agentic

# デフォルト構成（LLM=gpt-4o, TTS=pyttsx3）
OPENAI_API_KEY=... dimos run unitree-go2-agentic

# 従来構成
DIMOS_TTS=openai OPENAI_API_KEY=... dimos run unitree-go2-agentic
```

## 影響範囲外

- STT (`WhisperNode`)
- `unitree_go2_agentic_ollama.py` / `unitree_go2_agentic_huggingface.py` などの派生 blueprint
- MCP Server / Client 内部
- `_common_agentic` の構造

## 検証

1. `DIMOS_TTS` 未設定 + `DIMOS_LLM_MODEL` 未設定で `dimos run unitree-go2-agentic` が起動し、pyttsx3 経由で speak が鳴る
2. `DIMOS_TTS=openai` + `OPENAI_API_KEY` で従来動作（OpenAI TTS）
3. `DIMOS_LLM_MODEL=ollama:qwen3:8b` で LLM 呼び出しが Ollama にルーティングされる
4. `DIMOS_TTS=invalid` で起動時に明示エラー
5. pyttsx3 のシステム依存（espeak）が無い環境で `pyttsx3.init()` が落ちることを README に明記
