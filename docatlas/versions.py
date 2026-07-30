"""Version applicability, and version intent.

Two **different** questions live here. Merging them guarantees a hardcoded
preference:

  * **Which versions does this content apply to** — a fact the documentation
    states itself. The domain layer (a source adapter or a knowledge pack)
    recognises it in the prose, because how a version is written is each
    site's own typographic convention.
  * **Which versions does the user want** — an intent the core cannot guess.
    The caller decides, and passes it in as a structured condition.

The core does only the generic half: store the facts, compare them, filter by
intent. It does not know which of two version labels is newer — **the sort key
comes from the domain layer**. That is not fastidiousness: numbering schemes
where the older release carries the larger number are common, and any generic
"compare the numbers" rule orders those backwards.

## Three kinds of evidence, of differing strength

    since     "exists only from version X". Hard evidence, and **the only
              kind allowed to exclude**.
    until     "up to version X". Stored for reporting and comparison, but it
              never excludes (see below).
    mentions  the body names some version. Soft evidence; earns a bonus only
              when tracing migrations.

Why `until` never excludes: one chunk legitimately carries several conflicting
marks. A single table can have adjacent rows footnoted `(until X)` and
`(since X)`. Excluding on "up to X" would then delete a whole group of
operations that does exist in the target version. So `until` reports, it does
not rule.

## Where a mark sits decides how far it reaches

    heading   qualifies the whole section — this material does not exist at
              all in earlier versions.
    body      qualifies one row of one table, while the table itself is much
              older.

So **only a `since` in a heading may exclude content**. The two errors are not
symmetric: content from a newer version is visible to the user and can be
judged, whereas content silently withheld cannot be discovered at all. When in
doubt, over-serve.

## Four intents, opposite handling of the same evidence

    strict     exclude content that does not yet exist in the target version.
    migration  marked content is **promoted** instead — "when did it change"
               is the answer the user is after.
    compare    drop nothing; pass the marks through for the caller to arrange.
    any        behaves exactly as if this feature did not exist.

## Never filter without information

When the dataset declares no version vocabulary, or a chunk carries no mark,
treat it as applicable. Withholding official content because "I don't know"
is the kind of error that leaves no trace in the result.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Iterable

from .runtime import active

# Intents. `any` is equivalent to passing nothing.
STRICT = "strict"
MIGRATION = "migration"
COMPARE = "compare"
ANY = "any"
MODES = (STRICT, MIGRATION, COMPARE, ANY)

# Kinds of evidence.
SINCE = "since"
UNTIL = "until"
MENTIONS = "mentions"
MARK_KINDS = (SINCE, UNTIL, MENTIONS)

# How far a mark reaches. In a heading it covers the section, in the body only
# its own line — only the former is hard enough to exclude on.
HEADING = "heading"
BODY = "body"

# Version of the extraction rules. Bump it when the domain layer changes how it
# reads marks; existing libraries then recompute in one pass — otherwise marks
# produced by two different rule sets coexist and the filtered result cannot be
# explained.
MARKS_VERSION = "1"

# Bonuses when tracing migrations. Any version mark says "this passage is about
# a difference between versions", and a mark naming a version other than the
# user's is usually the migration evidence itself.
MIGRATION_MARKED_BONUS = 8.0
MIGRATION_OTHER_VERSION_BONUS = 10.0


@dataclass(frozen=True)
class Mark:
    """One piece of applicability evidence.

    `key` is the comparable key supplied by the domain layer; the core only
    ever compares it, never interprets it.
    """

    kind: str
    label: str
    key: str
    scope: str = BODY

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "version": self.label, "scope": self.scope}


@dataclass(frozen=True)
class Intent:
    """What the user wants, version-wise. Decided upstream, never inferred."""

    mode: str
    target: str = ""
    target_key: str = ""

    @property
    def excludes(self) -> bool:
        """Whether this run really drops content.

        Without a comparable target version there is nothing to exclude on.
        """
        return self.mode == STRICT and bool(self.target_key)


def vocabulary() -> str:
    """What "version" means for this dataset, for readable diagnostics."""
    return str(active().extension("VERSION_VOCABULARY", "") or "")


def supported() -> bool:
    """Whether this dataset can supply verifiable applicability information."""
    return callable(active().extension("version_marks"))


def sort_key(label: str) -> str:
    """Turn a version label into a comparable key.

    Empty when the domain layer does not recognise the label.
    """
    maker = active().extension("version_sort_key")
    if not maker or not label:
        return ""
    return str(maker(label) or "")


def marks_in(heading: str, body: str) -> list[Mark]:
    """Recognise a chunk's version marks, recording where each one sits.

    The domain layer only answers "which version marks are in this text";
    how far a mark reaches is the core's bookkeeping, not something every
    adapter should reimplement. A mark appearing in both places counts as a
    heading — the stronger scope wins.
    """
    reader = active().extension("version_marks")
    if not reader:
        return []
    found: dict[tuple[str, str], Mark] = {}
    for scope, text in ((BODY, body), (HEADING, heading)):
        for kind, label in reader(text or ""):
            if kind not in MARK_KINDS or not label:
                continue
            if key := sort_key(label):
                found[(kind, label)] = Mark(
                    kind=kind, label=label, key=key, scope=scope
                )
    return sorted(found.values(), key=lambda mark: (mark.kind, mark.key))


def parse_intent(mode: str | None, target: str | None) -> Intent | None:
    """Turn a caller's version condition into a structured intent.

    An unrecognised mode must raise rather than be ignored: filtering nothing
    in silence is more dangerous than refusing outright, because the caller
    goes on believing the restriction took effect.
    """
    mode = (mode or "").strip().casefold()
    target = (target or "").strip()
    if not mode and not target:
        return None
    if not mode:
        # A bare target version almost always means "restrict to this one".
        mode = STRICT
    if mode not in MODES:
        raise ValueError(
            f"Unknown version intent {mode!r}. Choose from: {', '.join(MODES)}. "
            f"strict=restrict to that version, migration=trace what changed "
            f"between versions, compare=keep every version, any=no restriction."
        )
    if mode == ANY:
        return None
    return Intent(mode=mode, target=target, target_key=sort_key(target))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def store_marks(
    connection: sqlite3.Connection, chunk_id: int, heading: str, body: str
) -> int:
    """Record a chunk's applicability. Returns how many marks were written."""
    marks = marks_in(heading, body)
    if not marks:
        return 0
    connection.executemany(
        "INSERT OR REPLACE INTO chunk_versions(chunk_id, kind, label, sort_key, scope)"
        " VALUES(?, ?, ?, ?, ?)",
        [
            (chunk_id, mark.kind, mark.label, mark.key, mark.scope)
            for mark in marks
        ],
    )
    return len(marks)


