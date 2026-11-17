"""Credentials must never reach stderr, even when the MCP SDK dumps raw upstream payloads."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from cryptography.hazmat.primitives import serialization

from persona_machine_mcp import proxy, redaction
from persona_machine_mcp.auth import PersonaAuth, TokenMinter
from persona_machine_mcp.cli import main
from persona_machine_mcp.config import Settings
from persona_machine_mcp.keys import generate_private_key
from persona_machine_mcp.redaction import REDACTED, RedactingFilter, redact, register_secret

SENTINEL_TOKEN = "puat_LEAKED_SENTINEL_0123456789"
JWT_LIKE = "eyJhbGciOiJFUzI1NiJ9.eyJpc3MiOiJvYWNfeCJ9.c2lnbmF0dXJlLWJ5dGVz"


def _pem() -> bytes:
    return generate_private_key("ec", 2048).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_redact_known_and_shaped_secrets():
    register_secret("registered-secret-value")
    pem = _pem().decode()
    text = (
        f"a {SENTINEL_TOKEN} b registered-secret-value c Authorization: Bearer abc.DEF-ghi_123= "
        f"d {JWT_LIKE} e\n{pem}\nf"
    )
    out = redact(text)
    for secret in (SENTINEL_TOKEN, "registered-secret-value", "abc.DEF-ghi_123=", JWT_LIKE):
        assert secret not in out
    assert "PRIVATE KEY" not in out
    assert "Bearer [REDACTED]" in out
    assert out.count(REDACTED) >= 5
    # Short values are never registered (they would shred ordinary words).
    register_secret("abc")
    assert "abc" in redact("abc")


def test_filter_rewrites_args_and_tracebacks(capsys):
    handler = redaction.install_stderr_logging(logging.INFO)
    assert handler in logging.getLogger().handlers
    # Idempotent: a second install must not duplicate output.
    assert redaction.install_stderr_logging(logging.INFO) is handler
    log = logging.getLogger("mcp.client.streamable_http")
    log.warning("Raw result: %s", {"serverInfo": SENTINEL_TOKEN})
    try:
        raise ValueError(f"input_value='{SENTINEL_TOKEN}'")
    except ValueError:
        logging.getLogger().exception("Unhandled exception in receive loop")
    err = capsys.readouterr().err
    assert SENTINEL_TOKEN not in err
    assert "Raw result" in err and "Traceback" in err and "input_value" in err
    assert err.count("Raw result") == 1


def test_filter_handles_unformattable_record():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%d", ("not-int",), None)
    assert RedactingFilter().filter(record) is True
    assert record.args == ()


@pytest.mark.anyio
async def test_sdk_raw_payload_logging_is_redacted_end_to_end(tmp_path, capsys):
    """An upstream that echoes our bearer inside a malformed initialize result makes the SDK log
    ``Raw result: {...}`` at WARNING; the stderr handler must scrub it."""
    redaction.install_stderr_logging(logging.INFO)
    minted = SENTINEL_TOKEN
    seen_bearer: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": minted, "expires_in": 900})
        if request.method == "GET":
            return httpx.Response(405)
        body = json.loads(request.content or b"{}")
        if body.get("method") == "initialize":
            seen_bearer.append(request.headers.get("Authorization", ""))
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        # invalid: must be an object; echoes the bearer we just sent
                        "serverInfo": request.headers.get("Authorization", ""),
                    },
                },
            )
        return httpx.Response(202)

    transport = httpx.MockTransport(upstream)

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, headers=headers, timeout=timeout, auth=auth)

    minter = TokenMinter(
        "oac_test",
        _pem(),
        "https://upstream.test/token",
        "https://upstream.test/mcp",
        transport=transport,
    )
    settings = Settings(
        home=tmp_path,
        key_path=None,
        client_id_path=tmp_path / "client_id",
        mcp_url="https://upstream.test/mcp",
        token_url="https://upstream.test/token",
    )
    with pytest.raises(proxy.UpstreamError):
        async with proxy._upstream(settings, PersonaAuth(minter), http_client_factory=factory):
            pass  # pragma: no cover - initialize must fail first
    assert seen_bearer == [f"Bearer {minted}"]
    err = capsys.readouterr().err
    assert minted not in err
    assert "Bearer" not in err or "Bearer [REDACTED]" in err
    assert REDACTED in err  # the SDK did log the raw payload; it reached stderr scrubbed


def test_cli_check_never_prints_sentinel_on_malformed_upstream(tmp_path, monkeypatch, capsys):
    from persona_machine_mcp import auth

    main(["keygen", "--home", str(tmp_path)])
    capsys.readouterr()

    def token(request):
        return httpx.Response(200, json={"access_token": SENTINEL_TOKEN, "expires_in": 900})

    original = auth.TokenMinter
    monkeypatch.setattr(
        proxy,
        "TokenMinter",
        lambda *a, **k: original(*a, **k, transport=httpx.MockTransport(token)),
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(405)
        body = json.loads(request.content or b"{}")
        if body.get("method") == "initialize":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": request.headers.get("Authorization", ""),
                    },
                },
            )
        return httpx.Response(202)

    transport = httpx.MockTransport(upstream)
    original_check = proxy.check

    async def patched_check(settings):
        def factory(headers=None, timeout=None, auth=None):
            return httpx.AsyncClient(
                transport=transport, headers=headers, timeout=timeout, auth=auth
            )

        return await original_check(settings, http_client_factory=factory)

    monkeypatch.setattr(proxy, "check", patched_check)
    code = main(
        [
            "--home",
            str(tmp_path),
            "--client-id",
            "oac_test",
            "--mcp-url",
            "https://upstream.test/mcp",
            "check",
        ]
    )
    out = capsys.readouterr()
    assert code == 5
    assert out.out == ""
    assert SENTINEL_TOKEN not in out.err
    assert "Upstream MCP connection or initialization failed" in out.err
