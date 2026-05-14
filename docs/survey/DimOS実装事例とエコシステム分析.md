---
title: Dimensional社のDimOS実装事例まとめ
date: 2026-04-28
tags:
  - dimos
  - dimensional
  - robotics
  - ecosystem
  - implementation
  - unitree-go2
aliases:
  - DimOS実装事例
  - DimOS エコシステム分析
related:
  - "[[DimOSコントリビュータ攻略マップ]]"
  - "[[DimOSとUnitree Go2で今できることと高精度SLAM・エージェント化の実装指針]]"
---

# Dimensional社のDimOS実装事例まとめ

> [!summary] エグゼクティブサマリー
> Dimensional社の「DimOS」は、**2025年11月に公開されたばかりの "agent-native" ロボティクスOSS**であり、実装事例の中核は依然として同社自身による公式デモに集中している。Unitree Go2四足歩行ロボットを最も成熟したリファレンス機として、ヒューマノイド Unitree G1、ロボットアーム（xArm/AgileX Piper）、DJIドローンへ展開中で、自然言語コマンド一つで自律探索・セマンティックナビ・物体操作を実行する「Vibecode robotics」を売りにしている。
>
> **サードパーティ事例はMediumの長文レビュー1本とQuasaの引用記事1本程度**にとどまり、Reddit・Hacker News・主要ポッドキャストでの自然発生的議論はほぼ皆無。**日本語圏（Qiita/Zenn/note/日本企業ブログ）での採用・解説記事は本調査時点でゼロ**であり、日本のロボティクスコミュニティでの認知度は極めて低い。

以下、「実装事例」を中心に、会社背景・GitHub・SNS反応・ROSとの関係まで体系的に整理する。

---

## 会社とプロダクトの簡潔な背景

**Dimensional INC** は San Francisco 拠点のスタートアップで、創業者は MIT 中退の **Stash Pomichter**（X: @stash_pomichter、元REMUS Capital投資担当、元Allurion/Fitgenetix関連）。シードラウンドは **Factorial Fund**（pre-seed）が公開されている唯一の投資家で、Y Combinator バッチには所属していない。設立年の登記情報は非公開だが、GitHub Organization の本格稼働と公式ローンチは **2025年11月6日**（Pomichterのローンチツイート）。チーム規模は GitHub のメインコントリビューター18名程度から推定して10名前後の小規模。Crunchbase/TechCrunch/The Information/Bloomberg では独立記事化されておらず、テック媒体での露出は X と Medium 中心。

公式ミッションは **"The Agentive Operating System for Generalist Robotics" / "Powering generalist robotics" / "Program atoms, not bits"** の三つのキャッチコピーに集約される。哲学的には「ROSは古典制御向けに設計されAIが後付けだったが、**DimOSはLLMエージェントをfirst-class citizenとして再設計する**」という立ち位置を取り、**ROS不要でPythonだけで物理アプリケーションを構築可能にする**ことが最大の差別化点となっている。

### 公式チャンネル一覧

| プラットフォーム | URL |
|---|---|
| 公式サイト | https://dimensionalos.com/ |
| ベータ申し込み | https://dimensionalos.com/prototype |
| GitHub Org | https://github.com/dimensionalOS |
| 本体リポジトリ | https://github.com/dimensionalOS/dimos |
| 公式X | https://x.com/dimensionalos |
| 創業者X | https://x.com/stash_pomichter |
| Discord | https://discord.gg/dimos |
| LinkedIn | https://www.linkedin.com/company/dimensionalos |
| PyPI | https://pypi.org/project/dimos/ |
| DeepWikiドキュメント | https://deepwiki.com/dimensionalOS/dimos |

> [!note] 検索ノイズ注意
> 本調査は **Dimensional Fund Advisors**（投資運用会社）、**DiMOS Operations**（独・自動車）、**Dimension Robotics**（北京）、**dimos.fr**（仏・屋根材ツール）、人名Dimosなどとは明確に区別している。

