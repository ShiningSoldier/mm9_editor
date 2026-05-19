"""
install_manager.py
==================

Install a saved MM9 editor output batch into a game ``data`` folder.

Normal editor Save writes patched archives under ``output/<batch>/data`` and
never touches the live game install.  This module implements the explicit
"Install Output to Game" step: validate the batch, back up full original REZ
archives, then replace the selected game archives.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


class InstallError(RuntimeError):
    pass


class RestoreError(RuntimeError):
    pass


@dataclass
class ArchiveInstall:
    name: str
    source_path: str
    target_path: str
    backup_path: str
    size: int


@dataclass
class InstallResult:
    batch_dir: str
    game_data_dir: str
    backup_dir: str
    archives: List[ArchiveInstall] = field(default_factory=list)
    manifest_path: Optional[str] = None

    def log_lines(self) -> List[str]:
        lines = [f"installed {len(self.archives)} archive(s) to {self.game_data_dir}"]
        lines.append(f"backup: {self.backup_dir}")
        for item in self.archives:
            lines.append(f"  {item.name}: {item.size:,} bytes")
        if self.manifest_path:
            lines.append(f"wrote {self.manifest_path}")
        return lines


@dataclass
class ArchiveRestore:
    name: str
    backup_path: str
    target_path: str
    safety_backup_path: str
    size: int


@dataclass
class RestoreResult:
    backup_source: str
    game_data_dir: str
    safety_backup_dir: str
    archives: List[ArchiveRestore] = field(default_factory=list)
    manifest_path: Optional[str] = None

    def log_lines(self) -> List[str]:
        lines = [f"restored {len(self.archives)} archive(s) to {self.game_data_dir}"]
        lines.append(f"current files backed up to: {self.safety_backup_dir}")
        for item in self.archives:
            lines.append(f"  {item.name}: {item.size:,} bytes")
        if self.manifest_path:
            lines.append(f"wrote {self.manifest_path}")
        return lines


def install_batch(batch_dir: str, game_data_dir: str,
                  backup_root: Optional[str] = None) -> InstallResult:
    batch_dir = os.path.abspath(batch_dir)
    game_data_dir = os.path.abspath(game_data_dir)

    if not os.path.isdir(batch_dir):
        raise InstallError(f"Output batch folder not found: {batch_dir}")
    if not os.path.isdir(game_data_dir):
        raise InstallError(f"Game data folder not found: {game_data_dir}")

    archives = archives_to_install(batch_dir)
    if not archives:
        raise InstallError(
            f"No patched .REZ archives found in {os.path.join(batch_dir, 'data')}")

    backup_root = os.path.abspath(
        backup_root or os.path.join(batch_dir, "backups_before_install"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(backup_root, f"install_{stamp}", "data")
    os.makedirs(backup_dir, exist_ok=True)

    result = InstallResult(
        batch_dir=batch_dir,
        game_data_dir=game_data_dir,
        backup_dir=backup_dir,
    )

    installed: List[ArchiveInstall] = []
    try:
        for source_path in archives:
            name = os.path.basename(source_path)
            target_path = os.path.join(game_data_dir, name)
            if not os.path.isfile(target_path):
                raise InstallError(
                    f"Target archive does not exist in game data folder: {target_path}")

            backup_path = os.path.join(backup_dir, name)
            shutil.copy2(target_path, backup_path)

            temp_target = target_path + ".installing"
            try:
                shutil.copy2(source_path, temp_target)
                if os.path.getsize(temp_target) != os.path.getsize(source_path):
                    raise InstallError(f"Short copy while installing {name}")
                os.replace(temp_target, target_path)
            finally:
                if os.path.exists(temp_target):
                    try:
                        os.remove(temp_target)
                    except OSError:
                        pass

            item = ArchiveInstall(
                name=name,
                source_path=source_path,
                target_path=target_path,
                backup_path=backup_path,
                size=os.path.getsize(source_path),
            )
            installed.append(item)
            result.archives.append(item)
    except Exception:
        _write_install_manifest(batch_dir, game_data_dir, backup_dir, installed,
                                failed=True)
        raise

    result.manifest_path = _write_install_manifest(
        batch_dir, game_data_dir, backup_dir, installed, failed=False)
    return result


def restore_backup(backup_path: str, game_data_dir: Optional[str] = None,
                   safety_backup_root: Optional[str] = None) -> RestoreResult:
    """Restore a backup created by :func:`install_batch`.

    *backup_path* may be the install backup folder, its ``data`` subfolder, or
    the ``install_manifest.json`` file.  The current live archives are copied
    to a fresh ``restore_<timestamp>_current/data`` folder before replacement.
    """
    info = _resolve_restore_source(backup_path)
    restore_game_data = os.path.abspath(game_data_dir or info.get("game_data_dir") or "")
    if not restore_game_data or not os.path.isdir(restore_game_data):
        raise RestoreError(
            "Game data folder was not found. Select a valid game folder or "
            "restore from a backup manifest that records one.")

    archives = backups_to_restore(backup_path)
    if not archives:
        raise RestoreError(f"No .REZ backups found in {backup_path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if safety_backup_root:
        safety_base = os.path.abspath(safety_backup_root)
    else:
        safety_base = os.path.dirname(info["install_dir"])
    safety_backup_dir = os.path.join(safety_base, f"restore_{stamp}_current", "data")
    os.makedirs(safety_backup_dir, exist_ok=True)

    result = RestoreResult(
        backup_source=info["install_dir"],
        game_data_dir=restore_game_data,
        safety_backup_dir=safety_backup_dir,
    )

    restored: List[ArchiveRestore] = []
    try:
        for backup_archive in archives:
            name = os.path.basename(backup_archive)
            target_path = os.path.join(restore_game_data, name)
            if not os.path.isfile(target_path):
                raise RestoreError(
                    f"Target archive does not exist in game data folder: {target_path}")

            safety_path = os.path.join(safety_backup_dir, name)
            shutil.copy2(target_path, safety_path)

            temp_target = target_path + ".restoring"
            try:
                shutil.copy2(backup_archive, temp_target)
                if os.path.getsize(temp_target) != os.path.getsize(backup_archive):
                    raise RestoreError(f"Short copy while restoring {name}")
                os.replace(temp_target, target_path)
            finally:
                if os.path.exists(temp_target):
                    try:
                        os.remove(temp_target)
                    except OSError:
                        pass

            item = ArchiveRestore(
                name=name,
                backup_path=backup_archive,
                target_path=target_path,
                safety_backup_path=safety_path,
                size=os.path.getsize(backup_archive),
            )
            restored.append(item)
            result.archives.append(item)
    except Exception:
        _write_restore_manifest(info["install_dir"], restore_game_data,
                                safety_backup_dir, restored, failed=True)
        raise

    result.manifest_path = _write_restore_manifest(
        info["install_dir"], restore_game_data, safety_backup_dir, restored,
        failed=False)
    return result


def archives_to_install(batch_dir: str) -> List[str]:
    """Return patched REZ archives that would be installed from *batch_dir*."""
    return _archives_to_install(os.path.abspath(batch_dir))


def backups_to_restore(backup_path: str) -> List[str]:
    """Return backed-up REZ archives that would be restored."""
    info = _resolve_restore_source(backup_path)
    manifest_path = info.get("manifest_path")
    archives: List[str] = []
    if manifest_path and os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for item in manifest.get("archives", []):
                backup_archive = item.get("backup_path")
                if backup_archive and os.path.isfile(backup_archive):
                    archives.append(os.path.abspath(backup_archive))
        except Exception as exc:
            raise RestoreError(f"Could not read install manifest: {exc}") from exc
        if archives:
            return _dedupe_existing(archives)

    data_dir = info["backup_data_dir"]
    if os.path.isdir(data_dir):
        for name in sorted(os.listdir(data_dir)):
            if name.upper().endswith(".REZ"):
                archives.append(os.path.abspath(os.path.join(data_dir, name)))
    return _dedupe_existing(archives)


def _archives_to_install(batch_dir: str) -> List[str]:
    manifest_path = os.path.join(batch_dir, "manifest.json")
    archives: List[str] = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for item in manifest.get("archives", []):
                output_archive = item.get("output_archive")
                if output_archive:
                    name = os.path.basename(output_archive)
                    batch_relative = os.path.join(batch_dir, "data", name)
                    if os.path.isfile(batch_relative):
                        archives.append(os.path.abspath(batch_relative))
                    elif os.path.isfile(output_archive):
                        archives.append(os.path.abspath(output_archive))
        except Exception as exc:
            raise InstallError(f"Could not read output manifest: {exc}") from exc
        if archives:
            return _dedupe_existing(archives)

    data_dir = os.path.join(batch_dir, "data")
    if os.path.isdir(data_dir):
        for name in sorted(os.listdir(data_dir)):
            if name.upper().endswith(".REZ"):
                archives.append(os.path.abspath(os.path.join(data_dir, name)))

    return _dedupe_existing(archives)


def _dedupe_existing(paths: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for path in paths:
        if not os.path.isfile(path):
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        out.append(os.path.abspath(path))
        seen.add(key)
    return out


def _write_install_manifest(batch_dir: str, game_data_dir: str,
                            backup_dir: str,
                            installed: List[ArchiveInstall],
                            failed: bool) -> str:
    path = os.path.join(os.path.dirname(backup_dir), "install_manifest.json")
    doc: Dict[str, Any] = {
        "version": 1,
        "installed_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "failed": failed,
        "batch_dir": batch_dir,
        "game_data_dir": game_data_dir,
        "backup_dir": backup_dir,
        "archives": [
            {
                "name": item.name,
                "source_path": item.source_path,
                "target_path": item.target_path,
                "backup_path": item.backup_path,
                "size": item.size,
            }
            for item in installed
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    return path


def _resolve_restore_source(path: str) -> Dict[str, str]:
    original = os.path.abspath(path)
    if os.path.isfile(original):
        manifest_path = original
        install_dir = os.path.dirname(manifest_path)
        backup_data_dir = ""
    else:
        candidate = original
        if os.path.basename(candidate).lower() == "data":
            backup_data_dir = candidate
            install_dir = os.path.dirname(candidate)
        else:
            install_dir = candidate
            backup_data_dir = os.path.join(candidate, "data")
        manifest_path = os.path.join(install_dir, "install_manifest.json")

    game_data_dir = ""
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            game_data_dir = manifest.get("game_data_dir") or ""
            backup_dir = manifest.get("backup_dir") or ""
            if backup_dir:
                backup_data_dir = backup_dir
        except Exception as exc:
            raise RestoreError(f"Could not read install manifest: {exc}") from exc

    if not backup_data_dir:
        backup_data_dir = os.path.join(install_dir, "data")
    if not os.path.isdir(backup_data_dir):
        raise RestoreError(f"Backup data folder not found: {backup_data_dir}")

    return {
        "input": original,
        "install_dir": install_dir,
        "backup_data_dir": backup_data_dir,
        "manifest_path": manifest_path if os.path.isfile(manifest_path) else "",
        "game_data_dir": game_data_dir,
    }


def _write_restore_manifest(backup_source: str, game_data_dir: str,
                            safety_backup_dir: str,
                            restored: List[ArchiveRestore],
                            failed: bool) -> str:
    path = os.path.join(os.path.dirname(safety_backup_dir), "restore_manifest.json")
    doc: Dict[str, Any] = {
        "version": 1,
        "restored_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "failed": failed,
        "backup_source": backup_source,
        "game_data_dir": game_data_dir,
        "safety_backup_dir": safety_backup_dir,
        "archives": [
            {
                "name": item.name,
                "backup_path": item.backup_path,
                "target_path": item.target_path,
                "safety_backup_path": item.safety_backup_path,
                "size": item.size,
            }
            for item in restored
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    return path
