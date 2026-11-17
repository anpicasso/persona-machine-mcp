"""Resolve configuration without creating files or contacting services."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

MCP_URL_DEFAULT = "https://withpersona.com/mcp"
TOKEN_URL_DEFAULT = "https://withpersona.com/api/v1/oauth/token"


@dataclass(frozen=True)
class Settings:
    home: Path
    key_path: Path | None
    client_id_path: Path
    client_id: str | None = field(default=None, repr=False)
    private_key_pem: bytes | None = field(default=None, repr=False)
    mcp_url: str = MCP_URL_DEFAULT
    token_url: str = TOKEN_URL_DEFAULT
    scope: str | None = None
    tools: tuple[str, ...] = ()
    exclude_tools: tuple[str, ...] = ()
    log_level: str = "INFO"
    sources: dict[str, str] = field(default_factory=dict)


def validate_client_id(value: str) -> str:
    if not value.startswith("oac_") or len(value) <= 4 or any(c.isspace() for c in value):
        raise ValueError(
            "Client id must start with oac_ and contain a nonempty, unpadded identifier"
        )
    return value


def _read_optional(path: Path, *, explicit: bool = False) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        if explicit:
            raise ValueError(f"Configured credential file not found: {path}") from None
        return None


def resolve(args: Namespace, environ: Mapping[str, str]) -> Settings:
    def flag(name: str):
        return getattr(args, name, None)

    def setting(name: str, env: str, default=None):
        value = flag(name)
        return value if value is not None else environ.get(env) or default

    default_home = Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    home = Path(setting("home", "PERSONA_MCP_HOME", default_home / "persona-machine-mcp"))
    home = home.expanduser()
    sources = {
        "home": "flag"
        if flag("home") is not None
        else ("env:PERSONA_MCP_HOME" if environ.get("PERSONA_MCP_HOME") else "default")
    }
    id_file = Path(environ.get("PERSONA_CLIENT_ID_FILE") or home / "client_id").expanduser()
    client_id = setting("client_id", "PERSONA_CLIENT_ID")
    if client_id is not None:
        sources["client_id"] = "flag" if flag("client_id") is not None else "env:PERSONA_CLIENT_ID"
    else:
        raw = _read_optional(id_file, explicit=bool(environ.get("PERSONA_CLIENT_ID_FILE")))
        client_id = raw.decode("utf-8").strip() or None if raw is not None else None
        sources["client_id"] = "file" if client_id else "unset"
    if client_id is not None:
        validate_client_id(client_id)

    key_path: Path | None
    if flag("key") is not None:
        key_path = Path(flag("key")).expanduser()
        pem = _read_optional(key_path, explicit=True)
        sources["key"] = "flag"
    elif environ.get("PERSONA_PRIVATE_KEY_PEM"):
        pem = environ["PERSONA_PRIVATE_KEY_PEM"].encode("utf-8")
        key_path = None
        sources["key"] = "env:PERSONA_PRIVATE_KEY_PEM"
    else:
        key_path = Path(
            environ.get("PERSONA_PRIVATE_KEY_PATH") or home / "private.pem"
        ).expanduser()
        explicit = bool(environ.get("PERSONA_PRIVATE_KEY_PATH"))
        pem = _read_optional(key_path, explicit=explicit)
        sources["key"] = "env:PERSONA_PRIVATE_KEY_PATH" if explicit else "file" if pem else "unset"

    def patterns(name: str, env: str) -> tuple[str, ...]:
        supplied = flag(name)
        if supplied is not None:
            return tuple(supplied)
        return tuple(part.strip() for part in environ.get(env, "").split(",") if part.strip())

    log_level = setting("log_level", "PERSONA_MCP_LOG", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError("Log level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return Settings(
        home=home,
        key_path=key_path,
        client_id_path=id_file,
        client_id=client_id,
        private_key_pem=pem,
        mcp_url=setting("mcp_url", "PERSONA_MCP_URL", MCP_URL_DEFAULT),
        token_url=setting("token_url", "PERSONA_TOKEN_URL", TOKEN_URL_DEFAULT),
        scope=setting("scope", "PERSONA_SCOPE"),
        tools=patterns("tools", "PERSONA_MCP_TOOLS"),
        exclude_tools=patterns("exclude_tools", "PERSONA_MCP_EXCLUDE_TOOLS"),
        log_level=log_level,
        sources=sources,
    )
