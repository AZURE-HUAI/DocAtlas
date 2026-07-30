# Building and maintaining a DocAtlas library

Program location: `{{DOCATLAS_ROOT}}`. Run every command from there.

## Where to go

**Start here when you do not know the state of the machine:**

```powershell
python -m docatlas doctor
```

Every library, what each holds, and the one command that moves it forward. Add
`--json` to read it as data. It answers even when no library has been chosen
yet, which is the state every other command refuses to run in.

| Situation | Section |
|---|---|
| New version of the same site | [New version](#new-version) |
| Adding a new site | [Adding a site](#adding-a-site) |
| An existing library is missing pages | [Widening the scope](#widening-the-scope) |
| Chunking or relation rules changed | [Reprocessing](#reprocessing) |
| Want to know the state of a library | [Health check](#health-check) |
| A library was just built, or the project changed | [Reinstalling](#reinstalling) |

## Principles

1. For a new site, sample 20 pages per category and accept that before going
   full.
2. Run `validate` at every step and **read the actual numbers**, not just
   whether the command errored.
3. A new version gets a new dataset id; never copy the old database.
4. Report only measured results; leave failures and coverage gaps visible.

## New version

For the same site at a different version number.

```powershell
Copy-Item datasets/<existing id>.toml datasets/<new id>.toml
$env:DOCATLAS_DATASET='<new id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
```

In the new TOML change only the dataset identity, the version number, and the
source addresses that depend on it. Once the inventory exists, queries work on
demand; run a full `crawl` only when a complete offline copy is asked for.

An empty inventory, or a page count clearly too low, means the source rules do
not match — investigate via [Adding a site](#adding-a-site).

## Adding a site

Start from the template: `datasets/EXAMPLE.toml` and
`docatlas/sources/example.py` are a working pair for an invented site, annotated
field by field and function by function. Copy both and replace their answers.

First establish where the site's page inventory comes from, what format the
bodies are in, what a canonical URL looks like, and how language and version are
marked. Then add three things:

- `datasets/<id>.toml`
- `docatlas/sources/<source>.py`
- tests for that adapter

Datasets and adapters you write are ignored by git, so your libraries stay on
your machine. To contribute one upstream, add it explicitly with `git add -f`.

The adapter must implement:

| Capability | Interface |
|---|---|
| Enumerate pages | the sitemap interface, or `inventory_feeds` + `read_feed` |
| Canonical URLs | `normalize_location`, `canonical_url`, `document_request_url` |
| Bodies and links | `parse_document`, `normalize_link_target` |
| Scope | `is_official_url` |
| Entity placement | `entity_placement` |
| Summaries | take `parse_document`'s `description` from `htmlmd.lead_sentence(markdown)`; do not write your own |

Implement as needed: `document_locale`, `page_members`, `version_marks`,
`version_sort_key`, `categorize_path`.

The TOML declares at minimum the dataset identity, language, source adapter,
categories, entity types and triggers.

Three things that are easy to get wrong:

- "Is this an official address" and "does this belong to this dataset" are two
  different questions and cannot share one test.
- In-body links to the same site must be normalised to canonical addresses at a
  fixed version, or relations will not line up with the inventory.
- Members listed in a type page's tables can be promoted to entities of their
  own via `page_members`.

### Domain relations (optional)

Generic official-link and page-ownership relations come for free. Add
`docatlas/knowledge/<name>.py` only where a relation depends on semantics
specific to that product:

```python
def relation_rules(graph):
    for source, target, _ in graph.name_matches("<type A>", "<type B>"):
        yield RelationCandidate(source=source, target=target,
                                relation_type="<relation>",
                                evidence_kind="exact_name", confidence=0.9)
```

A knowledge pack only produces candidates; it writes no SQL and never touches
database ids. Verification, deduplication and storage are the generic core's
job. Without a pack the generic relations still work. Create a relation only
when both entities and the evidence genuinely exist.

## Widening the scope

First establish which kind of gap it is:

| Symptom | Handling |
|---|---|
| In the inventory, body not fetched | `ask` / `get` fetches on demand; not a gap |
| A collected body links to it, the inventory does not have it | set `[inventory].referenced_category`; the reference closure collects it |
| The site never links to it | the closure cannot reach it; declare the directory in the dataset |

Before concluding it is the third kind, **confirm with real numbers** that the
directory really is never referenced. Do not widen the scope on a hunch.

Declare the smallest scope that works. One category may list several
directories, to collect a handful of pages that are scattered but belong
together — not to sweep in a whole parent directory:

```toml
[categories]
<category> = "<directory prefix>/"
<another category> = ["<directory A>", "<directory B>"]
```

The category named by `referenced_category` must **not** also appear in
`[categories]`. That table enumerates "category → path prefix" rules, whereas
the reference closure collects exactly the pages outside the declared
directories, so there is no prefix to write; an empty string makes the prefix
match everything and files the whole library under that one category. All it
needs is a display name in `[category_labels]` to be a full category that can be
filtered, sampled and exported.

**After changing the scope, rerun discovery with `--refresh-sitemaps`**, or
inventory entry points that already succeeded will not be reread:

```powershell
python -m docatlas crawl --discovery-only --refresh-sitemaps
```

## Sample acceptance

After a new site or a large change, sample before going full.

```powershell
$env:DOCATLAS_DATASET='<id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
python -m docatlas crawl --skip-discovery --sample-per-category 20
python -m docatlas validate --phase content
python -m docatlas ask "<an official term known to exist>" --token-budget 1500
```

Check each of: bodies, titles, source addresses, categories, language,
relations, and the missing-page report.

A new site must also be verified to route by `dataset_id` over the same MCP
connection — adding a site should never require changing MCP or the generic
relation core.

## Reprocessing

Reprocess from the raw documents already stored: no network, resumable.

| What changed | What to run |
|---|---|
| Chunking rules | bump `CHUNKER_VERSION`, then `python -m docatlas reprocess` |
| Relation rules only | `python -m docatlas cross-index` |

Follow either with `python -m docatlas validate --phase content`, looking in
particular at `relation_evidence_coverage` and `inventory_link_coverage`.

`reprocess` handles only pages not yet on the current rule version, so rerunning
after an interruption resumes. Add `--force` to redo everything.

## Health check

```powershell
python -m docatlas validate --phase content
```

`validate` reporting a chunker version mismatch means the program was updated
while the local library is still chunked by the old rules; one
`python -m docatlas reprocess` fixes it.

## Reinstalling

The installed Skill is a **snapshot**: it names one library and quotes this
manual as they both stood when the installer last ran. Nothing updates it in the
background, so rerun the installer after any of these:

- a library was just built, or a different one should become the default
- `git pull` brought in changes to this manual or `SKILL.md`
- the project moved or was renamed

```powershell
python install.py
```

`python -m docatlas doctor` reports the Skill as out of date when this is due —
it compares the installed copy against a fresh rendering, so it catches both a
changed default library and a moved-on repository.

After a `git pull`, also run `python -m docatlas validate --phase content`: when
the chunking rules changed, stored chunks are still on the old ones and
[Reprocessing](#reprocessing) applies.

The script **detects which supported clients this machine has** and writes the
skill copy and MCP configuration only into those, then checks itself. Which ones
it reached is whatever its output says — do not assume a client is present, and
do not copy files into a client directory by hand.

The skill copy and the MCP configuration contain the repository's real path and
are both generated by the script. **Never write those paths by hand.**

Common switches: `--dataset <id>` sets the default dataset, `--data-dir <path>`
the data location, `--print` prints the MCP snippet without changing any file.
