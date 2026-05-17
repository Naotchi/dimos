# PTT を pygame window から global hotkey へ置換

## 背景 / 課題

- `unitree-go2-agentic-voice-live` blueprint では `PttKeyboard` (`dimos/agents/realtime/ptt_keyboard.py`) が pygame ベースの独立ウィンドウを開き、SPACE 押下中だけ `mic_gate=True` を publish している。
- このウィンドウは OS レベルで **focus を持っていないとキー入力を拾えない**。会話中に他ウィンドウ (rerun viewer, terminal, editor) を触ったあと話そうとすると、PTT が反応せず無音区間ができる UX 不良が出ている。
- 本来のゴールは「rerun viewer (dimos-viewer) 内蔵 floating panel として統合」だが、それは dimos-viewer (別 repo、Rust) 側の作業が必要で優先度が低い。
- 本 spec の範囲は **「focus 不要で PTT キーが効く」だけを最小工数で満たす**。viewer 内蔵化は別 task。

## 採用方針

`pynput.keyboard.Listener` による **global hotkey** に置き換える。pygame ウィンドウは廃止。

- デフォルトキー: `F9` hold
  - SPACE はタイピング全般と衝突するため不可
  - F9 は通常のタイピング/teleop と衝突しない
- env `DIMOS_PTT_KEY` で上書き可能 (例: `DIMOS_PTT_KEY=f8`, `DIMOS_PTT_KEY=ctrl_r`)
- suppress なし (キー入力は他アプリにもそのまま伝播)
- 動作前提は X11 (既存コードも `SDL_VIDEODRIVER=x11` 前提)

UI は無し。状態は既存の `logger.info("PTT mic_gate=%s", value)` のみで確認する。

## 変更スコープ

### 1. `dimos/agents/realtime/ptt_keyboard.py` (fork-local、in-place 書き換え)

- pygame / pygame loop / render / window / threading.Event の待機ループを全削除
- 残すもの:
  - `class PttKeyboard(Module)`
  - `mic_gate: Out[bool]` ポート
  - `@rpc start` / `@rpc stop` lifecycle
  - 純粋関数 `process_ptt_event(state, kind, key, emit)` と `PttState` dataclass (テスト流用のため)
- 追加:
  - `__init__(trigger_key: str | None = None, **kwargs)` — `None` のとき `os.environ.get("DIMOS_PTT_KEY", "f9")` を読む。明示指定 (kwarg) が env より優先
  - キー名 → pynput Key 変換ヘルパー `_parse_key(name: str) -> Key | KeyCode`:
    - `f1`〜`f24` → `pynput.keyboard.Key.fN`
    - `space` / `tab` / `esc` / `ctrl_l` / `ctrl_r` / `shift_l` / `shift_r` / `alt_l` / `alt_r` 等 → `getattr(Key, name)` で解決
    - 1 文字 (`a`, `` ` ``) → `KeyCode.from_char(c)`
    - 解決失敗時は `ValueError` で fail fast (start 時ではなく `__init__` 時に判定)
    - 比較は case-insensitive (`F9` / `f9` 同等)
  - `pynput.keyboard.Listener(on_press=..., on_release=...)` を daemon thread として `start()` 内で起動、`stop()` で `Listener.stop()` + `join(DEFAULT_THREAD_JOIN_TIMEOUT)`
  - on_press/on_release では押されたキーが trigger_key と一致するときだけ `process_ptt_event` を呼ぶ
  - stop 時に `_state.active` なら `mic_gate.publish(False)` する保険は維持 (既存の挙動)
- 削除:
  - pygame import / `_WINDOW_*` 定数 / `_FONT_SIZE` / `_BG_COLOR` / `_render` メソッド / `os.environ.setdefault("SDL_VIDEODRIVER", ...)`
  - 内部の `_screen` / `_font` / `_clock` 属性

### 2. `pyproject.toml`

- `dependencies` に `"pynput>=1.7,<2"` を追加 (fork-local 1 行差分)

### 3. `tests/agents/realtime/test_ptt_keyboard.py`

- `process_ptt_event` の純粋関数テストは流用。`key="space"` を default trigger に合わせて `key="f9"` に置換
- `test_non_space_key_ignored` を `test_non_trigger_key_ignored` にリネームし、trigger key (`f9`) 以外を投げて無視されることを検証
- 新規テスト:
  - `_parse_key("f9")` が `Key.f9` を返す
  - `_parse_key("F9")` (大文字) も `Key.f9`
  - `_parse_key("space")` が `Key.space`
  - `_parse_key("a")` が `KeyCode.from_char("a")`
  - `_parse_key("not_a_key")` で `ValueError`
- env 上書きテスト:
  - `DIMOS_PTT_KEY=f8` 環境下で `PttKeyboard()` の `trigger_key` 解決が `Key.f8` になる
  - 明示 kwarg (`PttKeyboard(trigger_key="space")`) が env より優先される
- 注意: pynput Listener 自体は CI 環境 (X11 なし) では起動しないので、`start()` / `stop()` の統合テストは追加しない (既存も pygame 統合テストは無い)

## env 仕様

| Env | 既定 | 説明 |
|---|---|---|
| `DIMOS_PTT_KEY` | `f9` | PTT trigger key 名。pynput key 名 (`f1`〜`f24` / `space` / `tab` / `ctrl_l` 等) または 1 文字 (`a`, `` ` ``)。case-insensitive。解決失敗時はモジュール初期化時に `ValueError` |

