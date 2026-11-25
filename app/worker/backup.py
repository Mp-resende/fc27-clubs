"""Durable backups of the accumulated SQLite database.

Strategy
--------
The dataset is built incrementally and can never be re-fetched from the EA
(the endpoint only exposes the 10 most recent friendlies). Therefore the local
database is the only copy of the history and must be protected:

* a consistent snapshot is created with SQLite's online backup API, which is
  safe to run while the worker is writing;
* snapshots are stored under ``BACKUP_DIR`` and pruned to ``BACKUP_KEEP`` files;
* the scheduler runs one backup at startup and then every
  ``BACKUP_INTERVAL_SECONDS``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def _prune_backups(backup_dir: Path, keep: int) -> None:
    if keep <= 0:
        return
    files = sorted(
        backup_dir.glob("*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError as exc:  # pragma: no cover - best effort cleanup
            log.warning("backup.prune_failed", path=str(old), error=str(exc))


def create_backup() -> Path | None:
    """Create a consistent snapshot of the database. Never raises."""
    db_path = Path(settings.DB_PATH)
    if not db_path.exists():
        log.warning("backup.db_missing", path=str(db_path))
        return None

    backup_dir = Path(settings.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{db_path.stem}_{stamp}{db_path.suffix}"

    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        log.info("backup.created", path=str(dest))
    except Exception as exc:  # noqa: BLE001 - backup must never kill the worker
        log.error("backup.failed", error=str(exc))
        return None
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()

    _prune_backups(backup_dir, settings.BACKUP_KEEP)
    return dest
