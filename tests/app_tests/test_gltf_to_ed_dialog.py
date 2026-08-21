import json
import os
import tempfile
import types
import unittest

from tests._path import ROOT  # noqa: F401

from app import editor as editor_app
from features.dat_editing import gltf_brushes
from features.dat_editing import gltf_ed_assembly
from features.dat_editing import gltf_to_ed_service
from ui import gltf_to_ed_dialog as dialog


class GltfToEdDialogHelperTests(unittest.TestCase):
    def test_build_request_types_the_supported_ui_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "mesh.gltf")
            material_map = os.path.join(tmp, "materials.json")
            with open(source, "w", encoding="utf-8") as stream:
                stream.write("{}")
            with open(material_map, "w", encoding="utf-8") as stream:
                json.dump({"Stone": r"TEXTURES\WORLD\Stone.dtx"}, stream)

            request = dialog.build_conversion_request(
                source_path=source,
                output_path=os.path.join(tmp, "mesh.ed"),
                output_mode=gltf_ed_assembly.PREFAB,
                geometry_policy=gltf_brushes.STRICT_CONVEX,
                coordinate_preset=gltf_to_ed_service.EDITOR_DISPLAY,
                unit_scale="2.5",
                weld_tolerance="0.02",
                material_map_path=material_map,
                fallback_texture_width="256",
                fallback_texture_height="128",
                default_uv_projection="world_aligned",
                overwrite=True,
            )

        self.assertEqual(request.source_path, os.path.abspath(source))
        self.assertEqual(request.options.unit_scale, 2.5)
        self.assertEqual(request.options.weld_tolerance, 0.02)
        self.assertEqual(request.options.fallback_texture_size, (256.0, 128.0))
        self.assertEqual(request.options.default_uv_projection, "world_aligned")
        self.assertEqual(request.options.material_map_path, os.path.abspath(material_map))
        self.assertTrue(request.options.overwrite)

    def test_triangle_slab_fields_are_explicit_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "open.glb")
            with open(source, "wb") as stream:
                stream.write(b"glTF")
            values = dict(
                source_path=source,
                output_path=os.path.join(tmp, "open.ed"),
                output_mode=gltf_ed_assembly.FULL_WORLD,
                geometry_policy=gltf_brushes.TRIANGLE_SLAB,
                coordinate_preset=gltf_to_ed_service.RAW_DEDIT,
                unit_scale="1",
                weld_tolerance="0.01",
                slab_thickness="1",
                slab_back_texture=r"TEXTURES\Test\Back.dtx",
                slab_side_texture=r"TEXTURES\Test\Side.dtx",
            )
            request = dialog.build_conversion_request(**values)
            self.assertEqual(request.options.slab_thickness, 1.0)
            self.assertEqual(request.options.output_mode, "full_world")

            values["slab_side_texture"] = ""
            with self.assertRaisesRegex(ValueError, "back and side"):
                dialog.build_conversion_request(**values)

    def test_suggested_output_tracks_prefab_and_full_world_modes(self):
        source = os.path.join("C:", "work", "room.gltf")
        self.assertTrue(
            dialog.suggest_output_path(source, gltf_ed_assembly.PREFAB).endswith("room.ed")
        )
        self.assertTrue(
            dialog.suggest_output_path(source, gltf_ed_assembly.FULL_WORLD).endswith("room_world.ed")
        )

    def test_controller_runs_automatic_validation_only_after_ready_conversion(self):
        request = dialog.GltfToEdUiRequest(
            source_path="source.gltf",
            output_path="output.ed",
            options=gltf_to_ed_service.GltfToEdConversionOptions(),
        )
        calls = []
        conversion = types.SimpleNamespace(
            status="ready_prefab",
            json_report_path="output.gltf_to_ed_report.json",
        )
        validation = object()

        result = dialog.execute_conversion_request(
            request,
            converter=lambda source, output, options: (
                calls.append((source, output, options)) or conversion
            ),
            validator=lambda report_path: (
                calls.append(("validate", report_path)) or validation
            ),
        )

        self.assertIs(result.conversion, conversion)
        self.assertIs(result.validation, validation)
        self.assertEqual(calls[1], ("validate", conversion.json_report_path))

    def test_manual_dedit_action_sets_only_explicit_open_save_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "mesh.gltf_to_ed_report.json")
            with open(report_path, "w", encoding="utf-8") as stream:
                stream.write("{}")
            received = {}

            def validator(path, *, options):
                received["path"] = path
                received["options"] = options
                return "validated"

            result = dialog.validate_existing_report(
                report_path,
                record_dedit_pass=True,
                validator=validator,
            )

        self.assertEqual(result, "validated")
        self.assertEqual(received["path"], os.path.abspath(report_path))
        self.assertTrue(received["options"].dedit_opened)
        self.assertTrue(received["options"].dedit_saved)
        self.assertFalse(received["options"].run_processor)


class EditorGltfToEdIntegrationTests(unittest.TestCase):
    def test_editor_command_opens_dialog_in_work_directory(self):
        calls = []

        class FakeDialog:
            @staticmethod
            def open(parent, *, initial_dir=""):
                calls.append((parent, initial_dir))

        globals_ = editor_app.EditorApp.cmd_gltf_to_dedit_ed_conversion.__globals__
        old_dialog = globals_.get("GltfToEdDialog")
        try:
            globals_["GltfToEdDialog"] = FakeDialog
            app = object.__new__(editor_app.EditorApp)
            app.root = object()
            app.cfg = types.SimpleNamespace(work_dir=r"C:\work", editor_dir=r"C:\editor")
            app.editor_dir = r"C:\fallback"

            editor_app.EditorApp.cmd_gltf_to_dedit_ed_conversion(app)
        finally:
            globals_["GltfToEdDialog"] = old_dialog

        self.assertEqual(calls, [(app.root, r"C:\work")])


if __name__ == "__main__":
    unittest.main()
