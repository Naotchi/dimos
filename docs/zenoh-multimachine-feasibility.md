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
  - `DIMOS_ZENOH_SCouting=true` で LAN 全体の multicast 発見 + gossip が有効になる
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
3. **L2: 実 2 マシン検証**(ランブックに従い月曜に実施)
   - 案 A(推奨・有線 LAN): 両マシンで `DIMOS_ZENOH_SCOUTING=true`
   - 案 B(multicast が通らないネットワーク): どこかで zenohd router を起動し、
     両マシンとも `ROBOT_IP=<router-ip>` で接続

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
| L1 ping/pong | `DIMOS_ZENOH_SCOUTING=true` | ✅ 50/50、RTT 中央値 1.06ms |

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
  settle 待ちで吸収。実運用では `DIMOS_ZENOH_CONNECT_TIMEOUT` /
  QoS(`QOS_NEVER_DROP`)側で扱う話。
- ImageSink の片道レイテンシは壁時計差分なので、マシン間では NTP 同期が前提
  (RTT は送信側プロセス内の perf_counter 差分なので時計同期不要)。

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
DIMOS_ZENOH_SCOUTING=true python run_pong.py

# マシン B(送信側)— A が起動してから
cd <repo>/examples/zenoh_multimachine
DIMOS_ZENOH_SCOUTING=true python run_ping.py --count 200 --rate 20 --image
```

判定: `PASS`(ping 95% 以上)+ マシン A 側ログの `image_sink` が
`received: 100, fps ≈ 10, mbytes_per_s ≈ 9.3` なら成功。RTT をローカル値
(中央値 ~1ms)と比較して記録する。

### 案 B: zenoh router 経由(multicast が通らないネットワーク)

どちらかのマシン(または第 3 のホスト)で zenoh router を起動:

```bash
docker run --rm -p 7447:7447 eclipse/zenoh:1.9.0-3
```

両マシンとも `ROBOT_IP=<router のホスト IP>` を設定して実行
(dimos は `tcp/<ip>:7447` に明示接続する。scouting は不要):

```bash
ROBOT_IP=192.168.x.x python run_pong.py     # マシン A
ROBOT_IP=192.168.x.x python run_ping.py --count 200 --rate 20 --image   # マシン B
```

注意: router のバージョンは eclipse-zenoh(Python)の 1.9 系と合わせること。

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