---

## アーキテクチャと対応ハードウェア概観

DimOSは **Module / Stream / Blueprint / Skill / Agent / Transport** の6層構造を取り、RxPYベースのリアクティブ Pub/Sub で全ストリームを扱う。Module は `In[T]`/`Out[T]` 型付きストリームを宣言し、`autoconnect()` で名前と型が一致するモジュール同士を自動配線する Blueprint パターンが特徴。**Transport は LCM（既定）/DDS（CycloneDDS）/SHM/ROS2 の4種類から選択可能**で、v0.0.10で「コアからROS message依存を完全撤去」した経緯を持つ。スキル層では `AbstractRobotSkill` を継承した `__call__()` を実装し、`@skill` デコレータで定義したものは **MCP（Model Context Protocol）サーバを介してHTTP/JSON-RPCツールとして自動公開される**。これが Claude Code / Cursor / OpenClaw からの "vibecode" 操作の入口となる。

### 対応ハードウェアとステータス

対応ハードウェアはREADMEで4段階のステータス付きで明示されている。

| カテゴリ | プラットフォーム | ステータス |
|---|---|---|
| 四足歩行 | Unitree Go2 pro/air | 🟢 stable（フラッグシップ）|
| 四足歩行 | Unitree B1, AGIBOT D1, Dobot Rover | 🔴 experimental〜roadmap |
| ヒューマノイド | Unitree G1 | 🟡 beta |
| ヒューマノイド | Booster K1, AGIBOT X2/A2, K-Scale K-Bot | roadmap |
| ロボットアーム | xArm 6/7, AgileX Piper | 🟡 beta |
| ロボットアーム | OpenARMs, HighTorque Pantera | roadmap |
| ドローン | DJI Mavic 2, Holybro x500（MAVLink） | 🟠 alpha |
| センサー | Force/Torque sensor | 🔴 experimental |

---

## GitHubエコシステムの実態

dimensionalOS Organization 配下には合計32リポジトリがあり、本体 `dimos` 以外に SLAM・プランナ・LCMブリッジなどがネイティブモジュールとして外部化されている。スター数は時系列で大きく揺れ動いており、旧 `dimos-unitree` 系も含めて **3,100超**を観測した時期があったが、2026年3〜4月の本体統合・リブランド以降の現行 `dimos` リポジトリ表示は **約800〜900スター・150フォーク前後**で推移している（コードベース統合の影響）。コミット365、リリース7本、Issues約259件、Closed PR 1,000件超という指標から、**ローンチ5ヶ月で異例のペース**で開発が進んでいることが分かる。Medium レビュー（Alex647, 2026年3月）はコードベース規模を **Python約12.7万行・776ファイル**と計測しており、公開前から相当量を社内開発していた可能性が高い。

### 主要リポジトリ群

| リポジトリ | 役割 | 言語 |
|---|---|---|
| `dimos` | 本体フレームワーク | Python (94.5%) |
| `dimos-unitree` | Unitree Go2専用統合（旧フラッグシップ） | Python |
| `dimos-viewer` | Rerunベース可視化（rerun-io/rerunフォーク） | Rust |
| `dimos-lcm` | LCM/Foxgloveブリッジ、PyPI公開 | Python/C++ |
| `dimos-module-fastlio2` / `arise-slam` | LiDAR SLAMネイティブモジュール | C++ |
| `dimos-module-far-planner` / `pct-planner` / `local-planner` / `path-follower` | プランナ・パスフォロワ群 | C++ |
| `unitree_sdk2_python` | Unitree公式SDKのPythonフォーク | Python |
| `roboclaw` | OpenClaw連携プラグイン（MCP、~200 LOC） | TypeScript |
| `go2_ros2_sdk` / `PCT_planner` / `Genesis` | 外部OSSのフォーク群 | C++/Python |

