# go2-agentic 日本語化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitree_go2_agentic` ブループリント経由の Go2 エージェントを、入力(STT)・LLM 応答・出力(TTS) すべて日本語で動作させる。

**Architecture:** ライブラリ層 (`WhisperNode`, `PyTTSNode`, `WebInput`, `SpeakSkill`) は英語デフォルトのまま、optional な language/voice 設定を `ModuleConfig` 経由で受け取れるよう拡張する。`unitree_go2_agentic` / `_common_agentic` で日本語パラメータと日本語 system prompt を注入する。

**Tech Stack:** Python 3.12, `pydantic` (ModuleConfig), `pyttsx3`, `openai-whisper` / `faster-whisper`, `pytest`.

設計書: `docs/superpowers/specs/2026-05-14-go2-agentic-japanese-design.md`

---

## File Structure

**新規:**
- `dimos/agents/system_prompt_ja.py` — 日本語版 system prompt
- `dimos/stream/audio/tts/tests/test_node_pytts_voice_lang.py` — `voice_lang` 単体テスト

**変更:**
- `dimos/stream/audio/tts/node_pytts.py` — `voice_lang` パラメータ追加
- `dimos/agents/web_human_input.py` — `WebInputConfig` を新設、`whisper_language` を保持
- `dimos/agents/skills/speak_skill.py` — `SpeakSkillConfig` を新設、`voice_lang` を保持し `PyTTSNode` に伝搬
- `dimos/agents/skills/tests/test_speak_skill_env.py` — 既存テストの破壊回避＋`voice_lang` ケース追加
- `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py` — `system_prompt=SYSTEM_PROMPT_JA` を渡す
- `dimos/robot/unitree/go2/blueprints/agentic/_common_agentic.py` — `WebInput.blueprint(whisper_language="ja")`, `SpeakSkill.blueprint(voice_lang="ja")`
- `README.md` — 日本語ボイス導入の注意書きを追記

---

## Task 1: PyTTSNode に voice_lang パラメータを追加

**Files:**
- Modify: `dimos/stream/audio/tts/node_pytts.py`
- Create: `dimos/stream/audio/tts/tests/__init__.py` (存在しない場合のみ)
- Create: `dimos/stream/audio/tts/tests/test_node_pytts_voice_lang.py`

- [ ] **Step 1: テストディレクトリの確認**

```bash
ls dimos/stream/audio/tts/tests/ 2>/dev/null || echo "missing"
```

無い場合は次のステップで `__init__.py` を作成。あればスキップ。

- [ ] **Step 2: __init__.py を用意 (存在しなければ)**

```bash
[ -d dimos/stream/audio/tts/tests ] || mkdir -p dimos/stream/audio/tts/tests
[ -f dimos/stream/audio/tts/tests/__init__.py ] || touch dimos/stream/audio/tts/tests/__init__.py
```

- [ ] **Step 3: 失敗テストを書く**

`dimos/stream/audio/tts/tests/test_node_pytts_voice_lang.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
"""PyTTSNode voice_lang selection tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock


def _voice(id_: str, name: str, languages: list[bytes]) -> SimpleNamespace:
    return SimpleNamespace(id=id_, name=name, languages=languages)


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_selects_matching_voice_by_languages(init_mock: mock.MagicMock) -> None:
    engine = mock.MagicMock()
    engine.getProperty.return_value = [
        _voice("en-id", "english", [b"en-US"]),
        _voice("ja-id", "japanese", [b"ja-JP"]),
    ]
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode(voice_lang="ja")

    set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert set_calls, "voice should be set when a matching voice is found"
    assert set_calls[-1].args[1] == "ja-id"


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_falls_back_when_no_match(init_mock: mock.MagicMock) -> None:
    engine = mock.MagicMock()
    engine.getProperty.return_value = [
        _voice("en-id", "english", [b"en-US"]),
    ]
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode(voice_lang="ja")

    voice_set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert voice_set_calls == [], "voice should not be set when no matching voice exists"


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_none_does_not_touch_voice(init_mock: mock.MagicMock) -> None:
    engine = mock.MagicMock()
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode()

    voice_set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert voice_set_calls == [], "voice should not be set when voice_lang is None"


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_matches_via_id_or_name(init_mock: mock.MagicMock) -> None:
    """languages 属性が空でも id/name の言語コードでマッチさせる (mac/win 等で languages 空のケース)."""
    engine = mock.MagicMock()
    engine.getProperty.return_value = [
        _voice("com.apple.voice.compact.en-US.Samantha", "Samantha", []),
        _voice("com.apple.voice.compact.ja-JP.Kyoko", "Kyoko", []),
    ]
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode(voice_lang="ja")

    set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert set_calls, "voice should be set via id match"
    assert "ja" in set_calls[-1].args[1]
```