def load_marks(
    connection: sqlite3.Connection, chunk_ids: Iterable[int]
) -> dict[int, list[Mark]]:
    """Load marks in bulk. Unmarked chunks are absent from the result."""
    ids = list(chunk_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    found: dict[int, list[Mark]] = {}
    for row in connection.execute(
        f"SELECT chunk_id, kind, label, sort_key, scope FROM chunk_versions"
        f" WHERE chunk_id IN ({placeholders}) ORDER BY kind, sort_key",
        ids,
    ):
        found.setdefault(row["chunk_id"], []).append(
            Mark(
                kind=row["kind"],
                label=row["label"],
                key=row["sort_key"],
                scope=row["scope"],
            )
        )
    return found


def backfill(connection: sqlite3.Connection) -> int:
    """Add version marks to chunks already crawled, without going online.

    Runs only when the extraction rules changed, or when this library has
    never been processed. Datasets that declare no version vocabulary are
    skipped outright — a library of hundreds of thousands of pages should not
    scan its whole table for a feature it cannot use.
    """
    if not supported():
        return 0
    stored = connection.execute(
        "SELECT value FROM metadata WHERE key='version_marks'"
    ).fetchone()
    if stored and stored[0] == MARKS_VERSION:
        return 0
    connection.execute("DELETE FROM chunk_versions")
    written = 0
    for row in connection.execute(
        "SELECT c.id, p.title, c.heading_path, c.content_text"
        " FROM chunks c JOIN pages p ON p.id=c.page_id"
    ):
        written += store_marks(
            connection,
            row["id"],
            f"{row['title']}\n{row['heading_path']}",
            row["content_text"],
        )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('version_marks', ?)",
        (MARKS_VERSION,),
    )
    connection.commit()
    return written


