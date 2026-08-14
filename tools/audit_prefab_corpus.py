#!/usr/bin/env python3
"""Run the explicit Phase-7 authored-prefab acceptance audit."""

from __future__ import annotations

import argparse
import json
import os
import sys

EDITOR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EDITOR_ROOT not in sys.path:
    sys.path.insert(0, EDITOR_ROOT)

from catalog import load_catalog  # noqa: E402
from core import autodetect, bsp  # noqa: E402
from features.prefabs.corpus import (  # noqa: E402
    GOLDEN_BSP_RELATIVE_PATHS,
    audit_prefab_corpus,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit every authored MM9 prefab through the supported importer.",
    )
    parser.add_argument("--game-root", default=None)
    parser.add_argument(
        "--catalog",
        default=os.path.join(EDITOR_ROOT, "catalog", "data", "catalog.json"),
    )
    parser.add_argument("--prefab-root", default=None)
    parser.add_argument("--target-dat", default=None)
    parser.add_argument(
        "--include-all-bsp",
        action="store_true",
        help=(
            "Also compile every behavior-bearing BSP assembly. This is a slow "
            "investigation pass; default closure relies on the golden BSP suite."
        ),
    )
    parser.add_argument("--json", dest="json_path", default=None)
    parser.add_argument("--no-determinism-check", action="store_true")
    args = parser.parse_args(argv)

    paths = autodetect.detect(EDITOR_ROOT, game_root=args.game_root)
    resources = paths.resources()
    prefab_root = args.prefab_root or os.path.join(paths.game_data_dir, "PreFabs")
    catalog = load_catalog(args.catalog)
    if args.target_dat:
        with open(args.target_dat, "rb") as handle:
            target_bsp = bsp.parse(handle.read())
    else:
        target_bsp = bsp.parse(resources.read_bytes("WORLDS/BOOTCAMP.DAT"))

    availability_cache = {}
    script_cache = {}

    def _resource_exists(kind: str, path: str):
        if kind == "sprite":
            return None
        key = (str(kind).casefold(), str(path).casefold())
        if key not in availability_cache:
            availability_cache[key] = resources.exists(path)
        return availability_cache[key]

    def _read_script(path: str) -> str:
        key = str(path).casefold()
        if key not in script_cache:
            script_cache[key] = resources.read_text(path)
        return script_cache[key]

    report = audit_prefab_corpus(
        prefab_root,
        catalog=catalog,
        resource_exists=_resource_exists,
        script_loader=_read_script,
        target_bsp=target_bsp,
        bsp_relative_paths=(
            None if args.include_all_bsp else GOLDEN_BSP_RELATIVE_PATHS
        ),
        verify_determinism=not args.no_determinism_check,
    )
    print(
        f"Prefab corpus: {report.total_files} file(s); "
        f"states={report.state_counts}; failures={len(report.failures)}"
    )
    for record in report.failures:
        print(f"FAIL {record.relative_path}: {record.failure}")

    if args.json_path:
        output = os.path.abspath(args.json_path)
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2, ensure_ascii=False)
        print(f"Wrote {output}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
