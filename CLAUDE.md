# CLAUDE.md (fork-local instructions)

このファイルは fork（`Naotchi/dimos`）のチーム共有指示書。`AGENTS.md` は upstream 管理なので触らず、fork 固有の運用ルールはここに書いて commit する。

## Python / pytest の実行

- `.venv` は既に source 済み。`python` / `pytest` をそのまま使う。
- `python3` は **使わない**（システム Python を呼んでしまい依存が見えない）。
- `uv run python` も基本不要。

## Remote / ブランチ運用

- `origin` = `git@github.com:Naotchi/dimos.git` — 開発・push 先はここ。
- `upstream` = `https://github.com/dimensionalOS/dimos.git` — **fetch 専用、push / PR 禁止**（fork 内で完結）。
- `main` は **自分の統合ブランチ**（upstream の純粋ミラーではない）。「main を upstream のミラーにすべき」という提案はしない。
- feature ブランチは `main` から切る（`upstream/main` からではない）。
- `git push --force` 系は本人確認なしに実行しない。

## upstream との衝突を避ける編集方針

**fork の差分は「新規ファイル追加」を基本とし、upstream 由来ファイルの編集は最小限に抑える。** 目的は upstream/main を ff-only に近い形で取り込み続けられる状態を保つこと。

### 用語定義（重要）

「**upstream 由来ファイル**」= `upstream/main` に存在するファイル（= `dimensionalOS/dimos` 側で管理されているファイル）。これだけが編集を控える対象。

「**fork 固有ファイル**」= fork 側で新規追加し origin にのみ存在するファイル（`upstream/main` には存在しない）。**これらは自由に編集・リファクタしてよい**。既に origin にコミット済みの fork 開発物（`*_ja.py`、`scripts/bench_*.py`、`docs/superpowers/` 配下など）はすべてこちらに該当する。

あるファイルがどちらか判別したい時は `git cat-file -e upstream/main:<path>` などで確認する。

### 編集ルール

- fork 固有機能は **新規ファイルで追加**する（例: `*_ja.py`、`scripts/bench_*.py` のように別ファイル化）。
- **upstream 由来ファイル**への編集は、**新規ファイルを登録するための最小差分**（blueprint 登録など）に限る。ロジック改変や clean-up を upstream 由来ファイルに加えない。
- upstream 由来ファイルの既存挙動を変えたい場合も、まず「派生ファイルを新規追加 → 呼び出し側で切り替え」を検討する。
- **fork 固有ファイル**には上記制約は一切かからない。通常通り編集・リファクタ・削除して構わない。
- これにより upstream 追従は基本 `git merge upstream/main`（理想は ff-only / 衝突なし）で済む。diverge が避けられない場合のみ通常 merge / rebase。
