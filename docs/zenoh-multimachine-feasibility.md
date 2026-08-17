# Zenoh transport によるモジュール分散実行 — フィージビリティ検証

fork 固有ドキュメント。upstream で実装された zenoh transport を使い、dimos の各
Module を異なるマシンで動作させられるかを検証する。

## 背景(調査結果の要約)

- dimos の Module は既に**プロセス分離**されている(forkserver worker)。モジュール間の
  データプレーン(typed stream)と制御プレーン(@rpc)は pub/sub であり、
  `DIMOS_TRANSPORT=zenoh`(または `--transport=zenoh`)で全ストリーム + RPC が
  zenoh 経由になる。
- zenoh は **peer モード**で動作(router 不要)。`ZenohConfig`
  (`dimos/protocol/service/zenohservice.py`)のデフォルト:
  - `connect`: `--robot-ip` / `ROBOT_IP` から `tcp/<ip>:7447` を導出(robot 側 bridge 用)
  - `listen`: なし(zenoh デフォルトの ephemeral ポートで listen)
  - `scouting`: false = **multicast 発見は loopback のみ**(同一ホスト内のプロセス同士は
    発見し合う。リモートは connect endpoint 経由のみ)
  - `ZENOH_SCOUTING=true` で LAN 全体の multicast 発見 + gossip が有効になる
- **env 変数名の罠**: `GlobalConfig` は pydantic-settings に prefix なしで、env 名は
  **素のフィールド名**(`ROBOT_IP`, `ZENOH_SCOUTING`, `ZENOH_CONNECT_TIMEOUT`)。
  `DIMOS_` prefix が効くのは明示 alias を持つ `DIMOS_TRANSPORT` のみ。
  `DIMOS_ZENOH_SCOUTING` のような名前は**黙って無視される**ので注意。
- トピックはストリーム名から決定的に導出される(`/{name}` → zenoh key
  `dimos/{name}/<msg型>`)。**別々に起動した 2 つの dimos インスタンス間でも、
  ストリーム名と型が一致すれば自動的につながる。**
- coordinator ↔ worker の制御チャネル(multiprocessing pipe)は単一ホスト限定。
  したがってマルチマシン構成は「**各マシンで 1 つずつ dimos インスタンス(blueprint を
  分割)を起動し、zenoh で結合**」が正しい分解である。
- SHM 系 transport は同一ホスト限定。クロスマシンのストリームには使わない。
- `ZenohTransport.__reduce__` は topic のみ保持するため、transport 生成時の
  kwargs(listen/connect)は worker プロセスに伝搬しない。クロスマシン設定は
  **env 変数駆動**(`DIMOS_ZENOH_SCOUTING` / `ROBOT_IP`)で行うのが正しい。

## 検証ステップ

1. **L0: プロセス間スモークテスト**(完了 ✅)
   `ZenohTransport` を直接使う 2 プロセスの pub/sub。デフォルト設定(loopback
   scouting)で 10/10 メッセージ受信を確認済み。
   注意点: zenoh の key expr は先頭 `/` 禁止。`make_transport` 経由では
   `transport_topic()` が `/foo` → `dimos/foo` に変換するが、`ZenohTransport` を
   直接構築する場合は `dimos/...` 形式で渡すこと。
2. **L1: 2 つの coordinator インスタンス間の疎通(同一ホスト)**
   `examples/zenoh_multimachine/` の PoC で、ping/pong(RTT 計測)と Image
   スループット計測を、別々に起動した 2 つの dimos インスタンス間で行う。
   これが「マルチマシンの同一ホスト代理検証」— コードパスはマルチマシンと同一で、
   ネットワークだけが loopback。
3. **L1.5: Docker 2 コンテナ検証**(完了 ✅ — 下記「コンテナ検証」)
   ネットワーク名前空間が分かれるため実マルチマシンに近い代理検証。
4. **L2: 実 2 マシン検証**(ランブックに従い月曜に実施)
   - 案 A(推奨・有線 LAN): 両マシンで `ZENOH_SCOUTING=true`
   - 案 B(multicast が通らないネットワーク): zenohd router を gossip ハブとして起動し、
     両マシンとも `ROBOT_IP=<router-ip>` + `ZENOH_SCOUTING=true`

## PoC 構成(`examples/zenoh_multimachine/`)

fork 固有の新規ファイルのみで構成(upstream 由来ファイルの編集なし)。

