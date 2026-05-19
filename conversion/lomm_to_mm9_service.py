"""
lomm_to_mm9_service.py
======================

Reusable LoMM-to-MM9 conversion helpers for both the standalone CLI and the
editor UI.  This module owns install-root validation, LoMM WORLDS.REZ level
discovery, conversion to serialized MM9 DAT bytes, and transactional insertion
into MM9's live WORLDS.REZ with an automatic backup.
"""

from __future__ import annotations

import os
import json
import shutil
import tempfile
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional, Sequence

import _path_setup  # noqa: F401
from core import rezmgr
from mm9_patcher.mm9_patch import World

from conversion import lomm_to_mm9 as converter


REQUIRED_MM9_ARCHIVES = {
    "worlds": "WORLDS.REZ",
    "rude": "RUDE.REZ",
    "scripts": "SCRIPTS.REZ",
}

REQUIRED_LOMM_ARCHIVES = REQUIRED_MM9_ARCHIVES

OPTIONAL_ASSET_ARCHIVES = {
    "models": "MODELS.REZ",
    "skins": "SKINS.REZ",
}


class ConversionServiceError(RuntimeError):
    """Raised for user-correctable conversion setup or validation errors."""


@dataclass(frozen=True)
class Mm9Install:
    root: str
    data_dir: str
    worlds_rez: str
    rude_rez: str
    scripts_rez: str
    models_rez: Optional[str] = None
    skins_rez: Optional[str] = None


@dataclass(frozen=True)
class LommInstall:
    root: str
    data_dir: str
    worlds_rez: str
    rude_rez: str
    scripts_rez: str
    models_rez: Optional[str] = None
    skins_rez: Optional[str] = None


@dataclass(frozen=True)
class LommLevelEntry:
    virtual_path: str
    display_name: str
    size: int
    type_tag: str


@dataclass(frozen=True)
class ConvertLevelRequest:
    mm9_root: str
    lomm_root: str
    level_to_convert: str
    converted_level_name: str
    config_path: Optional[str] = None
    catalog_json: Optional[str] = None


@dataclass(frozen=True)
class ConvertLevelResult:
    source_virtual_path: str
    output_virtual_path: str
    dat_bytes: bytes
    stats: converter.ConversionStats
    object_count: int


@dataclass(frozen=True)
class InsertConvertedLevelResult:
    conversion: ConvertLevelResult
    worlds_rez: str
    backup_path: str
    backup_dir: str
    manifest_path: str
    temp_output_path: str
    added_virtual_path: str
    log: Sequence[str]


def validate_mm9_root(root: str) -> Mm9Install:
    """Validate a Might and Magic IX install using the editor's archive rules."""
    root_abs = _require_dir(root, "MM9 root")
    data_dir = _find_child_dir(root_abs, "data")
    if data_dir is None:
        raise ConversionServiceError(
            f"MM9 root does not contain a data folder:\n  {root_abs}"
        )

    archives = {}
    missing = []
    for key, filename in REQUIRED_MM9_ARCHIVES.items():
        path = _find_child_file(data_dir, filename)
        if path is None:
            missing.append(filename)
        else:
            archives[key] = path
    if missing:
        raise ConversionServiceError(
            "MM9 root is missing required archive(s): "
            + ", ".join(missing)
            + f"\nExpected under:\n  {data_dir}"
        )

    models_rez = _find_child_file(data_dir, OPTIONAL_ASSET_ARCHIVES["models"])
    skins_rez = _find_child_file(data_dir, OPTIONAL_ASSET_ARCHIVES["skins"])
    return Mm9Install(
        root=root_abs,
        data_dir=data_dir,
        worlds_rez=archives["worlds"],
        rude_rez=archives["rude"],
        scripts_rez=archives["scripts"],
        models_rez=models_rez,
        skins_rez=skins_rez,
    )


def validate_lomm_root(root: str) -> LommInstall:
    """Validate a Legends of Might and Magic install."""
    root_abs = _require_dir(root, "LoMM root")
    data_dir = _find_child_dir(root_abs, "data")
    if data_dir is None:
        raise ConversionServiceError(
            f"LoMM root does not contain a data folder:\n  {root_abs}"
        )
    archives = {}
    missing = []
    for key, filename in REQUIRED_LOMM_ARCHIVES.items():
        path = _find_child_file(data_dir, filename)
        if path is None:
            missing.append(filename)
        else:
            archives[key] = path
    if missing:
        raise ConversionServiceError(
            "LoMM root is missing required archive(s): "
            + ", ".join(missing)
            + "\n"
            f"Expected it under:\n  {data_dir}"
        )
    return LommInstall(
        root=root_abs,
        data_dir=data_dir,
        worlds_rez=archives["worlds"],
        rude_rez=archives["rude"],
        scripts_rez=archives["scripts"],
        models_rez=_find_child_file(data_dir, OPTIONAL_ASSET_ARCHIVES["models"]),
        skins_rez=_find_child_file(data_dir, OPTIONAL_ASSET_ARCHIVES["skins"]),
    )