### コアコントリビューター

コアコントリビューターは @spomichter（CEO自身、CLI/daemon/viewer/drone）、@PaulNechifor（コア・ロギング・MCP）、@mustafab0（マニピュレーション全般、Drake/Pinocchio）、@ruthwikdasyam（VR/Questテレオペ、フリート制御）、@jca0（MuJoCoモジュール）、@alexlin2（初期 Unitree WebRTC、Position Based Servoing）、@SummerYang（G1統合、macOS対応）、@leshy（Nixインストール、memory2）、@Kaweees（CycloneDDS QoS）、@jeff-hykin（Unified TUI、Unity Sim提案）の10名前後で、多くは社員もしくは緊密な提携開発者と推測される。**外部からの本格的なフォーク・派生実装は heonyun/dimos--- や PanGalacticFlow/dimos-dimensional-OS など単純ミラーが大半で、独自タスク追加レベルのフォークはまだ見られない。**

---

## 公式デモ・実装事例カタログ

調査の最重要部分として、確認できる実装事例を「公式／サードパーティ／研究・産業／日本」の4軸で整理する。

### 公式デモ・チュートリアル一覧

Dimensional社自身が公開している実装は、**Unitree Go2を主軸に、エージェント駆動の自律タスクを一気通貫で動かす**ものが中心。

| # | ハードウェア | タスク内容 | LLM/VLM | 一次出典 |
|---|---|---|---|---|
| 1 | Unitree Go2 | `dimos agent-send "explore the room"` でフロンティア自律探索、SLAMとA*/VFH+Pure Pursuit、動的障害物回避 | OpenAI GPT-4o | github.com/dimensionalOS/dimos README |
| 2 | Unitree Go2 | 「hey Robot, go find the kitchen」「go to the door」でセマンティックナビ。Detic物体検出＋3D投影 | GPT-4o + Detic + EdgeTAM | dimensionalos.com / dimos-unitree README |
| 3 | Unitree Go2 | WebRTCアクションプリミティブ（FrontFlip/FrontPounce/FrontJump/spinLeft）をfunction callで発動。「人を見たらジャンプ、犬を見たらフロントフリップ」 | OpenAIAgent | dimos-unitree README |
| 4 | Unitree Go2（replay） | Spatio-Temporal RAG：「キッチンに最も長くいるのは誰？」「先週木曜午前9時にオフィスにいたのは誰？」型の時間-空間クエリ。ChromaDB永続記憶 | LLM + Spatial Memory | x.com/stash_pomichter（2025-10-21, 140K views） |
| 5 | Unitree Go2 | マルチストーリー（多階層）自律ナビ。"out of the box on ANY robot, single pip install" | — | x.com/stash_pomichter（2025-10-24, 89K views） |
| 6 | Unitree Go2マルチ機 | フリート制御 `dimos run --robot-ips ip1,ip2,...` | — | PR #1487 by @ruthwikdasyam |
| 7 | Unitree G1ヒューマノイド | OpenClaw on G1：「flag」物体到達、人物検出（信頼度スコア表示）、色付きポイントクラウドの3Dマッピング、自然言語ナビ。"memory backbone for OpenClaw Agents" | Claude（Claude Code）+ DimOS MCP | x.com/stash_pomichter（2026-03-02, **410K views**）/ quasa.io |
| 8 | Unitree G1シミュレーション | `dimos --simulation run unitree-g1-sim`（MuJoCo） | — | リリースノート v0.0.10 |
| 9 | xArm 6/7 / AgileX Piper | Vibecode pick-and-place：自然言語からモータ指令まで。DrakeベースIK/FK・RRT・GraspGen統合 | LLMエージェント | リリースノート v0.0.10（PR #1237 by @mustafab0） |
| 10 | xArm / Piper | 「Open anything. On any arm. 99% success rate.」を謳うドア・物体オープニングデモ | — | x.com/dimensionalos（2025-10-11, 6.9K views） |
| 11 | DJI Mavic / MAVLink | RosettaDrone（Android）→MAVLink→DimOS。視覚サーボ、PIDトラッキング、drone-agentic でLLM制御。屋内/GPS屋外モード両対応 | LLM | dimos/robot/drone/README |
| 12 | K-Scale Labs K-Bot | 第一号機を「DimensionalHQで人質にした」というジョーク投稿（実装詳細は未公開、検証中と推測） | — | x.com/dimensionalos（2025-09-23） |
| 13 | 任意ロボット | dimos-viewer（Rerunベース）の3Dビュー上をクリック→PointStamped→A*→実機が移動 | — | PR #1414, #1394 |
| 14 | OpenClaw + 任意ロボット | WhatsApp/TelegramからのロボッThe制御：roclawプラグイン（TS、~200 LOC）経由でMCP接続 | Claude / GPT | github.com/dimensionalOS/roboclaw |

