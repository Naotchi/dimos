---
title: DimOS新規コントリビュータ向け攻略マップ
date: 2026-04-28
tags:
  - dimos
  - dimensional
  - open-source
  - contribute
  - robotics
aliases:
  - DimOS コントリビュータ戦略
  - DimOS コアコミッタへの道
related:
  - "[[DimOS実装事例とエコシステム分析]]"
  - "[[DimOSとUnitree Go2で今できることと高精度SLAM・エージェント化の実装指針]]"
---

# DimOS新規コントリビュータ向け攻略マップ

> [!abstract] BLUF（結論先出し）
> DimOS（Apache-2.0、Python 3.10+、Alpha pre-release）は2025年11月のローンチからわずか半年で **Star 約3.1k / Fork 約548 / Open Issue 約259 / Open PR 約73 / Closed PR 1,023件超**に達した極めて活発な若いプロジェクトで、3週間〜1か月サイクルで v0.0.4 から v0.0.11 までを高速にリリースしている。**外部コントリビュータのPR受け入れ実績が継続的に積まれており**（Cerebras Agent、ONNX移植、Temporal Memory、CycloneDDS、FASTLIO2/aarch64、GraspGen、asyncio撤廃、Rerun-web統合など）、@Kaweees/@jeff-hykin/@christiefhyangのように外部PRからOrg メンバー化した実例もあるため、**最初の数本のPRで核心モジュールに食い込み、半年〜1年で「unreleased」「experimental」と明示された領域のオーナーを取りに行くのが現実的なコアコミッタ路線**です。

以下では (1) プロジェクトの現状観測値と観測限界、(2) コミュニティ入口とPRワークフロー、(3) Open Issue/PRから拾えた具体タスク、(4) コアメンバー担当マップと手薄領域、(5) 段階別タスク推薦5〜10件、(6) Go2実機を活かす戦略、を順に提示する。

---

## プロジェクト現状の輪郭

DimOS は「Program Atoms — The Agentive Operating System for Generalist Robotics」を掲げる Python主体（94.5%）のオープンソース・ロボットOSで、`dimos run` を中心とした production CLI/daemon、Module/Blueprint/RxPYのリアクティブストリーム、LCM/DDS/SHM/ROS2 のマルチトランスポート、AGENTS.md による AI コーディングエージェント駆動開発を四本柱に据えている。最新の `pyproject.toml` バージョンは `0.0.11`、Python 3.10/3.11/3.12 対応、`Development Status :: 3 - Alpha`、ライセンスはApache-2.0。READMEの冒頭には「⚠️ Alpha Pre-Release: Expect Breaking Changes」と明示されており、機能成熟度マトリクス（🟢 stable / 🟡 beta / 🟠 alpha / 🔴 experimental）でハードと機能のステータスが公開されている。

### リリース速度の主要マイルストーン

特筆すべきはリリース速度で、v0.0.4（2026-05相当）から v0.0.11（2026-03-12）までの主要マイルストーンには以下が含まれる：

- Dask 完全削除（#1365）
- MCP server / MCP CLI（#1300, #1451）
- daemon mode（#1436）
- AGENTS.md（#1495）
- Temporal-Spatial Memory（#1511）
- Quest VR / phone / arm teleop
- Drake/Pinocchio/IK/RRT/GraspGenのフルManipulationスタック（#1079, #1116, #1213, #1237）
- CycloneDDS transport（#1230）
- MuJoCo simulation（#1035）
- コアからのROS message依存の完全除去（#1230）
- ARM/aarch64サポート、Nixインストール
- Project Go-Big（X上で告知された世界最大規模のヒューマノイド事前学習データセット計画、Brookfieldとの提携）

一方で**ManipulationはREADMEで `(unreleased)` と明示**、Agents/MCPは `experimental`、macOS/Force Torqueは `experimental`、公式ドキュメントサイトは "Coming Soon" という既知の弱点もある。

### 主要URL一覧

