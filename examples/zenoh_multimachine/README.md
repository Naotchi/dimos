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
- マシンをまたぐ場合は両側で `DIMOS_ZENOH_SCOUTING=true`(LAN multicast)、
  または zenoh router を立てて両側 `ROBOT_IP=<router-ip>`。
- `DIMOS_TRANSPORT` はスクリプトが `zenoh` を設定する(env で上書き可能)。

## ファイル

- `poc_modules.py` — ZenohPing / ZenohPong(RTT)、ZenohImageSource / ZenohImageSink(スループット)
- `run_pong.py` — 受信側 dimos インスタンス
- `run_ping.py` — 送信側 dimos インスタンス(exit 0/1 で合否、JSON 結果出力)
