# Azure Voice Live — ActiveResponse リファクタ設計

- **日付**: 2026-05-17
- **対象ファイル**: `dimos/agents/realtime/azure_voice_live.py`
- **テスト**: `tests/agents/realtime/test_azure_voice_live_forced_speech.py`
- **方針**: 振る舞い等価のリファクタ。forced-speech テスト 6 ケース全緑を維持。

## 動機

`AzureVoiceLiveAgent` の response 周りの状態が 6 変数に散らばっており、以下の構造的バグリスクがある:

1. `_next_trigger` を `response.create` の前に仕込む暗黙プロトコル — 忘れると `user` 扱いで `_on_response_done` が再起動し、二段目応答が暴発する
2. `_resp_done_event` を `_force_preface` / `_dispatch_and_wait` / `SPEECH_STARTED` で使い回す nullable 単一 Event — barge-in 解除漏れの温床
3. 累積状態（`had_audio` / `pending_calls` / `text_buf` / `trigger`）4 変数の reset 漏れリスク
4. `SPEECH_STARTED` ハンドラが 4 責務（再生破棄 / response.cancel / event.set / pending クリア）を持ち、barge-in 解除の意図が読み取りにくい

## 目的

- **拡張性**: 新しい trigger 種別（例: system_notice）や response パラメータを追加するときの差分を最小化する
- **暗黙プロトコルの排除**: 「create の前に trigger を仕込む」「単一 Event を使い回す」を構造で禁止する
- **挙動不変**: forced-speech テストは内部変数ではなく観測可能な振る舞いを見ており、全 6 ケース緑のまま通すことを契約とする

## 採用しなかった案

- **A: 発火だけ関数化**（`_create_preface_response()` などに圧縮） — Event 使い回しと累積変数 reset 漏れが残るため不採用
- **C: trigger ごとのクラス階層**（`PrefaceTrigger` / `ToolResultTrigger`） — 現状の trigger は 3 種に収束しており過剰設計。`SPEECH_STARTED` を active 内に侵食させると idle 中のキャンセル処理がかえって複雑化する

## 設計

### 新規型

```python
@dataclass(frozen=True)
class ResponseRequest:
    """エージェント側から response.create を出すときのパラメータ束。"""
    trigger: str                       # "preface_forced" | "tool_result" | ...
    instructions: str
    modalities: tuple[str, ...] = ("audio", "text")


@dataclass
class _ActiveResponse:
    """1 つの response の生存期間（CREATED〜DONE/cancel）に紐づく累積状態。"""
    trigger: str                       # "user" | "preface_forced" | "tool_result"
    had_audio: bool = False
    pending_calls: list[tuple[str, str, str]] = field(default_factory=list)
    text_buf: list[str] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self) -> _ResponseSnapshot:
        return _ResponseSnapshot(
            trigger=self.trigger,
            had_audio=self.had_audio,
            pending_calls=list(self.pending_calls),
            text="".join(self.text_buf).strip(),
        )
```

`_ResponseSnapshot` は既存のものを再利用する。

### インスタンス状態の集約

`__init__` から以下を削除:

- `_resp_had_audio`
- `_resp_pending_calls`
- `_resp_text_buf`
- `_resp_trigger`
- `_next_trigger`
- `_resp_done_event`

代わりに以下を追加:

```python
self._active: _ActiveResponse | None = None         # CREATED で昇格、DONE / cancel で None
self._pending_active: _ActiveResponse | None = None # _issue_response が事前構築、CREATED で消費
```

**race 対策の要点**: `_issue_response` 側で `_ActiveResponse` を事前に構築して `_pending_active` slot に置く。`response.create` の RPC 完了から `RESPONSE_CREATED` 受信までの間に `_active` を参照しても破綻しないように、`done` event は `_ActiveResponse` インスタンスが所有し、呼び出し側はそのインスタンスへの参照を `_issue_response` 内で保持する。

`_snapshot_response_state()` 関数は削除（`_active.snapshot()` + `_active = None` で代替）。

### 発火 helper

