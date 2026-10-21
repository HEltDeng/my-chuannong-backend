"""Consistent SQLite backup, verification, retention, and offline restore."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKUP_FORMAT = "sqlite3-online-backup-v1"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integrity_check(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "database file does not exist"
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as database:
            result = str(database.execute("PRAGMA integrity_check").fetchone()[0])
        return result == "ok", result
    except sqlite3.Error as exc:
        return False, f"sqlite error: {exc}"


def verify_backup(path: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    database_valid, result = integrity_check(resolved)
    report: dict[str, Any] = {
        "valid": False,
        "integrity": result,
        "filename": resolved.name,
        "bytes": resolved.stat().st_size if resolved.is_file() else 0,
        "sha256": sha256_file(resolved) if resolved.is_file() else None,
    }
    manifest = manifest_path or resolved.with_suffix(".json")
    errors: list[str] = []
    if not manifest.is_file():
        report["manifestPresent"] = False
        report["manifestMatches"] = False
        errors.append("MANIFEST_MISSING")
    else:
        report["manifestPresent"] = True
        try:
            expected = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            expected = None
            errors.append("MANIFEST_INVALID_JSON")
        if not isinstance(expected, dict):
            if expected is not None:
                errors.append("MANIFEST_NOT_OBJECT")
        else:
            if expected.get("format") != BACKUP_FORMAT:
                errors.append("MANIFEST_UNSUPPORTED_FORMAT")
            if expected.get("filename") != report["filename"]:
                errors.append("MANIFEST_FILENAME_MISMATCH")
            expected_bytes = expected.get("bytes")
            if (
                not isinstance(expected_bytes, int)
                or isinstance(expected_bytes, bool)
                or expected_bytes != report["bytes"]
            ):
                errors.append("MANIFEST_SIZE_MISMATCH")
            expected_sha = expected.get("sha256")
            actual_sha = report["sha256"]
            if (
                not isinstance(expected_sha, str)
                or not isinstance(actual_sha, str)
                or not hmac.compare_digest(expected_sha, actual_sha)
            ):
                errors.append("MANIFEST_SHA256_MISMATCH")
            if expected.get("integrity") != "ok":
                errors.append("MANIFEST_INTEGRITY_MISMATCH")
        report["manifestMatches"] = not errors
    report["manifestErrors"] = errors
    report["valid"] = database_valid and report["manifestMatches"]
    return report


def _prune_backups(destination: Path, retention_days: int, prefix: str) -> list[str]:
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    removed: list[str] = []
    for candidate in destination.glob(f"{prefix}-*.db"):
        resolved = candidate.resolve()
        if resolved.parent != destination.resolve() or not resolved.is_file():
            continue
        if resolved.stat().st_mtime >= cutoff:
            continue
        manifest = resolved.with_suffix(".json")
        resolved.unlink()
        removed.append(resolved.name)
        if manifest.is_file() and manifest.resolve().parent == destination.resolve():
            manifest.unlink()
    return removed


def backup_database(
    source: Path,
    destination: Path,
    *,
    retention_days: int = 14,
    prefix: str = "my-chuannong",
    prune: bool = True,
) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    _safe_chmod(destination, 0o750)
    name = f"{prefix}-{_timestamp()}.db"
    target = destination / name
    temporary = destination / f".{name}.{os.getpid()}.tmp"
    if target.exists() or temporary.exists():
        raise FileExistsError("Refusing to overwrite an existing backup")

    try:
        with closing(sqlite3.connect(source, timeout=30)) as source_db:
            with closing(sqlite3.connect(temporary, timeout=30)) as target_db:
                source_db.backup(target_db)
                target_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                target_db.commit()
        valid, result = integrity_check(temporary)
        if not valid:
            raise RuntimeError(f"Backup integrity check failed: {result}")
        _safe_chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    manifest = {
        "format": BACKUP_FORMAT,
        "filename": target.name,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "integrity": "ok",
    }
    manifest_path = target.with_suffix(".json")
    manifest_temporary = destination / f".{manifest_path.name}.{os.getpid()}.tmp"
    try:
        with manifest_temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _safe_chmod(manifest_temporary, 0o600)
        os.replace(manifest_temporary, manifest_path)
    except Exception:
        if target.is_file():
            target.unlink()
        raise
    finally:
        if manifest_temporary.exists():
            manifest_temporary.unlink()
    _safe_chmod(manifest_path, 0o600)
    removed = _prune_backups(destination, retention_days, prefix) if prune else []
    return {"backup": target, "manifest": manifest_path, "removed": removed, **manifest}


def restore_database(
    backup: Path,
    target: Path,
    safety_backup_directory: Path,
) -> dict[str, Any]:
    backup = backup.resolve()
    target = target.resolve()
    verification = verify_backup(backup)
    if not verification["valid"]:
        reasons = verification.get("manifestErrors") or [verification["integrity"]]
        raise RuntimeError(
            "Refusing backup that is not a verified backup: " + ",".join(reasons)
        )

    safety_backup = None
    if target.is_file():
        with closing(sqlite3.connect(target, timeout=30)) as current_db:
            current_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        safety_backup = backup_database(
            target,
            safety_backup_directory,
            retention_days=30,
            prefix="pre-restore",
            prune=False,
        )["backup"]

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.restore-{_timestamp()}.tmp"
    try:
        with closing(sqlite3.connect(backup, timeout=30)) as source_db:
            with closing(sqlite3.connect(temporary, timeout=30)) as target_db:
                source_db.backup(target_db)
                target_db.commit()
        valid, result = integrity_check(temporary)
        if not valid:
            raise RuntimeError(f"Restored database integrity check failed: {result}")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(target) + suffix)
            if sidecar.is_file():
                sidecar.unlink()
        _safe_chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "restored": True,
        "target": target,
        "sourceBackup": backup,
        "safetyBackup": safety_backup,
        "integrity": "ok",
    }
