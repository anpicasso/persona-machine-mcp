# persona-machine-mcp

**Unofficial. Not affiliated with or endorsed by Persona (withpersona.com).** MIT licensed.

A stdio MCP server that connects Hermes, Claude Code, Claude Desktop, Cursor, Codex CLI, or any
other MCP host to **Persona MCP Server V2**, using a machine client rather than a browser login.
Requires Python 3.10+; runtime uses the MCP Python SDK **1.x**, not 2.x.

A Persona machine client is an OAuth application registered with a public key. The bridge signs
short-lived `private_key_jwt` assertions and exchanges them with `client_credentials`. Persona
access tokens live about 15 minutes and have **no refresh token**. This bridge caches tokens only
in memory, re-mints 90 seconds before expiry, and re-mints/retries **once** after an upstream 401.
The MCP host sees an ordinary stdio server; it does not store or refresh bearer tokens.

## Quickstart

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and ensure `uvx` is on your
host application's PATH. The following `uvx` commands require the package's PyPI release. From
an unpublished checkout, use `uv sync` and replace `uvx persona-machine-mcp` with
`uv run persona-machine-mcp`.

```sh
uvx persona-machine-mcp keygen
```

This creates an EC P-256 keypair under `~/.config/persona-machine-mcp` (or
`$XDG_CONFIG_HOME/persona-machine-mcp`) and prints the **public** PEM. Private key: mode `0600`;
public key: mode `0644`. Never share the private key.

In **Persona Dashboard → API Keys → Machine clients**, create a client and paste the public PEM.
Choose minimal scopes. Use a **dedicated Dashboard user**: the machine client inherits that user's
role, and changes to or deletion of that user can affect access. Then save the exact issued id:

```sh
uvx persona-machine-mcp configure --client-id oac_YOUR_CLIENT_ID
uvx persona-machine-mcp check
```

`check` authenticates, initializes MCP, and prints `OK: <n> tools (<filtered>/<total>)` followed by
the visible tool names. The first number and numerator both count exposed tools after filtering.
No command means `serve`; stdout then carries only MCP protocol messages. All logs go to stderr.

## Limits

- These tokens work with **MCP V2** (`https://withpersona.com/mcp`) and the **Persona CLI API**.
  They are **not documented for general REST `/api/v1` access**. The OAuth token endpoint itself
  is under `/api/v1/oauth/token`; that does not make the resulting token a REST API key.
- Machine clients **cannot approve or decline inquiries via MCP**, regardless of scopes.
- Persona limits machine-client writes to approximately **20 writes/minute**. Handle limits in
  the host; this bridge does not automatically replay write calls except its single HTTP 401 retry.
- Persona roles/scopes remain authoritative. A local tool filter is not server-side authorization.
- No MCP V1/API-key mode, HTTP listener, registration automation, or write-path helpers in v0.1.

## MCP hosts

Complete setup first, and make `uvx` available in each host's execution environment. For a
non-default configuration directory, pass `--home /absolute/path` or set `PERSONA_MCP_HOME`.

### Hermes

```sh
hermes mcp add persona --command uvx --args persona-machine-mcp
hermes mcp test persona
```

Restart/reload the host after registering. See the
[Hermes MCP documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp).

### Claude Code

```sh
claude mcp add persona -- uvx persona-machine-mcp
```

### Claude Desktop / Cursor

Merge into the host's MCP configuration; `uvx` must be discoverable by the GUI process:

```json
{
  "mcpServers": {
    "persona": {
      "command": "uvx",
      "args": ["persona-machine-mcp"]
    }
  }
}
```

### Codex CLI

```sh
codex mcp add persona -- uvx persona-machine-mcp
```

Equivalent `~/.codex/config.toml` entry:

```toml
[mcp_servers.persona]
command = "uvx"
args = ["persona-machine-mcp"]
```

Pin the argument to `persona-machine-mcp==0.1.0` where reproducibility is important.