def list_lomm_levels(lomm_root: str) -> List[LommLevelEntry]:
    """Return v66 DAT levels inside a LoMM install's WORLDS.REZ."""
    install = validate_lomm_root(lomm_root)
    out: List[LommLevelEntry] = []
    with rezmgr.RezReader(install.worlds_rez) as reader:
        for vpath in reader.list_paths():
            ent = reader.find(vpath)
            if ent is None or ent.size == 0:
                continue
            if not rezmgr.is_v66_dat_magic(reader.peek_bytes(vpath, 4)):
                continue
            out.append(LommLevelEntry(
                virtual_path=ent.virtual_path(),
                display_name=_display_level_name(ent.virtual_path()),
                size=ent.size,
                type_tag=ent.type_str,
            ))
    return sorted(out, key=lambda item: item.display_name.lower())


def find_lomm_level(lomm_root: str, level_name: str) -> LommLevelEntry:
    """Resolve a user-provided LoMM level name to a concrete REZ entry."""
    install = validate_lomm_root(lomm_root)
    with rezmgr.RezReader(install.worlds_rez) as reader:
        candidates = _level_name_candidates(level_name)
        for candidate in candidates:
            ent = reader.find(candidate)
            if ent is None:
                continue
            if not rezmgr.is_v66_dat_magic(reader.peek_bytes(candidate, 4)):
                raise ConversionServiceError(
                    f"{candidate!r} exists in LoMM WORLDS.REZ but is not a v66 DAT level."
                )
            return LommLevelEntry(
                virtual_path=ent.virtual_path(),
                display_name=_display_level_name(ent.virtual_path()),
                size=ent.size,
                type_tag=ent.type_str,
            )
    raise ConversionServiceError(
        f"LoMM WORLDS.REZ does not contain level {level_name!r}."
    )


def normalize_output_level_name(name: str) -> str:
    """Return the MM9 WORLDS virtual path for a new converted level."""
    value = str(name or "").replace("\\", "/").strip().strip("/")
    if not value:
        raise ConversionServiceError("Converted level name cannot be empty.")
    if value.upper().startswith("WORLDS/"):
        value = value.split("/", 1)[1]
    value = value.strip().strip("/")
    if not value:
        raise ConversionServiceError("Converted level name cannot be empty.")
    if "/" in value or "\\" in value:
        raise ConversionServiceError(
            "Converted level name must be a single WORLDS.REZ entry name."
        )
    if value in {".", ".."} or any(ch in value for ch in ':*?"<>|'):
        raise ConversionServiceError(
            f"Converted level name contains unsupported characters: {name!r}"
        )
    if value.upper().endswith(".DAT"):
        value = value[:-4]
    return f"WORLDS/{value}"


def ensure_output_level_available(mm9_root: str, converted_level_name: str) -> str:
    """Validate that the requested output level name is not already in MM9."""
    install = validate_mm9_root(mm9_root)
    output_vpath = normalize_output_level_name(converted_level_name)
    with rezmgr.RezReader(install.worlds_rez) as reader:
        for candidate in _with_dat_variants(output_vpath):
            if reader.find(candidate) is not None:
                raise ConversionServiceError(
                    f"MM9 WORLDS.REZ already contains {output_vpath!r}."
                )
    return output_vpath


def convert_level_to_bytes(request: ConvertLevelRequest) -> ConvertLevelResult:
    """Convert a LoMM level to MM9 DAT bytes without writing a REZ archive."""
    mm9 = validate_mm9_root(request.mm9_root)
    lomm = validate_lomm_root(request.lomm_root)
    source = find_lomm_level(lomm.root, request.level_to_convert)
    output_vpath = ensure_output_level_available(mm9.root, request.converted_level_name)

    config_path = request.config_path or converter.DEFAULT_CONFIG
    try:
        config = converter._parse_config(converter._load_config(config_path))
    except FileNotFoundError as exc:
        raise ConversionServiceError(f"Config file not found: {config_path}") from exc
    except (RuntimeError, ValueError) as exc:
        raise ConversionServiceError(f"Invalid converter config: {exc}") from exc

    catalog_json = request.catalog_json
    if catalog_json and not os.path.isfile(catalog_json):
        catalog_json = None
    try:
        catalog = converter._Mm9Catalog(mm9.worlds_rez, catalog_json=catalog_json)
        with rezmgr.RezReader(lomm.worlds_rez) as reader:
            src_world = _world_from_bytes(reader.extract_to_bytes(source.virtual_path))

        try:
            stats = converter.convert(
                src_world=src_world,
                catalog=catalog,
                config=config,
                input_basename=os.path.basename(source.virtual_path),
                mm9_models_rez=mm9.models_rez,
                mm9_skins_rez=mm9.skins_rez,
                lomm_data_dir=None,
                lomm_models_rez=lomm.models_rez,
                lomm_skins_rez=lomm.skins_rez,
            )
        except (LookupError, ValueError) as exc:
            raise ConversionServiceError(f"Conversion failed: {exc}") from exc
    finally:
        if "catalog" in locals():
            catalog._reader.close()

    dat_bytes = _world_to_bytes(src_world)
    return ConvertLevelResult(
        source_virtual_path=source.virtual_path,
        output_virtual_path=output_vpath,
        dat_bytes=dat_bytes,
        stats=stats,
        object_count=len(src_world.objects),
    )


