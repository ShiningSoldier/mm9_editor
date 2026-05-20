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
from typing import Dict, List, Optional, Sequence, Tuple

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
    "sounds": "SOUNDS.REZ",
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
    sounds_rez: Optional[str] = None


@dataclass(frozen=True)
class LommInstall:
    root: str
    data_dir: str
    worlds_rez: str
    rude_rez: str
    scripts_rez: str
    models_rez: Optional[str] = None
    skins_rez: Optional[str] = None
    sounds_rez: Optional[str] = None


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
    sounds_rez = _find_child_file(data_dir, OPTIONAL_ASSET_ARCHIVES["sounds"])
    return Mm9Install(
        root=root_abs,
        data_dir=data_dir,
        worlds_rez=archives["worlds"],
        rude_rez=archives["rude"],
        scripts_rez=archives["scripts"],
        models_rez=models_rez,
        skins_rez=skins_rez,
        sounds_rez=sounds_rez,
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
        sounds_rez=_find_child_file(data_dir, OPTIONAL_ASSET_ARCHIVES["sounds"]),
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
                mm9_sounds_rez=mm9.sounds_rez,
                lomm_data_dir=lomm.data_dir,
                lomm_models_rez=lomm.models_rez,
                lomm_skins_rez=lomm.skins_rez,
                lomm_sounds_rez=lomm.sounds_rez,
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


@dataclass
class RezTransaction:
    target_path: str
    temp_path: str
    backup_path: str
    archive_name: str
    updates: List[Tuple[str, bytes, int]]


def _find_loose_asset(lomm_data_dir: Optional[str], subdir: str, stem: str) -> Optional[str]:
    if not lomm_data_dir or not os.path.isdir(lomm_data_dir):
        return None
    target = None
    try:
        for child in os.listdir(lomm_data_dir):
            if child.lower() == subdir.lower():
                target = os.path.join(lomm_data_dir, child)
                break
    except OSError:
        return None
    if not target or not os.path.isdir(target):
        return None
    for root, _, files in os.walk(target):
        rel = os.path.relpath(root, target).replace("\\", "/")
        if rel == ".":
            rel = ""
        for fn in files:
            p = f"{rel}/{fn}" if rel else fn
            if converter._normalize_asset_path(p)[0] == stem:
                return os.path.abspath(os.path.join(root, fn))
    return None


def _extract_asset(
    lomm_data_dir: Optional[str],
    subdir: str,
    lomm_rez: Optional[str],
    stem: str,
) -> Optional[Tuple[str, bytes, int]]:
    # 1. Search loose files
    loose_path = _find_loose_asset(lomm_data_dir, subdir, stem)
    if loose_path:
        with open(loose_path, "rb") as f:
            data = f.read()
        # Find relative path to target dir
        target_dir = None
        for child in os.listdir(lomm_data_dir):
            if child.lower() == subdir.lower():
                target_dir = os.path.join(lomm_data_dir, child)
                break
        rel_path = os.path.relpath(loose_path, target_dir).replace("\\", "/")
        if subdir.upper() == "SOUNDS":
            # Strip extension for sounds
            rel_path = os.path.splitext(rel_path)[0]
        vpath = f"{subdir.upper()}/{rel_path.upper()}"
        from core.rezmgr import _restype_for_filename
        restype = _restype_for_filename(loose_path)
        return vpath, data, restype

    # 2. Search REZ
    if lomm_rez and os.path.isfile(lomm_rez):
        with rezmgr.RezReader(lomm_rez) as reader:
            for p in reader.list_paths():
                if converter._normalize_asset_path(p)[0] == stem:
                    # Found it! Extract bytes and res_type
                    entry = reader.find(p)
                    if entry:
                        return p, reader.extract_to_bytes(p), entry.res_type
    return None


def _verify_inserted_assets(rez_path: str, added_paths: List[str]) -> None:
    with rezmgr.RezReader(rez_path) as reader:
        for p in added_paths:
            ent = reader.find(p)
            if ent is None:
                raise FileNotFoundError(f"{p!r} was not found in {os.path.basename(rez_path)} after install")
            # Try to read a few bytes to verify it's readable
            reader.peek_bytes(p, min(4, ent.size))


def _write_conversion_log(
    backup_dir: str,
    copied_assets: dict,
    log_lines: List[str],
) -> str:
    log_path = os.path.join(os.path.dirname(backup_dir), "conversion_log.txt")
    lines = []
    lines.append("=== LoMM to MM9 Conversion Log ===")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    total_copied = 0
    for subdir in ["MODELS", "SKINS", "SOUNDS"]:
        assets = copied_assets.get(subdir, [])
        lines.append(f"--- Copied {subdir} ({len(assets)}) ---")
        if not assets:
            lines.append("  (none)")
        for ref, vpath, src_desc in assets:
            lines.append(f"  Ref  : {ref}")
            lines.append(f"  VPath: {vpath}")
            lines.append(f"  From : {src_desc}")
            lines.append("")
            total_copied += 1
            log_lines.append(f"copied asset to MM9: {vpath} (from {src_desc})")
            
    lines.append(f"Total assets copied: {total_copied}")
    
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return log_path


def convert_and_insert_level(
    request: ConvertLevelRequest,
    backup_root: Optional[str] = None,
) -> InsertConvertedLevelResult:
    """Convert a LoMM level and add it to MM9 WORLDS.REZ transactionally."""
    conversion = convert_level_to_bytes(request)
    mm9 = validate_mm9_root(request.mm9_root)
    lomm = validate_lomm_root(request.lomm_root)
    _ensure_output_vpath_available_in_rez(mm9.worlds_rez, conversion.output_virtual_path)

    restype = rezmgr.restype_for_format_magic(conversion.dat_bytes[:4])
    if restype is None:
        raise ConversionServiceError("Converted level did not serialize as a DAT file.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_base = os.path.abspath(
        os.path.expanduser(backup_root)
        if backup_root
        else os.path.join(mm9.root, "mm9_editor", "backups")
    )
    backup_dir = os.path.join(backup_base, f"lomm_to_mm9_{stamp}", "data")
    log: List[str] = []

    # 1. Identify missing models, skins, and sounds, and resolve them
    missing_assets_configs = [
        # (audit_list, subdir, target_rez_path, target_rez_name, lomm_rez_path)
        (conversion.stats.audit_models.in_lomm_only, "MODELS", mm9.models_rez, "MODELS.REZ", lomm.models_rez),
        (conversion.stats.audit_skins.in_lomm_only, "SKINS", mm9.skins_rez, "SKINS.REZ", lomm.skins_rez),
        (conversion.stats.audit_sounds.in_lomm_only, "SOUNDS", mm9.sounds_rez, "SOUNDS.REZ", lomm.sounds_rez),
    ]

    copied_assets_by_type = {
        "MODELS": [],
        "SKINS": [],
        "SOUNDS": [],
    }

    updates_by_rez = {}

    for refs, subdir, target_rez_path, target_rez_name, lomm_rez_path in missing_assets_configs:
        if not refs:
            continue
        if not target_rez_path or not os.path.isfile(target_rez_path):
            raise ConversionServiceError(
                f"Cannot copy missing assets of type {subdir} because the target "
                f"archive {target_rez_name} is missing from the MM9 installation."
            )
        
        for ref in refs:
            stem, _ = converter._normalize_asset_path(ref)
            extracted = _extract_asset(lomm.data_dir, subdir, lomm_rez_path, stem)
            if extracted is None:
                raise ConversionServiceError(
                    f"Failed to find missing asset {ref!r} in LoMM files or archives."
                )
            vpath, data, restype_val = extracted
            
            is_loose = _find_loose_asset(lomm.data_dir, subdir, stem) is not None
            source_desc = "loose file" if is_loose else f"archive {os.path.basename(lomm_rez_path)}"
            copied_assets_by_type[subdir].append((ref, vpath, source_desc))
            
            if target_rez_path not in updates_by_rez:
                updates_by_rez[target_rez_path] = []
            updates_by_rez[target_rez_path].append((vpath, data, restype_val))

    # 2. Build transactions
    transactions: List[RezTransaction] = []
    
    # WORLDS.REZ is always present
    temp_output_worlds = os.path.join(
        mm9.data_dir,
        f".WORLDS.lomm_to_mm9_{stamp}_{os.getpid()}.REZ.tmp",
    )
    backup_path_worlds = os.path.join(backup_dir, "WORLDS.REZ")
    transactions.append(RezTransaction(
        target_path=mm9.worlds_rez,
        temp_path=temp_output_worlds,
        backup_path=backup_path_worlds,
        archive_name="WORLDS.REZ",
        updates=[(conversion.output_virtual_path, conversion.dat_bytes, restype)]
    ))

    for target_rez_path, updates in updates_by_rez.items():
        name = os.path.basename(target_rez_path)
        temp_path = os.path.join(
            mm9.data_dir,
            f".{name}.lomm_to_mm9_{stamp}_{os.getpid()}.REZ.tmp",
        )
        backup_path = os.path.join(backup_dir, name)
        transactions.append(RezTransaction(
            target_path=target_rez_path,
            temp_path=temp_path,
            backup_path=backup_path,
            archive_name=name,
            updates=updates
        ))

    try:
        # Step 1: Write and commit all temporary archives
        for tx in transactions:
            with rezmgr.RezWriter(tx.target_path, tx.temp_path) as writer:
                for vpath, data, restype_val in tx.updates:
                    writer.add(vpath, data, restype=restype_val)
                writer.commit()
            log.append(f"wrote temporary archive {tx.temp_path}")

        # Step 2: Back up the original archives
        os.makedirs(backup_dir, exist_ok=True)
        for tx in transactions:
            shutil.copy2(tx.target_path, tx.backup_path)
            log.append(f"backed up original {tx.archive_name} to {tx.backup_path}")

        # Step 3: Replace target archives
        for tx in transactions:
            os.replace(tx.temp_path, tx.target_path)
            log.append(f"installed modified {tx.archive_name} into {tx.target_path}")

        # Step 4: Verify all replaced archives
        for tx in transactions:
            if tx.archive_name == "WORLDS.REZ":
                _verify_inserted_level(tx.target_path, conversion.output_virtual_path)
            else:
                _verify_inserted_assets(tx.target_path, [u[0] for u in tx.updates])
            log.append(f"verified {tx.archive_name}")

        # Step 5: Write log and manifest
        try:
            _write_conversion_log(backup_dir, copied_assets_by_type, log)
        except Exception as exc:
            log.append(f"warning: could not write conversion log: {exc}")

        try:
            manifest_path = _write_conversion_manifest(
                request=request,
                conversion=conversion,
                mm9=mm9,
                backup_dir=backup_dir,
                transactions=transactions,
                stamp=stamp,
            )
            log.append(f"wrote conversion manifest {manifest_path}")
        except Exception as exc:
            manifest_path = ""
            log.append(f"warning: could not write conversion manifest: {exc}")

    except Exception as exc:
        log.append(f"error encountered: {exc}; rolling back modifications")
        for tx in transactions:
            if os.path.exists(tx.backup_path):
                restore_tmp = tx.temp_path + ".restore"
                try:
                    shutil.copy2(tx.backup_path, restore_tmp)
                    os.replace(restore_tmp, tx.target_path)
                    log.append(f"restored original {tx.archive_name} from backup")
                except Exception as restore_err:
                    log.append(f"error restoring {tx.archive_name}: {restore_err}")
            try:
                if os.path.exists(tx.temp_path):
                    os.remove(tx.temp_path)
            except OSError:
                pass
        
        if isinstance(exc, ConversionServiceError):
            raise
        raise ConversionServiceError(f"Failed to install converted level: {exc}") from exc

    return InsertConvertedLevelResult(
        conversion=conversion,
        worlds_rez=mm9.worlds_rez,
        backup_path=backup_path_worlds,
        backup_dir=backup_dir,
        manifest_path=manifest_path,
        temp_output_path=temp_output_worlds,
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
    transactions: List[RezTransaction],
    stamp: str,
) -> str:
    path = os.path.join(os.path.dirname(backup_dir), "install_manifest.json")
    archives_doc = []
    for tx in transactions:
        archives_doc.append({
            "name": tx.archive_name,
            "source_path": "",
            "target_path": tx.target_path,
            "backup_path": tx.backup_path,
            "size": os.path.getsize(tx.backup_path),
        })
    doc = {
        "version": 1,
        "installed_at": stamp,
        "failed": False,
        "batch_dir": "",
        "game_data_dir": mm9.data_dir,
        "backup_dir": backup_dir,
        "archives": archives_doc,
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
        "audit_sounds": _asset_audit_dict(stats.audit_sounds),
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
