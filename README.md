# DocAtlas

A local documentation knowledge base for AI agents. It crawls official technical
docs, splits them into small chunks with a full-text index and cross-references,
and hands the agent **just the few passages it needs** — each one carrying the
source URL.

Without it, an AI answering a technical question either makes things up from
memory or dumps a whole page into the context window and runs out of budget.

The repository ships **code only** — no documentation, and no opinion about
which docs you should collect. What comes with it is one worked example: a
template dataset and the template adapter it names, both annotated line by line,
for an invented site. Point DocAtlas at whatever documentation you actually use;
adding a site means writing one adapter, and the core stays untouched. You crawl
the docs yourself (see below) — no content is distributed here.

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

A library starts as a dataset: one toml in `datasets/`, naming the site, its
version, and the adapter that understands it. Copy
[datasets/EXAMPLE.toml](datasets/EXAMPLE.toml) and
[docatlas/sources/example.py](docatlas/sources/example.py) and work from there —
between them they explain every field and every function, and
[WORKFLOWS.md](skills/docatlas/WORKFLOWS.md) walks the process.

The dataset id is that toml's filename. With one in place, enumerate the site's
page list:

```bash
DOCATLAS_DATASET=<dataset-id> python -m docatlas crawl --discovery-only
```

That takes tens of minutes and downloads no article bodies — **and that is
enough to start using it.** The list records where every page lives, so anything
not held locally is fetched on demand.

For a fully local copy, follow up with `crawl --skip-discovery`; it resumes
after any interruption.

There is no built-in default library. Choose one per command with
`DOCATLAS_DATASET`, or make it stick with `python install.py --dataset <id>`.

## Use

Just ask in Claude Code or any MCP client; the agent queries the library and
cites its sources. Or run it yourself:

```bash
python -m docatlas ask "<an official term from your docs>"
```

## More

- `python -m docatlas --help` — every command and its options
- [skills/docatlas/WORKFLOWS.md](skills/docatlas/WORKFLOWS.md) — building a
  library, adding a site, reprocessing, health checks
- [Issue log](issues/README.md) — past bug and enhancement records (written in
  Chinese)
