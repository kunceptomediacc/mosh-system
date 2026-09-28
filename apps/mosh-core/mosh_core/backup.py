from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(database: str | Path, output_dir: str | Path) -> dict:
    source, destination = Path(database).resolve(), Path(output_dir).resolve()
    if not source.is_file():
        raise ValueError("source database does not exist")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = destination / f"mosh-{stamp}.db"
    live, copy = sqlite3.connect(source), sqlite3.connect(backup)
    try:
        live.backup(copy)
    finally:
        copy.close()
        live.close()
    check = sqlite3.connect(backup)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = check.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    finally:
        check.close()
    if integrity != "ok":
        backup.unlink(missing_ok=True)
        raise ValueError("backup integrity check failed")
    manifest = {
        "backup_file": backup.name,
        "sha256": _sha256(backup),
        "size_bytes": backup.stat().st_size,
        "schema_version": schema_version,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    manifest_path = backup.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "backup_path": str(backup), "manifest_path": str(manifest_path)}


def verify_backup(backup_path: str | Path) -> dict:
    backup = Path(backup_path).resolve()
    manifest_path = backup.with_suffix(".manifest.json")
    if not backup.is_file() or not manifest_path.is_file():
        raise ValueError("backup and manifest are required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("backup_file") != backup.name or manifest.get("sha256") != _sha256(backup):
        raise ValueError("backup manifest digest mismatch")
    connection = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        table_count = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        connection.close()
    if integrity != "ok" or schema_version != manifest.get("schema_version"):
        raise ValueError("backup verification failed")
    return {"verified": True, "sha256": manifest["sha256"], "schema_version": schema_version,
            "size_bytes": backup.stat().st_size, "table_count": table_count}
