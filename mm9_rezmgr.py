"""
mm9_rezmgr.py
=============

Reader for Lithtech .REZ archive files (v1 — the format MM9 uses).

The format is fully documented in https://github.com/jsj2008/lithtech libs/rezmgr/rezmgr.cpp. This is a
straight port of the read path:

    [167-byte main header]                  see _MainHeader
    [file payloads — each entry's bytes at its recorded position]
    [directory tree blocks — each block is a flat list of entries]

Reading the archive doesn't load any payload bytes; it just walks the
directory tree and remembers (path → (offset, size)) for each resource.
Payloads are pulled lazily via .extract() / .extract_to_bytes().

Public API:

    rez = RezReader(path).open()
    rez.list_paths()            # ['WORLDS/BOOTCAMP.DAT', 'WORLDS/...', ...]
    rez.find('WORLDS/BOOTCAMP.DAT')             # → ResourceEntry or None
    data = rez.extract_to_bytes('WORLDS/BOOTCAMP.DAT')
    rez.extract('WORLDS/BOOTCAMP.DAT', '/tmp/x.dat')
    rez.close()

The reader is read-only. The writer is a separate concern (Phase 2c, with
backups + conservative work_dir output).
"""

from __future__ import annotations

import io
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Format constants
# --------------------------------------------------------------------------

USER_TITLE_SIZE = 60
EXPECTED_FILETYPE_PREFIX = "RezMgr Version 1"
EXPECTED_USERTITLE       = "LithTech Resource File"

ENTRY_TYPE_RESOURCE  = 0
ENTRY_TYPE_DIRECTORY = 1


# --------------------------------------------------------------------------
# Header (167 bytes, packed)
# --------------------------------------------------------------------------

# struct FileMainHeaderStruct:
#   char  CR1, LF1
#   char  FileType[60]
#   char  CR2, LF2
#   char  UserTitle[60]
#   char  CR3, LF3, EOF1
#   DWORD FileFormatVersion
#   DWORD RootDirPos, RootDirSize, RootDirTime
#   DWORD NextWritePos
#   DWORD Time
#   DWORD LargestKeyAry
#   DWORD LargestDirNameSize, LargestRezNameSize, LargestCommentSize
#   BYTE  IsSorted
_HEADER_FMT = (
    "< "                       # little-endian, packed
    "BB "                      # CR1 LF1
    f"{USER_TITLE_SIZE}s "     # FileType
    "BB "                      # CR2 LF2
    f"{USER_TITLE_SIZE}s "     # UserTitle
    "BBB "                     # CR3 LF3 EOF1
    "I "                       # FileFormatVersion
    "I I I "                   # RootDirPos / Size / Time
    "I "                       # NextWritePos
    "I "                       # Time
    "I "                       # LargestKeyAry
    "I I I "                   # LargestDirNameSize / RezNameSize / CommentSize
    "B"                        # IsSorted
).replace(" ", "")
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


@dataclass
class _MainHeader:
    file_type:    str
    user_title:   str
    version:      int
    root_pos:     int
    root_size:    int
    root_time:    int
    next_write:   int
    last_modified: int
    largest_key_ary:    int
    largest_dir_name:   int
    largest_rez_name:   int
    largest_comment:    int
    is_sorted:    int

    @classmethod
    def parse(cls, buf: bytes) -> "_MainHeader":
        if len(buf) < _HEADER_SIZE:
            raise ValueError(f"too short for .rez header (need {_HEADER_SIZE} bytes, got {len(buf)})")
        (cr1, lf1, ft, cr2, lf2, ut, cr3, lf3, eof1,
         ver, rpos, rsize, rtime, nwrite, ltime,
         lka, ldn, lrn, lc, sorted_) = struct.unpack(_HEADER_FMT, buf[:_HEADER_SIZE])
        if cr1 != 0x0d or cr2 != 0x0d or cr3 != 0x0d:
            raise ValueError("not a .rez file (CR markers missing)")
        if lf1 != 0x0a or lf2 != 0x0a or lf3 != 0x0a:
            raise ValueError("not a .rez file (LF markers missing)")
        if eof1 != 0x1a:
            raise ValueError("not a .rez file (EOF marker missing)")
        if ver != 1:
            raise ValueError(f"unsupported .rez format version {ver} (expected 1)")
        return cls(
            file_type    = ft.decode("latin-1").rstrip(" \0"),
            user_title   = ut.decode("latin-1").rstrip(" \0"),
            version      = ver,
            root_pos     = rpos,
            root_size    = rsize,
            root_time    = rtime,
            next_write   = nwrite,
            last_modified = ltime,
            largest_key_ary  = lka,
            largest_dir_name = ldn,
            largest_rez_name = lrn,
            largest_comment  = lc,
            is_sorted    = sorted_,
        )


