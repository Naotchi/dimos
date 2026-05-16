# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub test module for the Azure Voice Live agent.

The real tests will mock ``azure.ai.voicelive.aio.connect`` with an
AsyncMock and exercise: session.update, response.audio.delta → playback,
function_call → MCP → function_call_output, SPEECH_STARTED → cancel +
skip_pending.  Written after manual E2E verifies the happy path.
"""

import pytest

pytestmark = pytest.mark.skip(reason="Voice Live tests pending — verify manually")


def test_placeholder() -> None:
    assert True
