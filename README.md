# DocAtlas

A local documentation knowledge base for AI agents. It crawls official technical
docs, splits them into small chunks with a full-text index and cross-references,
and hands the agent **just the few passages it needs** — each one carrying the
source URL.

Without it, an AI answering a technical question either makes things up from
memory or dumps a whole page into the context window and runs out of budget.

The repository ships **code only**. Adapters and configs are included for four
sites — Unreal Engine 5.8, cppreference, Blender Manual, Roblox Creator Hub —
but **you crawl the docs yourself** (see below). Adding another site means
writing one adapter; the core stays untouched.

## Install

Python 3.11+, no third-party packages.

```bash
git clone https://github.com/AZURE-HUAI/DocAtlas.git
cd DocAtlas
python install.py
```

`install.py` installs the Skill, registers the MCP server, and actually starts
that server once to confirm it connects — nothing is written to your config if
that check fails. Claude Code and Codex are registered automatically; for other
clients run `python install.py --print` and paste the snippet.

Options: `--data-dir D:/DocAtlasData` puts the databases on another drive,
`--dataset <id>` picks which library is the default.

## Build a library

```bash
DOCATLAS_DATASET=cppreference-2026-07-26 python -m docatlas crawl --discovery-only
```

That enumerates the site's page list (tens of minutes) without downloading
article bodies — **and that is enough to start using it.** The list records
where every page lives, so anything not held locally is fetched on demand.

For a fully local copy, follow up with `crawl --skip-discovery`; it resumes
after any interruption.

There is no built-in default library — pick the one you want with
`DOCATLAS_DATASET`, or make it stick with `python install.py --dataset <id>`.

## Use

Just ask in Claude Code or any MCP client; the agent queries the library and
cites its sources. Or run it yourself: `python -m docatlas ask "std::vector"`.

## More

- [Usage guide](docs/USAGE.md) — every command, crawling, data layout, new datasets
- [Architecture](docs/ARCHITECTURE.md) · [Data contract](docs/DATA_CONTRACT.md) · [AI routing](docs/AI_ROUTING.md)
- [Issue log](issues/README.md) · [Contributing](CONTRIBUTING.md)
