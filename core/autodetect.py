"""
autodetect.py
=============

Resolve editor-local paths and the surrounding Might and Magic IX install
folder.  The editor reads the original ``data/*.REZ`` archives directly and no
longer requires or supports a manually extracted ``mm9_data`` tree.

Expected layout
---------------
    mm9_editor/
        mm9_editor.py
        output/           # created on startup
        backups/          # created on startup

Supported game-install layout
-----------------------------
    Might and Magic IX/
        data/
            WORLDS.REZ
            RUDE.REZ
            SCRIPTS.REZ
            ...
        mm9_editor/
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


_ARCHIVE_FILENAMES: Dict[str, str] = {
    "worlds": "WORLDS.REZ",
    "rude": "RUDE.REZ",
    "scripts": "SCRIPTS.REZ",
    "textures": "TEXTURES.REZ",
    "skins": "SKINS.REZ",
    "models": "MODELS.REZ",
    "data": "DATA.REZ",
}

_REQUIRED_ARCHIVES = ("worlds", "rude", "scripts")
_OPTIONAL_ARCHIVES = ("textures", "skins", "models", "data")


class GameNotFoundError(RuntimeError):
    """Raised when required editor-local folders cannot be created."""


@dataclass
class GamePaths:
    """All resolved paths used by the editor."""

    editor_dir: str
    work_dir: str
    backup_root: str
    cache_dir: str
    game_root: Optional[str] = None
    game_data_dir: Optional[str] = None
    archives: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def archive_path(self, key: str) -> str:
        """Return the best-known archive path for *key*.

        Only detected game-install archives are valid in the REZ-only editor.
        """
        name = _ARCHIVE_FILENAMES.get(key)
        if not name:
            raise KeyError(f"no archive named {key!r}")
        if key in self.archives:
            return self.archives[key]
        raise KeyError(f"{name} was not detected")

    def has_archive(self, key: str) -> bool:
        """Return True if a game-install archive was detected for *key*."""
        return key in self.archives and os.path.isfile(self.archives[key])

    def resources(self):
        """Return a REZ-backed resource provider."""
        from core.game_resources import GameResources
        return GameResources.from_paths(self)


def _probe_writable(path: str) -> bool:
    """Return True if *path* can be created and written to."""
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".mm9w_", delete=True) as f:
            f.write(b"ok")
        return True
    except Exception:
        return False


def _localappdata_fallback(subpath: str) -> str:
    """Return a user-writable fallback path under LOCALAPPDATA / ~/.local."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.path.expanduser("~/.local/share")
    return os.path.join(base, "mm9_editor", subpath)


def _archive_map(data_dir: str) -> Dict[str, str]:
    archives: Dict[str, str] = {}
    for key, filename in _ARCHIVE_FILENAMES.items():
        path = os.path.join(data_dir, filename)
        if os.path.isfile(path):
            archives[key] = path
    return archives


def _is_complete_install(archives: Dict[str, str]) -> bool:
    return all(key in archives for key in _REQUIRED_ARCHIVES)


def _candidate_game_roots(editor_dir: str,
                          explicit_game_root: Optional[str]) -> List[str]:
    candidates: List[str] = []
    if explicit_game_root:
        candidates.append(os.path.abspath(explicit_game_root))
    candidates.extend([
        editor_dir,
        os.path.dirname(editor_dir),
    ])

    out: List[str] = []
    seen = set()
    for path in candidates:
        if not path:
            continue
        ap = os.path.abspath(path)
        key = os.path.normcase(ap)
        if key not in seen:
            out.append(ap)
            seen.add(key)
    return out


