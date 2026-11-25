from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.config import settings
from app.worker.backup import create_backup


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()


def test_backup_creates_consistent_snapshot(tmp_path: Path, monkeypatch: object) -> None:
    db = tmp_path / "fc27.db"
    _make_db(db)
    monkeypatch.setattr(settings, "DB_PATH", str(db))  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "BACKUP_KEEP", 5)  # type: ignore[attr-defined]

    dest = create_backup()

    assert dest is not None
    assert dest.exists()
    conn = sqlite3.connect(dest)
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "hello"
    finally:
        conn.close()


def test_backup_prunes_old_snapshots(tmp_path: Path, monkeypatch: object) -> None:
    db = tmp_path / "fc27.db"
    _make_db(db)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(5):
        old = backup_dir / f"old_{i}.db"
        old.write_text("x", encoding="utf-8")
        os.utime(old, (1000 + i, 1000 + i))

    monkeypatch.setattr(settings, "DB_PATH", str(db))  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "BACKUP_DIR", str(backup_dir))  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "BACKUP_KEEP", 3)  # type: ignore[attr-defined]

    dest = create_backup()
    assert dest is not None
    files = list(backup_dir.glob("*.db"))
    assert len(files) <= 3
