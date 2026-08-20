"""Stage and launch an isolated MM9 ``+runworld`` preview session."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple


_INVALID_RESOURCE_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RUNTIME_CONFIG_FILES = (
    "autoexec.cfg",
    "autoexec.lith",
    "defaults.cfg",
    "display.cfg",
    "dxcfg.ini",
    "clientfx.fxd",
    "ClassHlp.but",
    "legends.lyt",
)
_RUNTIME_SUPPORT_DIRS = ("Fonts", "EAL")
_ISOLATED_WRITE_DIRS = ("Minisaves", "SaveGames", "Saves")
_CONVERSION_RESOURCE_ARCHIVES = ("MODELS.REZ", "SKINS.REZ", "SOUNDS.REZ")


class RunWorldError(RuntimeError):
    """Raised when a safe current-level preview cannot be prepared."""


@dataclass
class RunWorldSession:
    """Files and process belonging to one isolated preview launch."""

    session_dir: str
    overlay_dir: str
    staged_dat: str
    world_name: str
    resource_paths: Tuple[str, ...]
    command: Tuple[str, ...]
    process: Optional[Any] = None


def _safe_resource_parts(virtual_path: str) -> Tuple[str, ...]:
    raw = str(virtual_path or "").strip().replace("/", "\\")
    if not raw:
        raise RunWorldError("The level has no virtual resource path.")
    if raw.startswith("\\") or re.match(r"^[A-Za-z]:", raw):
        raise RunWorldError(f"Resource path must be relative: {virtual_path!r}")
    parts = tuple(raw.split("\\"))
    if any(
        not part
        or part in {".", ".."}
        or _INVALID_RESOURCE_CHARS.search(part)
        for part in parts
    ):
        raise RunWorldError(f"Unsafe resource path: {virtual_path!r}")
    return parts


def normalize_world_path(virtual_path: str) -> Tuple[str, str]:
    """Return ``(overlay DAT path, +runworld value)`` for a REZ path."""
    parts = list(_safe_resource_parts(virtual_path))
    if parts[0].casefold() != "worlds":
        raise RunWorldError(
            f"Current level is not under the WORLDS resource tree: {virtual_path!r}"
        )
    stem, ext = os.path.splitext(parts[-1])
    if ext and ext.casefold() != ".dat":
        raise RunWorldError(f"Current level is not a DAT resource: {virtual_path!r}")
    if not stem:
        raise RunWorldError(f"Current level has an invalid name: {virtual_path!r}")
    parts[-1] = stem
    world_name = "\\".join(("worlds", *parts[1:]))
    overlay_path = "\\".join((*parts[:-1], stem + ".DAT"))
    return overlay_path, world_name


def game_resource_paths(game_root: str) -> Tuple[str, ...]:
    """Resolve the installed game's REZ.TXT entries to absolute paths."""
    root = os.path.abspath(str(game_root or ""))
    manifest = os.path.join(root, "rez.txt")
    if not os.path.isfile(manifest):
        raise RunWorldError(f"MM9 resource list was not found: {manifest}")

    resources: List[str] = []
    with open(manifest, "r", encoding="utf-8-sig", errors="replace") as handle:
        for raw_line in handle:
            value = raw_line.strip()
            if not value or value.startswith(("#", ";")):
                continue
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            path = value if os.path.isabs(value) else os.path.join(root, value)
            path = os.path.abspath(path)
            if not os.path.exists(path):
                raise RunWorldError(
                    f"MM9 resource listed by rez.txt was not found: {path}"
                )
            resources.append(path)
    if not resources:
        raise RunWorldError(f"MM9 resource list is empty: {manifest}")
    return tuple(resources)


def _overlay_file_path(overlay_dir: str, virtual_path: str) -> str:
    parts = _safe_resource_parts(virtual_path)
    root = os.path.abspath(overlay_dir)
    target = os.path.abspath(os.path.join(root, *parts))
    try:
        if os.path.commonpath((root, target)) != root:
            raise RunWorldError(f"Resource escapes the preview overlay: {virtual_path!r}")
    except ValueError as exc:
        raise RunWorldError(
            f"Resource escapes the preview overlay: {virtual_path!r}"
        ) from exc
    return target


def _copy_runtime_support(game_root: str, session_dir: str) -> None:
    for name in _RUNTIME_CONFIG_FILES:
        source = os.path.join(game_root, name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(session_dir, name))
    for name in _RUNTIME_SUPPORT_DIRS:
        source = os.path.join(game_root, name)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(session_dir, name))
    for name in _ISOLATED_WRITE_DIRS:
        os.makedirs(os.path.join(session_dir, name), exist_ok=True)


