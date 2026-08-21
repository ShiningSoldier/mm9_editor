"""Command-line entry point for the Phase 8 glTF/GLB -> ED validator."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from features.dat_editing import gltf_to_ed_validation


def _result(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    return value == "pass"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and resume evidence for a Phase 7 glTF-to-ED artifact."
    )
    parser.add_argument("conversion_report", help="Phase 7 .gltf_to_ed_report.json")
    parser.add_argument("--manifest", default="", help="Optional Phase 8 JSON manifest path")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Do not reuse external evidence from an existing same-ED manifest",
    )
    parser.add_argument(
        "--no-update-conversion-report",
        action="store_true",
        help="Leave the Phase 7 JSON/text report validation states unchanged",
    )

    dedit = parser.add_argument_group("DEDit evidence")
    dedit.add_argument("--dedit-opened", choices=("pass", "fail"))
    dedit.add_argument("--dedit-saved", choices=("pass", "fail"))
    dedit.add_argument("--dedit-saved-ed", default="")
    dedit.add_argument("--dedit-evidence", action="append", default=[])
    dedit.add_argument("--dedit-note", action="append", default=[])

    processor = parser.add_argument_group("Processor and DAT evidence")
    processor.add_argument(
        "--run-processor",
        action="store_true",
        help="Explicitly run the maintained isolated Processor harness",
    )
    processor.add_argument("--processor-path", default="")
    processor.add_argument("--processor-work-dir", default="")
    processor.add_argument("--processor-project-dir", default="")
    processor.add_argument("--processor-timeout", type=float, default=900.0)
    processor.add_argument(
        "--processor-log",
        default="",
        help="Existing Processor log evidence (does not launch Processor)",
    )
    processor.add_argument(
        "--compiled-dat",
        default="",
        help="Existing v66 DAT to validate, or companion to --processor-log",
    )

    game = parser.add_argument_group("in-game evidence")
    game.add_argument("--fresh-load", choices=("pass", "fail"))
    game.add_argument("--visuals", choices=("pass", "fail"))
    game.add_argument("--collision", choices=("pass", "fail"))
    game.add_argument("--in-game-evidence", action="append", default=[])
    game.add_argument("--in-game-note", action="append", default=[])
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    options = gltf_to_ed_validation.GltfToEdValidationOptions(
        manifest_path=args.manifest,
        resume=not args.reset,
        update_conversion_report=not args.no_update_conversion_report,
        dedit_opened=_result(args.dedit_opened),
        dedit_saved=_result(args.dedit_saved),
        dedit_saved_ed_path=args.dedit_saved_ed,
        dedit_evidence_paths=tuple(args.dedit_evidence),
        dedit_notes=tuple(args.dedit_note),
        run_processor=args.run_processor,
        processor_path=args.processor_path,
        processor_work_dir=args.processor_work_dir,
        processor_project_dir=args.processor_project_dir,
        processor_timeout_seconds=args.processor_timeout,
        processor_log_path=args.processor_log,
        compiled_dat_path=args.compiled_dat,
        in_game_fresh_load=_result(args.fresh_load),
        in_game_visuals_ok=_result(args.visuals),
        in_game_collision_ok=_result(args.collision),
        in_game_evidence_paths=tuple(args.in_game_evidence),
        in_game_notes=tuple(args.in_game_note),
    )
    report = gltf_to_ed_validation.validate_gltf_to_ed(
        args.conversion_report,
        options=options,
    )
    print(gltf_to_ed_validation.format_gltf_to_ed_validation_report(report), end="")
    return 1 if report.status in {"blocked", "validation_failed", "write_failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_argument_parser", "main"]
