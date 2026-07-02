import hashlib
import os
import struct
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from features.dat_editing import legacy_ed
from features.dat_editing import legacy_ed_writer
from features.dat_editing import surrogate_ed
from features.dat_editing import terrain_semantics


class SurrogateEdTests(unittest.TestCase):
    def test_clean_legacy_ed_writer_builds_direct_root_prefab(self):
        brush = legacy_ed_writer.LegacyEdBrush(
            points=(
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
            ),
            surfaces=(
                legacy_ed_writer.LegacyEdSurface(
                    vertex_indices=(0, 1, 2, 3),
                    plane_normal=(0.0, 1.0, 0.0),
                    plane_dist=0.0,
                    texture_name="CoreTex",
                    uv_o=(4.0, 5.0, 6.0),
                    uv_p=(2.0, 0.0, 0.0),
                    uv_q=(0.0, 0.0, 3.0),
                    texture_flags=7,
                    surface_flags=11,
                    shade_rgb=(1, 2, 3),
                ),
            ),
            color_rgb=(10, 20, 30),
        )

        generated = legacy_ed_writer.build_direct_root_prefab(
            [brush],
            brush_names=["CoreBrush"],
        )

        self.assertEqual(struct.unpack_from("<I", generated, 0)[0], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(struct.unpack_from("<I", generated, 41)[0], 1)
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="core_writer_prefab.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_object_count"], 1)
        face = scene.models[0].faces[0]
        self.assertEqual(face.extras["uv_o"], [4.0, 5.0, 6.0])
        self.assertEqual(face.extras["uv_p"], [2.0, 0.0, 0.0])
        self.assertEqual(face.extras["uv_q"], [0.0, 0.0, 3.0])
        self.assertEqual(face.extras["texture_flags"], 7)
        self.assertEqual(face.extras["surface_flags"], 11)
        self.assertEqual(face.extras["shade_rgb"], [1, 2, 3])
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="core_writer_prefab.ed",
        )
        self.assertEqual(layout.node_layout_kind, "direct_root_brush_nodes")
        self.assertEqual(layout.brush_names, ("CoreBrush",))

    def test_clean_legacy_ed_writer_builds_named_group_prefab(self):
        brush = legacy_ed_writer.LegacyEdBrush(
            points=(
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
            ),
            surfaces=(
                legacy_ed_writer.LegacyEdSurface(
                    vertex_indices=(0, 1, 2),
                    plane_normal=(0.0, 1.0, 0.0),
                    plane_dist=0.0,
                    texture_name="GroupTex",
                ),
            ),
        )

        generated = legacy_ed_writer.build_named_group_prefab(
            [brush, brush],
            group_name="Bench",
            brush_names=["GroupBrush0", "GroupBrush1"],
        )

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="core_writer_group_prefab.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 2)
        self.assertEqual(scene.metadata["recovered_object_count"], 2)
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="core_writer_group_prefab.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes")
        self.assertEqual(layout.root_child_count, 1)
        self.assertEqual(layout.group_child_count, 2)
        self.assertEqual(layout.brush_names, ("GroupBrush0", "GroupBrush1"))
        self.assertIn(b"\x05\x00Bench", generated)

    def test_clean_legacy_ed_writer_builds_full_world_node_hierarchy(self):
        brush_group = legacy_ed_writer.group_node(
            "PhysicsBSPGroup",
            [
                legacy_ed_writer.brush_node(
                    7,
                    "Brush_PhysicsBSP_7",
                    node_id=3,
                    display_name="Brush_PhysicsBSP_7",
                )
            ],
            node_id=2,
        )
        object_group = legacy_ed_writer.group_node(
            "LightGroup",
            [
                legacy_ed_writer.object_node(
                    "Light",
                    "Light0",
                    node_id=5,
                    properties=(
                        legacy_ed_writer.LegacyEdObjectProperty("Name", 0, 0, "Light0"),
                    ),
                )
            ],
            node_id=4,
        )
        root = legacy_ed_writer.world_root_node(
            [brush_group, object_group],
            node_id=1,
        )

        generated = legacy_ed_writer.build_node_hierarchy(root)
        parsed, end = _read_legacy_node_container(generated, 0, include_entry=False)

        self.assertEqual(end, len(generated))
        self.assertEqual(parsed["item"]["display_name"], "WorldRoot")
        self.assertEqual(parsed["item"]["node_id"], 1)
        self.assertEqual(len(parsed["children"]), 2)
        parsed_brush_group = parsed["children"][0]
        self.assertEqual(parsed_brush_group["type"], legacy_ed_writer.NODE_NODE)
        self.assertEqual(parsed_brush_group["item"]["display_name"], "PhysicsBSPGroup")
        parsed_brush = parsed_brush_group["children"][0]
        self.assertEqual(parsed_brush["type"], legacy_ed_writer.NODE_BRUSH)
        self.assertEqual(parsed_brush["brush_index"], 7)
        self.assertEqual(parsed_brush["item"]["class_name"], "Brush")
        self.assertEqual(parsed_brush["item"]["display_name"], "Brush_PhysicsBSP_7")
        self.assertEqual(parsed_brush["item"]["properties"]["Name"], "Brush_PhysicsBSP_7")
        self.assertEqual(len(parsed_brush["item"]["properties"]), 28)
        self.assertIn("TerrainOccluder", parsed_brush["item"]["properties"])
        self.assertIn("VisBlocker", parsed_brush["item"]["properties"])
        parsed_object_group = parsed["children"][1]
        self.assertEqual(parsed_object_group["item"]["display_name"], "LightGroup")
        parsed_object = parsed_object_group["children"][0]
        self.assertEqual(parsed_object["type"], legacy_ed_writer.NODE_OBJECT)
        self.assertEqual(parsed_object["item"]["class_name"], "Light")
        self.assertEqual(parsed_object["item"]["display_name"], "Light0")
        self.assertEqual(parsed_object["item"]["properties"]["Name"], "Light0")

    def test_builds_raw_surrogate_ed_from_selected_dat_model(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
        )

        self.assertEqual(report.status, "raw_surrogate_ed_built")
        self.assertEqual(struct.unpack_from("<I", generated, 0)[0], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.selected_model_names, ("MonsterDoor1",))
        self.assertGreater(report.point_count, 0)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.roundtrip_model_count, 1)
        self.assertEqual(report.roundtrip_polygon_count, 6)
        self.assertEqual(report.processor_readiness, "raw_brush_stream_only")
        self.assertTrue(any("not a full DEdit level" in item for item in report.cautions))

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(generated, source_path="surrogate.ed")
        self.assertEqual(scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 6)
        self.assertTrue(scene.materials)

    def test_writes_surrogate_ed_file_and_formats_report(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "monsterdoor1_surrogate.ed")

            report = surrogate_ed.write_surrogate_legacy_ed_from_dat(
                bootcamp,
                output,
                model_names=["MonsterDoor1"],
            )

            self.assertEqual(report.status, "raw_surrogate_ed_built")
            self.assertTrue(os.path.exists(output))
            self.assertEqual(report.output_path, os.path.abspath(output))
            text = surrogate_ed.format_surrogate_ed_build_report(report)
            self.assertIn("DAT surrogate legacy ED build", text)
            self.assertIn("MonsterDoor1", text)
            self.assertIn("processor readiness: raw_brush_stream_only", text)

    def test_builds_full_level_wrapper_surrogate_ed_from_selected_dat_model(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_level_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
        )

        self.assertEqual(report.status, "full_level_surrogate_ed_built")
        self.assertEqual(struct.unpack_from("<I", generated, 0)[0], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(generated[4], 1)
        self.assertEqual(report.wrapper_kind, "zlib_blocked_full_level")
        self.assertGreaterEqual(report.wrapper_block_count, 1)
        self.assertGreater(report.decompressed_byte_count, 0)
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.roundtrip_model_count, 1)
        self.assertEqual(report.roundtrip_polygon_count, 6)
        self.assertEqual(report.processor_readiness, "full_level_wrapper_surrogate")

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="surrogate_full.ed",
        )
        self.assertEqual(scene.metadata["wrapper"], "zlib_blocked_full_level")
        self.assertEqual(scene.metadata["declared_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 6)

    def test_builds_full_world_skeleton_surrogate_ed_with_root_group_brush_nodes(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedModels",
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.wrapper_kind, "zlib_blocked_full_world_skeleton")
        self.assertGreater(report.node_hierarchy_byte_count, 0)
        self.assertEqual(report.processor_readiness, "full_world_skeleton_surrogate")
        info_len = struct.unpack_from("<I", generated, 5)[0]
        info = generated[9:9 + info_len].decode("latin1")
        self.assertIn("PBlockSize 2048", info)
        self.assertIn("LMGridSize 64", info)
        wrapper_table_offset = 9 + info_len + 32
        self.assertEqual(struct.unpack_from("<I", generated, wrapper_table_offset)[0], 1)
        self.assertEqual(struct.unpack_from("<I", generated, wrapper_table_offset + 4)[0], 50000)
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="surrogate_full_world_skeleton.ed",
        )
        self.assertEqual(scene.metadata["wrapper"], "zlib_blocked_full_level")
        self.assertEqual(scene.metadata["declared_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_object_count"], 4)
        self.assertEqual(scene.metadata["recovered_object_property_count"], 90)
        self.assertEqual(scene.metadata["object_class_counts"]["Brush"], 1)
        self.assertEqual(scene.metadata["object_class_counts"]["WorldProperties"], 1)
        self.assertEqual(scene.metadata["object_class_counts"]["StartPoint"], 1)
        self.assertEqual(scene.metadata["object_class_counts"]["Light"], 1)
        self.assertEqual(report.object_count, 4)
        self.assertEqual(report.object_property_count, 90)
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="surrogate_full_world_skeleton.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 4)
        self.assertEqual(layout.group_child_count, 1)
        self.assertEqual(layout.brush_names, ("Brush_MonsterDoor1_0",))
        wrapper = legacy_ed._try_decompress_full_level_wrapper(generated)
        self.assertIsNotNone(wrapper)
        decompressed = wrapper["decompressed"]
        parsed, end = _read_legacy_node_container(
            decompressed,
            layout.node_start,
            include_entry=False,
        )
        self.assertEqual(decompressed[end:], b"\x00" * 4)
        self.assertEqual(parsed["item"]["display_name"], "Container")
        self.assertEqual(parsed["item"]["unknown2"], 24)
        group = parsed["children"][0]
        self.assertEqual(group["item"]["display_name"], "GeneratedModels")
        self.assertEqual(group["item"]["unknown2"], 16)
        brush = group["children"][0]
        self.assertEqual(brush["type"], legacy_ed_writer.NODE_BRUSH)
        self.assertEqual(brush["brush_index"], 0)
        self.assertEqual(brush["item"]["class_name"], "Brush")
        self.assertEqual(brush["item"]["display_name"], "")
        self.assertEqual(brush["item"]["properties"]["Name"], "Brush_MonsterDoor1_0")
        self.assertIn("TerrainOccluder", brush["item"]["properties"])
        self.assertIn("VisBlocker", brush["item"]["properties"])
        self.assertIn("NotAStep", brush["item"]["properties"])
        self.assertEqual(len(brush["item"]["properties"]), 28)
        world_properties = parsed["children"][1]
        self.assertEqual(world_properties["type"], legacy_ed_writer.NODE_OBJECT)
        self.assertEqual(world_properties["item"]["class_name"], "WorldProperties")
        self.assertEqual(world_properties["item"]["properties"]["Name"], "WorldProperties0")
        self.assertEqual(len(world_properties["item"]["properties"]), 45)
        start_point = parsed["children"][2]
        self.assertEqual(start_point["type"], legacy_ed_writer.NODE_OBJECT)
        self.assertEqual(start_point["item"]["class_name"], "StartPoint")
        self.assertEqual(start_point["item"]["properties"]["Name"], "StartPoint0")
        self.assertTrue(start_point["item"]["properties"]["MovePlayerToFloor"])
        self.assertEqual(len(start_point["item"]["properties"]), 6)
        light = parsed["children"][3]
        self.assertEqual(light["type"], legacy_ed_writer.NODE_OBJECT)
        self.assertEqual(light["item"]["class_name"], "Light")
        self.assertEqual(light["item"]["properties"]["Name"], "Light0")
        self.assertEqual(len(light["item"]["properties"]), 11)

    def test_full_world_skeleton_can_add_budgeted_physics_shell_slabs(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedPhysicsShellProbe",
            include_physics_shell_patch=True,
            physics_shell_name_prefix="PhysicsShellProbe",
            physics_shell_max_polygons=4,
            physics_shell_thickness=16.0,
        )

        shell_summaries = [
            summary for summary in report.model_summaries
            if summary.name.startswith("PhysicsShellProbe_")
        ]
        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(len(shell_summaries), 4)
        self.assertEqual(report.model_count, 5)
        self.assertEqual(report.point_count, 94)
        self.assertEqual(report.polygon_count, 57)
        self.assertEqual(report.object_count, 8)
        self.assertEqual(report.object_property_count, 202)
        self.assertTrue(any("PhysicsBSP shell patch emitted 4/" in item for item in report.notes))
        for summary in shell_summaries:
            self.assertEqual(summary.status, "written")
            self.assertGreaterEqual(summary.point_count, 6)
            self.assertGreaterEqual(summary.polygon_count, 5)

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="physics_shell_probe.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 5)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 57)
        self.assertEqual(scene.metadata["recovered_object_count"], 8)
        invisible_faces = [
            face for model in scene.models for face in model.faces
            if face.material_name == "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx"
        ]
        self.assertGreater(len(invisible_faces), 0)
        self.assertTrue(all(face.extras["texture_flags"] == 1 for face in invisible_faces))

    def test_anskramkeep_no_helper_physics_shell_candidate_baseline(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")

        with open(anskramkeep, "rb") as f:
            data = f.read()
        parsed = bsp.parse(data)
        selected_names = terrain_semantics.default_dat_to_ed_model_names(parsed)

        self.assertEqual(len(selected_names), 106)
        self.assertEqual(
            hashlib.sha256("\n".join(selected_names).encode("utf-8")).hexdigest(),
            "398d560d33b9b76afeb1da03f7d32ab834bd6ac6675f65ea801834d62d75ca7a",
        )

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=selected_names,
            group_name="ANSKRAMKEEP_ReconstructedDAT",
            include_physics_shell_patch=True,
            physics_shell_name_prefix="ANSKRAMKEEP_PhysicsShell",
            physics_shell_max_polygons=864,
            physics_shell_thickness=16.0,
        )
        shell_summaries = [
            summary for summary in report.model_summaries
            if summary.name.startswith("ANSKRAMKEEP_PhysicsShell_")
        ]

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(len(shell_summaries), 864)
        self.assertEqual(report.model_count, 970)
        self.assertEqual(report.point_count, 19490)
        self.assertEqual(report.polygon_count, 11721)
        self.assertEqual(report.object_count, 973)
        self.assertEqual(report.object_property_count, 27222)
        self.assertTrue(any("PhysicsBSP shell patch emitted 864/6450" in item for item in report.notes))

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="ANSKRAMKEEP_no_helper_physics_shell.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 970)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 11721)
        self.assertEqual(scene.metadata["recovered_object_count"], 973)
        self.assertEqual(scene.metadata["object_class_counts"]["Brush"], 970)
        rail_faces = [
            face for model in scene.models for face in model.faces
            if "rail.dtx" in face.material_name.lower()
        ]
        self.assertEqual(rail_faces, [])

    def test_builds_multi_brush_full_world_skeleton_with_load_objects(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MuseumDoor0", "MuseumDoor1", "MuseumDoor2", "MuseumDoor3"],
            group_name="GeneratedMuseumDoors",
        )

        written_names = tuple(summary.name for summary in report.model_summaries if summary.status == "written")
        expected_brush_names = tuple(f"Brush_{name}_{index}" for index, name in enumerate(written_names))
        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 4)
        self.assertEqual(report.polygon_count, 24)
        self.assertEqual(report.point_count, 32)
        self.assertEqual(report.object_count, 7)
        self.assertEqual(report.object_property_count, 174)

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="museum_doors_full_world_skeleton.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 4)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 24)
        self.assertEqual(scene.metadata["recovered_object_count"], 7)
        self.assertEqual(scene.metadata["recovered_object_property_count"], 174)
        self.assertEqual(scene.metadata["object_class_counts"]["Brush"], 4)
        self.assertEqual(scene.metadata["object_class_counts"]["WorldProperties"], 1)
        self.assertEqual(scene.metadata["object_class_counts"]["StartPoint"], 1)
        self.assertEqual(scene.metadata["object_class_counts"]["Light"], 1)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="museum_doors_full_world_skeleton.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 4)
        self.assertEqual(layout.group_child_count, 4)
        self.assertEqual(layout.brush_names, expected_brush_names)

        wrapper = legacy_ed._try_decompress_full_level_wrapper(generated)
        self.assertIsNotNone(wrapper)
        parsed, end = _read_legacy_node_container(
            wrapper["decompressed"],
            layout.node_start,
            include_entry=False,
        )
        self.assertEqual(wrapper["decompressed"][end:], b"\x00" * 4)
        self.assertEqual(parsed["item"]["display_name"], "Container")
        group = parsed["children"][0]
        self.assertEqual(group["item"]["display_name"], "GeneratedMuseumDoors")
        self.assertEqual(len(group["children"]), 4)
        self.assertEqual(
            [child["item"]["properties"]["Name"] for child in group["children"]],
            list(expected_brush_names),
        )
        self.assertEqual(parsed["children"][1]["item"]["class_name"], "WorldProperties")
        self.assertEqual(parsed["children"][2]["item"]["class_name"], "StartPoint")
        self.assertEqual(parsed["children"][3]["item"]["class_name"], "Light")

    def test_terrain_support_prism_side_faces_are_outward(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()
        parsed = bsp.parse(data)
        model_names = {
            "WorldObject12", "WorldObject13", "WorldObject14", "WorldObject15",
            "WorldObject4", "WorldObject5", "WorldObject16", "WorldObject7", "WorldObject17",
            "WorldObject18", "WorldObject19", "WorldObject20", "WorldObject21",
            "WorldObject22", "WorldObject23", "WorldObject24", "WorldObject25", "WorldObject26",
            "WorldObject28", "WorldObject29", "WorldObject30",
        }
        selected = [model for model in parsed.world_models if model.name in model_names]
        _raw, _report, brushes = surrogate_ed._build_raw_surrogate_from_selected(
            selected,
            source_path=bootcamp,
        )

        patches, _summaries, placement = surrogate_ed._terrain_support_patch_brushes_for_brushes(
            data,
            brushes,
            source_model_name="Terrain0",
            name_prefix="TerrainPatchTwinClusters",
            margin=0.0,
            thickness=128.0,
            max_polygons=96,
            side_texture="TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
        )

        start_patch = min(
            patches,
            key=lambda brush: (
                (sum(point[0] for point in brush.points[: len(brush.points) // 2]) / (len(brush.points) // 2) - placement.center[0]) ** 2
                + (sum(point[2] for point in brush.points[: len(brush.points) // 2]) / (len(brush.points) // 2) - placement.center[2]) ** 2
            ),
        )
        self.assertEqual(len(patches), 47)
        self.assertAlmostEqual(placement.center[0], 10407.0, places=2)
        self.assertAlmostEqual(placement.top_y, 689.5, places=2)
        _assert_brush_faces_enclose_points(start_patch)

        expanded_patches, _expanded_summaries, expanded_placement = (
            surrogate_ed._terrain_support_patch_brushes_for_brushes(
                data,
                brushes,
                source_model_name="Terrain0",
                name_prefix="TerrainPatchTwinClusters",
                margin=512.0,
                thickness=128.0,
                max_polygons=512,
                side_texture="TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
            )
        )
        self.assertEqual(len(expanded_patches), 329)
        self.assertAlmostEqual(expanded_placement.center[0], 10407.0, places=2)
        self.assertAlmostEqual(expanded_placement.center[1], 689.5, places=2)
        self.assertAlmostEqual(expanded_placement.center[2], -3578.20, places=2)

        connected_patches, _connected_summaries, connected_placement = (
            surrogate_ed._terrain_support_patch_brushes_for_brushes(
                data,
                brushes,
                source_model_name="Terrain0",
                name_prefix="ConnectedTerrainPatchTwinClusters",
                margin=0.0,
                selection_mode="connected_radius",
                radius=2048.0,
                thickness=128.0,
                max_polygons=512,
                side_texture="TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
            )
        )
        self.assertEqual(len(connected_patches), 386)
        self.assertAlmostEqual(connected_placement.center[0], 10407.0, places=2)
        self.assertAlmostEqual(connected_placement.center[1], 689.5, places=2)
        self.assertAlmostEqual(connected_placement.center[2], -3578.20, places=2)

        budgeted_patches, _budgeted_summaries, budgeted_placement = (
            surrogate_ed._terrain_support_patch_brushes_for_brushes(
                data,
                brushes,
                source_model_name="Terrain0",
                name_prefix="BudgetedConnectedTerrainPatchTwinClusters",
                margin=0.0,
                selection_mode="connected_budget",
                radius=4096.0,
                thickness=128.0,
                max_polygons=64,
                side_texture="TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
            )
        )
        self.assertEqual(len(budgeted_patches), 64)
        self.assertAlmostEqual(budgeted_placement.center[0], 10407.0, places=2)
        self.assertAlmostEqual(budgeted_placement.center[1], 689.5, places=2)
        self.assertAlmostEqual(budgeted_placement.center[2], -3578.20, places=2)

        paired_patches, paired_summaries, paired_placement = (
            surrogate_ed._terrain_support_patch_brushes_for_brushes(
                data,
                brushes,
                source_model_name="Terrain0",
                name_prefix="ConnectedTerrainPatchTwinClusters",
                margin=0.0,
                selection_mode="connected_radius",
                radius=4096.0,
                brush_mode="paired_triangles",
                thickness=128.0,
                max_polygons=1600,
                side_texture="TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
            )
        )
        self.assertEqual(len(paired_patches), 1386)
        self.assertEqual(sum(1 for item in paired_summaries if "," in item.notes[0]), 146)
        self.assertAlmostEqual(paired_placement.center[0], 10407.0, places=2)
        self.assertAlmostEqual(paired_placement.center[1], 689.5, places=2)
        self.assertAlmostEqual(paired_placement.center[2], -3578.20, places=2)

        triangulated_patches, triangulated_summaries, triangulated_placement = (
            surrogate_ed._terrain_support_patch_brushes_for_brushes(
                data,
                brushes,
                source_model_name="Terrain0",
                name_prefix="ConnectedTerrainPatchTwinClusters",
                margin=0.0,
                selection_mode="connected_radius",
                radius=10000.0,
                brush_mode="triangulated_ngons",
                thickness=128.0,
                max_polygons=5000,
                side_texture="TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
            )
        )
        self.assertEqual(len(triangulated_patches), 4080)
        self.assertEqual(sum(item.polygon_count for item in triangulated_summaries), 20750)
        self.assertAlmostEqual(triangulated_placement.center[0], 10407.0, places=2)
        self.assertAlmostEqual(triangulated_placement.center[1], 689.5, places=2)
        self.assertAlmostEqual(triangulated_placement.center[2], -3578.20, places=2)

    def test_builds_prefab_surrogate_ed_with_brush_object_stream(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
        )

        self.assertEqual(report.status, "prefab_surrogate_ed_built")
        self.assertEqual(struct.unpack_from("<I", generated, 0)[0], legacy_ed.LEGACY_ED_VERSION)
        self.assertEqual(generated[4], 0)
        self.assertEqual(struct.unpack_from("<I", generated, 41)[0], 1)
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 1)
        self.assertEqual(report.object_property_count, 26)
        self.assertEqual(report.processor_readiness, "legacy_prefab_object_stream_surrogate")

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="surrogate_prefab.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 1)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 6)
        self.assertEqual(scene.metadata["recovered_object_count"], 1)
        self.assertEqual(scene.metadata["object_class_counts"], {"Brush": 1})
        brush_start = scene.models[0].extras["record_start"]
        first_polygon = brush_start + 3 + 4 + report.point_count * 12 + 4
        first_vertex_count = struct.unpack_from("<I", generated, first_polygon)[0]
        first_texture_len_offset = first_polygon + 4 + first_vertex_count * 2 + 16 + 36 + 4
        first_texture_len = struct.unpack_from("<H", generated, first_texture_len_offset)[0]
        first_polygon_end = first_texture_len_offset + 2 + first_texture_len
        self.assertEqual(generated[first_polygon_end:first_polygon_end + 7], b"\x00" * 7)
        self.assertEqual(struct.unpack_from("<I", generated, first_polygon_end + 7)[0], 4)
        unknown_lengths = [
            item["byte_length"]
            for item in scene.metadata["unknown_ranges"]
        ]
        self.assertIn(12, unknown_lengths)
        self.assertEqual(unknown_lengths[-1], 37)
        self.assertIn(b"\x05\x00Brush\x06\x00\x00\x00", generated[-37:])

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="surrogate_prefab.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.polyhedron_count, 1)
        self.assertEqual(layout.surface_count, 6)
        self.assertEqual(layout.surface_trailing_field_count, 6)
        self.assertEqual(layout.node_layout_kind, "direct_root_brush_nodes")
        self.assertEqual(layout.root_child_count, 1)
        self.assertEqual(layout.brush_names, ("Brush0",))

        object_report = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="surrogate_prefab.ed",
        )
        record = object_report.records[0]
        self.assertEqual(record.class_name, "Brush")
        self.assertEqual(record.property_value("Name"), "Brush0")
        self.assertEqual(record.property_value("Pos"), (0.0, 0.0, 0.0))
        self.assertTrue(record.property_value("Solid"))
        self.assertTrue(record.property_value("LightMap"))
        self.assertTrue(record.property_value("GouraudShade"))
        self.assertEqual(record.property_value("DetailLevel"), 1.0)

    def test_builds_two_brush_prefab_surrogate_with_real_inter_node_tail(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["StoreDoorLeft", "StoreDoorRight"],
        )

        self.assertEqual(report.status, "prefab_surrogate_ed_built")
        self.assertEqual(report.model_count, 2)
        self.assertEqual(report.object_count, 2)
        self.assertEqual(report.object_property_count, 52)
        self.assertEqual(report.roundtrip_model_count, 2)
        self.assertEqual(report.roundtrip_polygon_count, report.polygon_count)
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="composite_surrogate_prefab.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 2)
        self.assertEqual(scene.metadata["recovered_object_count"], 2)
        self.assertEqual(scene.metadata["object_class_counts"], {"Brush": 2})
        first_end = scene.models[0].extras["record_end"]
        second_start = scene.models[1].extras["record_start"]
        self.assertEqual(generated[first_end:second_start], b"")
        self.assertEqual(
            generated[-32:],
            bytes.fromhex(
                "96 19 00 00 00 00 00 00 00 00 06 00 00 00 00 00 "
                "00 00 94 19 00 00 08 00 00 00 00 00 00 00 00 00"
            ),
        )
        object_report = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="composite_surrogate_prefab.ed",
        )
        self.assertEqual(
            [record.property_value("Name") for record in object_report.records],
            ["Brush0", "Brush1"],
        )
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="composite_surrogate_prefab.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.polyhedron_count, 2)
        self.assertEqual(layout.surface_count, report.polygon_count)
        self.assertEqual(layout.surface_trailing_field_count, report.polygon_count)
        self.assertEqual(layout.node_layout_kind, "direct_root_brush_nodes")
        self.assertEqual(layout.root_child_count, 2)
        self.assertEqual(layout.brush_names, ("Brush0", "Brush1"))

    def test_builds_three_brush_prefab_surrogate_with_direct_root_layout(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1", "MonsterDoor2", "MuseumDoor0"],
        )

        self.assertEqual(report.status, "prefab_surrogate_ed_built")
        self.assertEqual(report.model_count, 3)
        self.assertEqual(report.object_count, 3)
        self.assertEqual(report.object_property_count, 78)
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="three_brush_surrogate_prefab.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 3)
        self.assertEqual(scene.metadata["recovered_object_count"], 3)
        self.assertEqual(scene.metadata["object_class_counts"], {"Brush": 3})
        geometry_end = scene.models[-1].extras["record_end"]
        intro_start = geometry_end
        self.assertEqual(
            generated[intro_start:intro_start + 12],
            bytes.fromhex("03 00 01 00 00 00 00 00 00 00 00 00"),
        )
        self.assertEqual(
            generated[-32:],
            bytes.fromhex(
                "97 19 00 00 00 00 00 00 00 00 06 00 00 00 00 00 "
                "00 00 94 19 00 00 08 00 00 00 00 00 00 00 00 00"
            ),
        )
        object_report = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="three_brush_surrogate_prefab.ed",
        )
        self.assertEqual(
            [record.property_value("Name") for record in object_report.records],
            ["Brush0", "Brush1", "Brush2"],
        )
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="three_brush_surrogate_prefab.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.polyhedron_count, 3)
        self.assertEqual(layout.surface_count, 18)
        self.assertEqual(layout.surface_trailing_field_count, 18)
        self.assertEqual(layout.node_layout_kind, "direct_root_brush_nodes")
        self.assertEqual(layout.root_child_count, 3)
        self.assertEqual(layout.brush_names, ("Brush0", "Brush1", "Brush2"))

    def test_builds_grouped_prefab_surrogate_with_named_null_node_layout(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_grouped_prefab_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=[
                "MonsterDoor1",
                "MonsterDoor2",
                "MuseumDoor0",
                "MuseumDoor1",
                "StoreDoorLeft",
            ],
            group_name="Bench",
        )

        self.assertEqual(report.status, "grouped_prefab_surrogate_ed_built")
        self.assertEqual(report.model_count, 5)
        self.assertEqual(report.object_count, 5)
        self.assertEqual(report.object_property_count, 130)
        self.assertEqual(report.processor_readiness, "legacy_grouped_prefab_object_stream_surrogate")
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="grouped_surrogate_prefab.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 5)
        self.assertEqual(scene.metadata["recovered_object_count"], 5)
        self.assertEqual(scene.metadata["object_class_counts"], {"Brush": 5})
        geometry_end = scene.models[-1].extras["record_end"]
        self.assertEqual(
            generated[geometry_end:geometry_end + 18],
            bytes.fromhex(
                "01 00 00 00 00 00 "
                "05 00 01 00 00 00 00 00 00 00 00 00"
            ),
        )
        self.assertEqual(
            generated[-55:],
            bytes.fromhex(
                "e0 1f 00 00 00 00 00 00 00 00 06 00 00 00 00 00 "
                "00 00 e4 1f 00 00 10 00 00 00 "
                "05 00 42 65 6e 63 68 "
                "06 00 00 00 00 00 00 00 "
                "e6 1f 00 00 08 00 00 00 00 00 00 00 00 00"
            ),
        )
        object_report = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="grouped_surrogate_prefab.ed",
        )
        self.assertEqual(
            [record.property_value("Name") for record in object_report.records],
            ["Brush0", "Brush1", "Brush2", "Brush3", "Brush4"],
        )
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="grouped_surrogate_prefab.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.polyhedron_count, 5)
        self.assertEqual(layout.surface_count, 30)
        self.assertEqual(layout.surface_trailing_field_count, 30)
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes")
        self.assertEqual(layout.root_child_count, 1)
        self.assertEqual(layout.group_child_count, 5)
        self.assertEqual(layout.brush_names, ("Brush0", "Brush1", "Brush2", "Brush3", "Brush4"))

    def test_writes_prefab_surrogate_ed_file_and_formats_report(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "monsterdoor1_surrogate_prefab.ed")

            report = surrogate_ed.write_prefab_surrogate_legacy_ed_from_dat(
                bootcamp,
                output,
                model_names=["MonsterDoor1"],
            )

            self.assertEqual(report.status, "prefab_surrogate_ed_built")
            self.assertTrue(os.path.exists(output))
            self.assertEqual(report.output_path, os.path.abspath(output))
            text = surrogate_ed.format_surrogate_ed_build_report(report)
            self.assertIn("objects: records=1, properties=26", text)
            self.assertIn("processor readiness: legacy_prefab_object_stream_surrogate", text)

    def test_writes_full_level_wrapper_surrogate_ed_file_and_formats_report(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "monsterdoor1_surrogate_full.ed")

            report = surrogate_ed.write_full_level_surrogate_legacy_ed_from_dat(
                bootcamp,
                output,
                model_names=["MonsterDoor1"],
            )

            self.assertEqual(report.status, "full_level_surrogate_ed_built")
            self.assertTrue(os.path.exists(output))
            self.assertEqual(report.output_path, os.path.abspath(output))
            text = surrogate_ed.format_surrogate_ed_build_report(report)
            self.assertIn("wrapper: zlib_blocked_full_level", text)
            self.assertIn("processor readiness: full_level_wrapper_surrogate", text)

    def test_writes_full_world_skeleton_surrogate_ed_file_and_formats_report(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "monsterdoor1_surrogate_skeleton.ed")

            report = surrogate_ed.write_full_world_skeleton_surrogate_legacy_ed_from_dat(
                bootcamp,
                output,
                model_names=["MonsterDoor1"],
            )

            self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
            self.assertTrue(os.path.exists(output))
            self.assertEqual(report.output_path, os.path.abspath(output))
            text = surrogate_ed.format_surrogate_ed_build_report(report)
            self.assertIn("wrapper: zlib_blocked_full_world_skeleton", text)
            self.assertIn("node hierarchy: bytes=", text)
            self.assertIn("objects: records=4, properties=90", text)
            self.assertIn("processor readiness: full_world_skeleton_surrogate", text)

    def test_missing_model_reports_no_models_selected(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["DefinitelyMissingModel"],
        )

        self.assertEqual(generated, b"")
        self.assertEqual(report.status, "no_models_selected")
        self.assertTrue(report.blockers)


def _read_legacy_node_container(data: bytes, pos: int, *, include_entry: bool):
    node_type = None
    brush_index = None
    if include_entry:
        node_type = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if node_type == legacy_ed_writer.NODE_BRUSH:
            brush_index = struct.unpack_from("<I", data, pos)[0]
            pos += 4
    child_count = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    children = []
    for _ in range(child_count):
        child, pos = _read_legacy_node_container(data, pos, include_entry=True)
        children.append(child)
    item, pos = _read_legacy_node_item(data, pos)
    return {
        "type": node_type,
        "brush_index": brush_index,
        "children": children,
        "item": item,
    }, pos


def _read_legacy_node_item(data: bytes, pos: int):
    payload_len = struct.unpack_from("<H", data, pos)[0]
    payload_start = pos + 2
    payload_end = payload_start + payload_len
    class_name, cursor = _read_legacy_string(data, payload_start)
    prop_count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    properties = {}
    for _ in range(prop_count):
        prop_name, cursor = _read_legacy_string(data, cursor)
        type_code = data[cursor]
        cursor += 1
        _flags = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        value_len = struct.unpack_from("<H", data, cursor)[0]
        cursor += 2
        if type_code == 0:
            value, _value_end = _read_legacy_string(data, cursor)
        elif type_code == 5:
            value = bool(data[cursor])
        else:
            value = data[cursor:cursor + value_len]
        properties[prop_name] = value
        cursor += value_len
    if cursor != payload_end:
        raise AssertionError(f"node item payload ended at {cursor}, expected {payload_end}")
    node_id = struct.unpack_from("<I", data, payload_end)[0]
    unknown2 = struct.unpack_from("<I", data, payload_end + 4)[0]
    display_name, end = _read_legacy_string(data, payload_end + 8)
    return {
        "class_name": class_name,
        "properties": properties,
        "node_id": node_id,
        "unknown2": unknown2,
        "display_name": display_name,
    }, end


def _read_legacy_string(data: bytes, pos: int):
    length = struct.unpack_from("<H", data, pos)[0]
    start = pos + 2
    end = start + length
    return data[start:end].decode("latin1"), end


def _assert_brush_faces_enclose_points(brush):
    for surface in brush.surfaces:
        normal = surface.plane_normal
        dist = surface.plane_dist
        distances = [
            normal[0] * point[0] + normal[1] * point[1] + normal[2] * point[2] - dist
            for point in brush.points
        ]
        if max(distances) > 1.0e-3:
            raise AssertionError(
                f"face {surface.vertex_indices} is not wound outward: "
                f"min={min(distances):.6f}, max={max(distances):.6f}"
            )


if __name__ == "__main__":
    unittest.main()
