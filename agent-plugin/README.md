# Persona machine-client Agent Plugin

Portable [Agent Plugins v1](https://agent-plugins.org/) package. Unofficial; not affiliated with
Persona. Provides the `persona` stdio MCP server and a setup/rotation skill. Requires `uvx` on the
host PATH and a released `persona-machine-mcp==0.1.0` on PyPI.

Complete [the package quickstart](../README.md#quickstart) before enabling. Credentials are read
from the normal configuration home; no secrets are stored in these manifests.

## Hermes installation

From a reviewed checkout of this repository, copy **this directory**, not the Python repository
root, into the current Hermes profile's plugin directory:

```sh
# Set HERMES_HOME to the intended profile if not already selected in your shell.
install -d "${HERMES_HOME:-$HOME/.hermes}/plugins"
cp -R ./agent-plugin "${HERMES_HOME:-$HOME/.hermes}/plugins/persona-machine-mcp"
hermes plugins list
hermes plugins enable persona-machine-mcp
```

Use a fresh destination; review an existing installation rather than nesting or overwriting it.
This is a local-directory install, not a claim that the Python repository root is directly
installable with `hermes plugins install owner/repo`. Hermes discovers root `plugin.json` and
`mcp.json` inside the installed plugin directory. See the
[Hermes portable-plugin documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins#portable-agent-plugins-v1-packages).

Do not configure the manual `hermes mcp add persona ...` entry at the same time: a config entry
with that name wins over the plugin. Restart/reload the host after enabling. Plugin updates are
manual for this copied installation. No HTTP listener, native Hermes hooks, or host code changes
are included.