# --------------------------------------------------------------------------
# Directory tree
# --------------------------------------------------------------------------

@dataclass
class ResourceEntry:
    """A file inside the archive."""
    name:        str
    pos:         int      # offset of the file's bytes in the .rez
    size:        int      # number of bytes
    time:        int
    res_id:      int
    res_type:    int      # 4-char DWORD, e.g., "DAT " for .DAT files
    description: str
    keys:        List[int]
    parent:      "Directory"
    # Where this entry's record starts inside the directory block in the
    # .rez file. The writer uses this to update pos/size/time/res_type in
    # place. Set during reading; not part of the on-disk format.
    entry_offset: int = 0

    def virtual_path(self) -> str:
        parts = [self.name]
        d = self.parent
        while d.parent is not None:
            parts.append(d.name)
            d = d.parent
        return "/".join(reversed(parts))

    def typed_virtual_path(self) -> str:
        typ = self.type_str
        if not typ:
            return self.virtual_path()
        return f"{self.virtual_path()}.{typ}"

    @property
    def type_str(self) -> str:
        # MM9 stores the 4-char type code byte-reversed: 'DAT' is on disk as
        # bytes 0x54 0x41 0x44 0x00 ("TAD\0"). Reverse to recover the natural
        # extension, then strip null/space padding from both ends.
        le_bytes = self.res_type.to_bytes(4, "little")
        return le_bytes[::-1].decode("latin-1", errors="replace").strip("\x00 ")


@dataclass
class Directory:
    name:    str
    pos:     int       # 0 for root
    size:    int       # 0 for root (empty placeholder)
    time:    int
    parent:  Optional["Directory"] = None
    subdirs: Dict[str, "Directory"] = field(default_factory=dict)
    files:   Dict[str, ResourceEntry] = field(default_factory=dict)
    file_entries: List[ResourceEntry] = field(default_factory=list)

    def virtual_path(self) -> str:
        if self.parent is None:
            return ""
        parts = [self.name]
        d = self.parent
        while d.parent is not None:
            parts.append(d.name)
            d = d.parent
        return "/".join(reversed(parts))


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------