# ---------------------------------------------------------------------------
# Filtering by intent
# ---------------------------------------------------------------------------


def _excluded_by(marks: list[Mark], target_key: str) -> str:
    """Whether version rules drop this chunk; if so, the label that did it.

    Two gates, both settled by measurement:

    1. **Only a `since` in a heading counts.** A mark in the body qualifies
       only its own line, so excluding the whole chunk on it hides material
       that has been there all along.
    2. **Only the earliest one counts.** The chunk is genuinely absent from
       the target version only when even the earliest heading `since` is
       later than it.
    """
    since = [
        mark for mark in marks if mark.kind == SINCE and mark.scope == HEADING
    ]
    if not since:
        return ""
    earliest = min(since, key=lambda mark: mark.key)
    return earliest.label if earliest.key > target_key else ""


def _migration_bonus(marks: list[Mark], target_key: str) -> float:
    """How much to promote this chunk when tracing a migration.

    Any mark means "this passage is about a difference between versions", and
    a mark naming a version other than the user's is usually the "it used to
    be like this, now it is like that" evidence itself.
    """
    if not marks:
        return 0.0
    bonus = MIGRATION_MARKED_BONUS
    if target_key and any(mark.key != target_key for mark in marks):
        bonus += MIGRATION_OTHER_VERSION_BONUS
    return bonus


def apply(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    intent: Intent | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply a version intent to search results, and say what was done.

    Returns `(kept rows, report)`. The report must be able to answer "why is
    this result here / missing" — quietly returning fewer results is harder to
    notice than an outright error.
    """
    if intent is None:
        return rows, {}
    report: dict[str, Any] = {
        "mode": intent.mode,
        "target": intent.target,
        "dataset_supports_versions": supported(),
        "excluded": 0,
        "excluded_examples": [],
    }
    if vocab := vocabulary():
        report["vocabulary"] = vocab
    if not supported():
        report["note"] = (
            "This library declares no version vocabulary, so applicability "
            "cannot be checked. No version filtering was applied and every "
            "result is returned as usual."
        )
        return rows, report
    if intent.target and not intent.target_key:
        report["note"] = (
            f"This library does not recognise the version {intent.target!r}, "
            "so it cannot be compared and no version filtering was applied. "
            "Try again using the library's own way of writing versions."
        )
        return rows, report

    marks_by_chunk = load_marks(connection, [row["id"] for row in rows])
    kept: list[dict[str, Any]] = []
    for row in rows:
        marks = marks_by_chunk.get(row["id"], [])
        if marks:
            row["applies_to"] = [mark.as_dict() for mark in marks]
        if intent.excludes and (label := _excluded_by(marks, intent.target_key)):
            report["excluded"] += 1
            if len(report["excluded_examples"]) < 5:
                report["excluded_examples"].append(
                    {
                        "knowledge_id": f"K{row['id']}",
                        "title": row["page_title"],
                        "reason": f"added in {label}, absent from {intent.target}",
                    }
                )
            continue
        if intent.mode == MIGRATION:
            row["score"] = round(
                row["score"] + _migration_bonus(marks, intent.target_key), 2
            )
        kept.append(row)
    if intent.mode == MIGRATION:
        kept.sort(key=lambda item: -item["score"])
    return kept, report


def describe(report: dict[str, Any]) -> list[str]:
    """Write up what the version pass did, for humans and agents alike."""
    if not report:
        return []
    if note := report.get("note"):
        return [note]
    mode = report["mode"]
    target = report.get("target") or "(unspecified)"
    if mode == STRICT:
        if not report["excluded"]:
            return [
                f"Checked applicability against {target}; nothing was excluded."
            ]
        lines = [
            f"Strict {target}: {report['excluded']} item(s) absent from {target}"
            f" were excluded, having been introduced later."
        ]
        lines.extend(
            f"  excluded: {item['title']} ({item['reason']})"
            for item in report["excluded_examples"]
        )
        lines.append(
            "To see how the newer versions do it, ask again with the version "
            "intent set to compare."
        )
        return lines
    if mode == MIGRATION:
        return [
            "Ordered for migration tracing: content that states a difference "
            "between versions has been promoted — \"when did it change, what "
            "was it before\" is the answer such questions want."
        ]
    return [
        "Kept every version, each result carrying its own applicability, "
        "so they can be compared side by side."
    ]
