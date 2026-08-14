"""
game_resources.py
=================

REZ-backed access to Might and Magic IX resources.

The game sees resources through virtual paths such as ``WORLDS/STURMFORDCITY``
or ``RUDE/NPC1``.  This module lets callers use those game-facing virtual
paths while reading directly from the original ``data/*.REZ`` archives.
"""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import _path_setup  # noqa: F401
from core import rezmgr as mm9_rezmgr


_ROOT_TO_ARCHIVE: Dict[str, str] = {
    "WORLDS": "worlds",
    "RUDE": "rude",
    "SCRIPTS": "scripts",
    "TEXTURES": "textures",
    "SKINS": "skins",
    "MODELS": "models",
    "DATA": "data",
    "SOUNDS": "sounds",
}

_DEFAULT_EXT: Dict[str, str] = {
    "WORLDS": ".DAT",
    "RUDE": ".RUDE",
    "SCRIPTS": ".SCR",
    "TEXTURES": ".DTX",
    "SKINS": ".DTX",
    "MODELS": ".ABC",
    "SOUNDS": ".WAV",
}

@dataclass(frozen=True)
class ResourceLocation:
    """Resolved physical location for a virtual resource."""

    source: str                    # "rez"
    virtual_path: str
    archive_key: Optional[str] = None
    archive_path: Optional[str] = None