| 種別 | URL |
|---|---|
| 本体リポジトリ | https://github.com/dimensionalOS/dimos |
| Orgトップ | https://github.com/dimensionalOS |
| Orgリポジトリ一覧（32公開） | https://github.com/orgs/dimensionalOS/repositories |
| Releases | https://github.com/dimensionalOS/dimos/releases |
| Open Issues | https://github.com/dimensionalOS/dimos/issues?q=is%3Aissue+is%3Aopen |
| Open PR | https://github.com/dimensionalOS/dimos/pulls?q=is%3Apr+is%3Aopen |
| ラベル一覧（34個） | https://github.com/dimensionalOS/dimos/labels |
| `good first issue` | https://github.com/dimensionalOS/dimos/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22 |
| `help wanted` | https://github.com/dimensionalOS/dimos/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22 |
| `documentation` | https://github.com/dimensionalOS/dimos/issues?q=is%3Aissue+is%3Aopen+label%3Adocumentation |
| 公式サイト | https://dimensionalos.com/ |
| Discord招待 | https://discord.gg/dimos |
| X | https://x.com/dimensionalos |
| CEOのX | https://x.com/stash_pomichter |
| Bounty List（外部Google Sheets） | https://docs.google.com/spreadsheets/d/1tzYTPvhO7Lou21cU6avSWTQOhACl5H8trSv... |
| CLI docs | https://github.com/dimensionalOS/dimos/blob/main/docs/usage/cli.md |
| 開発手順（README内） | https://github.com/dimensionalOS/dimos#development |

---

## コミュニティ入口とPRを通すための実務知識

専用の `CONTRIBUTING.md` / `DEVELOPMENT.md` / `ROADMAP.md` / `CODE_OF_CONDUCT.md` の独立ファイルは本調査では確認できず、**貢献ガイドラインはREADMEと AGENTS.md**（PR #1495で導入されたAIコーディングエージェント向けオンボーディング）と **Bounty List Google Sheets** に集約されている。これはDimensional社が「Your agent reads this and starts coding」と明言する AI-first の貢献文化を反映しており、人間より先にCursor/Claude Code/OpenClawなどのエージェントが AGENTS.md を読むことを前提に書かれている。

### 最初の一歩（推奨順）

1. **Discord（https://discord.gg/dimos）に参加し**、自分の興味領域（Go2、MCP、シミュレーション、ドキュメントなど）とUnitree Go2実機を持っている事実を伝える
2. **Bounty List Google Sheets で募集中タスクを確認**し、興味があるタスクにはGitHub Issueを立てて担当意思を表明する（READMEに「If you would like to suggest a feature or sponsor a bounty, open an issue」と明示）
3. **`dev` ブランチをチェックアウト**（`GIT_LFS_SKIP_SMUDGE=1 git clone -b dev ...`）し、`uv venv && . .venv/bin/activate && uv pip install -e '.[base,dev]'` で開発環境を構築、`uv run pytest dimos` でfast suiteを回して通ることを確認する。**PRはmainではなく `dev` ブランチに対して出すのが慣習**

### コーディング規約

- **yapf**（行長100桁、`.style.yapf` 固定）+ **ruff**（v0.0.5 #295でグローバルreformat）+ **mypy**（CIで実行、PR #805で導入）+ pre-commit hooks
- type hints の `In[T]`/`Out[T]` アノテーションがModule定義の核であり、これはAGENTS.md の skill rules/blueprint quick-reference に記載
- CIは GitHub Actions で動き、`.github/workflows/` には mypy/pre-commit/pytest/16GB self-hosted runner/draft PRスキップ（#1397）/ROS CI変更検出（#364）/Transportベンチマーク（#1087）が含まれる
- カバレッジバッジ・CLA・サインオフ要件は本調査では明示観測不可
- Issue テンプレートはPR #1517（@SummerYang）で2026年3月に導入済み

### メンテナの応答速度

v0.0.10で88+ commits / 20 contributors / 700+ファイル変更が約1か月でmerge、v0.0.11で82 commits / 10 contributors / 396ファイルが約3週間でmergeと**極めて高速**。

---

## Open Issue / PRの観測できた範囲

> [!warning] 観測限界
> 本調査では GitHub の Issues一覧と個別 Issue ページの多くが fetch不能で、**Issue #1113（Teleop restructuring spec, by @ruthwikdasyam, 2026-01-26）** が個別観測できた唯一のOpen Issueです。Orgサイドバーに「6 issues need help」と表示されており、`help wanted` ラベル付き Issueが6件存在することが示唆されますが、具体タイトルは観測不可。

以下は現時点で観測できたOpen PR全件（外部 vs コア推定とともに）：

