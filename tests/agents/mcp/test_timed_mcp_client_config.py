"""TimedMcpClientConfig.model is a category-A field: explicit > env seed > default."""


def test_model_seeded_from_env(monkeypatch):
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    monkeypatch.setenv("DIMOS_LLM_MODEL", "seedmodel")
    assert TimedMcpClientConfig().model == "seedmodel"


def test_explicit_model_overrides_env(monkeypatch):
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    monkeypatch.setenv("DIMOS_LLM_MODEL", "seedmodel")
    assert TimedMcpClientConfig(model="explicit").model == "explicit"


def test_model_falls_back_to_default(monkeypatch):
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClientConfig

    monkeypatch.delenv("DIMOS_LLM_MODEL", raising=False)
    assert TimedMcpClientConfig().model == "gpt-4o"


def test_timed_client_resolves_subclassed_config():
    # Configurable picks the config class from the most-derived ``config:`` hint.
    from typing import get_type_hints

    from dimos.agents.mcp.mcp_client_ja import TimedMcpClient, TimedMcpClientConfig

    assert get_type_hints(TimedMcpClient)["config"] is TimedMcpClientConfig
