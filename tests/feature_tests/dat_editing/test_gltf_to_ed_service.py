import base64
import contextlib
import hashlib
import io
import json
import os
import struct
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import gltf_to_ed_cli
from features.dat_editing import gltf_to_ed_service
from features.dat_editing import legacy_ed


TEXTURE = "TEXTURES\\Test\\Phase7.dtx"


def _gltf_payload(*, open_mesh=False, material_name=TEXTURE):
    if open_mesh:
        points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        indices = (0, 1, 2)
    else:
        points = [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        ]
        uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        indices = (
            0, 2, 1,
            0, 1, 3,
            0, 3, 2,
            1, 2, 3,
        )
    positions = b"".join(struct.pack("<3f", *point) for point in points)
    texcoords = b"".join(struct.pack("<2f", *uv) for uv in uvs)
    encoded_indices = struct.pack("<" + "H" * len(indices), *indices)
    data = positions + texcoords + encoded_indices
    position_offset = 0
    texcoord_offset = len(positions)
    index_offset = texcoord_offset + len(texcoords)
    document = {
        "asset": {"version": "2.0", "generator": "Phase 7 test"},
        "buffers": [{"byteLength": len(data)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": position_offset, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": texcoord_offset, "byteLength": len(texcoords)},
            {"buffer": 0, "byteOffset": index_offset, "byteLength": len(encoded_indices)},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(points),
                "type": "VEC3",
            },
            {
                "bufferView": 1,
                "componentType": 5126,
                "count": len(uvs),
                "type": "VEC2",
            },
            {
                "bufferView": 2,
                "componentType": 5123,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
        "materials": [{"name": material_name}],
        "meshes": [{
            "name": "Phase 7 Mesh",
            "primitives": [{
                "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                "indices": 2,
                "material": 0,
            }],
        }],
        "nodes": [{"name": "Mesh Node", "mesh": 0}, {"name": "Ignored Empty"}],
        "scenes": [{"name": "Scene", "nodes": [0, 1]}],
        "scene": 0,
    }
    return document, data


def _write_gltf(path, *, open_mesh=False, material_name=TEXTURE, external_uri=""):
    document, data = _gltf_payload(open_mesh=open_mesh, material_name=material_name)
    if external_uri:
        document["buffers"][0]["uri"] = external_uri
        with open(os.path.join(os.path.dirname(path), external_uri), "wb") as stream:
            stream.write(data)
    else:
        encoded = base64.b64encode(data).decode("ascii")
        document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + encoded
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, separators=(",", ":"))


def _options(**changes):
    values = {
        "coordinate_preset": gltf_to_ed_service.RAW_DEDIT,
        "fallback_texture_size": (128.0, 128.0),
    }
    values.update(changes)
    return gltf_to_ed_service.GltfToEdConversionOptions(**values)