class RezReader:
    def __init__(self, path: str):
        self.path = path
        self._fh:    Optional[io.BufferedReader] = None
        self.header: Optional[_MainHeader] = None
        self.root:   Optional[Directory] = None
        self._entries: List[ResourceEntry] = []
        # Path index: lower-cased virtual path → ResourceEntry
        self._index_lower: Dict[str, ResourceEntry] = {}
        # Original-case virtual path → ResourceEntry (for listing)
        self._index_orig:  Dict[str, ResourceEntry] = {}
        self._display_paths: Dict[str, ResourceEntry] = {}

    # ----- lifecycle -----

    def open(self) -> "RezReader":
        self._fh = open(self.path, "rb")
        self._fh.seek(0)
        self.header = _MainHeader.parse(self._fh.read(_HEADER_SIZE))
        self.root = Directory(name="", pos=self.header.root_pos,
                              size=self.header.root_size,
                              time=self.header.root_time)
        self._read_directory_block(self.root)
        self._rebuild_indexes()
        return self

    def close(self) -> None:
        if self._fh: self._fh.close()
        self._fh = None

    def __enter__(self) -> "RezReader":
        return self.open()
    def __exit__(self, *exc) -> None:
        self.close()

    # ----- public lookups -----

    def list_paths(self) -> List[str]:
        return sorted(self._display_paths.keys())

    def find(self, virtual_path: str, *, case_sensitive: bool = False) -> Optional[ResourceEntry]:
        idx = self._index_orig if case_sensitive else self._index_lower
        key = virtual_path if case_sensitive else virtual_path.lower()
        return idx.get(key.replace("\\", "/"))

    def extract_to_bytes(self, virtual_path: str) -> bytes:
        ent = self.find(virtual_path)
        if ent is None:
            raise FileNotFoundError(f"{virtual_path!r} not in {self.path}")
        assert self._fh is not None
        self._fh.seek(ent.pos)
        data = self._fh.read(ent.size)
        if len(data) != ent.size:
            raise IOError(f"short read for {virtual_path}: got {len(data)} of {ent.size}")
        return data

    def peek_bytes(self, virtual_path: str, n: int = 4) -> bytes:
        """Read just the first n bytes of a file payload. Cheap — used to
        detect format by magic number without extracting the whole entry."""
        ent = self.find(virtual_path)
        if ent is None or ent.size == 0:
            return b""
        assert self._fh is not None
        self._fh.seek(ent.pos)
        return self._fh.read(min(n, ent.size))

    def extract(self, virtual_path: str, output_path: str) -> None:
        data = self.extract_to_bytes(virtual_path)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(data)

    # ----- directory tree walking -----

    def _read_directory_block(self, dir_obj: Directory) -> None:
        """Read a directory block at dir_obj.pos and populate it. Recurse into subdirs."""
        if dir_obj.pos == 0 or dir_obj.size == 0:
            return
        assert self._fh is not None
        self._fh.seek(dir_obj.pos)
        block = self._fh.read(dir_obj.size)
        if len(block) != dir_obj.size:
            raise IOError(f"short read for directory block at {dir_obj.pos}")

        cur = 0
        end = len(block)
        while cur < end:
            entry_type = struct.unpack_from("<I", block, cur)[0]; cur += 4

            if entry_type == ENTRY_TYPE_DIRECTORY:
                pos, size, time_ = struct.unpack_from("<III", block, cur); cur += 12
                name, cur = _read_cstring(block, cur)
                sub = Directory(name=name, pos=pos, size=size, time=time_, parent=dir_obj)
                dir_obj.subdirs[name] = sub
                self._read_directory_block(sub)

            elif entry_type == ENTRY_TYPE_RESOURCE:
                # Note: entry_offset is the absolute offset of the Type DWORD
                # we just consumed. cur was advanced by 4 right after reading
                # entry_type, so we subtract 4 to get back to it.
                entry_offset = dir_obj.pos + (cur - 4)
                pos, size, time_, res_id, res_type, num_keys = struct.unpack_from(
                    "<IIIIII", block, cur); cur += 24
                name, cur = _read_cstring(block, cur)
                desc, cur = _read_cstring(block, cur)
                keys = list(struct.unpack_from(f"<{num_keys}I", block, cur))
                cur += 4 * num_keys
                ent = ResourceEntry(
                    name=name, pos=pos, size=size, time=time_,
                    res_id=res_id, res_type=res_type,
                    description=desc, keys=keys, parent=dir_obj,
                    entry_offset=entry_offset,
                )
                dir_obj.file_entries.append(ent)
                self._entries.append(ent)
                old = dir_obj.files.get(name)
                if old is None or (old.type_str.upper() != "DAT"
                                   and ent.type_str.upper() == "DAT"):
                    dir_obj.files[name] = ent

            else:
                raise ValueError(f"unknown entry type {entry_type} at offset {dir_obj.pos + cur - 4}")

    def _rebuild_indexes(self) -> None:
        self._index_lower.clear()
        self._index_orig.clear()
        self._display_paths.clear()

        groups: Dict[str, List[ResourceEntry]] = {}
        for ent in self._entries:
            groups.setdefault(ent.virtual_path().lower(), []).append(ent)

        for ent in self._entries:
            base = ent.virtual_path()
            typed = ent.typed_virtual_path()
            duplicate = len(groups.get(base.lower(), [])) > 1
            display = typed if duplicate and ent.type_str else base
            self._display_paths[display] = ent
            self._add_index_alias(display, ent)
            if ent.type_str:
                self._add_index_alias(typed, ent)

            existing = self._index_lower.get(base.lower())
            if existing is None or (existing.type_str.upper() != "DAT"
                                    and ent.type_str.upper() == "DAT"):
                self._add_index_alias(base, ent)

    def _add_index_alias(self, path: str, ent: ResourceEntry) -> None:
        norm = path.replace("\\", "/")
        self._index_orig[norm] = ent
        self._index_lower[norm.lower()] = ent