def _detect_game_archives(editor_dir: str,
                          explicit_game_root: Optional[str],
                          notes: List[str],
                          warnings: List[str]) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    """Find a nearby MM9 ``data`` folder with the required REZ archives."""
    partial: Optional[tuple[str, str, Dict[str, str]]] = None

    for root in _candidate_game_roots(editor_dir, explicit_game_root):
        data_dir = os.path.join(root, "data")
        if not os.path.isdir(data_dir):
            continue
        archives = _archive_map(data_dir)
        if _is_complete_install(archives):
            notes.append(f"Game folder:   {root}")
            notes.append(f"Game data:     {data_dir}")
            found = ", ".join(_ARCHIVE_FILENAMES[k] for k in _REQUIRED_ARCHIVES)
            notes.append(f"Archives:      {found}")
            optional = [k for k in _OPTIONAL_ARCHIVES if k in archives]
            if optional:
                opt_names = ", ".join(_ARCHIVE_FILENAMES[k] for k in optional)
                notes.append(f"Optional REZ:  {opt_names}")
            return root, data_dir, archives
        if archives and partial is None:
            partial = (root, data_dir, archives)

    if explicit_game_root:
        expected = os.path.join(os.path.abspath(explicit_game_root), "data")
        missing = ", ".join(_ARCHIVE_FILENAMES[k] for k in _REQUIRED_ARCHIVES)
        raise GameNotFoundError(
            f"The selected game folder does not contain the required MM9 archives.\n"
            f"Expected them under:\n  {expected}\n\n"
            f"Required: {missing}"
        )

    if partial is not None:
        root, data_dir, archives = partial
        missing = [
            _ARCHIVE_FILENAMES[k] for k in _REQUIRED_ARCHIVES
            if k not in archives
        ]
        raise GameNotFoundError(
            f"Found a nearby data folder at {data_dir!r}, but it is missing "
            f"required archive(s): {', '.join(missing)}."
        )

    raise GameNotFoundError(
        "No nearby MM9 game data folder with WORLDS.REZ, RUDE.REZ, and "
        "SCRIPTS.REZ was found. Put the editor inside the game folder or "
        "launch with --game-root."
    )


def detect(editor_dir: Optional[str] = None,
           game_root: Optional[str] = None) -> GamePaths:
    """Build a :class:`GamePaths` object from the editor directory."""
    if editor_dir is None:
        editor_dir = os.path.dirname(os.path.abspath(__file__))
    editor_dir = os.path.abspath(editor_dir)

    notes: List[str] = []
    warnings: List[str] = []

    notes.append(f"Editor folder: {editor_dir}")

    detected_game_root, game_data_dir, archives = _detect_game_archives(
        editor_dir, game_root, notes, warnings)

    primary_work = os.path.join(editor_dir, "output")
    if _probe_writable(primary_work):
        work_dir = primary_work
        notes.append(f"Output folder: {work_dir}")
    else:
        work_dir = _localappdata_fallback("output")
        if not _probe_writable(work_dir):
            raise GameNotFoundError(
                f"Cannot write to either:\n  {primary_work}\n  {work_dir}\n"
                "Check folder permissions and try again."
            )
        warnings.append(
            f"Output folder in the editor directory is not writable.\n"
            f"Output files will be saved to:\n  {work_dir}"
        )
        notes.append(f"Output folder (fallback): {work_dir}")

    primary_backup = os.path.join(editor_dir, "backups")
    if _probe_writable(primary_backup):
        backup_root = primary_backup
        notes.append(f"Backup folder: {backup_root}")
    else:
        backup_root = _localappdata_fallback("backups")
        if not _probe_writable(backup_root):
            raise GameNotFoundError(
                f"Cannot write to either:\n  {primary_backup}\n  {backup_root}\n"
                "Check folder permissions and try again."
            )
        warnings.append(
            f"Backup folder in the editor directory is not writable.\n"
            f"Backups will be stored at:\n  {backup_root}"
        )
        notes.append(f"Backup folder (fallback): {backup_root}")

    primary_cache = _localappdata_fallback("cache")
    if _probe_writable(primary_cache):
        cache_dir = primary_cache
        notes.append(f"Cache folder:  {cache_dir}")
    else:
        cache_dir = os.path.join(editor_dir, "cache")
        if not _probe_writable(cache_dir):
            raise GameNotFoundError(
                f"Cannot write to either:\n  {primary_cache}\n  {cache_dir}\n"
                "Check folder permissions and try again."
            )
        warnings.append(
            f"LOCALAPPDATA cache folder is not writable.\n"
            f"Cached REZ assets will be stored at:\n  {cache_dir}"
        )
        notes.append(f"Cache folder (fallback): {cache_dir}")

    for key in ("worlds", "rude", "scripts"):
        notes.append(f"{_ARCHIVE_FILENAMES[key]:<13s}: {archives[key]}")
    for key in _OPTIONAL_ARCHIVES:
        if key in archives:
            notes.append(f"{_ARCHIVE_FILENAMES[key]:<13s}: {archives[key]}")
        else:
            warnings.append(
                f"Optional archive {_ARCHIVE_FILENAMES[key]} was not detected; "
                "related rendering or data-table features may be limited.")

    return GamePaths(
        editor_dir=editor_dir,
        work_dir=work_dir,
        backup_root=backup_root,
        cache_dir=cache_dir,
        game_root=detected_game_root,
        game_data_dir=game_data_dir,
        archives=archives,
        notes=notes,
        warnings=warnings,
    )
