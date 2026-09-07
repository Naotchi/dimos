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

## 標準モジュールでのオフロード検証

独自 PoC モジュールではなく dimos 標準モジュールでの分割(エッジ: replay ロボット /
クラウド: VoxelGridMapper→CostMapper):

```bash
# コンテナ 2 つで自動実行(受信側→ロボット側の順で起動、PASS/FAIL 判定)
./container_offload_test.sh

# 手動(2 ターミナル)の場合 — 必ずマッパー側を先に起動する(replay はループしない)
python run_mapping_offload.py --duration 300                     # 先
dimos --transport zenoh --replay --viewer none run unitree-go2-basic  # 後
```

## ファイル

- `poc_modules.py` — ZenohPing / ZenohPong(RTT)、ZenohImageSource / ZenohImageSink(スループット)
- `run_pong.py` — 受信側 dimos インスタンス
- `run_ping.py` — 送信側 dimos インスタンス(exit 0/1 で合否、JSON 結果出力)
- `run_mapping_offload.py` — 標準モジュール(VoxelGridMapper→CostMapper)のクラウド側ランナー
- `container_test.sh` / `container_offload_test.sh` — Docker 2 コンテナ検証(PoC 版 / 標準モジュール版)
- `Dockerfile` — コンテナ検証用の最小ランタイム(不足 so ライブラリのみ)