| PR# | タイトル | 注目点 |
|---|---|---|
| #1376 | lazy load legacy modules | @jeff-hykin（外部→Orgメンバー） |
| #1375 | generate simple irl trajectory | — |
| #1374 | fix(protobuf): pin major | @jeff-hykin |
| #1372 | Pin protobuf major | — |
| #1371 | feat: integrate gripper into coordinator tick loop | — |
| #1370 | #921 Trajectory Controller(s) | — |
| #1369 | Config adjustments | — |
| #1368 | fix(vision): correct LCM serialization for class_id | — |
| #1365 | feat(dask): remove dask | — |
| #1364 | Task: Create base manipulation module | — |
| #1362 | go2 webrtc TwistBase adapter | Go2実機検証が有効 |
| #1358 | Fastlio working with Go2 navigation stack | Go2実機検証が有効 |
| #1357 | go2 control coordinator TwistBase adapter | Go2実機検証が有効 |
| #1296 | [DRAFT] Create Zenoh Transport Protocol | @Kaweees（外部→Orgメンバー） |
| **#1293** | **Fix CI Divergence (Mostly mypy)** | **最も活発・外部参入点** |
| #1288 | AGIbot nav test blueprint using ROSNav bridge | — |
| #1164 | Organize Types for Coordinator/Cluster/Worker | @jeff-hykin |
| #1115 | Jing mujoco mac | — |
| #1108 | Temporal Animation + Renaming Entities | — |
| #1031 | tf fix for navigation to tagged location | Go2実機検証が有効 |
| #980 | Add Jetson Jetpack 6.2 + CUDA 12.6 deps | @Kaweees |
| **#967** | **VLM-enriched object detection navigation** | — |

### 過去にマージされた外部PRの代表例

- #310（Cerebras Agent, @joshuajerin）
- #350/#353（YOLOv11/FastSAM/CLIPのONNX化, @mdaiter）
- #767（Dask dead code削除, @ym-han）
- #973/#1093（Temporal Memory, @ClaireBookworm）
- #982（semantic navigation fix, @sinha7y）
- #1019/#1066/#972/#988/#1073（rerun-web強化, @Nabla7）
- #1081/#1149（FASTLIO2 + arm64 Docker, @baishibona）
- #1119/#1234（GraspGen統合, @JalajShuklaSS）
- #1174（CycloneDDS, @Kaweees）
- #1367（asyncio削除, @SamBull）

**外部PRが継続的にマージされる文化が定着していることの強い裏付け。**

---

## コアメンバー担当マップと手薄な領域（オーナーシップ機会）

10名規模のコアコントリビュータの担当領域をPR出現頻度から推定すると：

| コアメンバー | GitHub | 主担当 |
|---|---|---|
| Stash Pomichter（CEO） | @spomichter | リリースマネジメント、CLI/daemon、MCP CLI、Viewer、Drone、Temporal-Spatial Memory、AGENTS.md、ドキュメント、CI/CD |
| Paul Nechifor | @PaulNechifor / @paul-nechifor | コアアーキ、agents、worker pool、Dask撤廃、blueprint検証、navigation移行、MCP server |
| @mustafab0 | @mustafab0 | **Manipulationスタック筆頭（バス係数1）** |
| @ruthwikdasyam | @ruthwikdasyam | **Teleop筆頭（バス係数1）**、Go2 fleet、camera intrinsics、FastAPI WS |
| @jca0 | @jca0 | **MuJoCo simulationの主導**（#1035） |
| @alexlin2 | @alexlin2 | Perception全面refactor（#936）、Unitree WebRTC（#279）、ROS撤去（#1230） |
| Summer Yang | @SUMMERxYANG | G1 agent RPC、macOS、Issue templates |
| @leshy | @leshy | Transports / Streams / Mapper / Native SLAM統合 / Nix / Devcontainer |
| @Kaweees | @Kaweees | DDS / Zenoh transport、Jetson依存 |
| @jeff-hykin | @jeff-hykin | macOS / aarch64 / Nix / 型 / CI整合性 |

### 手薄な領域（オーナーシップを取りやすい）

「1人しか触っていない、もしくは触る人が極端に少ないモジュール」＝オーナーシップを取りやすい手薄領域：

