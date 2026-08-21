import contextlib
import io
import json
import os
import struct
import tempfile
import unittest
from types import SimpleNamespace

from tests._path import ROOT  # noqa: F401

from features.dat_editing import gltf_to_ed_service
from features.dat_editing import gltf_to_ed_validation
from features.dat_editing import gltf_to_ed_validation_cli
from tests.feature_tests.dat_editing.test_gltf_to_ed_service import _options, _write_gltf


class _StubDatValidation:
    def __init__(self, *, errors=(), warnings=()):
        self.errors = list(errors)
        self.warnings = list(warnings)
        self.object_count = 3
        self.parsed_bsp = SimpleNamespace(world_models=(object(),))


def _convert(tmp, *, output_mode="prefab"):
    source = os.path.join(tmp, "tetra.gltf")
    output = os.path.join(tmp, "tetra.ed")
    _write_gltf(source)
    report = gltf_to_ed_service.convert_gltf_to_ed(
        source,
        output,
        options=_options(output_mode=output_mode),
    )
    return report


class GltfToEdValidationTests(unittest.TestCase):
    def test_automatic_pipeline_reopens_ed_and_updates_phase7_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            conversion = _convert(tmp)

            report = gltf_to_ed_validation.validate_gltf_to_ed(
                conversion.json_report_path
            )

            self.assertEqual(report.status, "awaiting_external_validation")
            self.assertEqual(report.stages["conversion_report"].state, "pass")
            self.assertEqual(report.stages["ed_integrity"].state, "pass")
            self.assertEqual(report.stages["ed_roundtrip"].state, "pass")
            self.assertEqual(report.stages["dedit"].state, "not_run")
            self.assertEqual(report.stages["processor"].state, "not_applicable")
            self.assertTrue(report.artifacts["written"])
            self.assertTrue(report.artifacts["conversion_report_updated"])
            with open(report.json_manifest_path, "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            self.assertEqual(manifest["kind"], "mm9_gltf_to_ed_validation")
            self.assertEqual(manifest["ed"]["sha256"], conversion.output["sha256"])
            with open(conversion.json_report_path, "r", encoding="utf-8") as stream:
                updated = json.load(stream)
            self.assertEqual(updated["validation"]["phase8_status"], report.status)
            self.assertEqual(updated["validation"]["processor"], "not_applicable")

    def test_dedit_evidence_is_resumed_only_for_same_ed_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            conversion = _convert(tmp)
            first = gltf_to_ed_validation.validate_gltf_to_ed(
                conversion.json_report_path,
                options=gltf_to_ed_validation.GltfToEdValidationOptions(
                    dedit_opened=True,
                    dedit_saved=True,
                    dedit_notes=("opened and saved in DEdit",),
                ),
            )
            self.assertEqual(first.status, "validated_prefab")

            resumed = gltf_to_ed_validation.validate_gltf_to_ed(
                conversion.json_report_path
            )
            self.assertTrue(resumed.resume["used"])
            self.assertEqual(resumed.stages["dedit"].state, "pass")
            self.assertEqual(resumed.status, "validated_prefab")

            with open(conversion.output_path, "ab") as stream:
                stream.write(b"changed")
            changed = gltf_to_ed_validation.validate_gltf_to_ed(
                conversion.json_report_path
            )
            self.assertEqual(changed.status, "validation_failed")
            self.assertFalse(changed.resume["used"])
            self.assertEqual(changed.stages["dedit"].state, "not_run")
            self.assertIn("ED SHA-256 changed", changed.resume["invalidated_reason"])

    def test_existing_processor_log_and_stubbed_v66_dat_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            conversion = _convert(tmp, output_mode="full_world")
            log_path = os.path.join(tmp, "processor.log")
            dat_path = os.path.join(tmp, "tetra.dat")
            with open(log_path, "w", encoding="utf-8") as stream:
                stream.write("Processing WORLDS\\tetra.ed\nFound 0 problem brushes\n")
            with open(dat_path, "wb") as stream:
                stream.write(struct.pack("<I", 66) + b"tiny-stub")

            report = gltf_to_ed_validation.validate_gltf_to_ed(
                conversion.json_report_path,
                options=gltf_to_ed_validation.GltfToEdValidationOptions(
                    dedit_opened=True,
                    dedit_saved=True,
                    processor_log_path=log_path,
                    compiled_dat_path=dat_path,
                    in_game_fresh_load=True,
                    in_game_visuals_ok=True,
                    in_game_collision_ok=True,
                ),
                dat_validator=lambda _data: _StubDatValidation(),
            )

            self.assertEqual(report.stages["processor"].state, "pass")
            self.assertEqual(report.stages["compiled_dat"].state, "pass")
            self.assertEqual(report.stages["in_game"].state, "pass")
            self.assertEqual(report.status, "validated_full_world")

            with open(dat_path, "ab") as stream:
                stream.write(b"changed")
            stale = gltf_to_ed_validation.validate_gltf_to_ed(
                conversion.json_report_path
            )
            self.assertTrue(stale.resume["used"])
            self.assertEqual(stale.stages["processor"].state, "blocked")
            self.assertEqual(stale.stages["compiled_dat"].state, "blocked")
            self.assertEqual(stale.stages["in_game"].state, "blocked")
            self.assertEqual(stale.status, "blocked")

    def test_processor_launch_is_opt_in_and_runner_can_be_mocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            conversion = _convert(tmp, output_mode="full_world")
            dat_path = os.path.join(tmp, "generated.dat")
            stdout_path = os.path.join(tmp, "stdout.txt")
            stderr_path = os.path.join(tmp, "stderr.txt")
            for path, data in (
                (dat_path, struct.pack("<I", 66) + b"tiny-stub"),
                (stdout_path, b"processor stdout"),
                (stderr_path, b""),
            ):
                with open(path, "wb") as stream:
                    stream.write(data)
            calls = []

            def runner(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    status="compiled",
                    output_dat_path=dat_path,
                    processor_path=kwargs["processor_path"],
                    work_dir=kwargs["work_dir"],
                    returncode=0,
                    elapsed_seconds=0.01,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    log_paths=(),
                    notes=(),
                )

            report = gltf_to_ed_validation.validate_gltf_to_ed(
                conversion.json_report_path,
                options=gltf_to_ed_validation.GltfToEdValidationOptions(
                    run_processor=True,
                    processor_path=os.path.join(tmp, "Processor.exe"),
                    processor_work_dir=os.path.join(tmp, "processor-work"),
                ),
                processor_runner=runner,
                dat_validator=lambda _data: _StubDatValidation(),
            )

            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0]["preseed_reference_dat"])
            self.assertEqual(report.stages["processor"].state, "pass")
            self.assertEqual(report.stages["compiled_dat"].state, "pass")

    def test_cli_runs_only_automatic_stages_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            conversion = _convert(tmp)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = gltf_to_ed_validation_cli.main([
                    conversion.json_report_path,
                    "--no-update-conversion-report",
                ])
            self.assertEqual(exit_code, 0)
            self.assertIn("ed_roundtrip: pass", stdout.getvalue())
            self.assertIn("processor: not_applicable", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
