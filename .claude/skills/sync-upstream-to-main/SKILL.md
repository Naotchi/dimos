---
name: sync-upstream-to-main
description: Use when the user asks to pull in upstream changes, sync fork with upstream, or update main with dimensionalOS/dimos progress. Fetches upstream and merges upstream/main into the local main integration branch, with conflict handling and optional push to the Naotchi fork.
---

# Sync upstream into main

## Overview

`dimensionalOS/dimos` (upstream) の進捗を、自分の統合ブランチである `main` に取り込むワークフロー。
`main` は fork (`origin = Naotchi/dimos`) の統合先で、upstream には push しない。

## Remotes (前提)

| remote | URL | 用途 |
|--------|------|------|
| `origin` | `git@github.com:Naotchi/dimos.git` | fork。push 可 |
| `upstream` | `https://github.com/dimensionalOS/dimos.git` | fork 元。**push 禁止** |

異なる場合は `git remote -v` で確認し、必要なら remote を追加する。

## 手順

### 1. 作業ツリーをクリーンにする

```bash
git status
```

未コミットの変更があれば commit / stash しておく。汚れたまま merge しない。

### 2. upstream を fetch

```bash
git fetch upstream
```

### 3. main に切り替え、最新の origin/main に揃える

```bash
git checkout main
git pull --ff-only origin
```

`--ff-only` で失敗するなら main が分岐している。状況を確認してからユーザーに相談。

### 4. upstream/main を merge

```bash
git merge upstream/main
```

- ff できればそのまま進む
- merge commit が必要な場合はデフォルトメッセージで OK（特別な指示がなければ書き換えない）

### 5. コンフリクト発生時

**自動解決しない。** 必ずユーザーに状況を提示する:

```bash
git status   # コンフリクト中のファイル一覧
git diff --name-only --diff-filter=U
```

その上で以下を伝える:
- どのファイルが衝突したか
- `docs/survey/` 配下のファイルが含まれていれば、それは upstream には存在しない自前ファイルなので注意（基本は `--ours` で残す方針かどうかをユーザーに確認）
- 解決方針が決まるまで `git merge --abort` で戻せることも案内

ユーザー判断を仰いでから解決を進める。勝手に `--abort` も `-X theirs/ours` もしない。

### 6. push (origin への反映)

merge 成功後、ユーザーに確認してから:

```bash
git push origin main
```

**`git push upstream` は絶対にしない**（fork 元への push は禁止）。

## Quick reference

```bash
git status                          # 1. clean check
git fetch upstream                  # 2. fetch
git checkout main                   # 3. switch
git pull --ff-only origin           #    align with origin
git merge upstream/main             # 4. merge
# conflict -> ユーザーに相談
git push origin main                # 6. push to fork
```

## Red flags — STOP

- `git push upstream ...` を書こうとした → 禁止。`origin` のみ
- コンフリクトを自動で解決しようとしている → 止めてユーザーに提示
- 未コミットの変更があるまま merge しようとしている → stash か commit
- `main` 以外のブランチで merge しようとしている → main に切り替える
- `git pull --ff-only origin` が失敗 → main が分岐。原因を調べる前に進めない

## Notes

- `docs/survey/` は commit 対象外（[[project_dimos_remotes]] 参照）。merge 中に survey 配下が衝突した場合は特に慎重に確認する
- main は自分の統合ブランチ。upstream/main をミラーする運用ではなく **merge して取り込む** 方針