def convert_and_insert_level(
    request: ConvertLevelRequest,
    backup_root: Optional[str] = None,
) -> InsertConvertedLevelResult:
    """Convert a LoMM level and add it to MM9 WORLDS.REZ transactionally."""
    conversion = convert_level_to_bytes(request)
    mm9 = validate_mm9_root(request.mm9_root)
    _ensure_output_vpath_available_in_rez(mm9.worlds_rez, conversion.output_virtual_path)

    restype = rezmgr.restype_for_format_magic(conversion.dat_bytes[:4])
    if restype is None:
        raise ConversionServiceError("Converted level did not serialize as a DAT file.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_output = os.path.join(
        mm9.data_dir,
        f".WORLDS.lomm_to_mm9_{stamp}_{os.getpid()}.REZ.tmp",
    )
    backup_base = os.path.abspath(
        os.path.expanduser(backup_root)
        if backup_root
        else os.path.join(mm9.root, "mm9_editor", "backups")
    )
    backup_dir = os.path.join(backup_base, f"lomm_to_mm9_{stamp}", "data")
    backup_path = os.path.join(backup_dir, "WORLDS.REZ")
    log: List[str] = []

    try:
        with rezmgr.RezWriter(mm9.worlds_rez, temp_output) as writer:
            writer.add(conversion.output_virtual_path, conversion.dat_bytes, restype=restype)
            writer.commit()
        log.append(f"wrote temporary archive {temp_output}")

        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(mm9.worlds_rez, backup_path)
        log.append(f"backed up original WORLDS.REZ to {backup_path}")

        os.replace(temp_output, mm9.worlds_rez)
        log.append(f"installed converted level into {mm9.worlds_rez}")

        try:
            _verify_inserted_level(mm9.worlds_rez, conversion.output_virtual_path)
        except Exception as exc:
            restore_tmp = temp_output + ".restore"
            shutil.copy2(backup_path, restore_tmp)
            os.replace(restore_tmp, mm9.worlds_rez)
            raise ConversionServiceError(
                "Inserted WORLDS.REZ failed verification; the original archive "
                f"was restored from {backup_path}."
            ) from exc
        try:
            manifest_path = _write_conversion_manifest(
                request=request,
                conversion=conversion,
                mm9=mm9,
                backup_dir=backup_dir,
                backup_path=backup_path,
                stamp=stamp,
            )
            log.append(f"wrote conversion manifest {manifest_path}")
        except Exception as exc:
            manifest_path = ""
            log.append(f"warning: could not write conversion manifest: {exc}")
    except Exception as exc:
        try:
            if os.path.exists(temp_output):
                os.remove(temp_output)
        except OSError:
            pass
        if isinstance(exc, ConversionServiceError):
            raise
        raise ConversionServiceError(f"Failed to install converted level: {exc}") from exc

    return InsertConvertedLevelResult(
        conversion=conversion,
        worlds_rez=mm9.worlds_rez,
        backup_path=backup_path,
        backup_dir=backup_dir,
        manifest_path=manifest_path,
        temp_output_path=temp_output,
        added_virtual_path=conversion.output_virtual_path,
        log=tuple(log),
    )


def _require_dir(path: str, label: str) -> str:
    if not path:
        raise ConversionServiceError(f"{label} was not provided.")
    path_abs = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path_abs):
        raise ConversionServiceError(f"{label} does not exist:\n  {path_abs}")
    return path_abs


def _find_child_dir(parent: str, name: str) -> Optional[str]:
    direct = os.path.join(parent, name)
    if os.path.isdir(direct):
        return os.path.abspath(direct)
    wanted = name.lower()
    try:
        for child in os.listdir(parent):
            path = os.path.join(parent, child)
            if child.lower() == wanted and os.path.isdir(path):
                return os.path.abspath(path)
    except OSError:
        return None
    return None


