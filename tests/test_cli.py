import json
import stat
from pathlib import Path

import httpx
import pytest

from persona_machine_mcp import __version__
from persona_machine_mcp.cli import main


def test_keygen_configure_paths_jwks(tmp_path, capsys):
    base = ["--home", str(tmp_path)]
    assert main([*base, "keygen"]) == 0
    output = capsys.readouterr()
    assert output.out.startswith("-----BEGIN PUBLIC KEY-----")
    assert "PRIVATE KEY" not in output.out
    assert "Dashboard" in output.err
    assert main([*base, "configure", "--client-id", "oac_test"]) == 0
    capsys.readouterr()
    assert (tmp_path / "client_id").read_text() == "oac_test\n"
    assert stat.S_IMODE((tmp_path / "client_id").stat().st_mode) == 0o600
    assert main(["paths", *base]) == 0
    output = capsys.readouterr().out
    assert str(tmp_path) in output
    assert "oac_test" not in output
    assert "PRIVATE KEY" not in output
    assert "file" in output
    assert main(["jwks", *base]) == 0
    public = json.loads(capsys.readouterr().out)
    assert public["keys"][0]["kty"] == "EC"
    assert "d" not in public["keys"][0]
    assert main([*base, "keygen"]) == 1
    assert main([*base, "keygen", "--force", "--alg", "rsa"]) == 0
    capsys.readouterr()
    assert main([*base, "jwks", "--kid", "rotated"]) == 0
    assert json.loads(capsys.readouterr().out)["keys"][0]["kid"] == "rotated"


@pytest.mark.parametrize("command", ["token", "check", "serve"])
@pytest.mark.parametrize("error,code", [("invalid_client", 2), ("invalid_grant", 3), ("other", 4)])
def test_token_endpoint_exit_codes(tmp_path, monkeypatch, capsys, caplog, command, error, code):
    from persona_machine_mcp import auth, proxy

    assert main(["keygen", "--home", str(tmp_path)]) == 0
    capsys.readouterr()
    original = auth.TokenMinter

    def minter(*args, **kwargs):
        return original(
            *args,
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(400, json={"error": error, "secret": "DO-NOT-LOG"})
            ),
        )

    monkeypatch.setattr(proxy, "TokenMinter", minter)
    assert main([command, "--home", str(tmp_path), "--client-id", "oac_test"]) == code
    assert capsys.readouterr().out == ""
    assert "DO-NOT-LOG" not in caplog.text
    assert "Traceback" not in caplog.text


def test_token_output_and_resource_options(tmp_path, monkeypatch, capsys, caplog):
    from urllib.parse import parse_qs

    from persona_machine_mcp import auth, proxy

    main(["keygen", "--home", str(tmp_path)])
    capsys.readouterr()
    requests = []

    def token(request):
        requests.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "puat_SECRET"})

    original = auth.TokenMinter
    monkeypatch.setattr(
        proxy,
        "TokenMinter",
        lambda *args, **kwargs: original(*args, **kwargs, transport=httpx.MockTransport(token)),
    )
    base = ["token", "--home", str(tmp_path), "--client-id", "oac_test"]
    for options, resource in [
        ([], "https://withpersona.com/mcp"),
        (["--no-resource"], None),
        (["--resource", "https://example.test"], "https://example.test"),
    ]:
        assert main([*base, *options, "--scope", "inquiry:read"]) == 0
        assert capsys.readouterr().out == "puat_SECRET\n"
        assert requests[-1].get("resource") == ([resource] if resource else None)
        assert requests[-1]["scope"] == ["inquiry:read"]
    assert "puat_SECRET" not in caplog.text


@pytest.mark.parametrize(
    "args",
    [
        ["configure", "--client-id", "bad"],
        ["configure"],
        ["token"],
        ["check"],
        ["jwks"],
        ["keygen", "--bits", "1024"],
        ["--not-an-option"],
        ["nonsense"],
    ],
)
def test_usage_config_errors_are_one(args):
    assert main(args) == 1


def test_version_and_help(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
    assert main(["--help"]) == 0
    assert "keygen" in capsys.readouterr().out


def test_default_command_and_options_both_sides(tmp_path, monkeypatch):
    from persona_machine_mcp import proxy

    seen = []

    async def run(settings):
        seen.append(settings)

    monkeypatch.setattr(proxy, "run_bridge", run)
    assert main(["--home", str(tmp_path), "--tools", "list_*"]) == 0
    assert main(["--home", str(tmp_path), "serve", "--tools", "show_*"]) == 0
    assert [settings.tools for settings in seen] == [("list_*",), ("show_*",)]
    assert all(settings.home == Path(tmp_path) for settings in seen)


def test_upstream_failure_code(monkeypatch, caplog):
    from persona_machine_mcp import proxy

    async def run(settings):
        raise proxy.UpstreamError("Upstream MCP connection or initialization failed")

    monkeypatch.setattr(proxy, "run_bridge", run)
    assert main(["serve"]) == 5
    assert "Traceback" not in caplog.text
