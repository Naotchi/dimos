 ネットワーク通信遅延の要求

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

## `<10ms` の根拠

ドキュメント本文に数値の導出・理由は **書かれていない**。

- 追加コミット: `6426c53e` "docs: go2 preflight checklist (#1349)"（作者 leshy / upstream / 2026-02-24）
- コミットメッセージは "added go2 preflight checklist" と typo 修正のみで、10ms の根拠説明なし。
- つまり upstream 開発者が経験則的に置いた値で、計測・スペック由来の裏付け文書は存在しない。正式な根拠が要るなら upstream PR #1349 か本人に確認するのが確実。

## 「10ms ⟹ 100Hz で回したい」という解釈は誤り

1/10ms = 100Hz という逆算は算数としては正しいが、**この系の実装意図とは一致しない**。

### DimOS↔Go2 で実際に流れるストリームのレート

| ストリーム          | レート     | 出典                                                              |
| -------------- | ------- | --------------------------------------------------------------- |
| color_image    | ~14 Hz  | [[robot/unitree/go2/blueprints/basic/unitree_go2_basic.py]]:113 |
| global_map     | ~7.8 Hz | unitree_go2_basic.py:112                                        |
| global_costmap | ~7.6 Hz | unitree_go2_basic.py:114                                        |
| LiDAR          | 10 Hz   | hardware/sensors/lidar/livox/module.py:60                       |
| odom (sim)     | 50 Hz   | mujoco_connection.py:61                                         |

DimOS が WebRTC 越しにやっているのは **~7〜14Hz のセンサ受信 + 高レベル sport コマンド送信** で、100Hz の往復ループは存在しない。

### なぜ100Hz制御ループにならないか

Go2 の本当に速い制御（脚のゲイト制御、500Hz〜1kHz級）は **ロボット側 MCU がオンボードで閉じている**。DimOS は WebRTC で「進め」「ジャンプ」等の高レベルコマンドを投げるだけ（`execute_sport_command` は publish 後 ~2ms で return）で、リアルタイム制御の往復路には入っていない。100Hz tick が出てくるのは xarm/piper（`tick_rate=100.0`）や G1（500Hz）であって、Go2 ナビスタックではない。

### `<10ms` の正しい読み

制御レート予算の逆算ではなく **「リンク品質のサニティチェック」**。

- 良好な有線/Wi-Fi LAN の ping は通常 1〜5ms、`<10ms` は緩めの天井。
- これを超える / パケットロスがあると、混雑 Wi-Fi や遠い経路を意味し、WebRTC の映像/LiDAR ストリーム（~14Hz/~10Hz）がドロップ・ジッタで劣化する。
- つまり「100Hz を保証する数値」ではなく「ストリームが破綻しない清潔なローカルリンクか」を弾くゲート。
