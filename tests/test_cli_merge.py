"""Filters given on both sides of the subcommand must merge, never overwrite (review BLOCKER)."""

from __future__ import annotations

from persona_machine_mcp.cli import parse_args
from persona_machine_mcp.config import resolve
from persona_machine_mcp.proxy import tool_exposed


def test_list_filters_merge_across_root_and_subcommand():
    args = parse_args(
        [
            "--exclude-tools",
            "delete_*",
            "--tools",
            "list_*",
            "serve",
            "--exclude-tools",
            "update_*",
            "--tools",
            "show_*",
        ]
    )
    assert args.exclude_tools == ["delete_*", "update_*"]
    assert args.tools == ["list_*", "show_*"]
    assert not any(name.endswith("__sub") for name in vars(args))
    settings = resolve(args, {})
    assert settings.exclude_tools == ("delete_*", "update_*")
    assert settings.tools == ("list_*", "show_*")
    assert not tool_exposed("delete_account", settings)
    assert not tool_exposed("update_account", settings)
    assert tool_exposed("list_inquiries", settings)
    assert not tool_exposed("create_inquiry", settings)


def test_scalar_after_subcommand_wins_and_root_only_still_works():
    args = parse_args(["--log-level", "DEBUG", "check", "--log-level", "WARNING"])
    assert args.log_level == "WARNING"
    assert resolve(args, {}).log_level == "WARNING"
    args = parse_args(["--log-level", "DEBUG", "check"])
    assert resolve(args, {}).log_level == "DEBUG"
    args = parse_args(["check", "--scope", "inquiry:read"])
    assert resolve(args, {}).scope == "inquiry:read"
    assert resolve(parse_args([]), {}).exclude_tools == ()


def test_env_filters_only_apply_when_no_flag_given():
    env = {"PERSONA_MCP_EXCLUDE_TOOLS": "a_*, b_*"}
    assert resolve(parse_args(["serve"]), env).exclude_tools == ("a_*", "b_*")
    assert resolve(parse_args(["serve", "--exclude-tools", "c_*"]), env).exclude_tools == ("c_*",)
