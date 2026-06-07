import base64
import json
import os
import struct
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from features.dat_editing import gltf_export
from features.dat_editing import gltf_import
from features.dat_editing import mesh_import


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class GltfExportTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data)

    def test_exports_gltf_bin_and_embedded_dat_metadata(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="BootcampGltfTest",
                selected_model_names=[model.name],
            )

            self.assertTrue(os.path.isfile(result.gltf_path))
            self.assertTrue(os.path.isfile(result.bin_path))
            self.assertTrue(os.path.isfile(result.meta_path))
            self.assertEqual(result.model_count, 1)
            self.assertGreater(result.triangle_count, 0)

            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            self.assertEqual(gltf["asset"]["version"], "2.0")
            self.assertEqual(gltf["buffers"][0]["uri"], "BootcampGltfTest_geometry.bin")
            self.assertEqual(gltf["buffers"][0]["byteLength"], os.path.getsize(result.bin_path))
            self.assertEqual(len(gltf["nodes"]), 1)
            self.assertEqual(len(gltf["meshes"]), 1)
            self.assertTrue(gltf["meshes"][0]["primitives"])
            primitive = gltf["meshes"][0]["primitives"][0]
            self.assertIn("POSITION", primitive["attributes"])
            self.assertIn("TEXCOORD_0", primitive["attributes"])
            self.assertIn("MM9_polygon_indices", primitive["extras"])

            meta = gltf["extras"]["MM9_datmeta"]
            self.assertEqual(meta["kind"], "mm9_dat_geometry_roundtrip")
            self.assertEqual(meta["format"], "gltf")
            self.assertEqual(meta["source"]["size"], len(data))
            self.assertEqual(meta["models"][0]["name"], model.name)
            self.assertEqual(meta["coordinate_system"]["export_space"], "blender_display")

            with open(result.meta_path, "r", encoding="utf-8") as f:
                sidecar = json.load(f)
            self.assertEqual(sidecar["files"]["gltf"], os.path.basename(result.gltf_path))
            self.assertEqual(sidecar["models"][0]["name"], model.name)

    def test_raw_coordinate_gltf_export_uses_identity_transform(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.points), None)
        if model is None:
            self.skipTest("BOOTCAMP has no BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="RawGltf",
                selected_model_names=[model.name],
                raw_coordinates=True,
            )

            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            meta = gltf["extras"]["MM9_datmeta"]
            self.assertEqual(meta["coordinate_system"]["export_space"], "raw_dat")
            self.assertEqual(meta["coordinate_system"]["dat_to_export_matrix"][0][0], 1.0)

    def test_full_level_export_omits_empty_mesh_primitives(self):
        path, data, bsp_world = self.load_bootcamp()

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="FullLevelGltf",
            )

            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            self.assertEqual(result.model_count, len(gltf["meshes"]))
            self.assertEqual(len(gltf["nodes"]), len(gltf["meshes"]))
            self.assertGreater(len(gltf["meshes"]), 0)
            for mesh in gltf["meshes"]:
                self.assertTrue(mesh["primitives"])

    def test_exported_gltf_loads_back_into_geometry_scene(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="GltfSceneImport",
                selected_model_names=[model.name],
            )

            scene = gltf_import.load_gltf_geometry_scene(result.gltf_path)

            self.assertEqual(scene.metadata["kind"], "mm9_dat_geometry_roundtrip")
            self.assertEqual(scene.metadata["format"], "gltf")
            self.assertEqual(len(scene.mesh_models()), 1)
            imported_model = scene.mesh_models()[0]
            self.assertEqual(imported_model.name, model.name)
            self.assertEqual(len(imported_model.faces), len(scene.metadata["models"][0]["polygons"]))
            self.assertEqual(scene.material_texture_map(), {
                item["material_name"]: item["texture_name"]
                for item in scene.metadata["materials"]
            })
            self.assertEqual(imported_model.faces[0].extras["source_format"], "gltf")
            self.assertIn("original_vertex_indices", imported_model.faces[0].extras)
            self.assertTrue(all(uv is not None for uv in imported_model.faces[0].uv_coords))

    def test_exported_gltf_builds_mesh_import_plan_through_shared_compiler_path(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="GltfMeshImport",
                selected_model_names=[model.name],
            )
            plan = mesh_import.build_mesh_import_plan(
                bsp_world,
                result.gltf_path,
                meta_path="",
                new_name="ImportedFromGltf",
            )

            self.assertEqual(plan.new_name, "ImportedFromGltf")
            self.assertEqual(len(plan.models), 1)
            imported = plan.models[0].mesh
            self.assertEqual(imported.name, "ImportedFromGltf")
            self.assertEqual(len(imported.polygons), len(model.polygons))
            self.assertEqual(imported.texture_names, model.texture_names)
            self.assertTrue(imported.surfaces)
            self.assertTrue(all(getattr(surface, "mm9_uv_method", "") == "dedit_opq" for surface in imported.surfaces[:8]))
            self.assertEqual(imported.polygons[0].mm9_source_face["source_format"], "gltf")

    def test_gltf_import_uses_sidecar_when_embedded_extras_are_stripped(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="GltfSidecarFallback",
                selected_model_names=[model.name],
            )
            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            gltf.pop("extras", None)
            with open(result.gltf_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(gltf, f)

            scene = gltf_import.load_gltf_geometry_scene(result.gltf_path)

            self.assertEqual(scene.metadata["format"], "gltf")
            self.assertEqual(scene.metadata["models"][0]["name"], model.name)
            self.assertEqual(len(scene.mesh_models()[0].faces), len(scene.metadata["models"][0]["polygons"]))

    def test_gltf_import_accepts_embedded_base64_buffer(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="GltfBase64",
                selected_model_names=[model.name],
            )
            with open(result.gltf_path, "r", encoding="utf-8") as f:
                gltf = json.load(f)
            with open(result.bin_path, "rb") as f:
                payload = f.read()
            gltf["buffers"][0]["uri"] = (
                "data:application/octet-stream;base64,"
                + base64.b64encode(payload).decode("ascii")
            )
            os.remove(result.bin_path)
            with open(result.gltf_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(gltf, f)

            scene = gltf_import.load_gltf_geometry_scene(result.gltf_path)

            self.assertEqual(len(scene.mesh_models()), 1)
            self.assertEqual(scene.mesh_models()[0].name, model.name)

    def test_gltf_import_accepts_glb(self):
        path, data, bsp_world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.texture_names), None)
        if model is None:
            self.skipTest("BOOTCAMP has no textured BSP model")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_gltf_roundtrip(
                bsp_world,
                data,
                tmp,
                source_path=path,
                base_name="GltfBinary",
                selected_model_names=[model.name],
            )
            glb_path = os.path.join(tmp, "GltfBinary.glb")
            _write_glb_from_export(result.gltf_path, result.bin_path, glb_path)

            scene = gltf_import.load_gltf_geometry_scene(glb_path, result.meta_path)

            self.assertEqual(len(scene.mesh_models()), 1)
            self.assertEqual(scene.mesh_models()[0].name, model.name)

    def test_generic_gltf_missing_metadata_import_reports_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            gltf_path = os.path.join(tmp, "generic.gltf")
            payload = struct.pack(
                "<9f6f3H",
                0.0, 0.0, 0.0,
                64.0, 0.0, 0.0,
                0.0, 0.0, 64.0,
                0.0, 0.0,
                1.0, 0.0,
                0.0, 1.0,
                0, 1, 2,
            )
            gltf = {
                "asset": {"version": "2.0"},
                "scene": 0,
                "scenes": [{"nodes": [0]}],
                "nodes": [{"name": "GenericNode", "mesh": 0}],
                "meshes": [{"name": "GenericMesh", "primitives": [{
                    "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                    "indices": 2,
                    "material": 0,
                    "mode": 4,
                }]}],
                "materials": [{"name": "PlainMaterial"}],
                "buffers": [{
                    "uri": "data:application/octet-stream;base64," + base64.b64encode(payload).decode("ascii"),
                    "byteLength": len(payload),
                }],
                "bufferViews": [
                    {"buffer": 0, "byteOffset": 0, "byteLength": 36},
                    {"buffer": 0, "byteOffset": 36, "byteLength": 24},
                    {"buffer": 0, "byteOffset": 60, "byteLength": 6},
                ],
                "accessors": [
                    {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                    {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
                    {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
                ],
            }
            with open(gltf_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(gltf, f)

            plan = mesh_import.build_mesh_import_plan(
                bsp.BspWorld(version=66, world_info="test"),
                gltf_path,
                new_name="GenericImport",
            )
            summary = mesh_import.import_summary(plan)

            self.assertEqual(plan.metadata_source, "missing")
            self.assertTrue(any("DAT metadata is missing" in warning for warning in plan.import_warnings))
            self.assertTrue(any("PlainMaterial" in warning for warning in plan.import_warnings))
            self.assertIn("Metadata: missing", summary)
            self.assertIn("Warnings:", summary)

    def test_generic_gltf_import_skips_degenerate_triangles(self):
        with tempfile.TemporaryDirectory() as tmp:
            gltf_path = os.path.join(tmp, "generic_degenerate.gltf")
            payload = struct.pack(
                "<12f8f6H",
                0.0, 0.0, 0.0,
                1.0, 0.0, 0.0,
                2.0, 0.0, 0.0,
                0.0, 1.0, 0.0,
                0.0, 0.0,
                1.0, 0.0,
                1.0, 1.0,
                0.0, 1.0,
                0, 1, 2,
                0, 2, 3,
            )
            gltf = {
                "asset": {"version": "2.0"},
                "scene": 0,
                "scenes": [{"nodes": [0]}],
                "nodes": [{"name": "GenericNode", "mesh": 0}],
                "meshes": [{"name": "GenericMesh", "primitives": [{
                    "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                    "indices": 2,
                    "material": 0,
                    "mode": 4,
                }]}],
                "materials": [{"name": "PlainMaterial"}],
                "buffers": [{
                    "uri": "data:application/octet-stream;base64," + base64.b64encode(payload).decode("ascii"),
                    "byteLength": len(payload),
                }],
                "bufferViews": [
                    {"buffer": 0, "byteOffset": 0, "byteLength": 48},
                    {"buffer": 0, "byteOffset": 48, "byteLength": 32},
                    {"buffer": 0, "byteOffset": 80, "byteLength": 12},
                ],
                "accessors": [
                    {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
                    {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
                    {"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"},
                ],
            }
            with open(gltf_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(gltf, f)

            plan = mesh_import.build_mesh_import_plan(
                bsp.BspWorld(version=66, world_info="test"),
                gltf_path,
                new_name="GenericImport",
            )

            self.assertEqual(len(plan.models), 1)
            self.assertEqual(len(plan.models[0].mesh.polygons), 1)
            self.assertTrue(any("skipped 1 degenerate" in warning for warning in plan.import_warnings))


def _write_glb_from_export(gltf_path: str, bin_path: str, glb_path: str) -> None:
    with open(gltf_path, "r", encoding="utf-8") as f:
        gltf = json.load(f)
    with open(bin_path, "rb") as f:
        binary = f.read()
    gltf["buffers"][0].pop("uri", None)
    json_payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_payload += b" " * ((4 - len(json_payload) % 4) % 4)
    bin_payload = binary + b"\0" * ((4 - len(binary) % 4) % 4)
    length = 12 + 8 + len(json_payload) + 8 + len(bin_payload)
    with open(glb_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, length))
        f.write(struct.pack("<II", len(json_payload), 0x4E4F534A))
        f.write(json_payload)
        f.write(struct.pack("<II", len(bin_payload), 0x004E4942))
        f.write(bin_payload)


if __name__ == "__main__":
    unittest.main()