1. **Manipulation サブシステム**（`dimos/robot/manipulation/`、xArm/Piper、Drake/Pinocchio/IK/RRT/GraspGen）— @mustafab0がほぼ単独で6PR以上を担当。READMEで `(unreleased)` と明示。**OpenARMs/HighTorque Pantera/TAMP/VLA-nativeの枝はまだ書き手がいない。**
2. **Teleop**（VR/phone/arm/hand controller/SpaceMouse/keyboard）— @ruthwikdasyamがほぼ単独で5PR以上。Issue #1113でVRBaseModule/SmartDeviceBaseModule/HandControllerBaseModuleの3分割スペックを起票済みだが、Joystick/Xbox/PS/SpaceMouse/Keyboard部の実装は手付かず。**スペック完備のgood first issue〜中規模タスクの宝庫。**
3. **Simulation の Genesis/Isaac Sim 統合** — v0.0.5〜v0.0.11のリリースノートに具体的PRがほぼなく（MuJoCoはv0.0.10で大刷新済み）、**継続オーナーが不在。**
4. **`web/` + TypeScriptフロント**（`dimos_interface`、`robot_web_interface`）— リポ全体でTSは1.7%。@ruthwikdasyam（FastAPI WS）/@Nabla7（auto-open）/@spomichterにしか触る人がいない。
5. **`models/` 配下**（ONNX/Detic/segmentation/depth）— @mdaiterの貢献（#350/#353）が止まったままで、**新モデル（DepthAnything、SAM2.1、最新VLM）の差し込みは誰も連続して触っていない。**
6. **Native module別リポ群**（`dimos-module-arise-slam`、`dimos-module-fastlio2`、`dimos-module-far-planner`、`dimos-module-pct-planner`、`dimos-module-local-planner`、`dimos-module-path-follower`）— Open Issue/PRがほぼ全リポで0、活動量が極めて低い。
7. **テスト**（特にintegration/replay系）— スポットPRが中心で、**「テストを主な仕事にしているコントリビュータが居ません」。**
8. **ドキュメント**（特に `docs/manipulation/`、`docs/teleop/`、`docs/simulation/genesis/`、`docs/simulation/isaac/`）— ほぼ未整備で、**国際化（日本語）に至っては全く着手されていない。**

---

## 段階別タスク推薦

以下、**ハードウェア制御（低レベルドライバ実装、新モータ制御プロトコルなど）以外**という制約に厳密に従い、Unitree Go2実機を「検証用テストベッド」として活用するシナリオを含めて、`good first issue 級` → `中規模（数週間）` → `本格貢献級（数か月、コアコミッタ路線）` の3段階で9タスクを提案する。

### Good first issue級（最初の1〜2 PR、1〜3日）

#### タスクA：PR #1293（Fix CI Divergence / mypy）への補助PR

コメント48と現在Open PRで最も活発。@jeff-hykinが外部からCIと型整合性を直そうとしているスレッドに、**個別ファイルの type hints / `# type: ignore` 整理 / ruff警告消去を切り出した小PRを投下する**。技術スタックはPython + mypy + ruff、ハードウェア不要、想定工数2〜5時間。受理されやすいパターン（依存pin、CI fix、deps修正は短サイクルで通る）に乗っており、**コアの保守者層（@PaulNechifor/@leshy/@jeff-hykin）から早期に顔を覚えてもらえるのがコアコミッタへの最短ステップ。**

関連: https://github.com/dimensionalOS/dimos/pull/1293

#### タスクB：AGENTS.md / docs/usage/cli.md からのサンプル不足セクション補強

`docs/usage/cli.md` には `dimos run/stop/status/log/list/show-config/agent-send/mcp` の8サブコマンドが網羅されている一方、`docs/concepts/modules.md` / `docs/api/sensor_streams/index.md` / `dimos/core/README_BLUEPRINTS.md` の各セクションには `examples/` 配下の動作するスニペットがほとんど引用されていない。**Unitree Go2実機を持つ強みを生かして `dimos --replay run unitree-go2` のリプレイ→キャプチャ→ドキュメントへの埋め込みまで一気通貫した実例追加PRを出す。** Python + Markdown、ハードウェア不要〜Go2任意、想定工数1〜3日。ドキュメントは @spomichter/@leshy/@jeff-hykinが広く触っているため、ここに食い込むとCEOの目に留まりやすい。

