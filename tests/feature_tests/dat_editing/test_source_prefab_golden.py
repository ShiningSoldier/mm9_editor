import os
import json
import struct
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import project
from features.dat_editing import bsp_compile
from features.dat_editing import geometry_mesh
from features.dat_editing import gltf_export
from features.dat_editing import legacy_ed
from features.dat_editing import source_world


RECTANGULAR_LTA = r'''
( world
  ( header ( versioncode 2 ) ( infostring "golden rectangle" ) )
  ( polyhedronlist (
    ( polyhedron
      ( color 32 64 96 )
      ( pointlist
        ( 0 0 0 255 255 255 255 )
        ( 128 0 0 255 255 255 255 )
        ( 128 0 128 255 255 255 255 )
        ( 0 0 128 255 255 255 255 )
      )
      ( polylist (
        ( editpoly
          ( f 0 1 2 3 )
          ( n 0 1 0 )
          ( dist 0 )
          ( textureinfo
            ( 4 5 6 )
            ( 2 0 0 )
            ( 0 0 3 )
            ( sticktopoly 1 )
            ( name "TEXTURES\World\GoldenFloor.dtx" )
          )
          ( physicsmaterial "Stone" )
          ( surfacekey "GoldenFloor" )
          ( flags solid detail )
        )
      ) )
    )
  ) )
  ( nodehierarchy
    ( worldnode
      ( type null )
      ( label "Root" )
      ( childlist (
        ( worldnode ( type brush ) ( brushindex 0 ) ( label "GoldenFloorBrush" ) )
      ) )
    )
  )
)
'''


def _golden_ed_bytes(two_brushes: bool = False) -> bytes:
    data = bytearray()
    data.extend(struct.pack("<I", legacy_ed.LEGACY_ED_VERSION))
    data.extend(b"\x00" * 16)
    _append_brush(data, texture=b"TEXTURES\\World\\GoldenFloor.dtx", texture_flags=7)
    if two_brushes:
        data.extend(b"\x00" * 7)
        _append_brush(data, texture=b"TEXTURES\\World\\GoldenWall.dtx", texture_flags=11, z=256.0)
    data.extend(b"\x00" * 12)
    return bytes(data)


def _append_brush(data: bytearray, *, texture: bytes, texture_flags: int, z: float = 0.0) -> None:
    data.extend(bytes([255, 128, 64]))
    data.extend(struct.pack("<I", 4))
    for point in [
        (0.0, 0.0, z),
        (128.0, 0.0, z),
        (128.0, 0.0, z + 128.0),
        (0.0, 0.0, z + 128.0),
    ]:
        data.extend(struct.pack("<3f", *point))
    data.extend(struct.pack("<I", 1))
    data.extend(struct.pack("<I", 4))
    data.extend(struct.pack("<4H", 0, 1, 2, 3))
    data.extend(struct.pack("<3ff", 0.0, 1.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 4.0, 5.0, 6.0))
    data.extend(struct.pack("<3f", 2.0, 0.0, 0.0))
    data.extend(struct.pack("<3f", 0.0, 0.0, 3.0))
    data.extend(struct.pack("<I", texture_flags))
    data.extend(struct.pack("<H", len(texture)))
    data.extend(texture)


