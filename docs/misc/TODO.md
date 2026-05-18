- zellijでコピペしたときに g が消えることがある
- speak skillとai messageが両方あるのおかしくない？
	- ここが無駄（性能改善の余地）

  turn_done が 4.24s で内 LLM 合計 3.61s。注目はターン後半:

  +2.290  AI: tool_calls = [Sit, speak("こんにちは、私はダニールです...")]
  +2.295  Sit done
  +2.916  speak done
  +3.398  ← この後 LLM 2nd round が 1.32s 走って…
  +4.240  AI: "おすわりしました！こんにちは、私はダニールです..."（text-only、誰にも喋らない）

  つまり LangGraph の ReAct ループが、tool_result 受領後に 「最終 AIMessage」を必ず生成する仕様のせいで、すでに speak で発話済みの内容を
   テキストでもう一度生成している。このメッセージは音声化されない（テキストは見えないと system_prompt にも書いてある）=
  ユーザにとって完全に死荷重。

  これの影響:
  - ユーザ体感には影響なし（音は +2.62s で既に出ている）
  - ただし turn_done / llm_total_s が ~30% 膨張
  - 次のターンに移れる時間が遅くなる → 連投や対話のテンポに効く

  対策案

  (a) System prompt に「ツール実行後に最終要約を出さない」を追記

  ツール呼び出し後、追加の最終メッセージを生成しないでください。
  発話が必要なら必ず `speak` ツールで完結させてください。

  ただし LLM がこれを守る保証は弱め。

  (b) LangGraph 側で「全 tool_calls が speak を含むなら最終 LLM ステップをスキップする」カスタムノード追加

  確実だがブループリント改造が要る。TimedMcpClient の _process_message で n_steps が増える前に短絡できる。

  (c) 放置

  ユーザ体感に効かないので無視。speak_after_motion_s のような後段メトリクスを足したい場合に問題になるが、今は不要。

  結論

  - このログは concurrent パスが期待通り動く証拠として満点。
  - ただし「LLM ボトルネック度」を本気で削るなら、ツール後の重複最終メッセージが次の改善ターゲット。turn_total_s から e2e_response_s
  を引いた値（≈ 1.6s）が、ほぼ全部この無駄に相当している
- simplifyと元を両方残す？元は現実装にないから、勘違いの元になる
- 返事 -> tool -> 報告のstate管理を理解する
- バージインはmotionにも対応しているか？
- gpt-4oは非マルチモーダルだから、STT, TTSは別でやっている
- voice-liveのwebrtc化
- 地図にラベルを表示したい
- unitree-sdkをどこまでtoolにできているか調査
- lidarをmid-360に。その場合docking station経由になる
- voice liveの優位性は、LLMとTTSが並列に動いて応答が早いこと。local stt (large-v3)使えるからこれでいいかも
- worktree-llm-bench-v1が未mergeでsmokeもまだ。
- qwen3-30b-a3b-2507のTTFTは十分だけど、streamingなどチューニングの余地はあるかも
- 