def _find_child_file(parent: str, name: str) -> Optional[str]:
    direct = os.path.join(parent, name)
    if os.path.isfile(direct):
        return os.path.abspath(direct)
    wanted = name.lower()
    try:
        for child in os.listdir(parent):
            path = os.path.join(parent, child)
            if child.lower() == wanted and os.path.isfile(path):
                return os.path.abspath(path)
    except OSError:
        return None
    return None


def _display_level_name(virtual_path: str) -> str:
    value = str(virtual_path or "").replace("\\", "/").strip("/")
    if value.upper().startswith("WORLDS/"):
        value = value.split("/", 1)[1]
    if value.upper().endswith(".DAT"):
        value = value[:-4]
    return value


def _level_name_candidates(level_name: str) -> Sequence[str]:
    value = str(level_name or "").replace("\\", "/").strip().strip("/")
    if not value:
        return []
    if not value.upper().startswith("WORLDS/"):
        value = f"WORLDS/{value}"
    return _with_dat_variants(value)


def _with_dat_variants(virtual_path: str) -> List[str]:
    value = str(virtual_path or "").replace("\\", "/").strip().strip("/")
    if not value:
        return []
    variants = [value]
    if value.upper().endswith(".DAT"):
        variants.append(value[:-4])
    else:
        variants.append(value + ".DAT")
    out: List[str] = []
    seen = set()
    for item in variants:
        key = item.lower()
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _ensure_output_vpath_available_in_rez(worlds_rez: str, output_vpath: str) -> None:
    with rezmgr.RezReader(worlds_rez) as reader:
        for candidate in _with_dat_variants(output_vpath):
            if reader.find(candidate) is not None:
                raise ConversionServiceError(
                    f"MM9 WORLDS.REZ already contains {output_vpath!r}."
                )


def _verify_inserted_level(worlds_rez: str, output_vpath: str) -> None:
    with rezmgr.RezReader(worlds_rez) as reader:
        ent = reader.find(output_vpath)
        if ent is None:
            raise FileNotFoundError(f"{output_vpath!r} was not found after install")
        if not rezmgr.is_v66_dat_magic(reader.peek_bytes(output_vpath, 4)):
            raise ValueError(f"{output_vpath!r} is not a v66 DAT after install")
        _world_from_bytes(reader.extract_to_bytes(output_vpath))


def _write_conversion_manifest(
    request: ConvertLevelRequest,
    conversion: ConvertLevelResult,
    mm9: Mm9Install,
    backup_dir: str,
    backup_path: str,
    stamp: str,
) -> str:
    path = os.path.join(os.path.dirname(backup_dir), "install_manifest.json")
    doc = {
        "version": 1,
        "installed_at": stamp,
        "failed": False,
        "batch_dir": "",
        "game_data_dir": mm9.data_dir,
        "backup_dir": backup_dir,
        "archives": [
            {
                "name": "WORLDS.REZ",
                "source_path": "",
                "target_path": mm9.worlds_rez,
                "backup_path": backup_path,
                "size": os.path.getsize(backup_path),
            }
        ],
        "conversion": {
            "kind": "lomm_to_mm9",
            "mm9_root": mm9.root,
            "lomm_root": os.path.abspath(os.path.expanduser(request.lomm_root)),
            "source_level": request.level_to_convert,
            "source_virtual_path": conversion.source_virtual_path,
            "converted_level_name": request.converted_level_name,
            "added_virtual_path": conversion.output_virtual_path,
            "objects_after": conversion.object_count,
            "dat_size": len(conversion.dat_bytes),
            "stats": _conversion_stats_dict(conversion.stats),
        },
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    return path


def _conversion_stats_dict(stats: converter.ConversionStats) -> dict:
    return {
        "removed_by_class": dict(stats.removed_by_class),
        "patched_by_class": dict(stats.patched_by_class),
        "converted_by_class": {
            key: {"new_class": value[0], "count": value[1]}
            for key, value in stats.converted_by_class.items()
        },
        "audit_models": _asset_audit_dict(stats.audit_models),
        "audit_skins": _asset_audit_dict(stats.audit_skins),
    }


def _asset_audit_dict(audit: converter.AssetAudit) -> dict:
    return {
        "in_mm9": list(audit.in_mm9),
        "in_lomm_only": list(audit.in_lomm_only),
        "missing": list(audit.missing),
    }


def _world_from_bytes(data: bytes) -> World:
    fd, path = tempfile.mkstemp(prefix="lomm_src_", suffix=".DAT")
    os.close(fd)
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        return World.load(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _world_to_bytes(world: World) -> bytes:
    fd, path = tempfile.mkstemp(prefix="lomm_mm9_", suffix=".DAT")
    os.close(fd)
    try:
        world.save(path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