```python
async def _issue_response(self, req: ResponseRequest) -> _ResponseSnapshot:
    """エージェント側から発火する response を直列実行する。
    _ActiveResponse を事前構築して _pending_active に置き、
    RESPONSE_CREATED がそれを self._active に昇格させる。
    RESPONSE_DONE または barge-in で done が立つまで待つ。"""
    active = _ActiveResponse(trigger=req.trigger)
    self._pending_active = active
    await self._conn.response.create(response={
        "modalities": list(req.modalities),
        "instructions": req.instructions,
    })
    await active.done.wait()       # インスタンス参照を直接保持しているので race なし
    return active.snapshot()
```

`_force_preface` / `_dispatch_and_wait` の `_next_trigger = ...` + `Event()` + `create()` + `wait()` の 4 行セットがこの呼び出し 1 回に圧縮される。

### event handler の変更

| event | 旧 | 新 |
|---|---|---|
| `RESPONSE_CREATED` | `_resp_*` 4 変数を reset、`_resp_trigger = _next_trigger or "user"` | `if self._pending_active is not None: self._active = self._pending_active; self._pending_active = None` else `self._active = _ActiveResponse(trigger="user")` |
| `RESPONSE_AUDIO_DELTA` | `self._resp_had_audio = True` | `self._active.had_audio = True`（`_active` が None なら何もしない） |
| `RESPONSE_AUDIO_TRANSCRIPT_DELTA` / `RESPONSE_TEXT_DELTA` | `self._resp_text_buf.append(...)` | `self._active.text_buf.append(...)` |
| `RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE` | `self._resp_pending_calls.append(...)` | `self._active.pending_calls.append(...)` |
| `RESPONSE_DONE` | `snap = self._snapshot_response_state()`、`_resp_done_event.set()` | `snap = self._active.snapshot()`、`self._active.done.set()`、`self._active = None` |
| `SPEECH_STARTED` | `_resp_done_event.set()`、`_resp_pending_calls = []` | `self._active.pending_calls = []`、`self._active.done.set()`、`self._active = None` |

`RESPONSE_DONE` で `snap.trigger == "user"` のときだけ `_on_response_done(snap)` を起動する分岐は不変。`SPEECH_STARTED` の playback skip / response.cancel / `agent_idle.publish(False)` も不変。

## テスト互換性

forced-speech テスト 6 ケースは内部変数名ではなく、以下を観測している:

- `conn.response.create` の呼ばれた回数と引数（instructions）
- `RESPONSE_CREATED` / `RESPONSE_DONE` / `SPEECH_STARTED` を流し込んだあとのエージェントの応答
- `agent_idle` の publish

これらは設計上すべて等価に保たれる。テストコードは一切変更しない。

## 移行手順

1 PR 内で連続コミット:

1. `ResponseRequest` / `_ActiveResponse` を新規追加（既存コードは触らない）
2. `_issue_response` を実装、`_force_preface` / `_dispatch_and_wait` を切り替え
3. event handler 内の `_resp_*` 参照を `self._active.*` に置換、`__init__` の旧変数と `_snapshot_response_state` を削除
4. `pytest tests/agents/realtime/test_azure_voice_live_forced_speech.py` 緑確認
5. `unitree_go2_agentic_voice_live` ブループリントを手動セッションで動作確認（barge-in / tool call / preface のうち最低 1 回ずつ）

## CLAUDE.md ルールとの整合

本ファイル `dimos/agents/realtime/azure_voice_live.py` は upstream にも存在する。今回の変更は「新規ファイル追加」ではなく既存ファイル内の構造変更だが、

- ロジック挙動は不変（forced-speech テストが契約として固定）
- 追加する `ResponseRequest` / `_ActiveResponse` は fork 固有機能ではなく一般的な可読性改善

であるため、upstream に PR で還元する選択肢を残しつつ、まずは fork 内で完結させる。upstream 追従コストは現状と同等以下になる想定（state 散乱が解消されて衝突点が減るため）。

## リスク

- **中**: event handler 内の参照書き換え範囲がそこそこ広い。テスト緑を逐次確認しながら進める。
- **低**: 解消済み（事前構築 `_pending_active` パターンで race を回避）。