def _read_cstring(buf: bytes, off: int) -> Tuple[str, int]:
    end = buf.index(b"\x00", off)
    return buf[off:end].decode("latin-1"), end + 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cmd_list(path: str) -> int:
    with RezReader(path) as r:
        print(f"{path}")
        print(f"  user title : {r.header.user_title!r}")
        print(f"  next write : {r.header.next_write:>12}  (file size {os.path.getsize(path)})")
        print(f"  root dir   : pos={r.header.root_pos} size={r.header.root_size}")
        print(f"  files      : {len(r._entries)}")
        print()
        for vp in r.list_paths()[:30]:
            ent = r.find(vp)
            print(f"  {ent.size:>10} bytes  {vp}  [{ent.type_str}]")
        if len(r._entries) > 30:
            print(f"  ... ({len(r._entries) - 30} more)")
    return 0


def _cmd_extract(path: str, virtual_path: str, output: str) -> int:
    with RezReader(path) as r:
        r.extract(virtual_path, output)
        print(f"extracted {virtual_path} → {output}  ({os.path.getsize(output)} bytes)")
    return 0


# --------------------------------------------------------------------------
# Writer (full rewrite — see commit() for the layout it produces)
# --------------------------------------------------------------------------

# Layout of a Resource directory entry from the start (offsets):
#   +0  Type        (DWORD = 0)
#   +4  Pos         (DWORD)
#   +8  Size        (DWORD)
#   +12 Time        (DWORD)
#   +16 ID          (DWORD)
#   +20 ResType     (DWORD)
#   +24 NumKeys     (DWORD)
#   +28 Name (\0)
#   ... Description (\0)
#   ... Keys
_ENTRY_POS_OFFSET     = 4
_ENTRY_SIZE_OFFSET    = 8
_ENTRY_TIME_OFFSET    = 12
_ENTRY_RESTYPE_OFFSET = 20

# The header field offsets we care about. Header is 167 bytes, packed.
# NextWritePos sits after: CR1+LF1 (2) + FileType (60) + CR2+LF2 (2)
# + UserTitle (60) + CR3+LF3+EOF1 (3) + FileFormatVersion (4) + RootDirPos (4)
# + RootDirSize (4) + RootDirTime (4) = 143
_HEADER_NEXT_WRITE_OFFSET = 2 + USER_TITLE_SIZE + 2 + USER_TITLE_SIZE + 3 + 4 + 4 + 4 + 4
_HEADER_TIME_OFFSET       = _HEADER_NEXT_WRITE_OFFSET + 4


def _restype_for_filename(name: str) -> int:
    """Map a virtual-path's basename to the 4-char DWORD MM9 stores in the
    ResType field. MM9 writes the extension byte-reversed and NUL-padded
    on the right, so 'DAT' lands on disk as bytes 'T' 'A' 'D' '\\0'
    (uint32 = 0x00444154 little-endian)."""
    ext = os.path.splitext(name)[1].lstrip(".").upper()
    if not ext:
        return 0
    rev_padded = (ext[::-1] + "\x00\x00\x00\x00")[:4]   # 'DAT' → 'TAD\0'
    return struct.unpack("<I", rev_padded.encode("latin-1"))[0]


def is_v66_dat_magic(payload_first_bytes: bytes) -> bool:
    """True iff the first 4 bytes of a file payload identify it as a v66
    compiled world (the only format the editor can edit)."""
    if len(payload_first_bytes) < 4:
        return False
    return struct.unpack("<I", payload_first_bytes[:4])[0] == 66


def restype_for_format_magic(magic: bytes) -> Optional[int]:
    """Detect a likely ResType from the first 4 bytes of the new payload.
    Returns None if we can't infer it.
      0x00000042  → world DAT (version 66) → 'TAD '
      0x000004E1  → DEdit ED (version 1249) → 'DE  '"""
    if len(magic) < 4:
        return None
    val = struct.unpack("<I", magic[:4])[0]
    if val == 66:                       # .DAT v66
        return _restype_for_filename("x.DAT")
    if val == 1249:                     # .ED
        return _restype_for_filename("x.ED")
    return None


