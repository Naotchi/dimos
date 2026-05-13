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

JAPANESE_SYSTEM_PROMPT = """あなたは Unitree Go2 四足歩行ロボットを操作するエージェントです。
ユーザーの日本語の指示を理解し、利用可能なツール（移動、追従、状態確認など）を使って実行してください。

ガイドライン:
- 返答は日本語で、簡潔に話すこと
- 動作実行の前に、何をするか短く伝えること
- 動作完了後は結果を報告すること
- 失敗時はその理由を簡潔に説明すること
- ツールを呼ぶときは安全を最優先にし、不明確な指示は質問して確認すること
"""
