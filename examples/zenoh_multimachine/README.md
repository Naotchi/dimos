# zenoh multi-machine PoC

dimos の Module を別マシン(または別プロセス)で動かし、zenoh transport で
結合するフィージビリティ検証用 PoC。背景・結果・ランブックは
[docs/zenoh-multimachine-feasibility.md](../../docs/zenoh-multimachine-feasibility.md) を参照。

## 使い方

```bash
# 受信側(マシン A / ターミナル A): echo + Image シンク、常駐
python run_pong.py

# 送信側(マシン B / ターミナル B): RTT 計測 + Image 送信、終わったら統計を出して終了
python run_ping.py --count 200 --rate 20 --image
```

- 同一ホストならデフォルト設定で 2 プロセスがつながる(loopback scouting)。
- マシンをまたぐ場合は両側で `ZENOH_SCOUTING=true`(LAN multicast)。
  multicast が通らないネットワークでは zenoh router を gossip ハブとして立てて
  両側 `ROBOT_IP=<router-ip> ZENOH_SCOUTING=true`。
- env 名は prefix なし(`ZENOH_SCOUTING`、`ROBOT_IP`)。`DIMOS_` prefix が
  効くのは `DIMOS_TRANSPORT` のみ(スクリプトが `zenoh` を設定、env で上書き可能)。

## Docker 2 コンテナでの検証

```bash
./container_test.sh            # 同一 network、multicast scouting
./container_test.sh router     # zenohd router + gossip 発見
```

ホストの repo / venv / uv Python を read-only マウントするのでイメージビルドは
不足 so ライブラリの追加のみ(`Dockerfile`、初回に自動ビルド)。docker グループ
権限が必要。

## ファイル

- `poc_modules.py` — ZenohPing / ZenohPong(RTT)、ZenohImageSource / ZenohImageSink(スループット)
- `run_pong.py` — 受信側 dimos インスタンス
- `run_ping.py` — 送信側 dimos インスタンス(exit 0/1 で合否、JSON 結果出力)