> [!tip] 最重要デモ
> 2026年3月2日の Unitree G1 + OpenClaw + DimOS 動画で、**X上で410K views・4,200 likes**となりDimOS関連で最も拡散したコンテンツ。Chroma DB CEO の Jeff Huber や NVIDIA/元Hello RobotのChris Paxton等の著名ロボティクス研究者からも好意的引用を受けている。

---

## シミュレーション統合の実装事例

**MuJoCo がデフォルトのシミュレーションバックエンド**で、PR #1035（@jca0）以降、`dimos --simulation run unitree-go2` / `unitree-g1-sim` / `xarm-piper-teleop-sim` 等のブループリントが揃っている。MJCF/URDFパーサとmonotonic clockタイミング、macOS互換性パッチ（@SummerYang）まで対応済み。

これに加えて **NVIDIA Isaac Sim と Genesis の統合バインディング**が `dimos/simulation/isaac/` と `dimos/simulation/genesis/` に存在し（dimos-unitreeリポジトリ）、Genesis本体のフォークもorg内に置かれている。**実機なしで全パイプライン検証可能な Replay モード**（`dimos --replay run unitree-go2`）も特徴で、2.4GBのLFSデータセット（オフィス散歩データ `unitree_go2_office_walk2` ほか）を再生することで、SLAM・空間メモリ・エージェント連携を動かして見られる。

---

## LLM / VLM / エージェント統合事例

統合されているAIスタックは異例に広く、現時点で確認できるものは以下。

- **LLM**: OpenAI GPT-4o（既定）、Anthropic Claude、Cerebras（cerebras-cloud-sdk依存）、Alibaba Qwen（ALIBABA_API_KEY）、HuggingFace（Local/Remote）、TensorZero（LLMゲートウェイ）。Gemini/DeepSeekは開発中。
- **VLM・知覚**: Detic（dimos/models/Detic）、ultralytics、open_clip、torchreid、EdgeTAM/SAM2ベースのセグメンテーション、深度推定モデル、ZED/Realsendsドライバ統合。
- **エージェント・コーディング統合**: Claude Code / Cursor / OpenClaw を AGENTS.md に向け、DimOS の CLI と MCP 経由でアプリを構築する公式推奨ワークフロー（PR #1495）。MCP サーバ実装（PR #1300 by @PaulNechifor、#1451）により `@skill` メソッドをHTTP/JSON-RPCで自動公開。
- **エージェントチェーン**: PlanningAgent→ExecutionAgent の多段プラン分解（`test_planning_agent_web_interface.py`）、Spatial Memoryモジュールによる Spatiotemporal RAG。
- **テレオペ**: Meta Quest WebXR（PR #1215）、iPhone IMU（PR #1280）、Apple Vision Pro/PICO（spec）、Pinocchio IKベース dual-arm テレオペ（PR #1246）、SpaceMouse/Xbox/PlayStation（Issue #1113設計）。

