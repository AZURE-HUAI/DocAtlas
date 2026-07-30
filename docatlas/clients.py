"""Where each AI client keeps its configuration on this machine.

Both supported clients let the user move their whole configuration elsewhere,
and the switch is an environment variable — nothing on disk reveals it. Writing
to the default location regardless is the worst kind of wrong: the Skill lands
in a directory nobody reads, the installer reports success, and the client goes
on knowing nothing about DocAtlas. Neither side ever raises an error.

So the locations are resolved here, once, and every other module asks.

Both overrides were confirmed against the clients themselves rather than taken
from documentation:

- `CLAUDE_CONFIG_DIR` appears in the installed Claude Code executable, and
  running it with the variable set puts `.claude.json` **inside** that
  directory — note the asymmetry, since by default that file is a sibling of
  `~/.claude` rather than a child of it.
- `CODEX_HOME` is documented by OpenAI: Codex stores its local state there and
  reads `$CODEX_HOME/config.toml`, defaulting to `~/.codex`.
"""

from __future__ import annotations

import os
from pathlib import Path

# client -> (environment variable that moves it, location when it is unset)
_HOMES = {
    "claude-code": ("CLAUDE_CONFIG_DIR", Path.home() / ".claude"),
    "codex": ("CODEX_HOME", Path.home() / ".codex"),
}

NAMES = tuple(_HOMES)


def override(client: str) -> str | None:
    """The environment variable moving this client, when one is set."""
    variable, _ = _HOMES[client]
    return variable if os.environ.get(variable) else None


def home(client: str) -> Path:
    """The client's configuration directory, honouring its override."""
    variable, default = _HOMES[client]
    moved = os.environ.get(variable)
    return Path(moved).expanduser() if moved else default


def skill_dir(client: str, skill: str) -> Path:
    return home(client) / "skills" / skill


def mcp_config(client: str) -> Path:
    """The file holding this client's MCP server list.

    Claude Code is the awkward one. Unset, its config is `~/.claude.json`,
    beside the `~/.claude` directory rather than in it; set, the file moves
    inside the override. Anything that reads the default path once and appends
    a filename gets this wrong.
    """
    if client == "claude-code":
        moved = os.environ.get("CLAUDE_CONFIG_DIR")
        if moved:
            return Path(moved).expanduser() / ".claude.json"
        return Path.home() / ".claude.json"
    return home(client) / "config.toml"


def describe(client: str) -> str:
    """One line naming where this client is and why, for install and doctor
    output. Silent guessing is what this module exists to prevent, so the answer
    is always shown rather than only shown when it is surprising."""
    moved = override(client)
    return f"{home(client)}" + (f"  (via ${moved})" if moved else "")