#### タスクC：日本語ドキュメント i18n 起票

本調査では国際化に着手された痕跡が**完全にゼロ**。`docs/usage/cli.md` / `docs/installation/nix.md` / `dimos/core/README_BLUEPRINTS.md` の3本を `docs/ja/` 配下に翻訳し、Issueで「日本語i18nの段階導入提案」を起票してBounty化を打診する。技術スタックはMarkdownのみ、ハードウェア不要、想定工数3〜5日。**Project Go-Bigでアジア展開を意識するDimensional社にとって、日本語ドキュメントの先行導入は戦略的に価値が高く、Issue→翻訳PR→翻訳ガイドライン制定→i18nオーナーという順でオーナーシップを取れる。**

---

### 中規模（数週間、複数PR）

#### タスクD：Issue #1113「Teleop restructuring」の Joystick/Xbox/PS/SpaceMouse/Keyboard 系 HandControllerBaseModule 実装

@ruthwikdasyamがVRBaseModule/SmartDeviceBaseModule/HandControllerBaseModuleの3分割スペックを起票済みで、VRとPhone/Armは本人が #1215（Quest VR）/#1280（phone）/#1246（arm）で実装したものの、**HandController系（離散軸入力）の実装は未着手。**

技術スタックはPython + pygame/inputs/hidapi（OS抽象化）+ `In[T]/Out[T]` モジュール定義、ハードウェアはUSBジョイスティックのみ（Go2実機なくてもOK、Go2があればteleop実機検証で更に強い）、想定工数2〜4週間。Teleop はバス係数1領域なので、**ここを取れれば「Teleop の HandController オーナー」として Org 内ポジションが生まれる。**

関連Issue: https://github.com/dimensionalOS/dimos/issues/1113

#### タスクE：Genesis または Isaac Sim 上での Go2 ブループリント追加

MuJoCo は @jca0 の #1035 で土台ができmacOS/camera intrinsics/multicastで複数人が触っているが、**Genesis（dimensionalOS/Genesis fork）とIsaac Sim統合はリリースノートに具体PRがほぼ存在しない。** `dimos --simulation run unitree-go2` 相当を Genesis 側で動かすブループリント + examples/ 追加を実装する。技術スタックはPython + Genesis SDK/IsaacLab、ハードウェア不要（GPUは推奨）、想定工数3〜6週間。**「GenesisオーナーIt」として明確な空白地に旗を立てられる点が本格貢献への入口になる。**

#### タスクF：dimos-viewer 上の DimOS 専用ブループリント・テンプレート集

dimos-viewer は rerun-io fork（Rust主体）だが、Python/TS側のDimOS固有プリセット（Camera/3D split、TF tree、occupancy grid + waypoint クリック、Temporal-Spatial Memoryのヒートマップ、Manipulationのグラスプ可視化）はまだ揃っていない（@spomichterの #1414/#1394/#1473/#1478/#1525 はコア機能のみ）。Pythonの rerun SDK 上に「dimos-viewer プリセットライブラリ」を作り `dimos-viewer-presets` 風の補助モジュールを提案する。技術スタックはPython + rerun-sdk≥0.20、Go2実機があるとリプレイ録画→可視化のリアルなデモが作れて差別化、想定工数3〜5週間。**可視化のオーナーは事実上CEOが兼任しており、ここに専任が入ると非常に歓迎されやすい。**

#### タスクG：MCP skill ライブラリの拡張＋ベンチマーク

MCP server（#1300, @PaulNechifor）とMCP CLI（#1451, @spomichter）は揃ったものの、`@skill` をHTTP toolとして公開する「**実際に役立つスキルセット**」自体は薄い（PR #1499 の agent context logging でも改善継続中）。`is_flying_to_target`（#635）級の単機能スキルを5〜10個（`navigate_to_landmark`、`track_person`、`inspect_object`、`record_episode`、`dock_to_charger` 等）追加し、Cursor/Claude Codeから実機Go2で呼べるデモを作成、加えてMCP tool call のレイテンシ・成功率ベンチマークを `tests/integration/mcp/` に追加する。技術スタックはPython + MCPプロトコル + LLM API（OpenAI/Claude/Cerebras）、**Go2実機が大いに活きる**（「実機で動くMCPデモ」は社内的にも対外発信材料になる）、想定工数4〜6週間。**MCPはREADMEで `experimental` 明示の中核ロードマップ領域で、Bounty化されている可能性も高い。**

