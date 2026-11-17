---
name: persona-machine-mcp
description: Use when setting up, rotating, or troubleshooting Persona MCP V2 machine-client access. Keep private keys local, verify authentication with check, and restrict tools and scopes.
---

# Persona machine-client MCP

This unofficial bridge is not affiliated with Persona. It connects a stdio MCP host to Persona
MCP V2 without a browser login and owns the short-lived access-token lifecycle.

## Setup

1. Install `uv` and make `uvx` available to the host process. Use Python 3.10 or newer.
2. Run `uvx persona-machine-mcp keygen`. It prints only the public PEM to stdout and stores the
   private key with mode 0600 under `$XDG_CONFIG_HOME/persona-machine-mcp` (or
   `~/.config/persona-machine-mcp`). Do not paste the private key into chat or the Dashboard.
3. In Persona Dashboard → API Keys → Machine clients, register the public PEM. Select minimal
   scopes. Use a dedicated Dashboard user because the client inherits that user's role and
   lifecycle; deletion or permission changes can remove access.
4. Run `uvx persona-machine-mcp configure --client-id oac_...` with the exact issued id.
5. Run `uvx persona-machine-mcp check`. Require a successful initialization and visible tools.
6. Enable this plugin in the host only after setup. Do not also register another `persona` MCP
   server under the same name. Host restart/reload may be required.

## Rotation

Prefer generating keys in a separate directory with `keygen --dir <new-home>`, registering that
public key, configuring and checking the new home, then switching the host's `PERSONA_MCP_HOME`.
Revoke the old machine client after the replacement works. For an intentional in-place rotation,
stop the bridge, run `keygen --force`, re-register the new public key in Persona, run `configure`
with the replacement id, run `check`, and restart the host. Never overwrite a working key before
planning the cutover; it cannot authenticate once its matching registration is lost.

## Troubleshooting and boundaries

- `paths` reports winning configuration sources and paths, never key/token/id contents.
- Exit 1: configuration/key/id; 2: `invalid_client` (registration/id/key mismatch);
  3: `invalid_grant` (check clock/audience/assertion); 4: other token/network error;
  5: upstream MCP connection or initialization.
- Never set a static Authorization bearer on the host: tokens live about 15 minutes and have
  no refresh token. The bridge re-mints 90 seconds early and retries a 401 exactly once.
- Machine-client tokens work with MCP V2 and Persona CLI API, not documented REST `/api/v1`
  access. MCP cannot approve/decline inquiries; write operations are limited to about 20/min.
- Use `PERSONA_MCP_TOOLS` / `PERSONA_MCP_EXCLUDE_TOOLS` comma-separated globs (deny wins).
  Filters are not a substitute for server-side scopes or authorization for individual writes.
- With no client id, `serve` warns and proxies unauthenticated for local testing. `check` and
  `token` require a client id. An unauthenticated real Persona connection will fail.
- No private keys, client assertions or bearer tokens in source control, manifests or logs.
  `token` intentionally prints a secret to stdout; never use it in a shared terminal transcript.
- The host transports MCP over stdout. All bridge logs must stay on stderr.
- Custom token/MCP URLs receive credentials; use trusted HTTPS endpoints outside local tests.