class GameResources:
    """Read/list resources from detected game archives."""

    def __init__(self,
                 archives: Optional[Dict[str, str]] = None,
                 cache_dir: Optional[str] = None) -> None:
        self.archives = dict(archives or {})
        self.cache_dir = os.path.abspath(cache_dir) if cache_dir else None

    @classmethod
    def from_paths(cls, paths: object) -> "GameResources":
        return cls(
            archives=getattr(paths, "archives", {}),
            cache_dir=getattr(paths, "cache_dir", None),
        )

    # ------------------------------------------------------------------
    # Path normalization
    # ------------------------------------------------------------------

    def normalize(self, virtual_path: str) -> str:
        """Return a canonical forward-slash virtual path."""
        path = str(virtual_path or "").replace("\\", "/").strip().strip("/")
        parts = [p for p in path.split("/") if p]
        if not parts:
            return ""
        parts[0] = parts[0].upper()
        return "/".join(parts)

    def archive_key_for(self, virtual_path: str) -> Optional[str]:
        root = self._root_of(virtual_path)
        return _ROOT_TO_ARCHIVE.get(root)

    def archive_for(self, virtual_path: str) -> Optional[str]:
        key = self.archive_key_for(virtual_path)
        if not key:
            return None
        path = self.archives.get(key)
        return path if path and os.path.isfile(path) else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def exists(self, virtual_path: str) -> bool:
        return self.locate(virtual_path) is not None

    def locate(self, virtual_path: str) -> Optional[ResourceLocation]:
        """Resolve *virtual_path* without reading its bytes."""
        normalized = self.normalize(virtual_path)
        return self._locate_rez(normalized)

    def read_bytes(self, virtual_path: str) -> bytes:
        loc = self.locate(virtual_path)
        if loc is None:
            raise FileNotFoundError(f"{virtual_path!r} was not found")
        if loc.source == "rez":
            assert loc.archive_path and loc.virtual_path
            with mm9_rezmgr.RezReader(loc.archive_path) as reader:
                return reader.extract_to_bytes(loc.virtual_path)
        raise FileNotFoundError(f"{virtual_path!r} was not found in REZ archives")

    def read_text(self, virtual_path: str,
                  encoding: str = "latin-1") -> str:
        return self.read_bytes(virtual_path).decode(encoding)

    def list(self, prefix: str = "") -> List[str]:
        """List known virtual resources under *prefix*.

        Returned paths use game-style names.
        """
        normalized_prefix = self.normalize(prefix)
        if normalized_prefix and not normalized_prefix.endswith("/"):
            normalized_prefix += "/"

        paths = set()
        for path in self._list_rez(normalized_prefix):
            paths.add(path)
        return sorted(paths)

    def cache_archive_tree(self, archive_key: str, virtual_root: str,
                           extensions: Sequence[str]) -> Optional[str]:
        """Extract matching archive entries into a persistent cache directory.

        Existing viewport loaders expect ordinary files and scan a folder at GL
        init time.  This bridge materializes REZ assets once per archive
        version, keyed by absolute path + size + mtime.  Returned paths contain
        files relative to *virtual_root*, so ``TEXTURES/FOO.DTX`` becomes
        ``<cache>/FOO.DTX``.
        """
        archive_path = self.archives.get(archive_key)
        if not archive_path or not os.path.isfile(archive_path) or not self.cache_dir:
            return None

        virtual_root_norm = self.normalize(virtual_root).rstrip("/")
        if not virtual_root_norm:
            return None

        cache_root = self._cache_root_for_archive(archive_key, archive_path)
        marker = os.path.join(cache_root, ".complete")
        if os.path.isfile(marker):
            return cache_root

        os.makedirs(cache_root, exist_ok=True)
        ext_set = {
            ext.upper() if ext.startswith(".") else f".{ext.upper()}"
            for ext in extensions
        }

        extracted = 0
        with mm9_rezmgr.RezReader(archive_path) as reader:
            for vpath in reader.list_paths():
                norm = self.normalize(vpath)
                if not (norm == virtual_root_norm
                        or norm.startswith(virtual_root_norm + "/")):
                    continue
                ent = reader.find(vpath)
                if ent is None or ent.size == 0:
                    continue
                rel = norm[len(virtual_root_norm):].lstrip("/")
                rel = self._cache_relative_path(rel, ent.type_str, ext_set)
                if rel is None:
                    continue
                out_path = os.path.abspath(os.path.join(cache_root, *rel.split("/")))
                if not self._is_inside(out_path, cache_root):
                    continue
                if os.path.isfile(out_path) and os.path.getsize(out_path) == ent.size:
                    extracted += 1
                    continue
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(reader.extract_to_bytes(vpath))
                extracted += 1

        with open(marker, "w", encoding="ascii") as f:
            f.write(f"{archive_path}\n{extracted}\n")
        return cache_root

    # ------------------------------------------------------------------
    # REZ lookup
    # ------------------------------------------------------------------

    def _locate_rez(self, virtual_path: str) -> Optional[ResourceLocation]:
        archive_key = self.archive_key_for(virtual_path)
        archive_path = self.archive_for(virtual_path)
        if not archive_key or not archive_path:
            return None
        with mm9_rezmgr.RezReader(archive_path) as reader:
            for candidate in self._rez_candidates(virtual_path):
                ent = reader.find(candidate)
                if ent is not None:
                    return ResourceLocation(
                        source="rez",
                        virtual_path=candidate.replace("\\", "/"),
                        archive_key=archive_key,
                        archive_path=archive_path,
                    )
        return None

    def _list_rez(self, normalized_prefix: str) -> Iterable[str]:
        wanted_root = self._root_of(normalized_prefix)
        for archive_key, archive_path in sorted(self.archives.items()):
            if not os.path.isfile(archive_path):
                continue
            if wanted_root:
                root_key = _ROOT_TO_ARCHIVE.get(wanted_root)
                if root_key != archive_key:
                    continue
            try:
                with mm9_rezmgr.RezReader(archive_path) as reader:
                    for path in reader.list_paths():
                        norm = self.normalize(path)
                        if self._matches_prefix(norm, normalized_prefix):
                            yield norm
            except Exception:
                continue

    def _rez_candidates(self, virtual_path: str) -> Sequence[str]:
        candidates = [virtual_path]
        root = self._root_of(virtual_path)
        base, ext = os.path.splitext(virtual_path)
        if ext:
            candidates.append(base)
        elif root in _DEFAULT_EXT:
            candidates.append(virtual_path + _DEFAULT_EXT[root])
        return self._dedupe(candidates)

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def _root_of(self, virtual_path: str) -> str:
        normalized = self.normalize(virtual_path)
        return normalized.split("/", 1)[0] if normalized else ""

    def _rest_of(self, virtual_path: str) -> str:
        normalized = self.normalize(virtual_path)
        parts = normalized.split("/", 1)
        return parts[1] if len(parts) > 1 else ""

    def _matches_prefix(self, path: str, normalized_prefix: str) -> bool:
        return not normalized_prefix or path.startswith(normalized_prefix)

    def _dedupe(self, values: Iterable[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for value in values:
            key = value.lower().replace("\\", "/")
            if key not in seen:
                out.append(value)
                seen.add(key)
        return out

    def _cache_root_for_archive(self, archive_key: str,
                                archive_path: str) -> str:
        st = os.stat(archive_path)
        fingerprint = hashlib.sha1(
            f"{os.path.abspath(archive_path)}|{st.st_size}|{st.st_mtime_ns}".encode(
                "utf-8", errors="replace")
        ).hexdigest()[:16]
        return os.path.join(self.cache_dir or "", archive_key, fingerprint)

    def _cache_relative_path(self, rel_path: str, type_str: str,
                             ext_set: Sequence[str]) -> Optional[str]:
        rel = self.normalize(rel_path)
        if not rel:
            return None
        base, ext = os.path.splitext(rel)
        ext_upper = ext.upper()
        if ext_upper:
            return rel if not ext_set or ext_upper in ext_set else None

        type_ext = f".{str(type_str or '').strip().upper()}"
        if type_ext in ext_set:
            return rel + type_ext
        if len(ext_set) == 1:
            return rel + next(iter(ext_set))
        if not ext_set:
            return rel
        return None

    def _is_inside(self, path: str, root: str) -> bool:
        try:
            common = os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])
        except ValueError:
            return False
        return os.path.normcase(common) == os.path.normcase(os.path.abspath(root))
