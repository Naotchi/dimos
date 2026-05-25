# ネットワーク通信遅延の要求

[[platforms/quadruped/go2/index.md]]:74（pre-flight チェックの項目）

> Robot is reachable and low latency <10ms, 0% packet loss

→ ロボットへの ping が **10ms 未満・パケットロス 0%** であること。これが **PC ↔ Go2 間のネットワーク遅延に関する明示的な要求**です。

## 関連する遅延の記述（参考）

| 項目 | 値 | 出典 |
|------|----|------|
| WebRTC モーションコマンド送信 | `execute_sport_command` は publish 後 ~2ms で即時 return | [[superpowers/specs/2026-05-16-agentic-ja-bench-simplify-design.md]]:41 |
| LLM 推論（TTS 律速ライン） | TTFT < 500ms、TPOT < 50ms（≒20 tok/s 以上） | [[survey/unitree-go2-agentic-local-tts-llm-2026-05.md]]:23 |

下2つはネットワーク遅延ではなく、**コマンド処理レイテンシ（~2ms）** と **LLM 推論レイテンシ（TTFT/TPOT）** なので、「PC ↔ Go2 の通信遅延の要求」として該当するのは **<10ms, 0% packet loss**（[[platforms/quadruped/go2/index.md]]）です。

## 補足

- これは **upstream 由来**の [[platforms/quadruped/go2/index.md]] に書かれており、fork 固有の要求値ではありません。
- 実機（`ROBOT_IP=192.168.11.10`, STA mode）での確認が必要なら `ping` で実測できます。