---

## サードパーティ・コミュニティ実装事例

> [!warning] 独立した第三者実装事例が極めて乏しい
> 最も正直に報告すべき所見は、**独立した第三者実装事例が極めて乏しい**ことである。確認できたのは以下のみ。

| 実装者 | 媒体/ハードウェア | 内容 | 出典 |
|---|---|---|---|
| **Alex647**（個人ブロガー、所属未公表） | コードベース解析記事 | "DimensionalOS Might Be the Real Deal for AI Robots?"（2026年3月）。6リポジトリ・126K行を全読みし「** Android for robots**」「事実上の物理世界MCPサーバ」と評価。MCP・Module/Stream/Blueprint・transport 4種・OpenClaw + WhatsApp制御を高評価 | medium.com/@Alex647/dimensionalos-might-be-the-real-deal-for-airobots-ebf1c1e17e9c |
| **Quasa.io** | OpenClaw × Unitree G1引用解説 | Pomichter公開ラボ動画を「flag物体到達、人物検出、3D点群、キッチン誘導」として技術分解 | quasa.io/media/openclaw-meets-unitree-g1-... |
| **Openflows** | ガバナンス論評 | LLMが物理アクチュエータを直接制御することの安全性・統治観点 | openflows.org/currency/currents/dimensiona... |
| **AIToolly** | 短報 | GitHub Trending #3入りを「Proxy OS」として報じる（誤解含む） | aitoolly.com（2026-03-16） |
| **Chris Paxton**（NVIDIA/元Hello Robot系） | X引用RT | "very exciting, a key piece in what will make home robots viable" | x.com（2025-10-22） |
| **Jeff Huber**（Chroma DB CEO） | X引用RT | "OpenClaw meets robots! (and open source) Go @dimensionalos!" | x.com/jhuber/status/2029480646574575963 |
| **Rohan Paul** | X紹介 | DimOSの "vibecode robots" をAI界隈に拡散 | x.com/rohanpaul_ai |
| heonyun / PanGalacticFlow（個人GitHub） | リポジトリリフォーク | コード変更のない単純ミラー | github.com/heonyun/dimos--- ほか |

---

## 産業応用・日本における事例

**産業応用や大学研究室での採用事例は本調査では発見できなかった。** 論文（arXiv/ar5iv検索）、企業PoCブログ、ROSCon/GTC/YC DemoDay等のカンファレンス公式登壇も確認されておらず、Dimensional社自身が「infra/agents/navigation/manipulationのエンジニア採用中」のステージにある。**Reddit（r/robotics, r/LocalLLaMA, r/ROS）と Hacker News では DimOS関連の有意なスレッドはゼロ**で、コミュニティ醸成はXとDiscordに集中している。

### 日本における事例

**日本語圏でのDimOS採用・解説記事は本調査時点（2026年4月）でゼロ。** Qiita、Zenn、note.com のいずれの検索結果にも該当記事はなく、日本企業・大学・個人開発者による公開実装事例も発見できなかった。同名の「株式会社ディモス（dimos.co.jp）」は計量計測器のR&D会社で全く無関係。背景には、Dimensionalの公開が新しいこと、**Unitree Go2が日本では個人輸入レベルでしか流通していないこと**、日本のロボット開発者はROS2を中心とした既存スタックに集中していることが挙げられる。

---

## ROS / ROS2 との関係性

DimOSのROSへの態度は「対決」ではなく**「選択肢として共存」**である。`pyproject.toml` には rclpy/cyclonedds が含まれ、**Transport として ROS2/DDS/LCM/SHM の4種を切り替え可能**で、v0.0.10で「コアから ROS message依存を撤去」した後も Unitree Go2統合は WebRTC（直接）と ROS2 SDK（abizovnuralem系の go2_ros2_sdk フォーク）の二系統を維持している。`dimos_utils` には LCM→ROS2メッセージのコード生成ツールが含まれ、ROS2 Bag互換性も確保されている。