- `poc_modules.py` — `ZenohPing`(PoseStamped を送信し echo の RTT を計測)、
  `ZenohPong`(echo)、`ZenohImageSource` / `ZenohImageSink`(Image スループット計測)
- `run_pong.py` — 受信側インスタンス(マシン A): Pong + ImageSink、常駐
- `run_ping.py` — 送信側インスタンス(マシン B): Ping + ImageSource、
  規定数を送って統計を表示・JSON 出力して終了

## 結果(2026-08-16、単一ホスト・2 dimos インスタンス)

環境: この開発機 1 台、`feat/zenoh-multimachine-poc` ブランチ、eclipse-zenoh 1.9.0
(venv に追加インストール。lockfile 準拠バージョン)。

| テスト | 設定 | 結果 |
|---|---|---|
| L0 スモーク(生 `ZenohTransport` 2 プロセス) | デフォルト(loopback scouting) | ✅ 10/10 受信 |
| L1 ping/pong(2 coordinator インスタンス) | デフォルト | ✅ 200/200、ロス 0。RTT 中央値 **1.06ms** / p95 1.47ms / max 1.73ms |
| L1 ping/pong + Image 同時 | デフォルト | ✅ ping 200/200(RTT 劣化なし)、Image **100/100 受信、9.98fps / 9.3MB/s**(640×480 RGB 生 @10Hz 指定どおり)、片道中央値 1.6ms |
| L1 ping/pong(scouting 検証のつもりの回) | `DIMOS_ZENOH_SCOUTING=true` | ⚠️ PASS したが、後日 env 名が無効と判明(実際はデフォルト設定の再検証だった)。真の scouting 検証はコンテナで実施 |

**結論: フィージビリティあり。** 別々に起動した dimos インスタンス間で、
コード変更なしに(ストリーム名と型の一致だけで)typed stream が zenoh 経由で接続され、
メッセージロスなし・ミリ秒級レイテンシで通信できる。upstream 由来ファイルの編集は不要
だった(PoC はすべて fork 固有の新規ファイル)。

### 検証中に確認した注意点

- **venv に eclipse-zenoh が入っていなかった**(venv が lockfile より古い)。
  `uv pip install eclipse-zenoh==1.9.0` で解決。他マシンでも同様の確認が必要。
- zenoh の key expr は先頭 `/` 禁止。`ZenohTransport` を直接構築する場合は
  `dimos/...` 形式で渡す(`make_transport` 経由なら自動変換)。
- 起動直後は peer 発見前に送った ping がロスする(数百 ms 程度)。PoC では
  settle 待ちで吸収。実運用では `ZENOH_CONNECT_TIMEOUT` /
  QoS(`QOS_NEVER_DROP`)側で扱う話。
- ImageSink の片道レイテンシは壁時計差分なので、マシン間では NTP 同期が前提
  (RTT は送信側プロセス内の perf_counter 差分なので時計同期不要)。

## コンテナ検証(2026-08-17、Docker 2 コンテナ)

`examples/zenoh_multimachine/container_test.sh` で実施。素の Ubuntu イメージ
(`Dockerfile`、不足 so ライブラリのみ追加)にホストの repo / venv / uv Python を
read-only マウントする構成(イメージビルド不要)。コンテナはネットワーク
名前空間・IP が分かれるため、同一ホスト 2 プロセスより実マルチマシンに近い。

| モード | 構成 | 結果 |
|---|---|---|
| scouting | 同一 docker network、`ZENOH_SCOUTING=true`(multicast 発見) | ✅ ping 200/200(RTT 中央値 ~1.08ms)、Image 100/100(9.97fps / 9.3MB/s) |
| router | zenohd 1.9.0 コンテナ + 両側 `ROBOT_IP=<router>` + `ZENOH_SCOUTING=true`(gossip 発見 → ピア直結) | ✅ 同等の結果 |

### コンテナ検証で判明した zenoh の重要な挙動

切り分けのため素の zenoh API でも検証した結果:

1. **peer モードのセッションは router を介した「データ中継」ができない**(zenoh 1.9)。
   router へのリンク確立までは成功するが、subscribe interest が伝わらず配送 0 件。
   完全に分離されたネットワーク(コンテナ間直接到達不能)で再現確認済み。