- [ ] **Step 4: テスト実行 (失敗を確認)**

```bash
cd /home/naoki/dimos && uv run pytest dimos/stream/audio/tts/tests/test_node_pytts_voice_lang.py -v
```

期待: `TypeError: PyTTSNode.__init__() got an unexpected keyword argument 'voice_lang'` で全件 FAIL

- [ ] **Step 5: PyTTSNode に voice_lang を実装**

`dimos/stream/audio/tts/node_pytts.py:33-43` を以下に置換:

```python
    def __init__(
        self,
        rate: int = 200,
        volume: float = 1.0,
        voice_lang: str | None = None,
    ) -> None:
        """
        Initialize PyTTSNode.

        Args:
            rate: Speech rate (words per minute)
            volume: Volume level (0.0 to 1.0)
            voice_lang: Optional language code (e.g. "ja"). When set, scans
                available voices and selects the first one whose languages
                metadata, id, or name contains the code. If no match is found,
                logs a warning and keeps the default voice.
        """
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", rate)
        self.engine.setProperty("volume", volume)

        if voice_lang is not None:
            self._apply_voice_lang(voice_lang)

        self.text_subject = Subject()  # type: ignore[var-annotated]
        self.subscription = None

    def _apply_voice_lang(self, lang: str) -> None:
        """Select a voice matching the given language code, or warn if none."""
        code = lang.lower()
        voices = self.engine.getProperty("voices") or []
        for voice in voices:
            languages = getattr(voice, "languages", None) or []
            for raw in languages:
                tag = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
                if tag.lower().startswith(code):
                    self.engine.setProperty("voice", voice.id)
                    return
            haystack = f"{getattr(voice, 'id', '')} {getattr(voice, 'name', '')}".lower()
            if code in haystack:
                self.engine.setProperty("voice", voice.id)
                return
        logger.warning(
            "PyTTSNode: no voice matching language %r found; using default voice", lang
        )
```

- [ ] **Step 6: テスト実行 (PASS を確認)**

```bash
cd /home/naoki/dimos && uv run pytest dimos/stream/audio/tts/tests/test_node_pytts_voice_lang.py -v
```

期待: 4 件全て PASS

- [ ] **Step 7: コミット**

