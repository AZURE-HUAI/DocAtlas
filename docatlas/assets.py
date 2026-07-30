"""Download images referenced by page bodies."""

from __future__ import annotations

import concurrent.futures
import hashlib
from pathlib import Path
import sqlite3
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .constants import IMAGE_EXTENSIONS
from .runtime import active, bind
from .util import log, utc_now
from .net import fetch_bytes


def asset_local_path(url: str, content_type: str | None = None) -> Path:
    parsed = urllib.parse.urlsplit(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        content_suffixes = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
            "image/avif": ".avif",
        }
        suffix = content_suffixes.get((content_type or "").split(";")[0], ".bin")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return active().asset_dir / digest[:2] / f"{digest}{suffix}"


def fetch_asset(row: sqlite3.Row) -> dict[str, Any]:
    try:
        body, _, content_type = fetch_bytes(
            row["url"], timeout=180, retries=5, delay=0.05
        )
        path = asset_local_path(row["url"], content_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(body)
        temporary.replace(path)
        return {
            "ok": True,
            "id": row["id"],
            "local_path": path.relative_to(active().data_dir).as_posix(),
            "content_type": content_type,
            "bytes": len(body),
        }
    except Exception as exc:
        return {
            "ok": False,
            "id": row["id"],
            "error": f"{type(exc).__name__}: {exc}",
        }


def download_assets(
    connection: sqlite3.Connection, *, workers: int, max_assets: int
) -> None:
    total = connection.execute(
        "SELECT COUNT(*) FROM assets WHERE status IN ('pending', 'failed') AND attempts < 6"
    ).fetchone()[0]
    if max_assets:
        total = min(total, max_assets)
    log(f"Downloading referenced images, {total:,} target(s)")
    processed = 0
    uncommitted = 0
    commit_every = max(workers * 10, 50)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        while processed < total:
            rows = list(
                connection.execute(
                    """
                    SELECT id, url FROM assets
                    WHERE status IN ('pending', 'failed') AND attempts < 6
                    ORDER BY id LIMIT ?
                    """,
                    (min(max(workers * 40, 400), total - processed),),
                )
            )
            if not rows:
                break
            for result in executor.map(bind(fetch_asset), rows):
                processed += 1
                if result["ok"]:
                    connection.execute(
                        """
                        UPDATE assets SET status='success', local_path=?,
                            content_type=?, bytes=?, attempts=attempts+1,
                            error=NULL, fetched_at=? WHERE id=?
                        """,
                        (
                            result["local_path"],
                            result["content_type"],
                            result["bytes"],
                            utc_now(),
                            result["id"],
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE assets SET status='failed', attempts=attempts+1,
                            error=?, fetched_at=? WHERE id=?
                        """,
                        (result["error"][:2000], utc_now(), result["id"]),
                    )
                uncommitted += 1
                if uncommitted >= commit_every:
                    connection.commit()
                    uncommitted = 0
                if processed % 200 == 0 or processed == total:
                    log(f"Images {processed:,}/{total:,}")
            connection.commit()
            uncommitted = 0
