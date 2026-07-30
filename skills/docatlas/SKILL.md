---
name: docatlas
description: Query and maintain the local DocAtlas knowledge base of official documentation. One MCP server serves every installed dataset; the default recorded at install time is "{{DATASET_NAME}}". Search the local library first for anything the documentation covers; read WORKFLOWS.md to build, extend or health-check a library. Triggers - {{DATASET_TRIGGERS}}.
---

# DocAtlas

A local knowledge base of official documentation. Fetched bodies are chunked and
indexed, searched by official terminology, and every result carries the URL it
came from.

**Libraries differ in how complete they are.** Some hold only a page inventory
with no bodies yet, others part of the bodies. `docatlas_list_datasets` reports
the real progress of each. A result carrying `pages_not_fetched` means the
inventory has the page but its body has not been fetched; follow `next_steps`.

Program location: `{{DOCATLAS_ROOT}}`.

## When to use it

- The question falls inside an installed library → **search DocAtlas first**.
- Official wording, exact parameters, version differences, or how pages relate.
- Building, upgrading, widening or health-checking a library → read
  `WORKFLOWS.md`.

When DocAtlas finds nothing you may look it up elsewhere or say you are unsure,
but **never present something written from memory as a quote from the official
documentation** (see [When nothing is found](#when-nothing-is-found)).

## What it can do

| Need | MCP tool | Command line |
|---|---|---|
| Material for an answer | `docatlas_ask` | `python -m docatlas ask` |
| List matching titles | `docatlas_search` | `python -m docatlas search` |
| Expand one result | `docatlas_show` | `python -m docatlas show` |
| Entity relations | `docatlas_related` | `python -m docatlas related` |
| Datasets and capabilities | `docatlas_list_datasets` | `python -m docatlas paths` (ids and paths only) |

**Prefer MCP**; use the command line only where MCP is unavailable. Available
here: {{DOCATLAS_MCP_TOOLS}}.

## Choosing a dataset

One MCP server serves every dataset, and every call may name a `dataset_id`.

- **Unsure which one → call `docatlas_list_datasets` first** and go by what it
  returns: each library's id, language, categories, capabilities and progress.
- Do not assume a given library exists, and do not treat the default recorded at
  install time as the only option.
- The next line is a snapshot **from install time**: configured datasets are
  {{DOCATLAS_DATASETS}}, the default is `{{DATASET_ID}}`, and its source language
  is {{DATASET_LANGUAGE}}. Datasets added or removed since then make it stale, so
  the live answer from `docatlas_list_datasets` wins.

Switching dataset on the command line:

```powershell
$env:DOCATLAS_DATASET='<dataset_id>'; python -m docatlas ask "<official term>"
```

## Using the tools

### docatlas_ask

The default entry point; returns material that can be quoted directly.

- `token_budget`: 1500 for a simple question, 3000 in general, 6000 to read
  through one page. A larger budget goes deeper into the same page.
- `no_fetch=true`: local content only, never fetch.
- `format="json"`: for stable fields; the default is Markdown.

### docatlas_search

Lists titles and keyword matches, to settle "does this name exist in the
library at all".

Each result carries `match_stage`, saying how it matched: `entity` means the
library holds a page by exactly that name, every other stage is a keyword
neighbour. **This is what tells same-named concepts apart** — only the `entity`
result is the official page of that name, and it should not be recounted
alongside the others. `docatlas_ask` does not return this field.

### docatlas_show

Expands a knowledge ID (`K` followed by digits) to its full content. IDs come
from `ask` or `search`.

### docatlas_related

Relations between entities, each with direction, evidence, confidence and
source.

### docatlas_list_datasets

Every dataset on this machine and what it can do. Call it whenever the library,
the categories, or support for version filtering is in doubt.

## Querying

**Query in the language the documentation itself is written in.** Each library
reports its own source language (`{{DATASET_LANGUAGE}}` for the default one);
`docatlas_list_datasets` gives it per library. A question asked in another
language has to be turned into the official terms as that documentation spells
them before searching — querying in the wrong language mostly returns
`language_mismatch`. Explain the results back in the user's own language.

**Use the official term itself; do not pad it with generic words.** Adding
`node`, `function` or `class` — words the whole library is full of — only lets
long unrelated pages accumulate keyword hits. What actually narrows a search is
a more complete official name. When results are wrong, retry with a more
accurate term rather than piling words on.

**`category` filters, it does not hint.** Passing it declares "nothing from any
other category". Category values may only come from `docatlas_list_datasets`,
never guessed from directory names. When in doubt leave it out: not filtering
costs a few extra results, whereas filtering wrongly makes the most relevant
page vanish with nothing in the response to show it was dropped.

**Pass the address when the page is already known.** `query` accepts an official
URL or a path inside the library, and a `#section` narrows it further:

```text
docatlas_ask(query="https://docs.example.com/guide/widgets")
docatlas_ask(query="/guide/widgets#configuration")
```

After passing a `#section`, read `fragment_intent` back: `matched=false` means
the page has no such section and the whole page was returned instead. Search
again using a heading the page really has; **do not recount it as the answer to
that section**.

**Version intent**: pass one only where `docatlas_list_datasets` declares
content versions are supported. Four values of `version_mode` — `strict` limits
to the target version, `migration` traces what replaced an older feature
(keeping the older evidence), `compare` compares versions without excluding
anything, `any` does not restrict. A dataset's own version number identifies the
material selected; it is not the same as versions the bodies can be filtered by.

## Relations

`confidence=1.0` may be recounted as official fact; **anything below 1.0 must be
described as inferred or a candidate**, never as an official conclusion. Targets
that are generated and have no official page of their own are search aliases,
nothing more.

When the result is `entity_found_but_no_relations` and `next_steps` names an
unfetched path in the same library, pass that path verbatim as `query` to
`docatlas_ask` under the same `dataset_id` to fetch it, then retry
`docatlas_related`. For off-site targets, weak candidates or a failed fetch,
stop and state the boundary rather than retrying or widening the source scope.

## When nothing is found

Read `status` and `next_steps` first; steps that are safe and actionable may be
carried out directly.

| Status | Meaning |
|---|---|
| `pages_not_fetched` | in the inventory, body not fetched yet |
| `candidates_too_weak` | candidates exist, too weak to fetch safely |
| `language_mismatch` | the query's language differs from the library's |
| `no_match` / `entity_not_found` | nothing matched this query |
| `entity_found_but_no_relations` | the entity exists but has no relations |
| `target_outside_inventory` | the official page exists, outside the collected scope |
| `knowledge_id_not_found` | the knowledge ID is invalid or stale |

**"Not in this dataset" ≠ "not in the official docs".** What is collected is
decided by the dataset configuration, and DocAtlas never checks the live site.
An empty result can only be reported as "not collected in this dataset, or not
found". Where the user confirms the official site does have the page, this is a
question of scope and rewording the query will not help — see `WORKFLOWS.md`.

**Under no circumstances fill in official content from memory.**

## Citing

- Every answer carries the source URL DocAtlas returned; **never assemble an
  address yourself**.
- Default order: `ask` → `show` if needed → `related` if needed.
- Do not read the database, inventory files or export shards directly.
- Explain results in the user's language, keeping code symbols, version numbers
  and proper nouns spelled as the source spells them.