```bash
git add dimos/stream/audio/tts/node_pytts.py dimos/stream/audio/tts/tests/
git commit -m "$(cat <<'EOF'
feat(tts): add voice_lang param to PyTTSNode for non-English voices

PyTTSNode now optionally selects a voice whose languages/id/name matches
the provided code (e.g. "ja"). Falls back to the default voice with a
warning when no match exists.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: SpeakSkill に voice_lang を伝搬

**Files:**
- Modify: `dimos/agents/skills/speak_skill.py`
- Modify: `dimos/agents/skills/tests/test_speak_skill_env.py`

- [ ] **Step 1: 既存テストが現状 PASS することを確認 (回帰防止のベースライン)**

```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/skills/tests/test_speak_skill_env.py -v
```

期待: 既存 4 件 PASS

- [ ] **Step 2: voice_lang ケースの失敗テストを追加**

`dimos/agents/skills/tests/test_speak_skill_env.py` の末尾に追記:

```python
@mock.patch.dict(os.environ, {"DIMOS_TTS": "pyttsx3"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_pyttsx3_forwards_voice_lang(pytts_cls: mock.MagicMock) -> None:
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    skill = SpeakSkill(voice_lang="ja")
    try:
        skill.start()
        pytts_cls.assert_called_once_with(voice_lang="ja")
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "pyttsx3"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_pyttsx3_default_voice_lang_is_none(pytts_cls: mock.MagicMock) -> None:
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    skill = SpeakSkill()
    try:
        skill.start()
        pytts_cls.assert_called_once_with(voice_lang=None)
    finally:
        skill.stop()
```

- [ ] **Step 3: テスト実行 (失敗を確認)**

```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/skills/tests/test_speak_skill_env.py -v
```

期待: 新規 2 件が `TypeError` または `assert_called_once_with` ミスマッチで FAIL。既存 4 件は依然 PASS。

- [ ] **Step 4: SpeakSkill に Config を導入**

`dimos/agents/skills/speak_skill.py` のインポートに以下を追加:

```python
from dimos.core.module import ModuleConfig
```

`class SpeakSkill(Module):` の直前に config クラスを追加:

```python
class SpeakSkillConfig(ModuleConfig):
    voice_lang: str | None = None
```

`class SpeakSkill(Module):` のクラス本体冒頭 (`_tts_node: ...` の前) に追加:

```python
    config: SpeakSkillConfig
```

`start()` 内の `pyttsx3` ブランチを置換:

```python
        elif backend == "pyttsx3":
            self._tts_node = PyTTSNode(voice_lang=self.config.voice_lang)
            self._audio_output = None
```

- [ ] **Step 5: テスト実行 (PASS を確認)**

```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/skills/tests/test_speak_skill_env.py -v
```

期待: 6 件全て PASS

- [ ] **Step 6: コミット**

```bash
git add dimos/agents/skills/speak_skill.py dimos/agents/skills/tests/test_speak_skill_env.py
git commit -m "$(cat <<'EOF'
feat(speak): forward voice_lang config to PyTTSNode

SpeakSkill now accepts an optional voice_lang via SpeakSkillConfig and
passes it to PyTTSNode. OpenAI TTS backend is unchanged (OpenAI handles
language detection automatically).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: WebInput に whisper_language を追加

**Files:**
- Modify: `dimos/agents/web_human_input.py`
- Create: `dimos/agents/tests/__init__.py` (存在しなければ)
- Create: `dimos/agents/tests/test_web_human_input_config.py`

- [ ] **Step 1: tests dir 確認**

```bash
ls dimos/agents/tests/ 2>/dev/null || echo "missing"
```

- [ ] **Step 2: 必要なら __init__.py を作成**

```bash
[ -d dimos/agents/tests ] || mkdir -p dimos/agents/tests
[ -f dimos/agents/tests/__init__.py ] || touch dimos/agents/tests/__init__.py
```

- [ ] **Step 3: 失敗テストを書く**

`dimos/agents/tests/test_web_human_input_config.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
"""WebInput config tests (whisper language)."""

from __future__ import annotations


def test_default_whisper_language_is_english() -> None:
    from dimos.agents.web_human_input import WebInput

    wi = WebInput()
    assert wi.config.whisper_language == "en"


def test_whisper_language_can_be_overridden() -> None:
    from dimos.agents.web_human_input import WebInput

    wi = WebInput(whisper_language="ja")
    assert wi.config.whisper_language == "ja"
```

- [ ] **Step 4: テスト実行 (失敗を確認)**

```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/tests/test_web_human_input_config.py -v
```

期待: `AttributeError` または validation error で FAIL

- [ ] **Step 5: WebInput に Config を導入**

`dimos/agents/web_human_input.py` のインポート部に追加:

```python
from dimos.core.module import ModuleConfig
```

`class WebInput(Module):` の直前に追加:

```python
class WebInputConfig(ModuleConfig):
    whisper_language: str = "en"
```

`class WebInput(Module):` のクラス本体冒頭 (`_web_interface: ...` の前) に追加:

```python
    config: WebInputConfig
```

`start()` 内の `stt_node = WhisperNode()` を置換:

```python
        stt_node = WhisperNode(
            modelopts={"language": self.config.whisper_language, "fp16": False}
        )
```

- [ ] **Step 6: テスト実行 (PASS を確認)**

```bash
cd /home/naoki/dimos && uv run pytest dimos/agents/tests/test_web_human_input_config.py -v
```

期待: 2 件 PASS

- [ ] **Step 7: コミット**

```bash
git add dimos/agents/web_human_input.py dimos/agents/tests/
git commit -m "$(cat <<'EOF'
feat(web-input): add whisper_language config for STT

WebInput now exposes whisper_language via WebInputConfig (default "en")
and forwards it to WhisperNode modelopts. Enables non-English speech
input from the browser mic.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 日本語 system prompt を作成

**Files:**
- Create: `dimos/agents/system_prompt_ja.py`

- [ ] **Step 1: system_prompt_ja.py を作成**

`dimos/agents/system_prompt_ja.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

SYSTEM_PROMPT_JA = """
あなたは Dimensional が開発した AI エージェント Daneel です。Unitree Go2 四脚ロボットを制御します。常に日本語で応答してください。

# 最重要: 安全
人間の安全をすべてに優先してください。個人の境界を尊重し、人間を傷つける可能性のある行動、物や robot 自身を損なう可能性のある行動は絶対に取らないでください。

# アイデンティティ
あなたの名前は Daneel です。「ダニエル」「だにえる」「daniel」などの呼びかけは音声認識のゆれなので、自分への呼びかけとして扱ってください。挨拶された時は、物理空間で自律的に動作する AI エージェントだと簡潔に自己紹介してください。

# コミュニケーション
ユーザはスピーカー経由であなたの声を聞きますが、テキストは見えません。行動や応答は `speak` を使って伝えてください。簡潔に、1〜2文で。日本語で話してください。

# スキル連携

## ナビゲーション
- ほとんどの移動には `navigate_with_text` を使ってください。タグ付き場所 → 視認可能な物体 → セマンティックマップの順で探索します。
- 重要な場所は `tag_location` でタグ付けし、後で戻れるようにしてください。
- `start_exploration` の実行中は `stop_movement` 以外のスキルを呼ばないでください。
- ダイナミックな動作 (flip, jump, sit など) の後はナビゲーション前に必ず `execute_sport_command("RecoveryStand")` を呼んでください。

## GPS ナビゲーション
屋外/GPSベースの移動:
1. `get_gps_position_for_queries` でランドマークの座標を取得
2. その座標を `set_gps_travel_points` に渡す

## 位置認識
- `where_am_i` は現在の通り/エリアと近くのランドマークを返します
- `map_query` は OSM マップ上の場所を説明から検索し座標を返します

# 振る舞い

## 能動的であること
曖昧な要求からも妥当な行動を推測してください。例: 「新しい来客を迎えて」と言われたら玄関に向かってください。その際は前提を伝えてください。例: 「玄関に向かいます。別の場所が良ければ教えてください」

## デリバリーとピックアップ
- デリバリー: `speak` で到着を告げ、`wait` で 5 秒待ってから次の行動に移る
- ピックアップ: `speak` で手伝いを依頼し、応答を待ってから次の行動に移る

"""
```

- [ ] **Step 2: 構文チェック**

```bash
cd /home/naoki/dimos && uv run python -c "from dimos.agents.system_prompt_ja import SYSTEM_PROMPT_JA; print(len(SYSTEM_PROMPT_JA))"
```

期待: 文字数が表示され、エラーが出ない

- [ ] **Step 3: コミット**

```bash
git add dimos/agents/system_prompt_ja.py
git commit -m "$(cat <<'EOF'
feat(agents): add Japanese system prompt for Go2 agent

SYSTEM_PROMPT_JA mirrors SYSTEM_PROMPT but in Japanese. Skill identifiers
remain in English (they are code symbols). The English original is kept
as the default for other blueprints.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: go2-agentic blueprint を日本語化

**Files:**
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py`
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/_common_agentic.py`

- [ ] **Step 1: `unitree_go2_agentic.py` を更新**

`dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py` を以下に置換:

```python
#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.system_prompt_ja import SYSTEM_PROMPT_JA
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial

_LLM_MODEL = os.environ.get("DIMOS_LLM_MODEL", "gpt-4o")

unitree_go2_agentic = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    McpClient.blueprint(model=_LLM_MODEL, system_prompt=SYSTEM_PROMPT_JA),
    _common_agentic,
)

__all__ = ["unitree_go2_agentic"]
```

- [ ] **Step 2: `_common_agentic.py` を更新**

`dimos/robot/unitree/go2/blueprints/agentic/_common_agentic.py` の `_common_agentic = autoconnect(...)` ブロックを以下に置換:

```python
_common_agentic = autoconnect(
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
    WebInput.blueprint(whisper_language="ja"),
    SpeakSkill.blueprint(voice_lang="ja"),
)
```

- [ ] **Step 3: import が解決することを確認**

```bash
cd /home/naoki/dimos && uv run python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic; print('ok')"
```

期待: `ok` と表示される

- [ ] **Step 4: コミット**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic.py dimos/robot/unitree/go2/blueprints/agentic/_common_agentic.py
git commit -m "$(cat <<'EOF'
feat(go2-agentic): switch to Japanese system prompt, STT, and TTS voice

unitree_go2_agentic now wires SYSTEM_PROMPT_JA into McpClient and passes
whisper_language="ja" / voice_lang="ja" through WebInput and SpeakSkill
blueprints. End-to-end Japanese for input, LLM response, and speaker
output (assuming a Japanese pyttsx3 voice is installed; openai TTS works
out of the box).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: README に日本語ボイス導入の注意書きを追加

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 該当箇所を確認**

```bash
cd /home/naoki/dimos && grep -n "DIMOS_TTS\|go2-agentic\|pyttsx3" README.md
```

`DIMOS_TTS` のあるセクションを探す（既存変更で記載があるはず）。

- [ ] **Step 2: 注意書きを追記**

`README.md` の `DIMOS_TTS` を説明している箇所のすぐ下に、以下の段落を追加 (既存文章とのつながりは Step 1 の出力を見て自然に書く):

```markdown
> **Japanese (go2-agentic):** `unitree_go2_agentic` blueprint runs in Japanese by default — STT, LLM responses, and speaker output. For `DIMOS_TTS=pyttsx3` you need a Japanese voice installed on the host (on Linux: `sudo apt install espeak-ng mbrola mbrola-jp1`). For higher quality, set `DIMOS_TTS=openai` (no extra setup needed).
```

(挿入位置は Step 1 で確認した行の直後)

- [ ] **Step 3: コミット**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): note Japanese voice setup for go2-agentic pyttsx3

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 全体テストとリンタを通す

- [ ] **Step 1: 影響範囲のテストを一括実行**

```bash
cd /home/naoki/dimos && uv run pytest \
  dimos/stream/audio/tts/tests/test_node_pytts_voice_lang.py \
  dimos/agents/skills/tests/test_speak_skill_env.py \
  dimos/agents/tests/test_web_human_input_config.py \
  -v
```

期待: すべて PASS

- [ ] **Step 2: 型チェック (プロジェクトの慣習に従う)**

```bash
cd /home/naoki/dimos && uv run mypy dimos/agents/web_human_input.py dimos/agents/skills/speak_skill.py dimos/stream/audio/tts/node_pytts.py dimos/agents/system_prompt_ja.py dimos/robot/unitree/go2/blueprints/agentic/ 2>&1 | tail -20
```

期待: 新たな型エラーなし（既存のエラーは無関係なので無視可）

- [ ] **Step 3: lint**

```bash
cd /home/naoki/dimos && uv run ruff check dimos/agents/web_human_input.py dimos/agents/skills/speak_skill.py dimos/stream/audio/tts/node_pytts.py dimos/agents/system_prompt_ja.py dimos/robot/unitree/go2/blueprints/agentic/
```

期待: クリーン (もしくはプロジェクトの既存スタイル違反のみ)

- [ ] **Step 4: 修正が必要なら fix → 再実行 → コミット**

エラーが出た場合のみ:

```bash
cd /home/naoki/dimos && uv run ruff check --fix <paths>
# Edit ファイルを直接編集して修正
git add -p
git commit -m "fix: address lint/type issues in go2-agentic Japanese changes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: 手動 e2e 確認 (ユーザに依頼)

このタスクはコード変更を含まない。ユーザの実機/シミュレータ環境での確認手順を提示する。

- [ ] **Step 1: ユーザに以下の手順を依頼**

1. `DIMOS_TTS=pyttsx3`（または `openai`）と `DIMOS_LLM_MODEL`（任意）を設定して go2-agentic を起動
2. ブラウザで `http://localhost:5555` を開きマイクから日本語で話す
3. 期待される観察結果:
   - human message ログが日本語で記録される
   - LLM 応答が日本語
   - スピーカーから日本語の読み上げが出る（pyttsx3 の場合は日本語ボイスインストール済みであること）
4. うまくいかなかった場合のフィードバックを受け取って必要なら修正タスクを追加

---

## 完了条件

- 全ユニットテストが PASS（既存 + 新規）
- import エラーや構文エラーなし
- README に日本語ボイス導入の注記がある
- 手動 e2e で日本語入出力が動作することをユーザが確認
