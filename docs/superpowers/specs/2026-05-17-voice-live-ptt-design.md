# Voice Live Push-to-Talk (Spacebar) — Design

## 背景 / Why

現状 `unitree-go2-agentic-voice-live` ブループリントは Azure Voice Live のサーバ VAD に依存して連続入力を受け付けている。ローカル PC のスピーカー出力がマイクに回り込み、エージェント応答中の音声が次の「ユーザ発話」として誤検知され、応答が途中で打ち切られる現象が発生する。

WebUI のトークボタン的な明示的ゲートを、ブラウザを介さずローカルキーボード（スペースキー）で行いたい。既存の `keyboard_teleop` モジュールが pygame で別ウィンドウを開いて押下キーを取得しているのと同じ UX に揃える。

## 要件

- スペースキーを押している間だけマイク音声を Voice Live に送る
- キーを離したら送信停止 → サーバ VAD の `silence_duration_ms=500` が自然に commit
- 既存 voice_live ブループリントをそのまま PTT 化（別 blueprint を増やさない）
- rerun ウィンドウと並ぶ独立した小窓で PTT 状態を表示
- 当面テキスト入力は捨てる（WebUI 経由 `/human_input` は不要）

## 非要件 (YAGNI)

- `turn_detection=None` への切替（リリース即 commit）。500ms 末尾遅延が体感問題になったらフェーズ2。
- WebUI 側への spacebar バインド。ブラウザ非経由が要件。
- グローバルキーフック（他アプリ入力中の誤発火を避けるためウィンドウフォーカス前提）。

## 全体構成

```
PttKeyboard (新規)  ──mic_gate(bool)──▶  AzureVoiceLiveAgent (編集)
  pygame window                            既存の _mic_active を gate 入力で制御
  SPACE press  → True
  SPACE release → False
```

WebInputAudioOnly はブループリントから外す。AzureVoiceLiveAgent 内蔵の `SounddeviceAudioSource` をそのまま使用。

## コンポーネント詳細

### 1. `dimos/agents/realtime/azure_voice_live.py`（編集）

追加する port:

```python
mic_gate: In[bool]
```

既存挙動の変更:

- `start()` で `mic_gate.subscribe(self._on_mic_gate)` を登録
- `_on_mic_gate(active: bool)`:
  - `True` → `self._mic_active.set()`
  - `False` → `self._mic_active.clear()`
- `_handle_event` の `SESSION_UPDATED` 分岐で `self._mic_active.set()` していた箇所を **mic_gate が未接続のときだけ** 走らせる。接続済みなら gate 入力に従う。
  - 未接続判定: `In` port には接続数を確認できる API がある前提（無ければ `_mic_gate_connected` フラグを `start()` でチェック）。
  - 「接続済みか」を判定する API が無い場合は、`mic_gate` から初値 `False` を受け取った時点で「PTT モード」にスイッチする実装でも可（最初の publish が遅延する可能性に注意）。

`web_audio` は残す（barge-in テスト等で使う可能性）。デフォルトは未配線。

### 2. `dimos/agents/realtime/ptt_keyboard.py`（新規）

`keyboard_teleop.py` を参考にした pygame 小窓モジュール:

```python
class PttKeyboard(Module):
    mic_gate: Out[bool]

    # SDL ドライバを X11 固定（keyboard_teleop と同じ）
    # ウィンドウサイズ 400x150 程度
    # 表示: "Hold SPACE to talk"
    #   押下中: 赤丸 + "Recording..." 表示
    #   通常時: 灰丸 + "Idle"
```

実装ポイント:

- pygame の `KEYDOWN`/`KEYUP` で `K_SPACE` を捕捉
- キーリピートを無効化（`pygame.key.set_repeat(0)`）して長押し時に余計な publish が出ないようにする
- 状態変化時のみ `mic_gate.publish(...)` を呼ぶ（同じ値の連投を避ける）
- ウィンドウ閉じるイベントで `False` を最後に publish してから stop

### 3. `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`（編集）

- `WebInputAudioOnly` と `SpeakSkill` の関係はそのまま
- `WebInputAudioOnly` を削除し、`PttKeyboard.blueprint()` を追加
- autoconnect が PttKeyboard.mic_gate → AzureVoiceLiveAgent.mic_gate を自動で結ぶ

```python
unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    PttKeyboard.blueprint(),
    SpeakSkill.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)
```

### 4. `dimos/robot/all_blueprints.py`

変更不要（既存 voice_live エントリがそのまま新動作になる）。

## データフロー（押下→応答）

1. ユーザが pygame 窓をフォーカスし SPACE 押下
2. PttKeyboard が `mic_gate.publish(True)` → AzureVoiceLiveAgent が `_mic_active.set()`
3. 既存 `_on_mic_audio` がマイク PCM を `input_audio_buffer.append` で送信
4. ユーザが SPACE 離す → `mic_gate.publish(False)` → `_mic_active.clear()`
5. マイク送信停止 → Voice Live サーバ VAD が ~500ms 後 commit → response 開始
6. RESPONSE_AUDIO_DELTA → `_VoicePlayback` で再生

## エコー回避の根拠

- エージェント発話中はユーザが SPACE を押していない限りマイク音は送信されない
- スピーカー出力が `INPUT_AUDIO_BUFFER_SPEECH_STARTED` を誘発する経路自体が断たれる
- 意図的な barge-in は SPACE を押せば従来通り response.cancel が走る（`SPEECH_STARTED` 分岐は変更しない）

## テスト計画

- **ユニット**: `PttKeyboard` の状態遷移（press/release で True/False を一度ずつ publish）
- **統合**: AzureVoiceLiveAgent の `_on_mic_gate` で `_mic_active` が set/clear されること（pygame 不要のモック）
- **手動 E2E**: ブループリント起動 → 窓が出る → SPACE 押し下げで応答が返り、押していない時の発話で誤発火しないこと

## ロールバック / 互換性

- 既存 voice_live ブループリントの動作は「WebUI 経由 → PTT 経由」に置き換わる。WebUI 経路は別 blueprint で必要になったら復活できるよう、WebInputAudioOnly モジュールと port (`web_audio`, `human_input`) は削除しない。
- 将来 PTT 不要に戻したい場合は blueprint から `PttKeyboard.blueprint()` を外すだけで `_mic_active` が session-ready で自動 set される元の挙動に戻る。

## オープン事項

- `In` port が「接続済みかどうか」を runtime で判定できるか確認が必要。できなければ `_mic_active` のデフォルト初期値を False にして「PTT 接続前提」に切る方が単純（互換性は後者で犠牲）。実装フェーズで判断。
