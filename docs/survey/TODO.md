- localhost:5555が勝手に開かない
- --viewer rerun-webにしないとUIが変
- トレース基盤くみこみ
- obstacle avoidance効いてない
	- 気のせいかも
- unable to decode audio
	- apt install ffmpeg
- SLAM精度
- obsidian vaultをリポジトリに含める。superpowers/もいれる
- 音声が声の種類文一通り流れる
- 







このブループリントは autoconnect による合成パターンで、3層を積み上げて構成されています。

  構成レイヤ

  unitree_go2_agentic
   ├─ unitree_go2_spatial   ← 空間認識・地図・ナビゲーション
   │   └─ unitree_go2       ← マッピング・経路計画
   │       └─ unitree_go2_basic  ← ロボット接続・可視化基盤
   ├─ agent("azure_openai:gpt-5-rink")  ← LLM エージェント
   └─ _common_agentic       ← スキル群（行動API）

  各レイヤの構成要素

  1. unitree_go2_basic (blueprints/basic/)
  - go2_connection: Unitree Go2 実機との接続
  - LCM PubSub プロトコル / pSHMTransport (Mac 用 高帯域共有メモリ転送)
  - ClockSyncConfigurator: 時刻同期
  - 可視化: Rerun または Foxglove bridge (env で切替)、websocket_vis
  - n_workers=4

  2. unitree_go2 (blueprints/smart/unitree_go2.py)
  - voxel_mapper(voxel_size=0.1): ボクセル占有マップ
  - cost_mapper: コストマップ
  - replanning_a_star_planner: リプランニング A* 経路計画
  - wavefront_frontier_explorer: フロンティア探査（未踏領域探索）
  - n_workers=7

  3. unitree_go2_spatial (blueprints/smart/unitree_go2_spatial.py)
  - spatial_memory: 空間記憶
  - PerceiveLoopSkill: 知覚ループ（VLM ベースの環境認識）
  - n_workers=8

  4. agent (unitree_go2_agentic.py:23)
  - LLM: Azure OpenAI gpt-5-rink（推論バックエンド）

  5. _common_agentic — エージェントが呼び出せるスキル
  - navigation_skill: 目的地ナビゲーション
  - person_follow_skill: 人追従（カメラ内参 GO2Connection.camera_info_static）
  - unitree_skills: Unitree 固有動作（座る/立つ/姿勢など）
  - web_input: Web UI 経由のヒューマンインプット
  - speak_skill: 音声発話（TTS）

  全体像

  autoconnect がモジュール間の入出力トピックを自動配線するDSLで、知覚（VLM/空間記憶）→ 地図/計画（voxel/costmap/A）→ LLM 推論 →
  スキル実行（ナビ/追従/発話）* を疎結合に合成。LCM pub/sub と Rerun/Foxglove による可視化が横断的に乗っています。

  派生バリエーション（*_huggingface.py, *_ollama.py, *_mcp.py, *_temporal_memory.py）は同じ _common_agentic を共有し、agent(...)
  部分だけを差し替える設計です。