> [!tip] 実践的な移行パターン
> 「ROSなしで Pythonだけで」というメッセージは思想であって、**既存ROS2ユーザは段階的にDimOSのエージェント/スキル層だけを上に重ねる移行パターンが最も実際的**で、これが公式 dimos-unitree リポジトリの主要ユースケースになっている。ROS2→DimOSの具体的な移行事例ブログ・論文は本調査では未発見。

---

## SNS・コミュニティ反応の実態

X（旧Twitter）が事実上唯一の情報集約点で、公式 @dimensionalos（フォロワー約2,162）と CEO @stash_pomichter（約7,635）が中心となって発信している。CEO の主要投稿のエンゲージメントは以下の通りで、ロボティクスOSSとしては健闘している。

- 2025-10-21 Spatial Memory プレビュー：**140K views, 1.9K likes**
- 2025-10-24 マルチストーリー自律ナビ：**89K views, 1.2K likes**
- 2025-11-06 Dimensional 公式ローンチ：**192K views, 621 likes**
- 2026-03-02 OpenClaw on Unitree G1：**410K views, 4.2K likes（最高エンゲージメント）**
- 2026-03-15「Dimensional は #3 trending repo on Github（ローンチ72時間後）」：**19.8K views**

ローンチ後72時間でGitHub Trending #3に到達したという主張はCEO本人の発信が一次ソースで、第三者検証は限定的だがMediumのAlex647とQuasaの独立記事はこの主張を補強している。**YouTube公式チャンネルは確認できず**（dimensionalos.com上で "Watch video" として動画を埋め込み公開）、サードパーティ制作のデモ動画はGource（コミット可視化）程度しか見当たらない。**Latent Space/TWiML/Robohubなどの主要ポッドキャスト言及もなし**で、自動生成の "GitHub Daily Trend" Apple Podcast に登場する程度。

> [!quote] 総括
> **プロダクトとしての作り込みとSNSバズの間に、コミュニティ実装事例という"中間層"がまだ薄いのが現状である。**

---

## 結論：今が黎明期、注目すべきはコミュニティ拡張の速度

DimOS は 2025年11月公開・2026年4月時点で v0.0.11 という極めて若いOSSであり、「ROSなし・Pythonファースト・LLMエージェントをfirst-class citizenとして設計」という思想は明快で、Unitree Go2を中心に Spatial Memory・MCP・OpenClaw連携・マルチストーリーナビ・xArm/Piperのマニピュレーション・DJIドローン制御まで、ローンチ5ヶ月で12.6万行のPythonを書き切ったテクノロジースタックの密度は明らかに突出している。

一方で**実装事例の中核はほぼ全てDimensional社内の公式デモ**であり、MediumとQuasaを除けば独立した第三者検証・大学研究・企業PoC・日本での採用事例はまだ見えてこない。MCPを介してClaude Code/Cursor/OpenClawから物理ロボットを呼び出すという設計は「**物理世界のMCPサーバ**」というポジショニングとして唯一性が高く、ここを起点にWeb開発者のエコシステムを物理世界に引き込めるか、今後6〜12ヶ月でサードパーティ事例が爆発するかどうかの分水嶺となる。

> [!tip] 日本の開発者向け
> Unitree Go2/G1を持っているかMuJoCoシミュレーションで検証する開発者であれば、`pip install dimos[base,unitree]` から30分で公式デモを再現できる成熟度に達しているため、**Qiita/Zennでの日本初の検証記事を書く価値は十分にある状態**と評価できる。

---

## 関連ノート

- [[DimOSコントリビュータ攻略マップ]] — コントリビュータとして参加するための具体的タスクと戦略
- [[DimOSとUnitree Go2で今できることと高精度SLAM・エージェント化の実装指針]] — Go2スタックの技術詳細と実装指針