class RezWriter:
    """Conservative-write rez modifier.

    Doesn't modify `source_path`. Produces `output_path` by FULLY REWRITING
    the file:

        [167-byte header (placeholder, filled in last)]
        [file payloads — original bytes for unmodified entries, new bytes for
         replaced entries, in original document order]
        [directory tree blocks — same shape as the source, but each entry's
         pos/size/time/restype updated to point at the new payloads]
        [updated header]

    This avoids the trap that NextWritePos is *not* the end-of-file append
    cursor — it's the boundary between payloads and the dir tree, which gets
    overwritten and rewritten on every modification.

    The directory tree shape is preserved exactly: same dirs, same names, no
    additions or renames. That's enough for the editor's 'replace a level
    inside WORLDS.REZ' workflow. (Adding new entries is a separate feature.)
    """

    def __init__(self, source_path: str, output_path: str):
        if os.path.abspath(source_path) == os.path.abspath(output_path):
            raise ValueError(
                "RezWriter is conservative: source and output must differ. "
                "Pick an output path inside work_dir.")
        self.source_path = source_path
        self.output_path = output_path
        self._reader: Optional[RezReader] = None
        # original entry_offset → (new_bytes, override_restype_or_None)
        # entry_offset is stable for same-name/different-type resources that
        # share a virtual path, e.g. WORLDS/BOOTCAMP.DAT and BOOTCAMP.ED.
        self._replacements: Dict[int, Tuple[bytes, Optional[int]]] = {}
        self._replacement_names: Dict[int, str] = {}
        # parent_vpath → [(name, bytes, restype)]  (new entries not in source)
        self._additions: Dict[str, List[Tuple[str, bytes, int]]] = {}
        # populated during commit() so _write_dir_block can read them
        self._new_entry_pos:  Dict[Tuple[str, str], int] = {}
        self._new_entry_size: Dict[Tuple[str, str], int] = {}
        self._new_entry_type: Dict[Tuple[str, str], int] = {}
        self._commit_now: int = 0

    def __enter__(self) -> "RezWriter":
        self._reader = RezReader(self.source_path).open()
        return self

    def __exit__(self, *exc) -> None:
        if self._reader: self._reader.close()
        self._reader = None

    def replace(self, virtual_path: str, new_bytes: bytes,
                restype: Optional[int] = None) -> None:
        """Stage a replacement. Doesn't touch the file until commit()."""
        if self._reader is None:
            raise RuntimeError("RezWriter must be used as a context manager")
        ent = self._reader.find(virtual_path)
        if ent is None:
            raise FileNotFoundError(f"{virtual_path!r} not in {self.source_path}")
        if restype is None:
            restype = restype_for_format_magic(new_bytes[:4])
        self._replacements[ent.entry_offset] = (new_bytes, restype)
        self._replacement_names[ent.entry_offset] = ent.typed_virtual_path()

    def add(self, virtual_path: str, new_bytes: bytes,
            restype: Optional[int] = None) -> None:
        """Stage a brand-new entry that does not yet exist in the source REZ.
        Use replace() for entries that already exist."""
        if self._reader is None:
            raise RuntimeError("RezWriter must be used as a context manager")
        if self._reader.find(virtual_path) is not None:
            raise ValueError(
                f"{virtual_path!r} already exists in the source — use replace() instead")
        if restype is None:
            # Infer type from the name's implied extension if possible
            restype = _restype_for_filename(virtual_path.split("/")[-1])
        parts = virtual_path.rsplit("/", 1)
        parent_vpath = parts[0] if len(parts) > 1 else ""
        name = parts[-1]
        self._additions.setdefault(parent_vpath, []).append(
            (name, new_bytes, restype))

    # ----- write helpers -----

    def _write_dir_block(self, out, dir_obj: "Directory",
                         new_pos: Dict[int, int],   # entry_offset → new pos
                         new_size: Dict[int, int],  # entry_offset → new size
                         new_restype: Dict[int, int],
                         dir_block_pos: Dict[int, Tuple[int, int]],  # id(dir) → (pos, size)
                         now: int) -> Tuple[int, int]:
        """Serialize a directory block at the file's current write cursor.
        Returns (block_pos, block_size). Recurses into subdirs first so their
        positions/sizes are known by the time we write the parent.

        The block contains, in document order: entries for each subdir, then
        entries for each file. We can't easily reconstruct the *exact*
        original ordering from our parsed tree, so we use this canonical
        ordering. The engine doesn't care about ordering."""
        # Recurse: write children's blocks first, capture their (pos, size)
        for sub in dir_obj.subdirs.values():
            sp, ss = self._write_dir_block(
                out, sub, new_pos, new_size, new_restype, dir_block_pos, now)
            dir_block_pos[id(sub)] = (sp, ss)

        # Now write our block at the current cursor
        block_pos = out.tell()
        for sub in dir_obj.subdirs.values():
            sp, ss = dir_block_pos[id(sub)]
            out.write(struct.pack("<I", ENTRY_TYPE_DIRECTORY))
            out.write(struct.pack("<III", sp, ss, sub.time))
            out.write(sub.name.encode("latin-1") + b"\x00")
        for ent in dir_obj.file_entries:
            ep   = new_pos.get(ent.entry_offset, ent.pos)
            es   = new_size.get(ent.entry_offset, ent.size)
            etype = new_restype.get(ent.entry_offset, ent.res_type)
            etime = now if ent.entry_offset in new_pos else ent.time
            out.write(struct.pack("<I", ENTRY_TYPE_RESOURCE))
            out.write(struct.pack("<III", ep, es, etime))
            out.write(struct.pack("<III", ent.res_id, etype, len(ent.keys)))
            out.write(ent.name.encode("latin-1") + b"\x00")
            out.write(ent.description.encode("latin-1") + b"\x00")
            for k in ent.keys:
                out.write(struct.pack("<I", k))
        # Write any brand-new entries staged via add() for this directory
        parent_vpath = dir_obj.virtual_path()
        for name, _data, restype in self._additions.get(parent_vpath, []):
            key = (parent_vpath, name)
            ep = self._new_entry_pos[key]
            es = self._new_entry_size[key]
            out.write(struct.pack("<I", ENTRY_TYPE_RESOURCE))
            out.write(struct.pack("<III", ep, es, self._commit_now))
            out.write(struct.pack("<III", 0, restype, 0))  # res_id=0, no keys
            out.write(name.encode("latin-1") + b"\x00")
            out.write(b"\x00")                              # empty description
        block_size = out.tell() - block_pos
        return block_pos, block_size

    # ----- commit -----

    def commit(self) -> Dict[str, Any]:
        """Write the output file. Returns a small log of what happened."""
        if self._reader is None:
            raise RuntimeError("RezWriter must be used as a context manager")

        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)) or ".",
                    exist_ok=True)

        # Walk every entry in the original *document order*, pulling either the
        # replacement bytes or copying the original payload bytes.
        # Order: depth-first by virtual path (stable), entries' original positions.
        all_entries = sorted(self._reader._entries, key=lambda e: e.pos)

        log: List[str] = []
        new_pos:     Dict[int, int] = {}   # entry_offset → new pos
        new_size:    Dict[int, int] = {}   # entry_offset → new size
        new_restype: Dict[int, int] = {}   # entry_offset → new restype
        import time as _time
        now = int(_time.time())
        self._commit_now = now
        self._new_entry_pos  = {}
        self._new_entry_size = {}
        self._new_entry_type = {}

        with open(self.output_path, "wb") as out:
            # 1. Reserve space for the header — fill with zeros for now.
            out.write(b"\x00" * _HEADER_SIZE)

            # 2. Write payloads, in original positional order so unmodified
            #    entries can be copied via streaming reads (avoids holding the
            #    whole archive in memory).
            with open(self.source_path, "rb") as src:
                for ent in all_entries:
                    repl_key = ent.entry_offset
                    if ent.size == 0 and repl_key not in self._replacements:
                        # zero-size placeholder (DIRTYPEWORLDS) — no payload to write
                        new_pos [ent.entry_offset] = 0
                        new_size[ent.entry_offset] = 0
                        continue
                    canon = self._replacement_names.get(
                        repl_key, ent.typed_virtual_path())
                    payload_pos = out.tell()
                    if repl_key in self._replacements:
                        data, restype = self._replacements[repl_key]
                        out.write(data)
                        new_size[ent.entry_offset] = len(data)
                        if restype is not None:
                            new_restype[ent.entry_offset] = restype
                        log.append(
                            f"  replaced {canon}: {ent.size} → {len(data)} bytes "
                            f"(payload moved {ent.pos} → {payload_pos})")
                    else:
                        # Copy bytes from source, in chunks
                        src.seek(ent.pos)
                        remaining = ent.size
                        while remaining > 0:
                            chunk = src.read(min(remaining, 1 << 20))
                            if not chunk:
                                raise IOError(f"short read while copying {canon}")
                            out.write(chunk)
                            remaining -= len(chunk)
                        new_size[ent.entry_offset] = ent.size
                    new_pos[ent.entry_offset] = payload_pos

            # 2b. Write payloads for brand-new entries (staged via add()).
            for parent_vpath, entries in self._additions.items():
                for name, data, restype in entries:
                    key = (parent_vpath, name)
                    self._new_entry_pos[key]  = out.tell()
                    self._new_entry_size[key] = len(data)
                    self._new_entry_type[key] = restype
                    out.write(data)
                    vpath = f"{parent_vpath}/{name}" if parent_vpath else name
                    log.append(f"  added {vpath}: {len(data)} bytes")

            # 3. Record the boundary: this is what NextWritePos must equal.
            next_write_pos = out.tell()

            # 4. Write the directory tree.
            block_table: Dict[int, Tuple[int, int]] = {}
            root_pos, root_size = self._write_dir_block(
                out, self._reader.root, new_pos, new_size, new_restype,
                block_table, now)

            # 5. Build the header and write it at offset 0.
            ft  = b"RezMgr Version 1 Copyright (C) 1995 MONOLITH INC.".ljust(USER_TITLE_SIZE, b" ")
            ut  = b"LithTech Resource File".ljust(USER_TITLE_SIZE, b" ")
            # Recalculate largest_rez_name to cover any new entry names
            largest_rez_name = self._reader.header.largest_rez_name
            for entries in self._additions.values():
                for name, _data, _restype in entries:
                    largest_rez_name = max(largest_rez_name, len(name) + 1)
            header = struct.pack(
                _HEADER_FMT,
                0x0d, 0x0a,                       # CR1 LF1
                ft,                                # FileType[60]
                0x0d, 0x0a,                       # CR2 LF2
                ut,                                # UserTitle[60]
                0x0d, 0x0a, 0x1a,                 # CR3 LF3 EOF1
                1,                                 # FileFormatVersion
                root_pos, root_size, now,         # root dir pos/size/time
                next_write_pos,                    # NextWritePos
                now,                               # Time
                self._reader.header.largest_key_ary,
                self._reader.header.largest_dir_name,
                largest_rez_name,
                self._reader.header.largest_comment,
                1,                                 # IsSorted (we keep it sorted)
            )
            assert len(header) == _HEADER_SIZE
            out.seek(0)
            out.write(header)

        return {
            "replacements":      len(self._replacements),
            "output":            self.output_path,
            "old_total_size":    os.path.getsize(self.source_path),
            "new_total_size":    os.path.getsize(self.output_path),
            "log":               log,
        }


