from argparse import Namespace
from pathlib import Path

import pytest

from persona_machine_mcp.config import resolve


def test_flag_env_file_precedence(tmp_path):
    (tmp_path / "client_id").write_text("oac_file\n")
    (tmp_path / "private.pem").write_text("file-pem")
    external = tmp_path / "external.pem"
    external.write_text("flag-pem")
    env = {"PERSONA_MCP_HOME": str(tmp_path)}
    settings = resolve(Namespace(), env)
    assert settings.client_id == "oac_file"
    assert settings.private_key_pem == b"file-pem"
    assert settings.sources["client_id"] == "file"
    env.update(PERSONA_CLIENT_ID="oac_env", PERSONA_PRIVATE_KEY_PEM="env-pem")
    settings = resolve(Namespace(), env)
    assert settings.client_id == "oac_env"
    assert settings.private_key_pem == b"env-pem"
    assert settings.sources["key"] == "env:PERSONA_PRIVATE_KEY_PEM"
    settings = resolve(Namespace(client_id="oac_flag", key=str(external)), env)
    assert settings.client_id == "oac_flag"
    assert settings.private_key_pem == b"flag-pem"
    assert settings.sources["client_id"] == "flag"
    assert settings.sources["key"] == "flag"
    assert "flag-pem" not in repr(settings)
    assert "oac_flag" not in repr(settings)


def test_inline_pem_precedes_env_path_and_legacy_id_file(tmp_path):
    legacy = tmp_path / "old-id"
    legacy.write_text("oac_legacy\n")
    settings = resolve(
        Namespace(),
        {
            "PERSONA_MCP_HOME": str(tmp_path),
            "PERSONA_PRIVATE_KEY_PEM": "inline",
            "PERSONA_PRIVATE_KEY_PATH": str(tmp_path / "does-not-exist"),
            "PERSONA_CLIENT_ID_FILE": str(legacy),
        },
    )
    assert settings.private_key_pem == b"inline"
    assert settings.client_id == "oac_legacy"
    assert settings.client_id_path == legacy


def test_xdg_and_home_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve(Namespace(), {}).home == tmp_path / ".config/persona-machine-mcp"
    assert resolve(Namespace(), {"XDG_CONFIG_HOME": str(tmp_path)}).home == (
        tmp_path / "persona-machine-mcp"
    )
    assert (
        resolve(
            Namespace(home=str(tmp_path / "flag")), {"PERSONA_MCP_HOME": str(tmp_path / "env")}
        ).home
        == tmp_path / "flag"
    )


def test_filters_and_urls(tmp_path):
    env = {
        "PERSONA_MCP_HOME": str(tmp_path),
        "PERSONA_MCP_TOOLS": "list_*, show_* , ,fetch",
        "PERSONA_MCP_EXCLUDE_TOOLS": "*_secret,delete_*",
        "PERSONA_MCP_URL": "https://example.test/mcp",
        "PERSONA_TOKEN_URL": "https://example.test/token",
        "PERSONA_SCOPE": "inquiry:read",
        "PERSONA_MCP_LOG": "debug",
    }
    settings = resolve(Namespace(), env)
    assert settings.tools == ("list_*", "show_*", "fetch")
    assert settings.exclude_tools == ("*_secret", "delete_*")
    assert settings.mcp_url == env["PERSONA_MCP_URL"]
    assert settings.token_url == env["PERSONA_TOKEN_URL"]
    assert settings.scope == "inquiry:read"
    assert settings.log_level == "DEBUG"
    assert resolve(Namespace(tools=["one"], exclude_tools=["two"]), env).tools == ("one",)


@pytest.mark.parametrize("client_id", ["bad", " oac_bad", "oac_", "oac_bad\n"])
def test_bad_client_id_rejected(tmp_path, client_id):
    with pytest.raises(ValueError, match="oac_"):
        resolve(Namespace(home=str(tmp_path), client_id=client_id), {})


def test_missing_credentials_can_resolve_and_does_not_create_home(tmp_path):
    target = tmp_path / "not-created"
    settings = resolve(Namespace(home=str(target)), {})
    assert settings.client_id is None
    assert settings.private_key_pem is None
    assert not target.exists()
