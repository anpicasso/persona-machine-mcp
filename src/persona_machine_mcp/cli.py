"""Argparse CLI. Only explicit command output is written to stdout."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import anyio

from . import __version__, proxy
from .auth import TokenError
from .config import Settings, resolve, validate_client_id
from .keys import (
    generate_private_key,
    jwks,
    load_private_key,
    public_pem,
    write_keypair,
    write_private_file,
)

log = logging.getLogger(__name__)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _common(parser: argparse.ArgumentParser) -> None:
    for flag, help_text in (
        ("--home", "Configuration directory"),
        ("--client-id", "Persona machine client id (oac_...)"),
        ("--key", "Private PEM key path"),
        ("--mcp-url", "Upstream Streamable HTTP endpoint"),
        ("--token-url", "OAuth token endpoint"),
        ("--scope", "Space-separated subset of client scopes"),
        ("--log-level", "DEBUG, INFO, WARNING, ERROR, or CRITICAL"),
    ):
        parser.add_argument(flag, default=argparse.SUPPRESS, help=help_text)
    parser.add_argument(
        "--tools",
        action="append",
        default=argparse.SUPPRESS,
        metavar="GLOB",
        help="Expose matching tools (repeatable)",
    )
    parser.add_argument(
        "--exclude-tools",
        action="append",
        default=argparse.SUPPRESS,
        metavar="GLOB",
        help="Hide matching tools after allow-list (repeatable)",
    )


def build_parser() -> Parser:
    parser = Parser(description="Unofficial Persona MCP V2 machine-client bridge")
    _common(parser)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    descriptions = {
        "serve": "Run the stdio MCP bridge (default)",
        "keygen": "Generate an EC P-256 or RSA keypair",
        "configure": "Save a machine client id",
        "check": "Authenticate, initialize, and list visible tools",
        "token": "Print one access token (treat stdout as secret)",
        "jwks": "Print the public key as JWKS",
        "paths": "Show resolved paths and sources without secrets",
    }
    for name, help_text in descriptions.items():
        child = commands.add_parser(name, help=help_text, description=help_text)
        _common(child)
        if name == "keygen":
            child.add_argument("--alg", choices=("ec", "rsa"), default="ec")
            child.add_argument("--bits", choices=(2048, 3072, 4096), type=int, default=2048)
            child.add_argument("--dir", type=Path)
            child.add_argument("--force", action="store_true")
        elif name == "token":
            resource = child.add_mutually_exclusive_group()
            resource.add_argument("--resource")
            resource.add_argument("--no-resource", action="store_true")
        elif name == "jwks":
            child.add_argument("--kid")
    return parser


async def _token(settings: Settings, resource: str | None) -> str:
    return await proxy.make_minter(settings, resource=resource).get()


def _find_token_error(exc: BaseException) -> TokenError | None:
    if isinstance(exc, TokenError):
        return exc
    for child in getattr(exc, "exceptions", ()):
        found = _find_token_error(child)
        if found:
            return found
    if exc.__cause__ is not None:
        return _find_token_error(exc.__cause__)
    return None


def _dispatch(args: argparse.Namespace, settings: Settings) -> None:
    command = args.command or "serve"
    if command == "serve":
        anyio.run(proxy.run_bridge, settings)
    elif command == "keygen":
        key = generate_private_key(args.alg, args.bits)
        private, public = write_keypair(args.dir or settings.home, key, args.force)
        print(public_pem(key).decode("ascii"), end="")
        print(f"Saved {private} (0600) and {public} (0644).", file=sys.stderr)
        print(
            "Paste the public PEM into Dashboard → API Keys → Machine clients; then run "
            "persona-machine-mcp configure --client-id oac_... and check.",
            file=sys.stderr,
        )
    elif command == "configure":
        if not settings.client_id:
            raise ValueError("configure requires --client-id oac_...")
        client_id = validate_client_id(settings.client_id)
        destination = settings.home / "client_id"
        write_private_file(destination, (client_id + "\n").encode("utf-8"))
        print(destination)
    elif command == "check":
        result = anyio.run(proxy.check, settings)
        count = len(result.tools)
        print(f"OK: {count} tools ({count}/{result.total})")
        for tool in result.tools:
            print(tool.name)
    elif command == "token":
        resource = None if args.no_resource else args.resource or settings.mcp_url
        print(anyio.run(_token, settings, resource))
    elif command == "jwks":
        if not settings.private_key_pem:
            raise ValueError("Missing private key; run keygen or set --key")
        key, _ = load_private_key(settings.private_key_pem)
        print(json.dumps(jwks(key, args.kid), indent=2))
    elif command == "paths":
        print(
            json.dumps(
                {
                    "home": {"path": str(settings.home), "source": settings.sources["home"]},
                    "key": {
                        "path": str(settings.key_path) if settings.key_path else None,
                        "source": settings.sources["key"],
                    },
                    "client_id": {
                        "path": str(settings.client_id_path),
                        "source": settings.sources["client_id"],
                    },
                },
                indent=2,
            )
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="persona-machine-mcp %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        args = build_parser().parse_args(argv)
        settings = resolve(args, os.environ)
        logging.getLogger().setLevel(settings.log_level)
        _dispatch(args, settings)
        return 0
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        token_error = _find_token_error(exc)
        if token_error:
            log.error("%s", token_error)
            return {"invalid_client": 2, "invalid_grant": 3}.get(token_error.code, 4)
        if isinstance(exc, proxy.UpstreamError):
            log.error("Upstream MCP connection or initialization failed")
            return 5
        if isinstance(exc, (ValueError, OSError)):
            log.error("%s", exc)
            return 1
        # Unexpected failures stay one-line and never expose arbitrary exception data.
        log.error("Unexpected bridge failure (%s)", type(exc).__name__)
        return 5