2. **client モードなら router 中継で通る**(分離ネットワークでも 10/10)。
   ただし dimos の `ZenohConfig` は peer モード固定(env で変更不可)なので、
   dimos からは現状使えない。
3. **peer モードの現実解は「router = gossip ハブ」**: multicast を切っても
   gossip でピアのロケータを教え合い、**ピア同士が直接 TCP 接続**して配送される
   (pub 側の links に router + 相手ピア直結の両方が現れることを確認)。
   → 「multicast は遮断、TCP unicast は通る」という典型的な企業ネットワークで有効。
   逆に、ピア間の TCP も遮断されている環境は現状の dimos では不可(client モード
   対応が必要 → upstream への提案候補)。

## 実 2 マシン検証ランブック(月曜実施用)

前提: 両マシンとも本ブランチを checkout し、venv に `eclipse-zenoh==1.9.0` が
入っていること(`python -c "import zenoh"` で確認)。

### 案 A(推奨): multicast scouting

同一 L2 セグメント(有線 LAN 推奨)にあること。zenoh のスカウトは UDP 224.0.0.224:7446、
データリンクは**ランダムポートの TCP** なので、マシン間のファイアウォールは
相互の TCP 接続を許可しておく(検証時は一時的に無効化が手っ取り早い:
`sudo ufw status` で確認)。

```bash
# マシン A(受信側)
cd <repo>/examples/zenoh_multimachine
ZENOH_SCOUTING=true python run_pong.py

# マシン B(送信側)— A が起動してから
cd <repo>/examples/zenoh_multimachine
ZENOH_SCOUTING=true python run_ping.py --count 200 --rate 20 --image
```

**env 名に注意**: `ZENOH_SCOUTING`(prefix なし)。`DIMOS_ZENOH_SCOUTING` は
無効(黙って無視される)。

判定: `PASS`(ping 95% 以上)+ マシン A 側ログの `image_sink` が
`received: 100, fps ≈ 10, mbytes_per_s ≈ 9.3` なら成功。RTT をローカル値
(中央値 ~1ms)と比較して記録する。

### 案 B: zenoh router を gossip ハブに(multicast が通らないネットワーク)

どちらかのマシン(または第 3 のホスト)で zenoh router を起動:

```bash
docker run --rm -p 7447:7447 eclipse/zenoh:1.9.0
```

両マシンとも `ROBOT_IP=<router のホスト IP>` **と** `ZENOH_SCOUTING=true` を
設定して実行(router 接続 → gossip でピアを発見 → ピア同士が直接 TCP 接続):

```bash
ROBOT_IP=192.168.x.x ZENOH_SCOUTING=true python run_pong.py     # マシン A
ROBOT_IP=192.168.x.x ZENOH_SCOUTING=true python run_ping.py --count 200 --rate 20 --image   # マシン B
```

注意:
- `ZENOH_SCOUTING=true` は必須。peer モードは router のデータ中継に乗れないため、
  gossip(scouting=true に含まれる)なしでは router にリンクしても配送されない
  (コンテナ検証で確認済み)。
- データはピア間の直接 TCP で流れるため、マシン間の TCP 接続
  (ephemeral ポート含む)が許可されている必要がある。
- router のバージョンは eclipse-zenoh(Python)の 1.9 系と合わせること。

### トラブルシュート

- つながらない → まず両側の起動ログの `transport=zenoh scouting=...` 行を確認。
- 案 A で発見できない → AP/スイッチが multicast をフィルタしている可能性。案 B へ。
- `RUST_LOG=zenoh=debug` で zenoh 本体のログが出る(セッション確立の可視化)。
- 観測ツール: `dimos --transport=zenoh spy` 相当(`dimos/cli/spy`)でトピックを覗ける。

## 次のステップ(フィージビリティ確認後)

1. 実ロボット blueprint の分割: 例えば Go2 実機系(hardware 層)をマシン A、
   perception / agent 層をマシン B に分け、各マシンの起動スクリプトを
   fork 固有ファイルとして追加する(`autoconnect` の組を分割するだけ)。
2. SHM 系 transport(Image/PointCloud のデフォルト)がクロスマシン境界に
   ならないよう、分割境界のストリームは zenoh になっていることを
   起動ログ(`Transport` 行)で確認する。
3. 帯域の大きい Image は JPEG 圧縮系 transport の zenoh 版が upstream に
   ないため、必要なら fork 固有で `JpegZenohTransport` 相当を追加検討。
