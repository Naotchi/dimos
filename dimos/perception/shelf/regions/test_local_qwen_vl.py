# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import pytest

from dimos.perception.shelf.regions.local_qwen_vl import _resolve_endpoint


def test_resolve_endpoint_prefers_shelf_vars(monkeypatch):
    monkeypatch.setenv("SHELF_VLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("SHELF_VLM_MODEL", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setenv("SHELF_VLM_API_KEY", "shelf-key")
    monkeypatch.setenv("DIMOS_LLM_BASE_URL", "http://other:9999/v1")
    monkeypatch.setenv("DIMOS_LLM_MODEL", "other-model")
    base_url, model, api_key = _resolve_endpoint()
    assert base_url == "http://localhost:1234/v1"
    assert model == "qwen/qwen3.6-35b-a3b"
    assert api_key == "shelf-key"


def test_resolve_endpoint_falls_back_to_dimos_llm_vars(monkeypatch):
    monkeypatch.delenv("SHELF_VLM_BASE_URL", raising=False)
    monkeypatch.delenv("SHELF_VLM_MODEL", raising=False)
    monkeypatch.delenv("SHELF_VLM_API_KEY", raising=False)
    monkeypatch.setenv("DIMOS_LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("DIMOS_LLM_MODEL", "qwen/qwen3.6-35b-a3b")
    monkeypatch.delenv("DIMOS_LLM_API_KEY", raising=False)
    base_url, model, api_key = _resolve_endpoint()
    assert base_url == "http://localhost:1234/v1"
    assert model == "qwen/qwen3.6-35b-a3b"
    assert api_key == "lm-studio"  # default placeholder for keyless local servers


def test_resolve_endpoint_raises_without_base_url(monkeypatch):
    for var in ("SHELF_VLM_BASE_URL", "DIMOS_LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SHELF_VLM_MODEL", "m")
    with pytest.raises(ValueError):
        _resolve_endpoint()
