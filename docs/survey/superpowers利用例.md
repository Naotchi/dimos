# superpowers 利用例: unitree-go2-agentic を env で LLM 切替 + ローカル TTS 化

Claude Code v2.1.140 / Opus 4.7 medium effort で superpowers 系 skill を一通り通した実セッションのログ。
`brainstorming` → `writing-plans` → `subagent-driven-development` → `finishing-a-development-branch` の流れ。

---

## 1. brainstorming — 要件と設計の擦り合わせ

> `/superpowers:brainstorming`
>
> **User:** blueprint `unitree-go2-agentic` を実行したい。LLM は env で指定するつもり。TTS をローカルに変更したい。

### コンテキスト確認（Claude）

- Blueprint は `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py`
- LLM は `McpClient.blueprint(model=...)` で渡せる（現在は `gpt-4o` ハードコード、ollama 版は `"ollama:qwen3:8b"`）
- TTS は `SpeakSkill` 内で `OpenAITTSNode` 直書き。ローカル候補は既存の `node_pytts.py`（pyttsx3）あり。ただし出力 PCM を emit せず内部で再生する形なので差分あり

### AskUserQuestion で要点確認

| 質問 | 回答 |
| --- | --- |
| ローカル TTS のエンジンは？ | pyttsx3（既存 `node_pytts.py`） |
| LLM を env で指定とは？ | まず現状を教えて |
| env 形式とデフォルトは？ | `DIMOS_LLM_MODEL` 1 本、デフォルト `gpt-4o` |
| TTS の切替方式は？ | env (`DIMOS_TTS=openai|pyttsx3`, デフォルト `pyttsx3`) |

### 現状の LLM 受け渡し（調査結果）

`McpClient` の `model` 引数 (`mcp_client.py:46, 215, 222`):

- `McpClientConfig.model: str = "gpt-4o"` がデフォルト
- 値はそのまま LangChain `create_agent(model=...)` に渡される
- LangChain 側が prefix で provider 判定 — `gpt-4o`（openai） / `ollama:qwen3:8b` / `anthropic:claude-...` など 1 つの文字列で表現可能
- → env 1 本で素直にハマる構造

### 設計案

**目的:** `unitree-go2-agentic` を、LLM を env で、TTS をローカル（pyttsx3）に切替可能にして起動できるようにする。

1. **LLM env 化** — `unitree_go2_agentic.py`

   ```python
   import os
   model = os.environ.get("DIMOS_LLM_MODEL", "gpt-4o")
   unitree_go2_agentic = autoconnect(
       unitree_go2_spatial,
       McpServer.blueprint(),
       McpClient.blueprint(model=model),
       _common_agentic,
   )
   ```

2. **TTS env 切替** — `dimos/agents/skills/speak_skill.py`
   - `SpeakSkill.start()` で `os.environ.get("DIMOS_TTS", "pyttsx3")` を読む
   - `"openai"` → 現状どおり `OpenAITTSNode + SounddeviceAudioOutput`
   - `"pyttsx3"` → `PyTTSNode` を `consume_text` させるだけ（内部再生、完了で `emit_text` 発火）
   - `_speak_blocking` の完了待ちは両ノード共通で `emit_text()` を subscribe する形に統一
3. **依存追加** — `pyproject.toml` に `pyttsx3`
4. **起動例**

   ```bash
   # ローカル全部
   DIMOS_LLM_MODEL=ollama:qwen3:8b DIMOS_TTS=pyttsx3 dimos run unitree-go2-agentic
   # デフォルト
   OPENAI_API_KEY=... dimos run unitree-go2-agentic
   # 全部 OpenAI（従来動作）
   DIMOS_TTS=openai OPENAI_API_KEY=... dimos run unitree-go2-agentic
   ```

**影響範囲外:** STT（既に WhisperNode でローカル）、`_ollama`/`_huggingface` 派生 blueprint、`_common_agentic`。

**検証ポイント:** pyttsx3 の OS 依存（Linux なら espeak/libespeak1）。

> **User:** OK、依存は `pyproject.toml` に追加しよう。

→ Spec を `docs/superpowers/specs/2026-05-13-unitree-go2-agentic-local-tts-llm-env-design.md` に書いて commit。

---

## 2. writing-plans — 実装プラン作成

`/superpowers:writing-plans` を起動。

実装に入る前の所見:

> `node_pytts.py:19` が存在しない `dimos.stream.audio.text.abstract` から import しようとしている（正しくは `.base`）。
> このまま import すると `ImportError`。**Task 1 でこれを修正してから本題に入る**構成にする。

Plan を `docs/superpowers/plans/2026-05-13-unitree-go2-agentic-local-tts-llm-env.md` に出力（473 行）して commit。

実行方式の選択:

1. **Subagent-Driven**（推奨）— タスクごとに subagent、間でレビュー
2. **Inline Execution** — このセッションで `executing-plans`

> **User:** 1

---

