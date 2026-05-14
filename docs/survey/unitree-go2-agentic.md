unitree-go2-agentic の STT → LLM → TTS は 別々のモジュールが LCM / MCP / RxPY を介して疎結合
  で繋がっています。dimos/stream/audio/pipelines.py の stt() / tts() は使われていません（スタンドアロン用ヘルパ）。

  全体図

  [ブラウザ http://localhost:5555]
     ├─ テキスト入力 ──────────────────┐
     └─ マイク音声 → AudioNormalizer    │
                    → WhisperNode      │
                    → text ────────────┤
                                       ▼
                         LCM topic "/human_input"
                                       │
                                       ▼
                                [McpClient]
                                gpt-4o + LangGraph
                                create_agent(tools=...)
                                       │ tool call
                                       ▼
                         MCP HTTP (localhost:9990/mcp)
                                       │
                                       ▼
                                [McpServer]
                                       │
                                       ▼
                         [SpeakSkill.speak(text)]   ← agent が呼ぶツール
                           OpenAITTSNode
                           → SounddeviceAudioOutput
                           → ロボットのスピーカー

  1. STT（音声 → テキスト）

  WebInput (dimos/agents/web_human_input.py:35) が起動時に以下を組む：

  - RobotWebInterface(port=5555) でブラウザに UI を出す
  - ブラウザからのテキストは query_stream で直接 publish
  - ブラウザからの音声は audio_subject → AudioNormalizer → WhisperNode でテキスト化
  - どちらも pLCMTransport("/human_input") に publish

  WhisperNode (dimos/stream/audio/stt/node_whisper.py:50) は
  ローカルで実行：openai-whisper（インストールされていれば）か、フォールバックで faster-whisper。デフォルト model="base",
  language="en"。OpenAI クラウドへは投げない。

  2. LLM（テキスト → ツール呼び出し / 応答）

  McpClient (dimos/agents/mcp/mcp_client.py:51)：

  - human_input: In[str] が LCM /human_input を購読 → HumanMessage を _message_queue に積む (mcp_client.py:199)
  - 起動時に MCP サーバ (http://localhost:9990/mcp) から tools/list を取得し LangChain ツール化 (_fetch_tools)
  - langgraph.create_agent(model=gpt-4o, tools=…, system_prompt=…) でステートグラフを構築 (mcp_client.py:222)
  - ワーカースレッドがキューを回し、LLM がツールを選んだら HTTP tools/call で McpServer を叩く

  ツール（speak、ナビゲーション、Person follow、Unitree skills 等）は McpServer 側に集約され、対応するモジュール（SpeakSkill など）の
  @skill メソッドが実行される。

  3. TTS（テキスト → 音声）

  SpeakSkill (dimos/agents/skills/speak_skill.py:31)：

  - 起動時に OpenAITTSNode(speed=1.2, voice=ONYX) と SounddeviceAudioOutput(24kHz) を生成 (speak_skill.py:41)
  - LLM が speak(text) ツール呼び出し → _speak_blocking が Subject[str] 経由でテキストを TTS ノードに流す
  - OpenAI TTS API で合成された音声 PCM が SounddeviceAudioOutput 経由でスピーカ再生
  - blocking=True のときはオーディオ完了イベントを待つ（max(5, len(text)*0.1) 秒のタイムアウト）

  つまり TTS は LLM 出力のテキスト全体に対してではなく、エージェントが明示的に speak() ツールを呼んだときだけ鳴る。

  キーポイント（Voice Live 連携を考えるなら）

  - STT 差し替え点: WebInput.start() 内の WhisperNode を Azure STT ノードに置換 → /human_input に publish するだけ。
  - TTS 差し替え点: SpeakSkill.start() 内の OpenAITTSNode を Azure TTS ノードに置換。
  - LLM は LangGraph + OpenAI 互換 (gpt-4o) なので、Azure OpenAI / Azure AI 推論エンドポイントに差し替えは McpClientConfig.model
  経由で可能。
  - Voice Live のような 双方向ストリーミング を活かすには、WebInput ＋ SpeakSkill を 1 つの Voice Live
  セッションノードに統合し、ツール呼び出しだけを LangGraph
  に橋渡しする必要がある（現状の片方向パイプラインとはアーキテクチャが合わない）。