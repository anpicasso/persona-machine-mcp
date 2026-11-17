import time
from urllib.parse import parse_qs

import anyio
import anyio.lowlevel
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from persona_machine_mcp.auth import PersonaAuth, TokenError, TokenMinter
from persona_machine_mcp.keys import generate_private_key

TOKEN_URL = "https://example.test/token"
RESOURCE = "https://example.test/mcp"


def make_minter(handler, *, resource=RESOURCE, alg="ec", scope=None):
    key = generate_private_key(alg, 2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return TokenMinter(
        "oac_test", pem, TOKEN_URL, resource, scope, transport=httpx.MockTransport(handler)
    ), key


@pytest.mark.anyio
@pytest.mark.parametrize("alg", ["ec", "rsa"])
@pytest.mark.parametrize("resource", [RESOURCE, None])
async def test_assertion_claims_form_resource_and_scope(alg, resource):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": "private-token", "expires_in": 900})

    minter, key = make_minter(handler, resource=resource, alg=alg, scope="inquiry:read")
    assert await minter.get() == "private-token"
    assert await minter.get(force=True) == "private-token"
    assertions = []
    for request in requests:
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["client_credentials"]
        assert form["client_assertion_type"] == [
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ]
        assert form["scope"] == ["inquiry:read"]
        if resource:
            assert form["resource"] == [resource]
        else:
            assert "resource" not in form
        assertion = form["client_assertion"][0]
        claims = jwt.decode(
            assertion,
            key.public_key(),
            algorithms=["ES256" if alg == "ec" else "RS256"],
            audience=TOKEN_URL,
        )
        assert set(claims) == {"iss", "sub", "aud", "iat", "exp", "jti"}
        assert claims["iss"] == claims["sub"] == "oac_test"
        assert claims["aud"] == TOKEN_URL
        assert claims["exp"] == claims["iat"] + 300
        assert abs(time.time() - claims["iat"]) < 5
        assert len(claims["jti"]) >= 32
        assertions.append(claims["jti"])
    assert len(set(assertions)) == 2


@pytest.mark.anyio
async def test_cache_refresh_skew_and_single_flight(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("persona_machine_mcp.auth.time.time", lambda: now[0])
    calls = []

    async def handler(request):
        calls.append(request)
        await anyio.lowlevel.checkpoint()
        return httpx.Response(200, json={"access_token": f"token-{len(calls)}", "expires_in": 900})

    minter, _ = make_minter(handler)
    results = []

    async def get():
        results.append(await minter.get())

    async with anyio.create_task_group() as group:
        for _ in range(10):
            group.start_soon(get)
    assert results == ["token-1"] * 10
    assert len(calls) == 1
    now[0] = 1809
    assert await minter.get() == "token-1"
    now[0] = 1810
    assert await minter.get() == "token-2"
    assert len(calls) == 2
    assert "scope" not in parse_qs(calls[0].content.decode())


@pytest.mark.anyio
@pytest.mark.parametrize("second_status", [200, 401])
async def test_401_remints_and_retries_exactly_once(second_status):
    tokens = []
    received = []

    def token_endpoint(request):
        tokens.append(request)
        return httpx.Response(200, json={"access_token": f"token-{len(tokens)}"})

    def upstream(request):
        received.append(request.headers["Authorization"])
        return httpx.Response(401 if len(received) == 1 else second_status)

    minter, _ = make_minter(token_endpoint)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream), auth=PersonaAuth(minter)
    ) as client:
        response = await client.get(RESOURCE)
    assert response.status_code == second_status
    assert received == ["Bearer token-1", "Bearer token-2"]
    assert len(tokens) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,body,code",
    [
        (400, {"error": "invalid_client"}, "invalid_client"),
        (400, {"error": "invalid_grant"}, "invalid_grant"),
        (503, {"error": "secret-marker"}, "http_503"),
        (500, ["secret-marker"], "http_500"),
        (200, {"access_token": "secret-marker", "expires_in": "bad"}, "http_200"),
        (200, {"access_token": "secret-marker", "expires_in": -1}, "http_200"),
        (200, {"access_token": "secret-marker", "expires_in": float("inf")}, "http_200"),
        (200, {}, "http_200"),
    ],
)
async def test_token_error_codes_and_no_body_leak(status, body, code, caplog):
    import json

    minter, _ = make_minter(lambda request: httpx.Response(status, content=json.dumps(body)))
    with pytest.raises(TokenError) as caught:
        await minter.get()
    assert caught.value.code == code
    assert "secret-marker" not in str(caught.value)
    assert "secret-marker" not in caplog.text


@pytest.mark.anyio
async def test_network_and_non_json_error():
    def broken(request):
        raise httpx.ConnectError("secret-marker", request=request)

    minter, _ = make_minter(broken)
    with pytest.raises(TokenError) as caught:
        await minter.get()
    assert caught.value.code == "network"
    assert "secret-marker" not in str(caught.value)
    minter, _ = make_minter(lambda request: httpx.Response(502, text="not JSON secret-marker"))
    with pytest.raises(TokenError) as caught:
        await minter.get()
    assert caught.value.code == "http_502"
