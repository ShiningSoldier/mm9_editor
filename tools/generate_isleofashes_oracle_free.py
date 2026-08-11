#!/usr/bin/env python3
"""Generate the DAT-only ISLEOFASHES adaptive structural terrain candidate."""

from __future__ import annotations

import argparse
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from features.dat_editing import compiler_strategy


def _write_text(path: str, value: str) -> str:
    absolute = os.path.abspath(path)
    os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
    with open(absolute, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value.rstrip() + "\n")
    return absolute


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an oracle-free ISLEOFASHES ED with DAT-native movable "
            "doors and playable-area allocated adaptive structural Terrain* support."
        )
    )
    parser.add_argument(
        "--source-dat",
        required=True,
        help="Path to ISLEOFASHES.DAT.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for the generated ED and reports.",
    )
    parser.add_argument(
        "--worlds-install-dir",
        default="",
        help="Optional MM9 data\\WORLDS directory recorded as the suggested target.",
    )
    args = parser.parse_args()

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    report = compiler_strategy.build_oracle_free_dat_to_ed_baseline_report(
        source_dat_path=os.path.abspath(args.source_dat),
        work_dir=output_dir,
        worlds_install_dir=(
            os.path.abspath(args.worlds_install_dir)
            if args.worlds_install_dir
            else ""
        ),
    )
    text_report_path = _write_text(
        os.path.join(output_dir, "ISLEOFASHES_oracle_free_adaptive_structural_report.txt"),
        compiler_strategy.format_oracle_free_dat_to_ed_baseline_report(report),
    )

    acceptance_report_path = ""
    acceptance_manifest_path = ""
    if report.acceptance is not None:
        acceptance_report_path = _write_text(
            os.path.join(output_dir, "ISLEOFASHES_acceptance_report.txt"),
            compiler_strategy.format_full_world_skeleton_acceptance_report(
                report.acceptance
            ),
        )
        acceptance_manifest_path = os.path.join(
            output_dir,
            "ISLEOFASHES_acceptance_manifest.json",
        )
        compiler_strategy.write_full_world_skeleton_acceptance_manifest(
            report.acceptance,
            acceptance_manifest_path,
            original_source=os.path.abspath(args.source_dat),
            text_report_path=acceptance_report_path,
        )

    baseline_manifest_path = os.path.join(
        output_dir,
        "ISLEOFASHES_oracle_free_adaptive_structural_manifest.json",
    )
    compiler_strategy.write_oracle_free_dat_to_ed_baseline_manifest(
        report,
        baseline_manifest_path,
        text_report_path=text_report_path,
        acceptance_report_path=acceptance_report_path,
        acceptance_manifest_path=acceptance_manifest_path,
    )

    print(compiler_strategy.format_oracle_free_dat_to_ed_baseline_report(report))
    print(f"baseline manifest: {baseline_manifest_path}")
    return 0 if not report.blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
