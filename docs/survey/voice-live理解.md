  「ユーザーが喋ったら、AI が必ず一言返してから、必要ならロボットを動かす」

  登場人物（変数3つだけ覚える）

  - had_audio — この応答で AI は声を出したか？（True/False）
  - pending_calls — この応答で AI が「ツール呼びたい」と言ったリスト
  - trigger — いまの応答は誰のせいで始まった？（user / preface_forced / tool_result）

  応答が終わるたびに、この3つを見て次の行動を決める。それだけ。

  1ターンの流れ

  ユーザー「足元見て」
     ↓
  AI 応答①が始まる (trigger=user)
     ↓
  AI: 声を出した？
     ├─ Yes →「はい！」と喋った  → had_audio=True
     └─ No  → 黙ってツールだけ呼ぼうとした → had_audio=False
     ↓
  AI: ツール呼びたい？
     └─ "observe" を呼びたい → pending_calls=[observe]
     ↓
  応答①おわり (RESPONSE_DONE)
     ↓
  === ここから後始末 (_on_response_done) ===
     ↓
  had_audio が False なら？
     → 「実行します」と強制的に一言喋らせる ← これが forced preface
         (応答② trigger=preface_forced)
     ↓
  pending_calls を1個ずつ実行
     → observe を MCP 経由で実行 → 結果が返る
     ↓
  このツールは「結果を声で報告するリスト」に入ってる？
     ├─ Yes (observe など) → 「足元に箱があります」と喋らせる
     │                       (応答③ trigger=tool_result)
     └─ No → 黙る（次のユーザー発話を待つ）

  なぜ trigger が3種類いるの？

  応答が終わると毎回「次どうする？」を決めるけど、自分が作った応答(preface/tool_result)で同じ後始末をやると無限ループする。だから:

  - user の応答が終わった時だけ → 後始末を起動
  - preface_forced / tool_result の応答が終わった時 → 何もしない

  バージイン（ユーザーが割り込んで喋った）

  AI「これからカメラで……」← 喋ってる最中
    ↓
  ユーザー「やめて」← マイクが拾う
    ↓
  1. 再生中の音声を捨てる (skip_pending)
  2. Azure に「いまの応答キャンセル」(response.cancel)
  3. 「次の応答待ち」で止まってた処理を解除 (event.set)

  3 の解除がないと、preface や tool_result を待ってた処理が永久に止まる。