---

### 本格貢献級（数か月、コアコミッタ路線）

#### タスクH：テスト・ベンチマーク基盤の専任オーナー化

「テストを主な仕事にしているコントリビュータが居ない」という観測事実は、逆に**誰も取っていない大きなニッチ**を示す。具体的には：

1. `tests/integration/` の replay-based regression test を Go2/Drone/Manipulation 各ブループリントに展開
2. Transport ベンチマーク（既存 #1087）をCycloneDDS/SHM/LCM/Zenoh（PR #1296 に紐下げ）で比較する継続ハーネスに進化
3. GitHub Actions に self-hosted ランナー以外の経路（GitHub-hosted）でも動く軽量スイートを設計
4. カバレッジバッジ導入（README観測上未掲示）

技術スタックはPython + pytest + GitHub Actions + Codecov、Go2実機でのreplayキャプチャに使える、想定工数2〜4か月。**v0.0.x→v0.1.0（Beta）への移行で「テストの信頼性」はCEOの説得材料になり、CI/テスト系で安定してPRを出せる人は @PaulNechifor/@leshy/@spomichterから最も信頼を得やすいポジション。**

#### タスクI：Manipulation の OpenARMs/HighTorque Pantera 追加＋skill 層整備

Manipulation は @mustafab0 が単独で書いており、READMEで `(unreleased)` 明示の最大ロードマップ領域。OpenARMsとHighTorque Pantera はREADMEで「immediate roadmap」に挙がっているもののステータス絵文字が未確定。**新規アームのURDF/MJCF統合→IK/Drakeバインディング→MuJoCo blueprint→agentic pick-and-place skill までフルスタックで担当する。**

> [!warning] ハードウェア制御制約
> これは「アーム実機の制御信号送出」が含まれるため、本タスクの「ハードウェア制御以外」制約に抵触する可能性がある。回避策として「**シミュレーション側のみで完結する範囲**」（URDF/MJCF統合、Drake/Pinocchio配線、シミュレーション用skill層、TAMP/VLA-nativeプランナの実装）に限定すれば制約を満たせる。

技術スタックはPython + Drake + Pinocchio + OMPL + MuJoCo、Go2実機は無関係、想定工数3〜6か月。**バス係数1領域かつunreleased、Bounty設定の可能性が最も高い。**

---

## タスク一覧サマリー

| 段階 | タスク | HW要件 | 主要技術 | 想定工数 | コアコミッタへの寄与 |
|---|---|---|---|---|---|
| GFI | A: #1293 mypy/CI補助 | 不要 | Python, mypy, ruff | 2〜5時間 | 早期にPaul/leshy/jeffの信頼獲得 |
| GFI | B: examples/→docs/結合補強 | Go2任意 | Markdown, Python | 1〜3日 | spomichterの目に留まる |
| GFI | C: 日本語docs i18n起票 | 不要 | Markdown | 3〜5日 | i18nオーナー新規ポジション |
| 中 | D: Issue #1113 HandController実装 | USBジョイスティック | Python, pygame/inputs | 2〜4週 | Teleop バス係数解消の片腕 |
| 中 | E: Genesis/Isaacブループリント | 不要（GPU推奨） | Python, Genesis | 3〜6週 | Sim空白地のオーナー |
| 中 | F: dimos-viewerプリセット集 | Go2任意 | Python, rerun-sdk | 3〜5週 | 可視化の専任化 |
| 中 | G: MCP skill拡張＋ベンチ | **Go2実機が活きる** | Python, MCP, LLM APIs | 4〜6週 | experimental ロードマップ中核 |
| 本格 | H: テスト/CI/ベンチ基盤の専任 | Go2でreplay取得 | pytest, GH Actions, Codecov | 2〜4か月 | β昇格時の最大説得材料 |
| 本格 | I: Manipulation skill/sim拡張 | 不要（sim限定） | Drake, Pinocchio, MuJoCo | 3〜6か月 | unreleased領域のオーナー |

---

## Unitree Go2実機保有という差別化を最大化する戦略

Go2実機を持っていることはREADMEで 🟢 stable（最も成熟）と表示される唯一の四足ロボットを、外部から検証できる稀有なポジションを意味する。具体的に活かせる場面は次の三つ。