### Hermes Agent Plugin

The portable Agent Plugins v1 package is **`agent-plugin/`**, not this repository's root. It
bundles the `persona` MCP entry pinned to `0.1.0` and a setup/rotation skill. From a reviewed
checkout, with a fresh destination:

```sh
# HERMES_HOME must point to the intended profile if you use named profiles.
install -d "${HERMES_HOME:-$HOME/.hermes}/plugins"
cp -R ./agent-plugin "${HERMES_HOME:-$HOME/.hermes}/plugins/persona-machine-mcp"
hermes plugins list
hermes plugins enable persona-machine-mcp
```

This documented local-directory installation avoids assuming that `hermes plugins install`
selects a nested plugin automatically. See [plugin setup](agent-plugin/README.md) and the
[Hermes plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins#portable-agent-plugins-v1-packages).
Do not enable both the plugin and the manual `persona` MCP entry; the manual config takes
precedence. This copied installation is updated manually.

## Configuration

Flags may appear before or after the command. Precedence is **flag > environment > home file**.
No shell interpolation is performed on values; quote glob patterns to prevent shell expansion.

| Flag | Environment | Home file / default |
| --- | --- | --- |
| `--home DIR` | `PERSONA_MCP_HOME` | `$XDG_CONFIG_HOME/persona-machine-mcp`, else `~/.config/persona-machine-mcp` |
| `--client-id ID` | `PERSONA_CLIENT_ID` | `client_id`; must start with `oac_` |
| `--key PATH` | `PERSONA_PRIVATE_KEY_PEM` (inline PEM), then `PERSONA_PRIVATE_KEY_PATH` | `private.pem` |
| `--mcp-url URL` | `PERSONA_MCP_URL` | `https://withpersona.com/mcp` |
| `--token-url URL` | `PERSONA_TOKEN_URL` | `https://withpersona.com/api/v1/oauth/token` |
| `--scope "a b"` | `PERSONA_SCOPE` | Unset: client defaults |
| `--tools GLOB` (repeatable) | `PERSONA_MCP_TOOLS` (comma-separated) | All tools |
| `--exclude-tools GLOB` (repeatable) | `PERSONA_MCP_EXCLUDE_TOOLS` (comma-separated) | None excluded |
| `--log-level LEVEL` | `PERSONA_MCP_LOG` | `INFO`; `DEBUG`, `WARNING`, `ERROR`, `CRITICAL` also supported |

Legacy `PERSONA_CLIENT_ID_FILE` overrides the path used for file-based client-id resolution.
The normal path is `<home>/client_id`, **not next to an externally configured key**; when migrating
an older standalone bridge, set this legacy variable explicitly if your id is next to that key.
`configure` always writes `<home>/client_id` with mode `0600`; remove a stale legacy override when
switching to the new home. `PERSONA_PRIVATE_KEY_PEM` wins over the environment path, but never
over `--key`. Files must contain an unencrypted private PEM (PKCS#8 output is generated).
EC must be P-256; RSA must be at least 2048 bits. Other keys are rejected.

### Commands

```sh
# Default command is serve (stdio).
uvx persona-machine-mcp serve

# RSA is optional; default keygen is EC P-256.
uvx persona-machine-mcp keygen --alg rsa --bits 3072 --dir /secure/persona

# Paths and winning sources only: no client-id value, PEM or token.
uvx persona-machine-mcp paths

# Public JWKS. Default kid is the RFC 7638 SHA-256 thumbprint.
uvx persona-machine-mcp jwks
uvx persona-machine-mcp jwks --kid rotation-2

# Intentionally prints a secret to stdout. Do not log or paste the result.
uvx persona-machine-mcp token
uvx persona-machine-mcp token --no-resource
uvx persona-machine-mcp token --resource https://withpersona.com/mcp --scope 'inquiry:read'

uvx persona-machine-mcp --version
uvx persona-machine-mcp --help
```

`token` defaults its resource to the resolved MCP URL. `--no-resource` omits that form field.
With **no client id**, `serve` warns and connects **unauthenticated**, for offline/local testing;
`check` and `token` require credentials. Real Persona will not accept unauthenticated requests.
Bad credentials are rejected eagerly, before the stdio server opens.

### Tool filtering

```sh
uvx persona-machine-mcp serve --tools 'list_inquir*' --tools 'show_*' --exclude-tools '*secret*'
uvx persona-machine-mcp check --tools 'list_inquir*' --tools 'show_*'
```

An allow match is required when an allow-list exists; deny matches win afterward. Environment
forms are comma-separated, for example `PERSONA_MCP_TOOLS='list_inquir*,show_*'`. Filters apply
to `check`, discovery and invocation: calling a hidden tool returns `tool not exposed by this bridge`.
Tool listing follows all upstream cursor pages. Resources/prompts are exposed only when the
upstream advertises those capabilities; they are not affected by tool-name filtering.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success (or clean Ctrl-C shutdown) |
| 1 | Usage/configuration error: missing key, invalid PEM, invalid `oac_` id, file permissions |
| 2 | Token endpoint `invalid_client`: check client id and registered public key |
| 3 | Token endpoint `invalid_grant`: check clock, audience, assertion/registration |
| 4 | Other token endpoint failure: HTTP status, malformed response, network |
| 5 | Upstream MCP connection/initialization or unexpected bridge failure |

Diagnostics omit token endpoint response bodies, assertions and access tokens. `invalid_client`
and `invalid_grant` are classified by structured error fields, never by provider message text.

## Security and rotation

- Keep private keys and the client-id file at `0600`, outside source control. Public PEMs are
  `0644`. Permission guarantees target POSIX; on Windows, also restrict access with filesystem ACLs.
- Never commit keys, copy them into an MCP/plugin manifest, or log the output of `token`. The
  `.gitignore` excludes PEM files, `.env` and `client_id`; that is defense in depth, not a vault.
- Only register **public** PEM/JWKS. The generated JWKS contains no private parameters.
- Use minimal Persona scopes and a dedicated Dashboard user. Treat MCP write calls as real
  operations; the bridge neither adds approvals nor grants permission to act.
- Custom token and MCP endpoints receive assertions/bearers. Use trusted HTTPS endpoints outside
  local tests; do not send real credentials to a diagnostic mock server.
- For in-place rotation: stop the host's bridge, run `keygen --force`, re-register the new public
  key in Persona, save the replacement client id with `configure`, run `check`, then restart.
  **`--force` destroys the previous local key.** Without it, either existing key file blocks keygen.
- For a safer cutover, generate/configure/check a separate `--home` (or `keygen --dir`) and then
  switch the host to that home. Revoke the old client after the replacement works. Protect and
  eventually remove old private keys according to your retention policy.

## Development and release

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run python -X dev -m pytest -q
uv build
uvx --from ./dist/persona_machine_mcp-*.whl persona-machine-mcp --version
```

Tests mint against `httpx.MockTransport` and exercise a real stdio bridge subprocess against a
loopback FastMCP/uvicorn upstream. They need no Persona account or internet service. Dependency
installation itself may require internet access. CI tests Python 3.10–3.14 with the committed
`uv.lock` and builds an sdist and wheel.

Before tagging `v0.1.0`, configure a PyPI trusted publisher for owner **anpicasso**, repository
**persona-machine-mcp**, workflow **publish.yml**, and environment **pypi**. Configure GitHub's
`pypi` environment with required reviewers if approval is desired. A pushed `v*` tag triggers the
publish workflow, verifies the tag equals the package version, runs tests/lint, builds, and
publishes using OIDC (`id-token: write`), without a long-lived PyPI token. Nothing is published by
local build/test commands.

Roadmap: **v0.2 — `--http PORT`**. Not implemented in this release.

Author: [Angello Picasso](https://github.com/anpicasso). [MIT license](LICENSE).
