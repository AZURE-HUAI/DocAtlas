# DocAtlas

[![tests](https://github.com/AZURE-HUAI/DocAtlas/actions/workflows/tests.yml/badge.svg)](https://github.com/AZURE-HUAI/DocAtlas/actions/workflows/tests.yml)

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

Options: `--dataset <id>` picks which library is the default, and `--data-dir
D:/DocAtlasData` puts the crawled libraries somewhere other than `data/` inside
this folder. The second is worth a thought before you crawl rather than after: a
large documentation set runs to hundreds of megabytes, several of them to a few
gigabytes. Changing your mind later is safe — the installer stops rather than
repoint the directory out from under libraries already built, and `--move-data`
takes them along.

If you keep a client's configuration somewhere other than the default —
`CLAUDE_CONFIG_DIR` for Claude Code, `CODEX_HOME` for Codex — DocAtlas follows
it. Every run prints the directory it resolved for each one, because a Skill
written where the client does not read it looks exactly like a successful
install from both ends.

**This step wires up your agent and collects no documentation.** None ships
here, and which docs to collect is your call — that is the next section.

To ask what state this machine is in and what to do next, at any point:

```bash
python -m docatlas doctor
```

It lists every library, what each one holds, and the single command that moves
it forward; `--json` gives the same report machine-readably. It is the one
command that still answers before anything is set up.

### Uninstall

```bash
python install.py --uninstall
```

Lists every Skill copy, MCP entry and settings file it finds and removes
nothing; add `--yes` to go ahead. Crawled libraries are kept unless you also
pass `--purge-data` — that is the part that took hours. The repository itself is
left alone; delete the folder to finish.

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

```powershell
$env:DOCATLAS_DATASET='<dataset-id>'; python -m docatlas crawl --discovery-only
```

That takes tens of minutes and downloads no article bodies — **and that is
enough to start using it.** The list records where every page lives, so anything
not held locally is fetched on demand.

For a fully local copy, follow up with `crawl --skip-discovery`; it resumes
after any interruption.

There is no built-in default library. Choose one per command as above, or make
it stick with `python install.py --dataset <id>`.

### Then tell your agent about it

The Skill is a **snapshot** taken when the installer ran: it names one library
and quotes this manual as they both stood at that moment. Building a library
does not update it. So once yours exists, run the installer again:

```bash
python install.py --dataset <dataset-id>
```

The same applies after `git pull`. `python -m docatlas doctor` reports the Skill
as out of date whenever this is due, so you do not have to remember.

## Use

Just ask in Claude Code or any MCP client; the agent queries the library and
cites its sources. Or run it yourself:

```bash
python -m docatlas ask "<an official term from your docs>"
```

## More

- `python -m docatlas doctor` — what is installed, what state each library is
  in, what to do next
- `python -m docatlas --help` — every command and its options
- [skills/docatlas/WORKFLOWS.md](skills/docatlas/WORKFLOWS.md) — building a
  library, adding a site, reprocessing, health checks
- [Issue log](issues/README.md) — past bug and enhancement records (written in
  Chinese)

## Licence

Copyright (C) 2026 AZURE-HUAI. [GNU AGPL v3](LICENSE) or later.

Use it, sell it, build on it. The one condition: if you hand a modified version
to anyone — shipped as a copy **or reachable over a network** — the source of
your version has to be available to them too. Running it privately, modified or
not, carries no obligation.

Site adapters are loaded into the program and count as part of it, so they carry
the same terms. An AI client that connects over MCP does not — that is two
programs talking across a protocol, and your agent is unaffected.

The documentation you crawl stays under whatever terms its publisher sets; this
licence covers the code here, nothing it fetches.
