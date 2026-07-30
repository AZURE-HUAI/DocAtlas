"""MCP server entry point that works from any working directory.

DocAtlas is not installed into the Python environment; it is a plain folder, so
`python -m docatlas` only resolves from the repository root. MCP clients each
pick their own working directory for the subprocess, and not all of them support
a `cwd` field: Claude Code's stdio config takes only `command`, `args` and `env`
(and `claude mcp add` has no `--cwd`), so a `cwd` entry is silently ignored and
the failure surfaces as `No module named docatlas`, which does not look like a
configuration problem.

Using this file as the entry point sidesteps all of that. Python puts the
**script's own directory** on `sys.path[0]`, which here is the repository root,
so `import docatlas` resolves from anywhere. The client config only needs this
file's absolute path:

    {"command": "python", "args": ["C:/path/to/DocAtlas/mcp_server.py"]}

No cwd, no PYTHONPATH, no pip install.
"""

from docatlas.mcpserver import serve

if __name__ == "__main__":
    raise SystemExit(serve())