## 影響範囲

- `unitree_go2_agentic_voice_live` blueprint (`dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`) は `PttKeyboard.blueprint()` の呼び出しのみで、API 変更なしのため **触らない**
- 他に `PttKeyboard` を使う blueprint は無い (`grep` で確認済み)
- fork CLAUDE.md の「upstream 既存ファイル編集は最小限」ポリシー的に、`ptt_keyboard.py` も `pyproject.toml` も fork-local 確認済み (`ptt_keyboard.py` は `feat(voice-live): add PttKeyboard` で fork のみ、`pyproject.toml` の `dimos-viewer`/`azure-ai-voicelive` も fork 追加分)

## Out of scope

- dimos-viewer (Rust) 側への floating PTT panel 追加 — 別 task
- 状態表示 UI (rerun TextLog entity への mic_gate 状態 publish 等)
- Wayland 対応 (現状 `SDL_VIDEODRIVER=x11` 前提)
- toggle モード ("1 回押下で ON、もう 1 回で OFF") — hold で十分
- 修飾キー組み合わせ (`ctrl+space` 等) — pynput では HotKey API があるが、hold 用途には素直な単キーで十分

## Risk と対策

- **Wayland session で動かない**: pynput X11 backend は Wayland で機能しない。`os.environ.get("XDG_SESSION_TYPE")` が `wayland` のとき `logger.warning` を出す (起動失敗にはしない。一部 Wayland compositor で XWayland 経由で動くケースもあるため)
- **F9 が他アプリの debugger ホットキー等と衝突**: suppress しないので他アプリにも届く。voice-live 起動中だけ余計な PTT 発火が起きるが、副作用は agent 側で mic を一瞬開くだけで害は軽微。気になる人は `DIMOS_PTT_KEY` で逃がす
- **pynput が壊れる/別 backend 不一致**: 起動時に Listener を thread start するだけで例外を握りつぶさない (例外発生時は通常通り spawn 失敗ログ)

## 完了条件

- [ ] `ptt_keyboard.py` が pygame 非依存になっている (`import pygame` が消える)
- [ ] `DIMOS_PTT_KEY=f8 python -c "from dimos.agents.realtime.ptt_keyboard import PttKeyboard; print(PttKeyboard()._trigger_key)"` で `Key.f8` が表示される
- [ ] 既存テスト `tests/agents/realtime/test_ptt_keyboard.py` が更新後グリーン
- [ ] `python -m pytest tests/agents/realtime/test_ptt_keyboard.py` でグリーン
- [ ] `unitree-go2-agentic-voice-live` 起動時に PTT pygame window が開かず、ターミナル focus 中でも F9 hold で `PTT mic_gate=True` がログされる (手動確認)
