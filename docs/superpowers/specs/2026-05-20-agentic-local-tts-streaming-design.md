# Streaming TTS for `unitree-go2-agentic-local-tts`

- Date: 2026-05-20
- Status: design approved, pending implementation plan
- Scope: fork-only (`*_ja` files); no upstream-owned file is modified

## 目的

`unitree-go2-agentic-local-tts` の TTS を**文単位でストリーミング**化し、最初の音声が出るまでの時間(TTFB)を縮める。

### 現状フロー(非ストリーミング)

1. LLM(agent)が**完成した** `AIMessage` を発行。
2. `AssistantSpeechNodeJa` が AIMessage の全文を TTS ノードへ1回で渡す。
3. `StyleBertVits2TTSNode` / `VoicevoxTTSNode` とも `_synthesize_speech` が**全文を一括合成**し、`AudioEvent` を**1個だけ** emit。

ボトルネックは2段: **(a) LLM 全文の完成待ち** + **(b) 全文を1回で合成する待ち**。最初の音は両方が終わるまで鳴らない。

### 確定事実(調査結果)

- `TimedMcpClient._process_message` は既に `state_graph.stream(stream_mode=["updates","messages"])` を使用。`("messages", (AIMessageChunk, meta))` でトークンデルタが流れるが、現状は `StepFirstTokenTracker`(TTFT 計測)に渡すだけで**破棄**している。`meta["langgraph_node"]` で生成ノードが分かる。
- `self.agent.publish(msg)` は `"updates"` ブランチのみ = **完成メッセージのみ**を発行。トークンデルタは client の stream ループ内でしか観測できない → **client の改修は必須**。
- `agent: Out[BaseMessage]` は履歴(`_history`)・bench・他購読者が依存するため、**完成メッセージの発行は維持必須**。
- SBV2/VOICEVOX ノードは `consume_text` で受けた observable の `on_next(text)` 1回ごとに1合成して `AudioEvent` を1個 emit。**文を1つずつ流せばノード自体は無改修**で文単位再生になる。
- consumer 側 `AssistantSpeechNodeJa` の idle watchdog は既に「1発話=複数チャンク」を想定済み(`_on_audio_chunk` のコメント参照)。
- 触れるファイルは全て fork 固有(`mcp_client_ja.py` / `speak_skill_ja.py` / 新規ヘルパ)。upstream 由来は base `mcp_client.py` と `node_output.py` のみで、**いずれも不変**。

## 設計(B案 / producer 側分割 / config トグル)

LLM のトークンを逐次受けて文末で区切り、最初の文が出来た時点で合成・再生を開始する。

### 配線方針(autoconnect との両立)

- **producer は常に両方を発行**:
  - 既存 `agent: Out[BaseMessage]`(完成メッセージ。履歴/bench/他購読者用、従来どおり)
  - 新規 `agent_text: Out[str]`(文粒度)。トークンを流すだけなので追加コストはほぼゼロ。
- **consumer は両方の In を宣言**するが、`start()` で `config.streaming` に応じて**片方だけ subscribe**する(購読呼び出しはノード側が手動で行うため、autoconnect の配線存在とは独立に切替え可能)。
  - `streaming=True` → `agent_text`(`In[str]`)を購読し、文を直接 TTS へ。
  - `streaming=False` → 従来の `agent`(`In[BaseMessage]`)を購読 = **現状挙動を完全維持**。

両ポートを同時に購読しない設計なので二重発話は起きない。

### producer 側ロジック(`dimos/agents/mcp/mcp_client_ja.py`)

- `agent_text: Out[str]` ポートを追加。
- stream ループ内、`mode=="messages"` のチャンクから LLM ノード(`agent` / `model`)由来のデルタ文字列を取り出し、`SentenceAccumulator.push(delta)` に投入。返ってきた完成文を各々 `self.agent_text.publish(sentence)`。
- LLM ステップ終了時(`updates` の LLM ノード処理後)に `SentenceAccumulator.flush()` を呼び、非空なら publish。これによりステップ末尾の句点なし残りも再生される。
- **対象は全 LLM ステップのテキスト**(現状「全 AIMessage content を話す」の踏襲)。tool-call のみのステップは content が空 → accumulator から完成文も flush も出ない。
- `agent.publish(msg)`(完成メッセージ)・bench イベント・`_history` への append は**従来どおり**。