def _cmd_replace(rez: str, virtual_path: str, payload: str, output: str) -> int:
    with open(payload, "rb") as f:
        data = f.read()
    with RezWriter(rez, output) as w:
        w.replace(virtual_path, data)
        result = w.commit()
    print(f"wrote {result['output']} "
          f"({result['old_total_size']} → {result['new_total_size']} bytes)")
    for line in result.get("log", []):
        print(line)
    return 0


import shutil  # used by RezWriter.commit


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Read a Lithtech .REZ archive.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="list contents of a .REZ")
    pl.add_argument("rez")
    pe = sub.add_parser("extract", help="extract one file from a .REZ")
    pe.add_argument("rez")
    pe.add_argument("virtual_path")
    pe.add_argument("output")
    pr = sub.add_parser("replace", help="replace one file inside a .REZ "
                                        "(produces a new .REZ; source untouched)")
    pr.add_argument("rez")
    pr.add_argument("virtual_path")
    pr.add_argument("payload",
                    help="filesystem path to the new bytes for this entry")
    pr.add_argument("output",
                    help="path to write the modified .REZ to")
    args = p.parse_args(argv)
    if args.cmd == "list":    return _cmd_list(args.rez)
    if args.cmd == "extract": return _cmd_extract(args.rez, args.virtual_path, args.output)
    if args.cmd == "replace": return _cmd_replace(args.rez, args.virtual_path,
                                                   args.payload, args.output)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
