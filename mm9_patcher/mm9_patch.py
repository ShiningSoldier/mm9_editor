#!/usr/bin/env python3
"""
mm9_patch.py
============

Patcher for Might and Magic IX world files (.DAT v66).

Treats a .DAT as four regions:

    [44-byte header]
    [BSP / world models]            (44 .. ObjectDataPos)
    [WorldObject section]           (ObjectDataPos .. RenderDataPos)
    [RenderData (lightmaps etc.)]   (RenderDataPos .. EOF)

Adding NPCs, props, lights, triggers, etc. only touches the WorldObject
section. We append new records (cloned from existing ones in the same DAT or
any other DAT, with field-level overrides), recompute RenderDataPos in the
header, and write the result. The BSP, lightmaps, and PVS are bit-for-bit
preserved.

Path formats
------------
All commands that take a .DAT path also accept the compact form:

    WORLDS.REZ::WORLDS/BOOTCAMP.DAT

where WORLDS.REZ is the archive and WORLDS/BOOTCAMP.DAT is the virtual path
inside it. The level is extracted to a temporary file for reading (inspect,
roundtrip) or as a template source (apply). This requires that the script is
run from inside the mm9_editor folder so that mm9_rezmgr is importable; if
it is not found, only plain .DAT paths work.

Usage
-----
    # Inspect all objects in a level (from loose DAT):
    python mm9_patch.py inspect  WORLDS/BOOTCAMP.DAT

    # Inspect directly from the REZ archive:
    python mm9_patch.py inspect  data/WORLDS.REZ::WORLDS/BOOTCAMP.DAT

    # List all DAT entries in a REZ (omit the :: part):
    python mm9_patch.py inspect  data/WORLDS.REZ

    # Round-trip a level (parse → re-serialise → confirm byte-identical):
    python mm9_patch.py roundtrip WORLDS/BOOTCAMP.DAT
    python mm9_patch.py roundtrip data/WORLDS.REZ::WORLDS/BOOTCAMP.DAT

    # Apply a YAML mod script:
    python mm9_patch.py apply config.yaml

YAML/JSON config schema:

    target: WORLDS/BOOTCAMP.DAT
    output: WORLDS/BOOTCAMP_modded.DAT     # optional
    backup: true                            # optional
    add:
      - clone:
          from: WORLDS/BOOTCAMP.DAT         # optional, defaults to target
          class: CommonerHuman2MaleA        # required
          instance: CommonerHuman2MaleA0    # optional, defaults to first
        overrides:
          Name: TestPeasant1
          Pos: [10000, 552, -2624]
          Rotation: [0, 1.5708, 0, 0]
          ScriptName: ""
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import struct
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional RezReader import (only needed for REZ:: path syntax)
# ---------------------------------------------------------------------------

def _try_import_rez_reader():
    """Return RezReader class or None if mm9_rezmgr is not importable."""
    # The script lives in mm9_patcher/; parent dir contains the core package.
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        from core.rezmgr import RezReader  # type: ignore
        return RezReader
    except ImportError:
        return None

_REZ_SEP = "::"


def _is_rez_spec(path: str) -> bool:
    """True for 'some.REZ' or 'some.REZ::entry'."""
    base = path.split(_REZ_SEP, 1)[0]
    return base.lower().endswith(".rez")


def _extract_rez_entry_to_tmp(rez_path: str, entry: str) -> str:
    """
    Extract *entry* from *rez_path* into a NamedTemporaryFile and return
    the temp file path. Caller is responsible for deleting it.
    Raises RuntimeError if RezReader is not available.
    """
    RezReader = _try_import_rez_reader()
    if RezReader is None:
        raise RuntimeError(
            "mm9_rezmgr is not importable — run this script from inside the "
            "mm9_editor folder, or use a plain .DAT path instead."
        )
    reader = RezReader(rez_path).open()
    data = reader.extract_to_bytes(entry)
    fd, tmp_path = tempfile.mkstemp(suffix=".DAT", prefix="mm9patch_")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return tmp_path


def _list_rez_dats(rez_path: str) -> List[str]:
    """Return sorted list of all .DAT entry paths inside a REZ archive."""
    RezReader = _try_import_rez_reader()
    if RezReader is None:
        raise RuntimeError(
            "mm9_rezmgr is not importable — run this script from inside the "
            "mm9_editor folder."
        )
    reader = RezReader(rez_path).open()
    return sorted(p for p in reader.list_paths() if p.upper().endswith(".DAT"))


# ---------------------------------------------------------------------------
# Header (44 bytes)
# ---------------------------------------------------------------------------

HEADER_FMT = "<11I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
DAT_VERSION = 66


@dataclass
class Header:
    version: int
    obj_pos: int
    ren_pos: int
    dummy: Tuple[int, ...]

    @classmethod
    def parse(cls, data: bytes) -> "Header":
        v, op, rp, *dummy = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
        if v != DAT_VERSION:
            raise ValueError(f"Unsupported DAT version {v} (expected {DAT_VERSION})")
        return cls(v, op, rp, tuple(dummy))

    def pack(self) -> bytes:
        return struct.pack(HEADER_FMT, self.version, self.obj_pos, self.ren_pos, *self.dummy)


# ---------------------------------------------------------------------------
# WorldObject parser / serializer
# ---------------------------------------------------------------------------

@dataclass
class Property:
    name: str
    code: int
    flags: int
    value: Any
    orig_dlen: Optional[int] = None
    dirty: bool = False
    # MM9's files occasionally store a property DataLength that doesn't match
    # the actual value-byte count (a redundant field the engine ignores for
    # variable-size types like strings). We preserve the original DataLength
    # for unchanged properties so unmodified files round-trip byte-identically;
    # for changed properties we always recompute it.


@dataclass
class WorldObject:
    type_str: str
    props: List[Property] = field(default_factory=list)

    def get(self, name: str, default: Any = None) -> Any:
        for p in self.props:
            if p.name == name:
                return p.value
        return default

    def set(self, name: str, value: Any) -> None:
        for p in self.props:
            if p.name == name:
                p.value = _coerce(p.code, value)
                p.dirty = True
                return
        raise KeyError(f"Property {name!r} not found on {self.type_str}")


def _read_lt_string(buf: bytes, off: int) -> Tuple[str, int]:
    n = struct.unpack_from("<H", buf, off)[0]
    s = buf[off + 2 : off + 2 + n].decode("latin-1")
    return s, off + 2 + n


def _write_lt_string(s: str) -> bytes:
    b = s.encode("latin-1")
    return struct.pack("<H", len(b)) + b


def _coerce(code: int, raw: Any) -> Any:
    if code == 0:
        return "" if raw is None else str(raw)
    if code in (1, 2):
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise ValueError(f"code {code} expects [x, y, z], got {raw!r}")
        return tuple(float(x) for x in raw)
    if code == 3:
        return float(raw)
    if code == 5:
        return 1 if raw else 0
    if code in (4, 6):
        return int(raw) & 0xFFFFFFFF
    if code == 7:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            raise ValueError(f"code 7 expects [x, y, z, w], got {raw!r}")
        return tuple(float(x) for x in raw)
    raise ValueError(f"unknown property code {code}")


def _read_value(code: int, buf: bytes, off: int, declared_len: int) -> Tuple[Any, int]:
    if code == 0:
        return _read_lt_string(buf, off)
    if code in (1, 2):
        return tuple(struct.unpack_from("<3f", buf, off)), off + 12
    if code == 3:
        return struct.unpack_from("<f", buf, off)[0], off + 4
    if code == 5:
        return buf[off], off + 1
    if code in (4, 6):
        return struct.unpack_from("<I", buf, off)[0], off + 4
    if code == 7:
        return tuple(struct.unpack_from("<4f", buf, off)), off + 16
    return buf[off : off + declared_len], off + declared_len


def _write_value(code: int, value: Any) -> bytes:
    if code == 0:
        return _write_lt_string("" if value is None else str(value))
    if code in (1, 2):
        return struct.pack("<3f", *value)
    if code == 3:
        return struct.pack("<f", float(value))
    if code == 5:
        return struct.pack("<B", 1 if value else 0)
    if code in (4, 6):
        return struct.pack("<I", int(value) & 0xFFFFFFFF)
    if code == 7:
        return struct.pack("<4f", *value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raise ValueError(f"unknown property code {code}")


def parse_objects(buf: bytes, offset: int) -> Tuple[List[WorldObject], int]:
    o = offset
    count = struct.unpack_from("<I", buf, o)[0]
    o += 4
    objs: List[WorldObject] = []
    for _ in range(count):
        declared = struct.unpack_from("<H", buf, o)[0]
        o += 2
        body_start = o
        type_str, o = _read_lt_string(buf, o)
        prop_count = struct.unpack_from("<I", buf, o)[0]
        o += 4
        props: List[Property] = []
        for _ in range(prop_count):
            name, o = _read_lt_string(buf, o)
            code = buf[o]; o += 1
            flags = struct.unpack_from("<I", buf, o)[0]; o += 4
            dl = struct.unpack_from("<H", buf, o)[0]; o += 2
            value, o = _read_value(code, buf, o, dl)
            props.append(Property(name, code, flags, value, orig_dlen=dl))
        # Object's declared body length is informational; we don't enforce it
        # because some MM9 files have property-level DataLength quirks that
        # would make this check fail spuriously. We trust value-type sizes.
        objs.append(WorldObject(type_str, props))
    return objs, o


def serialize_objects(objs: List[WorldObject]) -> bytes:
    out = bytearray()
    out += struct.pack("<I", len(objs))
    for obj in objs:
        body = bytearray()
        body += _write_lt_string(obj.type_str)
        body += struct.pack("<I", len(obj.props))
        for p in obj.props:
            body += _write_lt_string(p.name)
            body += struct.pack("<B", p.code)
            body += struct.pack("<I", p.flags)
            v_bytes = _write_value(p.code, p.value)
            if p.orig_dlen is not None and not p.dirty:
                dlen = p.orig_dlen
            else:
                dlen = len(v_bytes)
            body += struct.pack("<H", dlen)
            body += v_bytes
        if len(body) > 0xFFFF:
            raise RuntimeError(
                f"WorldObject {obj.type_str!r} body is {len(body)} bytes; "
                f"DataLength overflows uint16."
            )
        out += struct.pack("<H", len(body)) + body
    return bytes(out)


# ---------------------------------------------------------------------------
# World file
# ---------------------------------------------------------------------------

@dataclass
class World:
    header: Header
    pre_objects: bytes
    objects: List[WorldObject]
    render_data: bytes

    @classmethod
    def load(cls, path: str) -> "World":
        with open(path, "rb") as f:
            data = f.read()
        hdr = Header.parse(data)
        pre = data[HEADER_SIZE : hdr.obj_pos]
        objs, obj_end = parse_objects(data, hdr.obj_pos)
        if obj_end != hdr.ren_pos:
            raise RuntimeError(
                f"WorldObject section ended at {obj_end} but RenderDataPos is {hdr.ren_pos}"
            )
        render = data[hdr.ren_pos:]
        return cls(hdr, pre, objs, render)

    def save(self, path: str) -> None:
        obj_section = serialize_objects(self.objects)
        new_obj_pos = HEADER_SIZE + len(self.pre_objects)
        new_ren_pos = new_obj_pos + len(obj_section)
        new_hdr = Header(self.header.version, new_obj_pos, new_ren_pos, self.header.dummy)
        with open(path, "wb") as f:
            f.write(new_hdr.pack())
            f.write(self.pre_objects)
            f.write(obj_section)
            f.write(self.render_data)


def find_template(world: World, class_name: str, instance_name: Optional[str]) -> WorldObject:
    matches = [o for o in world.objects if o.type_str == class_name]
    if not matches:
        raise LookupError(f"No instances of class {class_name!r} found")
    if instance_name is None:
        return matches[0]
    for o in matches:
        if o.get("Name") == instance_name:
            return o
    names = ", ".join(o.get("Name", "?") for o in matches[:8])
    raise LookupError(
        f"Class {class_name!r} found but no instance named {instance_name!r}. "
        f"Available: {names}{' ...' if len(matches) > 8 else ''}"
    )


# ---------------------------------------------------------------------------
# Path resolution helper (handles REZ:: specs)
# ---------------------------------------------------------------------------

class _TmpCleanup:
    """Context manager that deletes a temp file on exit (only if we created it)."""
    def __init__(self, path: str, owned: bool) -> None:
        self.path   = path
        self.owned  = owned
    def __enter__(self):
        return self.path
    def __exit__(self, *_):
        if self.owned:
            try:
                os.unlink(self.path)
            except OSError:
                pass


def _open_dat_path(spec: str, cfg_dir: str = "") -> "_TmpCleanup":
    """
    Resolve *spec* to a plain .DAT file path, extracting from a REZ archive
    if needed, and return a _TmpCleanup context manager.

    Accepted formats:
      - plain path:          WORLDS/BOOTCAMP.DAT
      - REZ+entry:           data/WORLDS.REZ::WORLDS/BOOTCAMP.DAT
    """
    if _REZ_SEP in spec:
        rez_part, entry = spec.split(_REZ_SEP, 1)
        rez_part = _resolve(rez_part, cfg_dir)
        tmp = _extract_rez_entry_to_tmp(rez_part, entry)
        return _TmpCleanup(tmp, owned=True)
    return _TmpCleanup(_resolve(spec, cfg_dir), owned=False)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace) -> int:
    spec = args.path

    # If it's a bare .REZ path (no ::), list the DAT entries inside it.
    if _is_rez_spec(spec) and _REZ_SEP not in spec:
        try:
            dats = _list_rez_dats(spec)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"{spec}  ({len(dats)} DAT entries)")
        for p in dats:
            print(f"  {p}")
        print(f"\nTo inspect a specific level:")
        print(f"  python mm9_patch.py inspect \"{spec}::{dats[0] if dats else 'WORLDS/LEVELNAME.DAT'}\"")
        return 0

    with _open_dat_path(spec) as dat_path:
        try:
            w = World.load(dat_path)
        except Exception as e:
            print(f"error loading {spec!r}: {e}", file=sys.stderr)
            return 1
        label = spec
        print(f"{label}")
        print(f"  version={w.header.version}  obj_pos={w.header.obj_pos}  ren_pos={w.header.ren_pos}")
        print(f"  pre-object bytes: {len(w.pre_objects):>10}")
        print(f"  object section  : {len(serialize_objects(w.objects)):>10}  ({len(w.objects)} objects)")
        print(f"  render data     : {len(w.render_data):>10}")
        print()
        ctr = Counter(o.type_str for o in w.objects)
        print(f"Class counts ({len(ctr)} classes):")
        for c, n in sorted(ctr.items(), key=lambda x: -x[1]):
            print(f"  {n:>4}  {c}")
        print("\nKey landmarks:")
        for o in w.objects:
            if o.type_str in ("StartPoint", "ExitTrigger"):
                pos = o.get("Pos") or (0, 0, 0)
                rot = o.get("Rotation") or (0, 0, 0, 0)
                print(f"  {o.type_str:14s}  Name={str(o.get('Name')):20s}  "
                      f"Pos=({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f})  "
                      f"YawRad={rot[1]:.3f}")
    return 0


def cmd_roundtrip(args: argparse.Namespace) -> int:
    spec = args.path
    with _open_dat_path(spec) as dat_path:
        try:
            with open(dat_path, "rb") as f:
                original = f.read()
            w = World.load(dat_path)
        except Exception as e:
            print(f"error loading {spec!r}: {e}", file=sys.stderr)
            return 1
    obj_section = serialize_objects(w.objects)
    new_obj_pos = HEADER_SIZE + len(w.pre_objects)
    new_ren_pos = new_obj_pos + len(obj_section)
    new_hdr = Header(w.header.version, new_obj_pos, new_ren_pos, w.header.dummy).pack()
    rebuilt = new_hdr + w.pre_objects + obj_section + w.render_data
    if rebuilt == original:
        print(f"OK: {spec} round-trips byte-identically ({len(original)} bytes)")
        return 0
    minlen = min(len(rebuilt), len(original))
    diff_at = next((i for i in range(minlen) if rebuilt[i] != original[i]), minlen)
    print(f"FAIL: byte mismatch at 0x{diff_at:x} for {spec}")
    print(f"  original len={len(original)}  rebuilt len={len(rebuilt)}")
    print(f"  orig [diff..+16]={original[diff_at:diff_at+16].hex()}")
    print(f"  rbld [diff..+16]={rebuilt[diff_at:diff_at+16].hex()}")
    return 1


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config)
    cfg_dir = os.path.dirname(os.path.abspath(args.config))
    target_spec = cfg["target"]
    output_path = cfg.get("output") or _default_output(_resolve(target_spec, cfg_dir))
    output_path = _resolve(output_path, cfg_dir)
    do_backup = bool(cfg.get("backup", False))

    print(f"target: {target_spec}")
    print(f"output: {output_path}")

    with _open_dat_path(target_spec, cfg_dir) as target_dat:
        target = World.load(target_dat)
        target_abs = os.path.abspath(target_dat)
    print(f"  loaded {len(target.objects)} existing objects")

    sources: Dict[str, World] = {target_abs: target}
    additions = cfg.get("add") or []
    for i, entry in enumerate(additions):
        clone = entry["clone"]
        src_spec = clone.get("from", target_spec)
        with _open_dat_path(src_spec, cfg_dir) as src_dat:
            ap = os.path.abspath(src_dat)
            if ap not in sources:
                sources[ap] = World.load(src_dat)
            src = sources[ap]
        cls = clone["class"]
        inst = clone.get("instance")
        template = find_template(src, cls, inst)
        new_obj = copy.deepcopy(template)
        for k, v in (entry.get("overrides") or {}).items():
            new_obj.set(k, v)
        target.objects.append(new_obj)
        name = new_obj.get("Name") or "?"
        pos = new_obj.get("Pos") or (0, 0, 0)
        print(f"  + [{i}] {cls:24s}  Name={name:24s}  "
              f"Pos=({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f})  "
              f"(template from {os.path.basename(src_spec)})")

    if do_backup and os.path.abspath(output_path) == os.path.abspath(_resolve(target_spec, cfg_dir)):
        bak = _resolve(target_spec, cfg_dir) + ".bak"
        if not os.path.exists(bak):
            with open(_resolve(target_spec, cfg_dir), "rb") as f_in, open(bak, "wb") as f_out:
                f_out.write(f_in.read())
            print(f"  backup written: {bak}")

    target.save(output_path)
    print(f"  wrote {output_path}: now {len(target.objects)} objects")

    verify = World.load(output_path)
    print(f"  verify: re-parsed OK, {len(verify.objects)} objects, "
          f"obj_pos={verify.header.obj_pos}  ren_pos={verify.header.ren_pos}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml  # type: ignore
    except ImportError:
        return json.loads(text)

    # MM9 stores several "numeric" properties (NPCNbr, RangeAttackType,
    # MaxRailPath, etc.) as IEEE-754 float bit-patterns inside LongInt
    # (code-6) slots. The !float_bits YAML tag lets users write e.g.
    #     NPCNbr: !float_bits 437
    # and have it serialized as the right uint32.
    def _flt(loader, node):
        return struct.unpack("<I", struct.pack("<f", float(node.value)))[0]

    L = yaml.SafeLoader
    yaml.add_constructor("!float_bits", _flt, Loader=L)
    return yaml.load(text, Loader=L)


def _default_output(target: str) -> str:
    base, ext = os.path.splitext(target)
    return f"{base}_modded{ext}"


def _resolve(path: str, base: str) -> str:
    if os.path.isabs(path) or os.path.exists(path):
        return path
    candidate = os.path.normpath(os.path.join(base, path))
    return candidate if os.path.exists(candidate) else path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Inspect and patch MM9 .DAT v66 world files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Path formats accepted by 'inspect' and 'roundtrip':\n"
            "  WORLDS/BOOTCAMP.DAT                        plain .DAT file\n"
            "  data/WORLDS.REZ                            list all DAT entries in archive\n"
            "  data/WORLDS.REZ::WORLDS/BOOTCAMP.DAT       extract and inspect one level\n"
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="dump class counts and key landmarks")
    p_ins.add_argument("path", help=".DAT file, .REZ archive, or .REZ::entry path")
    p_ins.set_defaults(func=cmd_inspect)

    p_rt = sub.add_parser("roundtrip", help="parse and re-serialize; verify byte-identical output")
    p_rt.add_argument("path", help=".DAT file or .REZ::entry path")
    p_rt.set_defaults(func=cmd_roundtrip)

    p_app = sub.add_parser("apply", help="apply a YAML/JSON patch config")
    p_app.add_argument("config")
    p_app.set_defaults(func=cmd_apply)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