### 1. Open PRの実機検証担当として参加

@kahzmic の #1358（FASTLIO2 + Go2 nav stack, Draft）、@sinha7y の #1031（tf fix for navigation to tagged location）、@mustafab0の #1357/#1362（Go2 webrtc TwistBase）、@ruthwikdasyamの #1487（Go2 fleet control）など、**Go2実機検証が必要なPRが常に複数Openしており**、コメントで「実機で再現確認しました」「v0.0.11 + Mid-360 Lidarで動作OK」とフィードバックを返すだけで、コアメンバーの作業を実質的に加速できる。**これはPRを書く前に信頼を獲得する最良の方法。**

### 2. 高品質なリプレイデータセットの取得・公開

`dimos --replay run unitree-go2` 用の高品質リプレイデータセットをGo2で取得・公開すること。`replay integration test` は既にv0.0.8から二層化されているものの、データセットの多様性は不足気味（観測上）。**家庭環境・オフィス・屋外などのリプレイをLFSで投下し、回帰テストを駆動できるようにすれば、タスクHのテスト基盤と直結する。**

### 3. Project Go-Bigへの先行データ貢献

X上で告知された「世界最大規模のヒューマノイド事前学習データセット」計画（Brookfield提携）は**データ規模が勝負**であり、Go2実機からの行動・知覚ストリームをパイプラインに乗せて自動収集する仕組みを作れば、データセット側の専任ポジションが生まれる可能性がある（ただしhumanoid事前学習に四足データが組み込まれるかは公開情報からは不明、観測不可）。

---

## 結論と最初の30日アクションプラン

DimOSは**新規コントリビュータを受け入れる文化が定着した、ローンチ半年でStar 3.1k規模の若い高速プロジェクト**で、外部PRからOrgメンバー化した実例（@Kaweees, @jeff-hykin, @christiefhyang）が既に複数ある。最大の特徴は「**AIコーディングエージェント駆動開発**」（AGENTS.md + MCP）と「**Bounty List経由のタスク募集**」で、人間の貢献者はCursor/Claude CodeにAGENTS.mdを読ませてから動き出すスタイルが推奨されている。

### 最初の30日

**① Discord参加 → ② `dev` ブランチでローカル環境構築 → ③ Bounty List Google Sheetsを確認 → ④ タスクA（#1293 mypy/CI）+ タスクB（docs/examples 結合）+ タスクC（日本語i18n起票）の3本を並行実行**するのが最も効率的。

これでCI系で @jeff-hykin・@PaulNechifor、ドキュメント系で @spomichter・@leshy、i18n起票でCEOの戦略意図に絡むという3軸の接点を一気に作れる。

### 次の60日〜半年

- 60日：タスクD（Teleop HandController, Issue #1113）またはタスクG（MCP skill＋実機ベンチマーク）
- 半年〜1年：タスクH（テスト/CI基盤専任）またはタスクI（Manipulationシム拡張）の本格貢献領域を取りに行く

> [!quote] 最も確度の高い道筋
> **現在観測できる事実に基づいたコアコミッタ入りへの最も確度の高い道筋。**

---

> [!note] 観測限界に関する正直な注記
> 本調査では GitHub の `/issues?q=...` / `/pulls?q=is:pr+is:closed` / `/graphs/contributors` / `/pulse` / `/commits/main/<dir>` / `/tree/main/dimos` の多くがドメイン許可制約でfetch不能だったため、「6 issues need help」の具体タイトル、CONTRIBUTING.md/AGENTS.md/PULL_REQUEST_TEMPLATE.mdの本文、各PRのレビュー時間タイムスタンプ、コミット数の正確なランキング、Bounty List Google Sheetsの内容、Discord内チャネル構成と自己紹介慣習は観測不可でした。これらはDiscord参加・GitHub にログインしての直接確認・`gh api repos/dimensionalOS/dimos/stats/contributors` 経由のAPI取得で補完するのが推奨です。

---

## 関連ノート

- [[DimOS実装事例とエコシステム分析]] — DimOSの実装事例・エコシステム全体像
- [[DimOSとUnitree Go2で今できることと高精度SLAM・エージェント化の実装指針]] — Go2スタックの技術詳細と実装指針