### 文分割ヘルパ(新規 fork 固有ファイル)

- `stream_tracker.py` と同じ発想の**純関数ロジック**。状態付きクラス `SentenceAccumulator`:
  - `push(delta: str) -> list[str]`: デルタを内部バッファへ連結し、文末記号で終わる完成文のリストを返す(バッファには未完の末尾が残る)。
  - `flush() -> str | None`: 残バッファを返してクリア(空なら `None`)。
- 区切り文字: `。`, `！`, `？`, `!`, `?`, 改行。連続する区切り記号はまとめて1文の末尾に含める。
- 単体テスト可能(I/O・スレッド非依存)。

### consumer 側(`dimos/agents/skills/speak_skill_ja.py`)

- `agent_text: In[str]` を追加(既存 `agent: In[BaseMessage]` は残す)。
- `AssistantSpeechNodeJaConfig` に `streaming: bool` を追加。
  - `Field(default_factory=_default_tts_streaming)` で、env `DIMOS_TTS_STREAMING`(例: `1`/`0`)を **default seed** として読む。明示設定・YAML・bench が常に優先(`Sbv2ParamsConfig` 等と同じ category A パターン)。既定は ON。
- `start()`:
  - `streaming=True` → `agent_text` を購読(`_on_agent_text`)。`agent` は購読しない。
  - `streaming=False` → 従来どおり `agent` を購読(`_on_agent_message`)。
- `_on_agent_text(text: str)`: 受けた文字列を直接扱う(AIMessage 判定不要)。空/空白は無視。`_text_subject.on_next(text)` で TTS へ。
- 発話開始判定は **idle→busy 遷移**を使う: idle 中に最初の文が来たら `speak_invoke` ログ + `_first_chunk_pending` 武装 + busy 化(`tts_idle=False`)。busy 中の追加文は `_text_subject` に流すだけ。watchdog が playback 後に idle へ戻す。`_on_agent_message`(非ストリーミング経路)も同じ idle→busy ヘルパを共有する。

### TTS ノード

SBV2 / VOICEVOX とも**無改修**。文を1つずつ `on_next` するため、文ごとに1合成・1 `AudioEvent` を emit し、文単位で再生される。

### bench イベント

- `speak_invoke` / `first_audio_out`: 「発話=1ターン」で各1回。idle→busy 遷移で発話開始を捕捉。
- `tts_idle`: 現状の watchdog を維持(複数チャンク対応済み)。
- 効果指標: streaming では「最初の1文の合成完了」で `first_audio_out` が全文一括より早く出る。`bench_agentic_local_tts.py` / `llm-bench-compare` で streaming ON/OFF を比較できる。

## テスト方針(TDD)

1. `SentenceAccumulator` の純関数テスト: 単一文・複数文・記号境界(`。！？!?`/改行)・連続記号・空入力・分割途中での `push` 累積・`flush` の空/非空。
2. consumer の config トグルテスト(既存 `tests/agents/skills/test_speak_skill_ja_impl_switch.py` / `test_sbv2_params_config.py` に倣う): `streaming=True/False` で購読先(`agent_text` vs `agent`)が切り替わること、env `DIMOS_TTS_STREAMING` が default seed として効くこと、明示指定が env を上書きすること。

## 非対象(YAGNI)

- エンジン内部のサブ文ストリーミング(モデルレベルのチャンク出力)は対象外。SBV2/VOICEVOX とも非対応寄りで難度が高い。
- 一括モードの削除はしない(bench 比較・退避のため config トグルで残す)。
- 専用 `SentenceSegmenter` モジュールの新設はしない(配線増を避け、producer 側ヘルパで完結)。

## 影響ファイル

| ファイル | 区分 | 変更 |
|---|---|---|
| `dimos/agents/mcp/mcp_client_ja.py` | fork | `agent_text: Out[str]` 追加、stream ループでトークン→文 publish |
| `dimos/agents/skills/speak_skill_ja.py` | fork | `agent_text: In[str]`、`streaming` config、購読切替え、`_on_agent_text` |
| 新規 文分割ヘルパ(fork) | fork | `SentenceAccumulator` 純関数クラス |
| `tests/...`(fork) | fork | 上記2点のテスト |
| base `mcp_client.py` / `node_output.py` | upstream | **不変** |
