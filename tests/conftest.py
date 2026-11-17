"""Offline test defaults; no user credentials may reach a test."""

import os

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("PERSONA_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PERSONA_MCP_HOME", str(tmp_path))
