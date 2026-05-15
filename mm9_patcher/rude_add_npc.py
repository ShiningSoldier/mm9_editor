#!/usr/bin/env python3
"""
rude_add_npc.py
===============

Register a fresh NPC with MM9's RUDE dialogue system. Three things have to be
in place for clicking on an NPC to open a dialogue box (and not produce
"Invalid NPC Name! Invalid NPC ID! Rude: failed to init dialogue!"):

  1. an entry in NPCNAME.RUDE       <NPCNbr>,"<display name>"
  2. an entry in TOPBLURB.RUDE      <NPCNbr>,<NPCNbr>,"<greeting line>"
  3. a file NPC<NPCNbr>.RUDE         containing at least one dialogue option

This script does all three. Existing entries are preserved; if an NPCNbr is
already registered the script refuses to overwrite (use --force to override).

Usage
-----
    python rude_add_npc.py register \
        --rude-dir data/RUDE/RUDE \
        --npc-nbr 437 \
        --name    "Test Peasant" \
        --blurb   "Hail, traveler!"

    # Inspect what NPCNbrs are currently in use
    python rude_add_npc.py list --rude-dir data/RUDE/RUDE

Format reference (NPC<N>.RUDE column meaning, derived from the shipped data):
    col 0   NPCNbr
    col 1   questId  (current dialogue state; matches NPCNbr for fresh NPCs)
    col 2   branchId (which option within that state)
    col 3   "player_text"   (button shown to the player)
    col 4   "npc_response"  (the NPC's reply)
    col 5   next_questId    (-1 closes the dialogue, otherwise the new state)
    col 6+  24 numeric effect/condition columns — all 0 = no side effects

The minimal dialogue ("just say goodbye") is exactly:
    <N>,<N>,1,"Goodbye.","asd",-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Set, Tuple

NPC_FILE_RX = re.compile(r"^NPC(\d+)\.RUDE$", re.IGNORECASE)
EFFECT_COLUMNS = ",".join(["0"] * 24)


# ---------------------------------------------------------------------------
# Reading existing files
# ---------------------------------------------------------------------------

def list_used_npcnbrs(rude_dir: str) -> Set[int]:
    used: Set[int] = set()
    for name in os.listdir(rude_dir):
        m = NPC_FILE_RX.match(name)
        if m:
            used.add(int(m.group(1)))
    return used


def read_csv_first_col_int(path: str) -> Set[int]:
    nums: Set[int] = set()
    if not os.path.exists(path):
        return nums
    with open(path, "r", encoding="latin-1") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            head = line.split(",", 1)[0].strip()
            try:
                nums.add(int(head))
            except ValueError:
                pass
    return nums


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

def append_csv_line(path: str, line: str) -> None:
    """Append a line, ensuring the file ends with a newline first."""
    needs_newline = False
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) != b"\n"
    with open(path, "a", encoding="latin-1") as f:
        if needs_newline:
            f.write("\n")
        f.write(line.rstrip("\n") + "\n")


def write_minimal_npc_rude(path: str, npc_nbr: int, dialogue: List[Tuple[str, str]]) -> None:
    """
    Write NPC<N>.RUDE with the supplied (player_text, npc_response) pairs.
    The first option is mapped as branch 1, second as branch 2, etc.
    A trailing "Goodbye." option is appended automatically with next_questId=-1.
    """
    lines: List[str] = []
    branch = 1
    for player_text, npc_response in dialogue:
        # Reply once and stay in the same state (loop back). next_questId=NPCNbr.
        lines.append(
            f'{npc_nbr},{npc_nbr},{branch},"{_escape(player_text)}",'
            f'"{_escape(npc_response)}",{npc_nbr},{EFFECT_COLUMNS}'
        )
        branch += 1
    # Always include a Goodbye option that closes the dialogue.
    lines.append(
        f'{npc_nbr},{npc_nbr},{branch},"Goodbye.","Farewell.",-1,{EFFECT_COLUMNS}'
    )
    with open(path, "w", encoding="latin-1") as f:
        f.write("\n".join(lines) + "\n")


def _escape(s: str) -> str:
    # RUDE strings are surrounded by " and inner quotes are not common in the
    # shipped data; just strip them as a defensive measure.
    return s.replace('"', "'")


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    used = list_used_npcnbrs(args.rude_dir)
    print(f"Found {len(used)} NPC<N>.RUDE files in {args.rude_dir}")
    if not used:
        print("  (no NPC files found — folder may be empty or freshly extracted)")
        print("\nFirst 20 available NPCNbrs starting from 437:")
        print("  " + ", ".join(str(x) for x in range(437, 457)))
        return 0
    print(f"  range: {min(used)} .. {max(used)}")
    gaps = set(range(1, max(used) + 1)) - used
    print(f"  unused slots in [1..{max(used)}]: {len(gaps)}")
    print(f"\nFirst 20 unused NPCNbrs above the highest known one ({max(used)}):")
    candidates = []
    n = max(used) + 1
    while len(candidates) < 20:
        if n not in used:
            candidates.append(n)
        n += 1
    print("  " + ", ".join(str(x) for x in candidates))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    rude_dir = args.rude_dir
    n = args.npc_nbr

    name_path  = os.path.join(rude_dir, "NPCNAME.RUDE")
    blurb_path = os.path.join(rude_dir, "TOPBLURB.RUDE")
    npc_path   = os.path.join(rude_dir, f"NPC{n}.RUDE")

    used_files = list_used_npcnbrs(rude_dir)
    used_names = read_csv_first_col_int(name_path)
    used_blurb = read_csv_first_col_int(blurb_path)

    conflicts = []
    if n in used_files: conflicts.append(f"NPC{n}.RUDE already exists")
    if n in used_names: conflicts.append(f"NPCNAME.RUDE already has an entry for {n}")
    if n in used_blurb: conflicts.append(f"TOPBLURB.RUDE already has an entry for {n}")
    if conflicts and not args.force:
        for c in conflicts:
            print(f"  [conflict] {c}")
        print(f"\nUse --force to overwrite, or pick a different --npc-nbr.")
        return 2

    # 1. NPCNAME.RUDE
    if n not in used_names or args.force:
        line = f'{n},"{_escape(args.name)}"'
        if n in used_names:
            _replace_first_col_line(name_path, n, line)
        else:
            append_csv_line(name_path, line)
        print(f"  + NPCNAME.RUDE  : {line}")

    # 2. TOPBLURB.RUDE
    if n not in used_blurb or args.force:
        line = f'{n},{n},"{_escape(args.blurb)}"'
        if n in used_blurb:
            _replace_first_col_line(blurb_path, n, line)
        else:
            append_csv_line(blurb_path, line)
        print(f"  + TOPBLURB.RUDE : {line}")

    # 3. NPC<N>.RUDE
    dialogue: List[Tuple[str, str]] = []
    if args.line:
        for spec in args.line:
            if "::" not in spec:
                print(f"  [error] --line requires 'PLAYER TEXT::NPC RESPONSE', got {spec!r}")
                return 2
            p, r = spec.split("::", 1)
            dialogue.append((p.strip(), r.strip()))
    write_minimal_npc_rude(npc_path, n, dialogue)
    print(f"  + {os.path.basename(npc_path):16s}: {len(dialogue)+1} dialogue option(s)")

    print(f"\nDone. Next step: repack the RUDE folder back into RUDE.REZ.")
    print(f"  Lith21tools/lithrez.exe c {rude_dir} RUDE.REZ")
    return 0


def _replace_first_col_line(path: str, npc_nbr: int, new_line: str) -> None:
    out = []
    found = False
    with open(path, "r", encoding="latin-1") as f:
        for line in f:
            head = line.split(",", 1)[0].strip()
            try:
                if int(head) == npc_nbr and not found:
                    out.append(new_line + "\n")
                    found = True
                    continue
            except ValueError:
                pass
            out.append(line)
    if not found:
        out.append(new_line + "\n")
    with open(path, "w", encoding="latin-1") as f:
        f.writelines(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Register a new NPC in MM9's RUDE files.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="show used / unused NPCNbrs in the RUDE folder")
    pl.add_argument("--rude-dir", required=True)
    pl.set_defaults(func=cmd_list)

    pr = sub.add_parser("register", help="register an NPCNbr (NPCNAME + TOPBLURB + NPC<N>.RUDE)")
    pr.add_argument("--rude-dir", required=True)
    pr.add_argument("--npc-nbr", type=int, required=True)
    pr.add_argument("--name",    required=True)
    pr.add_argument("--blurb",   default="Hail, traveler!")
    pr.add_argument("--line", action="append", default=[],
                    help="custom dialogue option, format 'PLAYER TEXT::NPC RESPONSE'. "
                         "May be passed multiple times. A 'Goodbye.' is always added at the end.")
    pr.add_argument("--force", action="store_true",
                    help="overwrite existing entries for this NPCNbr")
    pr.set_defaults(func=cmd_register)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