class SourcePrefabGoldenTests(unittest.TestCase):
    def test_lta_rectangular_brush_preserves_source_opq_and_manifest_link(self):
        scene = source_world.lta_text_to_geometry_scene(RECTANGULAR_LTA, source_path="golden.lta")

        model = _compile_first_source_model(scene, "GoldenLtaBrush")

        self.assertEqual(model.texture_names, ["TEXTURES\\World\\GoldenFloor.dtx"])
        self.assertEqual(model.points[2], (128.0, 0.0, 128.0))
        self.assertEqual(model.polygons[0].vertex_indices, [3, 2, 1, 0])
        self.assertEqual(model.surfaces[0].uv_o, (4.0, 5.0, 6.0))
        self.assertEqual(model.surfaces[0].uv_p, (2.0, 0.0, 0.0))
        self.assertEqual(model.surfaces[0].uv_q, (0.0, 0.0, 3.0))
        self.assertEqual(getattr(model.surfaces[0], "mm9_uv_method"), "source_opq")
        self.assertEqual(model.polygons[0].mm9_source_face["source_format"], "lta")
        self.assertEqual(model.polygons[0].mm9_source_face["brush_index"], 0)
        self.assertEqual(model.polygons[0].mm9_source_face["polygon_index"], 0)
        self.assertEqual(model.polygons[0].mm9_source_face["surface_key"], "GoldenFloor")
        self.assertEqual(model.polygons[0].mm9_source_face["surface_flags"], ["solid", "detail"])

        summary = project._manifest_model_summary(model.name, model)
        self.assertEqual(summary["uv_method_counts"], {"source_opq": 1})
        self.assertEqual(summary["source_face_count"], 1)
        self.assertEqual(summary["source_format_counts"], {"lta": 1})
        self.assertEqual(summary["source_physics_material_counts"], {"Stone": 1})
        self.assertEqual(summary["source_surface_key_counts"], {"GoldenFloor": 1})
        self.assertEqual(summary["source_surface_flag_counts"], {"solid": 1, "detail": 1})
        record = bsp_compile.compile_world_model_record(model)
        self.assertGreater(len(bsp_compile.patch_next_world_item(record, 0)), 128)

    def test_source_prefab_scene_exports_to_inspection_gltf(self):
        scene = source_world.lta_text_to_geometry_scene(RECTANGULAR_LTA, source_path="golden.lta")

        with tempfile.TemporaryDirectory() as tmp:
            result = gltf_export.export_geometry_scene_gltf(scene, tmp, base_name="GoldenSourcePrefab")

            self.assertTrue(os.path.exists(result.gltf_path))
            self.assertTrue(os.path.exists(result.bin_path))
            self.assertEqual(result.model_count, 1)
            with open(result.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual(meta["kind"], "mm9_geometry_scene_inspection")
            self.assertEqual(meta["source"]["scene_metadata"]["format"], "lta")
            self.assertEqual(meta["models"][0]["polygons"][0]["source_extras"]["surface_key"], "GoldenFloor")
            self.assertEqual(meta["models"][0]["polygons"][0]["source_extras"]["surface_flags"], ["solid", "detail"])

    def test_legacy_ed_multibrush_fixture_preserves_texture_flags_and_topology(self):
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(_golden_ed_bytes(two_brushes=True), source_path="golden.ed")

        self.assertEqual(scene.metadata["recovered_brush_count"], 2)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 2)
        self.assertGreater(scene.metadata["skipped_candidate_count"], 0)
        self.assertGreater(scene.metadata["skipped_range_count"], 0)
        self.assertTrue(scene.metadata["skipped_ranges"])
        meshes = [
            geometry_mesh.geometry_model_to_bsp_mesh(
                source_model,
                f"GoldenEdBrush{index}",
                scene.material_texture_map(),
                geometry_mesh.identity_matrix(),
            )
            for index, source_model in enumerate(scene.mesh_models())
        ]

        self.assertEqual([len(mesh.polygons) for mesh in meshes], [1, 1])
        self.assertEqual([mesh.surfaces[0].texture_flags for mesh in meshes], [7, 11])
        self.assertEqual(meshes[0].surfaces[0].uv_o, (4.0, 5.0, 6.0))
        self.assertEqual(meshes[1].texture_names, ["TEXTURES\\World\\GoldenWall.dtx"])
        self.assertEqual(meshes[1].polygons[0].mm9_source_face["source_format"], "legacy_ed")
        self.assertEqual(meshes[1].polygons[0].mm9_source_face["brush_index"], 1)
        self.assertEqual(project._manifest_model_summary(meshes[1].name, meshes[1])["source_format_counts"], {
            "legacy_ed": 1,
        })

    def test_real_chair_prefab_remains_a_multibrush_golden_when_available(self):
        path = r"C:\lithtech\PreFabs\Furniture\Chair.ed"
        if not os.path.exists(path):
            self.skipTest(f"missing legacy ED prefab: {path}")

        scene = legacy_ed.load_legacy_ed_geometry_scene(path)
        model = _compile_first_source_model(scene, "GoldenChairBrush0")

        self.assertEqual(scene.metadata["recovered_brush_count"], 6)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 38)
        self.assertEqual(len(model.polygons), len(scene.mesh_models()[0].faces))
        self.assertEqual(project._manifest_model_summary(model.name, model)["source_format_counts"], {
            "legacy_ed": len(model.polygons),
        })
        self.assertTrue(all(getattr(surface, "mm9_uv_method", "") == "source_opq" for surface in model.surfaces))
        self.assertGreater(len(bsp_compile.compile_world_model_record(model).raw_bytes), 128)

    def test_real_door_prefab_is_a_controller_style_golden_when_available(self):
        path = r"C:\lithtech\PreFabs\Doors\A1_Door.ed"
        if not os.path.exists(path):
            self.skipTest(f"missing legacy ED door prefab: {path}")

        scene = legacy_ed.load_legacy_ed_geometry_scene(path)
        meshes = [
            geometry_mesh.geometry_model_to_bsp_mesh(
                source_model,
                f"GoldenDoorBrush{index}",
                scene.material_texture_map(),
                geometry_mesh.identity_matrix(),
            )
            for index, source_model in enumerate(scene.mesh_models())
        ]

        self.assertEqual(scene.metadata["recovered_brush_count"], 5)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 25)
        self.assertIn("TEXTURES\\LevelTextures\\Area1\\Building-General\\StandardDoor.dtx", scene.material_texture_map())
        self.assertEqual(sum(len(mesh.polygons) for mesh in meshes), 25)
        self.assertTrue(all(project._manifest_model_summary(mesh.name, mesh)["source_face_count"] for mesh in meshes))
        self.assertTrue(all(bsp_compile.compile_world_model_record(mesh).raw_bytes for mesh in meshes))


def _compile_first_source_model(scene, model_name):
    source_model = scene.mesh_models()[0]
    return geometry_mesh.geometry_model_to_bsp_mesh(
        source_model,
        model_name,
        scene.material_texture_map(),
        geometry_mesh.identity_matrix(),
    )


if __name__ == "__main__":
    unittest.main()