class GltfToEdServiceTests(unittest.TestCase):
    def test_service_writes_prefab_and_authoritative_reports_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "tetra.gltf")
            output = os.path.join(tmp, "nested", "tetra.ed")
            _write_gltf(source)

            report = gltf_to_ed_service.convert_gltf_to_ed(
                source,
                output,
                options=_options(coordinate_preset=gltf_to_ed_service.EDITOR_DISPLAY),
            )

            self.assertEqual(report.status, "ready_prefab")
            self.assertEqual(report.output_path, os.path.abspath(output))
            self.assertTrue(os.path.isfile(output))
            self.assertTrue(os.path.isfile(report.json_report_path))
            self.assertTrue(os.path.isfile(report.text_report_path))
            self.assertEqual(report.validation["preflight"], "pass")
            self.assertEqual(report.validation["ed_writer"], "pass")
            self.assertEqual(report.validation["ed_reader_roundtrip"], "pass")
            self.assertEqual(report.validation["artifact_write"], "pass")
            self.assertEqual(report.validation["report_write"], "pass")
            self.assertEqual(report.validation["processor"], "not_run")
            self.assertEqual(report.source["format"], "gltf")
            self.assertEqual(report.source["asset"]["generator"], "Phase 7 test")
            self.assertEqual(report.inventory["triangle_count"], 4)
            self.assertEqual(report.inventory["ignored_non_mesh_node_count"], 1)
            self.assertEqual(report.inventory["generated_brush_count"], 1)
            self.assertEqual(report.materials[0]["dimension_source"], "fallback")
            self.assertEqual(report.components[0]["bounds"]["min"], [-1.0, 0.0, 0.0])
            self.assertEqual(report.components[0]["bounds"]["max"], [0.0, 1.0, 1.0])

            with open(report.json_report_path, "r", encoding="utf-8") as stream:
                on_disk = json.load(stream)
            self.assertEqual(on_disk, report.to_dict())
            self.assertEqual(on_disk["schema_version"], 1)
            self.assertEqual(on_disk["kind"], "mm9_gltf_to_ed_conversion")
            self.assertIn("blockers", on_disk)
            self.assertIn("cautions", on_disk)
            self.assertIn("notes", on_disk)
            with open(report.text_report_path, "r", encoding="utf-8") as stream:
                text = stream.read()
            self.assertIn("status: ready_prefab", text)
            self.assertIn("processor: not_run", text)

            with open(output, "rb") as stream:
                analysis = legacy_ed.analyze_legacy_ed_bytes(stream.read())
            self.assertEqual(analysis.node_layout.node_layout_kind, "named_group_brush_nodes")

    def test_service_writes_full_world_with_scaffold_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "tetra.gltf")
            output = os.path.join(tmp, "tetra_world.ed")
            _write_gltf(source)

            report = gltf_to_ed_service.convert_gltf_to_ed(
                source,
                output,
                options=_options(
                    output_mode="full_world",
                    group_name="Phase 7 World",
                    start_point_position=(10.0, 20.0, 30.0),
                ),
            )

            self.assertEqual(report.status, "ready_full_world")
            self.assertEqual(report.output["wrapper_kind"], "zlib_blocked_full_level")
            self.assertEqual(report.output["group_name"], "Phase_7_World")
            self.assertEqual(
                report.output["full_world_scaffold"]["start_point"]["position"],
                [10.0, 20.0, 30.0],
            )
            with open(output, "rb") as stream:
                analysis = legacy_ed.analyze_legacy_ed_bytes(stream.read())
            self.assertEqual(
                analysis.node_layout.node_layout_kind,
                "named_group_brush_nodes_with_root_objects",
            )

    def test_blocked_conversion_writes_reports_but_not_ed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "open.gltf")
            output = os.path.join(tmp, "open.ed")
            _write_gltf(source, open_mesh=True)

            report = gltf_to_ed_service.convert_gltf_to_ed(
                source,
                output,
                options=_options(),
            )

            self.assertEqual(report.status, "blocked")
            self.assertFalse(os.path.exists(output))
            self.assertTrue(os.path.isfile(report.json_report_path))
            self.assertTrue(os.path.isfile(report.text_report_path))
            self.assertEqual(report.validation["preflight"], "failed")
            self.assertEqual(report.validation["ed_writer"], "not_run")
            self.assertEqual(report.validation["artifact_write"], "not_run")
            self.assertEqual(report.validation["report_write"], "pass")
            self.assertTrue(report.blockers)

    def test_import_failure_is_a_structured_blocked_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "missing.gltf")
            output = os.path.join(tmp, "missing.ed")

            report = gltf_to_ed_service.convert_gltf_to_ed(
                source,
                output,
                options=_options(),
            )

            self.assertEqual(report.status, "blocked")
            self.assertEqual(report.source["sha256"], None)
            self.assertEqual(report.source["byte_size"], 0)
            self.assertEqual({item.code for item in report.blockers}, {"source_not_found"})
            self.assertFalse(os.path.exists(output))
            self.assertTrue(os.path.isfile(report.json_report_path))
            self.assertTrue(os.path.isfile(report.text_report_path))

    def test_no_overwrite_preserves_every_existing_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "tetra.gltf")
            output = os.path.join(tmp, "tetra.ed")
            _write_gltf(source)
            first = gltf_to_ed_service.convert_gltf_to_ed(source, output, options=_options())
            paths = (output, first.json_report_path, first.text_report_path)
            before = {}
            for path in paths:
                with open(path, "rb") as stream:
                    before[path] = stream.read()

            second = gltf_to_ed_service.convert_gltf_to_ed(source, output, options=_options())

            self.assertEqual(second.status, "write_failed")
            self.assertEqual(second.validation["artifact_write"], "failed")
            self.assertEqual(second.validation["report_write"], "failed")
            self.assertFalse(second.output["reports_written"])
            for path in paths:
                with open(path, "rb") as stream:
                    self.assertEqual(stream.read(), before[path])

    def test_explicit_overwrite_replaces_ed_and_both_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "tetra.gltf")
            output = os.path.join(tmp, "tetra.ed")
            _write_gltf(source)
            first = gltf_to_ed_service.convert_gltf_to_ed(source, output, options=_options())
            with open(output, "rb") as stream:
                prefab_bytes = stream.read()

            second = gltf_to_ed_service.convert_gltf_to_ed(
                source,
                output,
                options=_options(output_mode="full_world", overwrite=True),
            )

            self.assertEqual(first.status, "ready_prefab")
            self.assertEqual(second.status, "ready_full_world")
            with open(output, "rb") as stream:
                full_world_bytes = stream.read()
            self.assertNotEqual(full_world_bytes, prefab_bytes)
            self.assertEqual(full_world_bytes[4], 1)
            with open(second.json_report_path, "r", encoding="utf-8") as stream:
                self.assertEqual(json.load(stream)["status"], "ready_full_world")
            with open(second.text_report_path, "r", encoding="utf-8") as stream:
                self.assertIn("status: ready_full_world", stream.read())

    def test_external_buffer_cannot_be_selected_as_overwrite_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "external.gltf")
            output = os.path.join(tmp, "geometry.ed")
            _write_gltf(source, external_uri="geometry.ed")
            with open(output, "rb") as stream:
                original = stream.read()

            report = gltf_to_ed_service.convert_gltf_to_ed(
                source,
                output,
                options=_options(overwrite=True),
            )

            self.assertEqual(report.status, "blocked")
            self.assertIn(
                "artifact_path_collides_with_external_buffer",
                {item.code for item in report.blockers},
            )
            with open(output, "rb") as stream:
                self.assertEqual(stream.read(), original)

    def test_cli_uses_service_and_material_map_hash_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "mapped.gltf")
            output = os.path.join(tmp, "mapped.ed")
            material_map = os.path.join(tmp, "materials.json")
            dimensions = os.path.join(tmp, "dimensions.json")
            _write_gltf(source, material_name="Stone")
            with open(material_map, "w", encoding="utf-8") as stream:
                json.dump({"Stone": TEXTURE}, stream)
            with open(dimensions, "w", encoding="utf-8") as stream:
                json.dump({TEXTURE: [64, 32]}, stream)
            with open(material_map, "rb") as stream:
                expected_hash = hashlib.sha256(stream.read()).hexdigest()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = gltf_to_ed_cli.main([
                    source,
                    output,
                    "--coordinate-preset",
                    "raw_dedit",
                    "--material-map",
                    material_map,
                    "--texture-dimensions",
                    dimensions,
                ])

            self.assertEqual(exit_code, 0)
            self.assertIn("status: ready_prefab", stdout.getvalue())
            json_path, _text_path = gltf_to_ed_service.report_paths_for_output(output)
            with open(json_path, "r", encoding="utf-8") as stream:
                report = json.load(stream)
            self.assertEqual(report["options"]["material_map"]["sha256"], expected_hash)
            self.assertEqual(report["materials"][0]["texture_width"], 64.0)
            self.assertEqual(report["materials"][0]["texture_height"], 32.0)


if __name__ == "__main__":
    unittest.main()
