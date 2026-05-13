from dimos.agents.voice_live.voice_live_node import AzureVoiceLiveNode


def test_node_constructor_stores_config():
    node = AzureVoiceLiveNode(
        endpoint="wss://example.azure.com/voice-live",
        api_key="test-key",
        model="gpt-4o-realtime",
        voice="ja-JP-NanamiNeural",
        instructions="日本語で話して",
        tools=[],
        on_tool_call=lambda call_id, name, args_json: None,
    )
    assert node.endpoint == "wss://example.azure.com/voice-live"
    assert node.api_key == "test-key"
    assert node.model == "gpt-4o-realtime"
    assert node.voice == "ja-JP-NanamiNeural"
    assert node.instructions == "日本語で話して"
    assert node.tools == []