def _conversion_resource_paths(level: Any) -> Tuple[str, ...]:
    stage_dir = str(getattr(level, "conversion_stage_dir", "") or "")
    if not stage_dir:
        return ()
    data_dir = os.path.join(stage_dir, "data")
    return tuple(
        os.path.abspath(os.path.join(data_dir, name))
        for name in _CONVERSION_RESOURCE_ARCHIVES
        if os.path.isfile(os.path.join(data_dir, name))
    )


def _dedupe_resources(paths: Sequence[str]) -> Tuple[str, ...]:
    result: List[str] = []
    seen = set()
    for path in paths:
        absolute = os.path.abspath(path)
        key = os.path.normcase(absolute)
        if key not in seen:
            seen.add(key)
            result.append(absolute)
    return tuple(result)


def stage_current_level(
    project: Any,
    level: Any,
    *,
    game_root: str,
    staging_root: str,
) -> RunWorldSession:
    """Materialize *level* and build an isolated, launch-ready directory."""
    root = os.path.abspath(str(game_root or ""))
    executable = os.path.join(root, "lithtech.exe")
    if not os.path.isfile(executable):
        raise RunWorldError(f"MM9 executable was not found: {executable}")
    if not staging_root:
        raise RunWorldError("No writable editor output directory is configured.")

    virtual_path = str(getattr(level, "rez_vpath", "") or "")
    overlay_world_path, world_name = normalize_world_path(virtual_path)
    dat_bytes, dat_write = project.build_runtime_dat(level)
    blocking_issues = list(getattr(dat_write, "blocking_issues", ()) or ())
    if blocking_issues:
        details = "\n".join(
            f"- {issue.get('message') or issue.get('code') or issue}"
            if isinstance(issue, dict) else f"- {issue}"
            for issue in blocking_issues
        )
        raise RunWorldError(
            "The current level has runtime compatibility issues and cannot be "
            f"previewed safely:\n\n{details}"
        )
    extra_entries = project.build_runtime_overlay_entries(level)
    installed_resources = game_resource_paths(root)

    preview_root = os.path.abspath(os.path.join(staging_root, "run-preview"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    session_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
    session_dir = os.path.abspath(os.path.join(preview_root, session_id))
    try:
        if os.path.commonpath((preview_root, session_dir)) != preview_root:
            raise RunWorldError("Preview session path escaped the output directory.")
    except ValueError as exc:
        raise RunWorldError(
            "Preview session path escaped the output directory."
        ) from exc
    overlay_dir = os.path.join(session_dir, "overlay")
    os.makedirs(overlay_dir, exist_ok=False)
    _copy_runtime_support(root, session_dir)

    staged_dat = _overlay_file_path(overlay_dir, overlay_world_path)
    os.makedirs(os.path.dirname(staged_dat), exist_ok=True)
    with open(staged_dat, "wb") as handle:
        handle.write(dat_bytes)
    for virtual_resource, data in extra_entries.items():
        target = _overlay_file_path(overlay_dir, virtual_resource)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)

    resources = _dedupe_resources((
        *installed_resources,
        *_conversion_resource_paths(level),
        overlay_dir,
    ))
    command: List[str] = [os.path.abspath(executable)]
    for resource in resources:
        command.extend(("-rez", resource))
    command.extend(("+runworld", world_name))

    with open(
        os.path.join(session_dir, "preview_resources.txt"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write("\n".join(resources) + "\n")
    with open(
        os.path.join(session_dir, "preview.json"),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            {
                "world": world_name,
                "source_virtual_path": virtual_path,
                "staged_dat": staged_dat,
                "resources": list(resources),
                "command": command,
                "validation_warnings": list(
                    getattr(dat_write, "validation_warnings", ()) or ()
                ),
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )

    return RunWorldSession(
        session_dir=session_dir,
        overlay_dir=overlay_dir,
        staged_dat=staged_dat,
        world_name=world_name,
        resource_paths=resources,
        command=tuple(command),
    )


def launch_current_level(
    project: Any,
    level: Any,
    *,
    game_root: str,
    staging_root: str,
    popen_factory: Any = subprocess.Popen,
) -> RunWorldSession:
    """Stage and start MM9 without blocking the editor UI."""
    session = stage_current_level(
        project,
        level,
        game_root=game_root,
        staging_root=staging_root,
    )
    try:
        session.process = popen_factory(
            list(session.command),
            cwd=session.session_dir,
        )
    except OSError as exc:
        raise RunWorldError(f"Could not start Might and Magic IX: {exc}") from exc
    return session