## 3. subagent-driven-development — タスク単位で subagent を回す

`/superpowers:subagent-driven-development` を起動。

main 上での実装は明示同意が必要 → `AskUserQuestion` で:

> どのブランチワークスペースで作業しますか？ → **feature ブランチを切る**

```bash
git checkout -b feat/go2-agentic-local-tts-llm-env
```

### タスクごとの subagent dispatch

各タスクは「実装 → spec review → code quality review」の 3 段で別 subagent を投入。

| # | タスク | 実装モデル | レビュー |
| --- | --- | --- | --- |
| 1 | `PyTTSNode` import パス修正 (`.abstract` → `.base`) | Haiku 4.5 | spec + quality |
| 2 | `pyttsx3` を agents extras に追加 | Haiku 4.5 | spec + quality |
| 3 | `SpeakSkill` の env 切替 | **Sonnet 4.6** | spec + quality |
| 4 | LLM env 読み込み | Haiku 4.5 | spec + quality |
| 5 | README 更新 | Haiku 4.5 | spec + quality |

### スモーク検証

```bash
$ uv run dimos list | grep go2-agentic
unitree-go2-agentic
unitree-go2-agentic-huggingface
unitree-go2-agentic-ollama

# OK: ollama + pyttsx3
DIMOS_LLM_MODEL=ollama:qwen3:8b DIMOS_TTS=pyttsx3 uv run python -c "from ... import unitree_go2_agentic"

# OK: 不正値は ValueError
DIMOS_TTS=bogus uv run python -c "..."
# -> DIMOS_TTS must be 'openai' or 'pyttsx3', got: 'bogus'
```

### Final review (Sonnet 4.6) の指摘

> 「pyttsx3 を agents extras ではなく main deps に」

→ **却下**。理由:

- agents extras には既に `openai` / `sounddevice` / `faster-whisper` が居る
- `speak_skill.py` は `OpenAITTSNode` / `SounddeviceAudioOutput` を unconditional に import
- つまり `SpeakSkill` は agents extras 無しではそもそも import できない
- pyttsx3 を agents extras に置く方が周囲と整合する

他の指摘（LLM env の import 時評価、テストの型注釈、README 末尾の空行）はすべて Minor。Critical issue なし。

### 完了したコミット

```
9af0f1a8a docs(readme): document DIMOS_LLM_MODEL / DIMOS_TTS env for go2-agentic
0f87f685a feat(go2-agentic): read LLM model from DIMOS_LLM_MODEL env
f9e75cd75 feat(speak): switch TTS backend via DIMOS_TTS env (default pyttsx3)
a84c18a27 deps(agents): add pyttsx3 for local TTS
86aa9829d fix(tts): correct PyTTSNode import path (.abstract -> .base)
```

---

## 4. finishing-a-development-branch — ブランチの締め

> **User:** `finishing-a-development-branch` とはなに？

実装が一段落した後の「このブランチをどう着地させるか」を構造化して提示する skill。状態確認 → 着地オプション提示 → 実行、の流れ。

このリポジトリでは memory に「origin = `Naotchi/dimos` fork、upstream = `dimensionalOS/dimos` には push 禁止」とあるので、選択肢は:

1. origin に push して fork 上で PR を作る
2. ローカルで main に fast-forward merge
3. もう少し手を入れる
4. push せずブランチを残して終わる

> **User:** skill を起動して任せる

`/superpowers:finishing-a-development-branch` 起動。状態確認:

- テスト 4/4 PASS
- normal repo（worktree なし）、base = main
- origin = Naotchi/dimos fork（push 可）
- upstream = dimensionalOS/dimos（memory により push 禁止）

提示された選択肢:

1. Merge back to main locally
2. Push and create a Pull Request
3. Keep the branch as-is
4. Discard this work

> **User:** 3

→ ブランチ `feat/go2-agentic-local-tts-llm-env`（5 commits ahead of main）を保持して終了。

---

## まとめ: skill の使われ方

```
brainstorming
  ├─ AskUserQuestion で要件を 4 問で確定
  ├─ 現状コードを実調査して設計案を提示
  └─ spec.md として commit

writing-plans
  ├─ 実装前の所見（既存バグ発見）
  └─ plan.md (473 行) を commit

subagent-driven-development
  ├─ feature ブランチを切る（main 直接禁止）
  ├─ タスクごとに「実装 / spec review / quality review」を別 subagent
  │   (Haiku 4.5 中心、複雑なタスクのみ Sonnet 4.6)
  ├─ 全体 Final review (Sonnet 4.6)
  └─ レビュー指摘は鵜呑みにせず根拠を見て採否判断

finishing-a-development-branch
  ├─ ブランチ状態を確認
  └─ merge / PR / 保持 / 破棄 の 4 択を提示
```

各 skill が境界を明確に区切ってくれるので、「要件確認のまま実装に滑り込む」「レビュー無しで commit」「ブランチを放置」といったよくある事故を構造的に防げる。
