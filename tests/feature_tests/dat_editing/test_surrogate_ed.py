import hashlib
import os
import struct
import tempfile
import types
import unittest

from tests._path import ROOT  # noqa: F401
from tests._investigation import investigation_test, slow_dat_to_ed_test

from core import bsp
from features.dat_editing import legacy_ed
from features.dat_editing import legacy_ed_writer
from features.dat_editing import surrogate_ed
from features.dat_editing import terrain_reconstruction
from features.dat_editing import terrain_semantics


@investigation_test
class SurrogateEdTests(unittest.TestCase):
    def test_dat_native_object_converter_is_class_agnostic(self):
        dat_path = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        with open(dat_path, "rb") as f:
            data = f.read()

        specs, notes = surrogate_ed.dat_native_object_specs_from_dat_bytes(
            data,
            class_names=("Barrel", "DestructableBrush"),
        )

        self.assertEqual({item.class_name for item in specs}, {"Barrel", "DestructableBrush"})
        self.assertEqual(sum(item.class_name == "Barrel" for item in specs), 4)
        self.assertEqual(sum(item.class_name == "DestructableBrush" for item in specs), 6)
        barrel = next(item for item in specs if item.class_name == "Barrel")
        properties = {item.name: item for item in barrel.properties}
        self.assertEqual(properties["Name"].value, barrel.name)
        self.assertIn("Filename", properties)
        self.assertEqual(barrel.source_model_name, "")
        self.assertTrue(any("outside the requested class filter" in note for note in notes))

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

    def test_legacy_ed_writer_welds_dedit_near_duplicate_brush_points(self):
        brush = legacy_ed_writer.LegacyEdBrush(
            points=(
                (0.0, 0.0, 0.0),
                (0.005, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
            ),
            surfaces=(
                legacy_ed_writer.LegacyEdSurface(
                    vertex_indices=(0, 2, 3),
                    plane_normal=(0.0, 1.0, 0.0),
                    plane_dist=0.0,
                ),
                legacy_ed_writer.LegacyEdSurface(
                    vertex_indices=(1, 3, 2),
                    plane_normal=(0.0, -1.0, 0.0),
                    plane_dist=0.0,
                ),
            ),
        )

        normalized = legacy_ed_writer.normalize_brush_points(brush)
        generated = legacy_ed_writer.build_raw_brush_stream((brush,))
        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="welded_writer.ed",
        )

        self.assertEqual(len(normalized.points), 3)
        self.assertEqual(normalized.surfaces[0].vertex_indices, (0, 1, 2))
        self.assertEqual(normalized.surfaces[1].vertex_indices, (0, 2, 1))
        self.assertEqual(len(scene.mesh_models()[0].points), 3)
        self.assertEqual(len(set(scene.mesh_models()[0].points)), 3)

    def test_legacy_ed_writer_removes_compiled_boundary_residue(self):
        brush = legacy_ed_writer.LegacyEdBrush(
            points=(
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                (0.5, 0.0, 0.0),
                (0.0, 0.0, 0.5),
            ),
            surfaces=(
                legacy_ed_writer.LegacyEdSurface(
                    vertex_indices=(0, 1, 2, 0, 3, 1, 2, 4),
                    plane_normal=(0.0, 1.0, 0.0),
                    plane_dist=0.0,
                ),
            ),
        )

        normalized = legacy_ed_writer.normalize_brush_points(brush)

        self.assertEqual(len(normalized.points), 3)
        self.assertEqual(normalized.surfaces[0].vertex_indices, (0, 1, 2, 0, 1, 2))

    def test_airail_object_property_template_matches_source_shape(self):
        properties = legacy_ed_writer.airail_object_properties(
            name="AITrk2",
            pos=(0.0, -176.0, 1248.0),
            rail_links=("AITrk3", "AITrk7"),
        )
        by_name = {prop.name: prop for prop in properties}

        self.assertEqual(len(properties), 36)
        self.assertEqual(by_name["Name"].value, "AITrk2")
        self.assertEqual(by_name["Pos"].value, (0.0, -176.0, 1248.0))
        self.assertEqual(by_name["Visible"].value, False)
        self.assertEqual(by_name["BoxPhysics"].value, True)
        self.assertEqual(by_name["StartOn"].value, True)
        self.assertEqual(by_name["RailLink0"].value, "AITrk3")
        self.assertEqual(by_name["RailLink1"].value, "AITrk7")
        self.assertEqual(by_name["RailLink2"].value, "")
        self.assertEqual(by_name["RailLink3"].value, "")
        self.assertEqual(by_name["ShowSurface"].flags, 1)
        self.assertEqual(by_name["NumSurfacePolies"].type_code, 6)

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

    def test_full_world_skeleton_reuses_precomputed_sky_marker_bundle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        with open(bootcamp, "rb") as f:
            data = f.read()

        brush = legacy_ed_writer.LegacyEdBrush(
            name="PrecomputedSkyMarker",
            points=((0.0, 0.0, 0.0), (64.0, 0.0, 0.0), (0.0, 0.0, 64.0)),
            surfaces=(legacy_ed_writer.LegacyEdSurface(
                vertex_indices=(0, 1, 2),
                plane_normal=(0.0, 1.0, 0.0),
                plane_dist=0.0,
                texture_name="TEXTURES\\LevelTextures\\Misc\\SkyMarker.dtx",
            ),),
        )
        summary = surrogate_ed.SurrogateEdModelSummary(
            name=brush.name,
            status="written",
            point_count=3,
            polygon_count=1,
            texture_count=1,
        )
        bundle = (
            (brush,),
            (summary,),
            (legacy_ed_writer.full_world_brush_node_properties(brush.name),),
            ("precomputed SkyMarker bundle reused",),
        )

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            include_sky_marker_brushes=True,
            sky_source_ed_path="missing-source-oracle.ed",
            _precomputed_sky_marker_brush_bundle=bundle,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 2)
        self.assertEqual(report.polygon_count, 7)
        self.assertTrue(any("precomputed SkyMarker bundle reused" in note for note in report.notes))
        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="precomputed_sky_marker.ed",
        )
        self.assertTrue(any("PrecomputedSkyMarker" in name for name in layout.brush_names))

    def test_full_world_skeleton_can_emit_airail_objects_from_source_oracle(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedAirailProbe",
            include_airail_objects=True,
            airail_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.object_count, 234)
        self.assertEqual(report.object_property_count, 8370)
        self.assertTrue(any("Generated AIRail object records: 230" in item for item in report.notes))
        self.assertTrue(any("source ED oracle matches=230" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_airail_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["WorldProperties"], 1)
        self.assertEqual(object_scan.class_counts["StartPoint"], 1)
        self.assertEqual(object_scan.class_counts["Light"], 1)
        self.assertEqual(object_scan.class_counts["AIRail"], 230)
        airails = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "AIRail"
        }
        self.assertEqual(len(airails), 230)
        self.assertEqual(airails["AITrk2"].property_value("RailLink0"), "AITrk3")
        self.assertEqual(airails["AITrk2"].property_value("RailLink1"), "AITrk7")
        self.assertAlmostEqual(airails["AITrk2"].property_value("Pos")[1], -176.0, places=2)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_airail_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 234)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_sky_objects_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedSkyProbe",
            include_sky_objects=True,
            sky_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 7)
        self.assertEqual(report.object_property_count, 136)
        self.assertTrue(any("Generated sky object records: 3" in item for item in report.notes))
        self.assertTrue(any("Sky source ED oracle loaded 3 sky object" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_sky_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["WorldProperties"], 1)
        self.assertEqual(object_scan.class_counts["StartPoint"], 1)
        self.assertEqual(object_scan.class_counts["Light"], 1)
        self.assertEqual(object_scan.class_counts["SkyPointer"], 1)
        self.assertEqual(object_scan.class_counts["DemoSkyWorldModel"], 1)
        self.assertEqual(object_scan.class_counts["TOD_Sky"], 1)
        sky_objects = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name in {"SkyPointer", "DemoSkyWorldModel", "TOD_Sky"}
        }
        self.assertEqual(sky_objects["SkyPointer0"].property_value("SkyDims"), (0.0, 0.0, 0.0))
        self.assertEqual(sky_objects["SkyBox0"].property_value("SkyDims"), (128.0, 128.0, 128.0))
        self.assertEqual(sky_objects["TOD_Sky0"].property_value("Visible"), True)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_sky_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 7)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_sound_objects_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedSoundProbe",
            include_sound_objects=True,
            sound_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 24)
        self.assertEqual(report.object_property_count, 470)
        self.assertTrue(any("Generated AmbientSound object records: 20" in item for item in report.notes))
        self.assertTrue(any("Sound source ED oracle loaded 20 AmbientSound" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_sound_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["AmbientSound"], 20)
        sounds = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "AmbientSound"
        }
        self.assertEqual(sounds["beachsound1"].property_value("Filename"), "Sounds\\Ambient\\Water\\waves02.wav")
        self.assertEqual(sounds["beachsound1"].property_value("OuterRadius"), 3500.0)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_sound_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 24)
        self.assertEqual(layout.group_child_count, 1)

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="bootcamp_sound_probe.ed",
        )
        sound_faces = [
            face for model in scene.models for face in model.faces
            if terrain_semantics.helper_texture_role(face.material_name) == "sound"
        ]
        self.assertEqual(sound_faces, [])

    def test_full_world_skeleton_can_emit_gameplay_trigger_objects_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedGameplayTriggerProbe",
            include_gameplay_trigger_objects=True,
            gameplay_trigger_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 6)
        self.assertEqual(report.object_property_count, 161)
        self.assertTrue(any("Generated gameplay trigger object records: 2" in item for item in report.notes))
        self.assertTrue(any("Gameplay trigger source ED oracle loaded 2" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_gameplay_trigger_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["Trigger"], 1)
        self.assertEqual(object_scan.class_counts["ExitTrigger"], 1)
        triggers = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name in {"Trigger", "ExitTrigger", "PortalTrigger"}
        }
        self.assertEqual(triggers["Trigger0"].property_value("TargetName1"), "ExitTrigger0")
        self.assertEqual(triggers["Trigger0"].property_value("MessageName1"), "trigger")
        self.assertEqual(triggers["Trigger0"].property_value("Dims"), (50.0, 50.0, 150.0))
        self.assertEqual(triggers["ExitTrigger0"].property_value("DestinationWorld"), "IsleofAshes")

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_gameplay_trigger_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 6)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_static_prop_objects_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedStaticPropProbe",
            include_static_prop_objects=True,
            static_prop_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 147)
        self.assertEqual(report.object_property_count, 5810)
        self.assertTrue(any("Generated static Prop object records: 143" in item for item in report.notes))
        self.assertTrue(any("Static prop source ED oracle loaded 143 Prop" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_static_prop_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["Prop"], 143)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "Prop"
        }
        self.assertEqual(props["Boat0"].property_value("Filename"), "models\\props\\vikingship.abc")
        self.assertEqual(props["Boat0"].property_value("Skin"), "skins\\props\\vikingship.dtx")
        self.assertEqual(props["Boat0"].property_value("Pos"), (13820.0, 401.0, 4960.0))
        self.assertEqual(props["Boat0"].property_value("Solid"), False)
        self.assertEqual(props["Prop31"].property_value("MoveToFloor"), False)
        self.assertEqual(len(props["Boat0"].properties), 40)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_static_prop_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 147)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_low_risk_behavior_prop_objects_from_source_oracle(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedLowRiskBehaviorPropProbe",
            include_low_risk_behavior_prop_objects=True,
            low_risk_behavior_prop_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 56)
        self.assertEqual(report.object_count, 12)
        self.assertEqual(report.object_property_count, 426)
        self.assertTrue(any("Generated low-risk behavior prop object records: 8" in item for item in report.notes))
        self.assertTrue(any("Barrel=4, BonePile=4" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_low_risk_behavior_prop_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["Barrel"], 4)
        self.assertEqual(object_scan.class_counts["BonePile"], 4)
        self.assertNotIn("WallTorch", object_scan.class_counts)
        self.assertNotIn("Fire", object_scan.class_counts)
        self.assertNotIn("TreasureChest", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name in {"Barrel", "BonePile"}
        }
        self.assertEqual(props["Barrel8"].property_value("Filename"), "models\\Props\\Barrel03.ABC")
        self.assertEqual(props["Barrel8"].property_value("Solid"), True)
        self.assertEqual(props["Barrel8"].property_value("MoveToFloor"), True)
        self.assertEqual(props["BonePile0"].property_value("Filename"), "models\\Props\\Trashpile.ABC")
        self.assertEqual(len(props["Barrel8"].properties), 41)
        self.assertEqual(len(props["BonePile0"].properties), 43)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_low_risk_behavior_prop_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 12)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_wall_torch_objects_from_source_oracle(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedWallTorchProbe",
            include_wall_torch_objects=True,
            wall_torch_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 56)
        self.assertEqual(report.object_count, 105)
        self.assertEqual(report.object_property_count, 6049)
        self.assertTrue(any("WallTorch source ED oracle loaded 101" in item for item in report.notes))
        self.assertTrue(any("Generated WallTorch object records: 101" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_wall_torch_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["WallTorch"], 101)
        self.assertNotIn("Fire", object_scan.class_counts)
        self.assertNotIn("TreasureChest", object_scan.class_counts)
        self.assertNotIn("PropDamager", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "WallTorch"
        }
        self.assertEqual(props["WallTorch3"].property_value("Filename"), "models\\props\\walltorch.abc")
        self.assertEqual(props["WallTorch3"].property_value("Skin"), "skins\\props\\walltorch.dtx")
        self.assertEqual(props["WallTorch3"].property_value("On"), True)
        self.assertEqual(props["WallTorch3"].property_value("SoundRadius"), 200.0)
        self.assertEqual(props["WallTorch3"].property_value("SoundFile"), "Sounds\\ambient\\torchlight.wav")
        self.assertEqual(props["WallTorch3"].property_value("Fire"), True)
        self.assertEqual(props["WallTorch3"].property_value("Solid"), True)
        self.assertEqual(props["WallTorch3"].property_value("MoveToFloor"), False)
        self.assertEqual(len(props["WallTorch3"].properties), 59)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_wall_torch_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 105)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_fire_objects_from_source_oracle(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedFireProbe",
            include_fire_objects=True,
            fire_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 56)
        self.assertEqual(report.object_count, 12)
        self.assertEqual(report.object_property_count, 458)
        self.assertTrue(any("Fire source ED oracle loaded 8" in item for item in report.notes))
        self.assertTrue(any("Generated Fire object records: 8" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_fire_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["Fire"], 8)
        self.assertNotIn("WallTorch", object_scan.class_counts)
        self.assertNotIn("TreasureChest", object_scan.class_counts)
        self.assertNotIn("PropDamager", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "Fire"
        }
        self.assertEqual(props["ImpGateFire7"].property_value("Pos"), (-2340.0, -224.0, 2893.0))
        self.assertEqual(props["ImpGateFire7"].property_value("On"), True)
        self.assertEqual(props["ImpGateFire7"].property_value("SoundRadius"), 400.0)
        self.assertEqual(props["ImpGateFire7"].property_value("SoundFile"), "Sounds\\Ambient\\inferno.wav")
        self.assertEqual(props["ImpGateFire7"].property_value("Fire"), True)
        self.assertEqual(props["ImpGateFire7"].property_value("LightMinRadius"), 55.0)
        self.assertEqual(props["ImpGateFire7"].property_value("LightMaxRadius"), 80.0)
        self.assertEqual(props["ImpGateFire7"].property_value("MoveToFloor"), False)
        self.assertEqual(len(props["ImpGateFire7"].properties), 46)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_fire_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 12)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_candle_prop_objects_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedCandlePropProbe",
            include_candle_prop_objects=True,
            candle_prop_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 33)
        self.assertEqual(report.object_property_count, 1337)
        self.assertTrue(any("CandleProp source ED oracle loaded 29" in item for item in report.notes))
        self.assertTrue(any("Generated CandleProp object records: 29" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_candle_prop_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["CandleProp"], 29)
        self.assertNotIn("Brazier", object_scan.class_counts)
        self.assertNotIn("Fire", object_scan.class_counts)
        self.assertNotIn("TreasureChest", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "CandleProp"
        }
        self.assertEqual(props["BrazierBowl17"].property_value("Filename"), "models\\Props\\Chandelier2.ABC")
        self.assertEqual(props["BrazierBowl17"].property_value("Skin"), "Skins\\Props\\Chandelier2.dtx")
        self.assertEqual(props["BrazierBowl17"].property_value("Pos"), (10737.0, 659.0, -850.0))
        self.assertEqual(props["BrazierBowl17"].property_value("Visible"), True)
        self.assertEqual(props["BrazierBowl17"].property_value("Solid"), False)
        self.assertEqual(props["BrazierBowl17"].property_value("MoveToFloor"), False)
        self.assertEqual(len(props["BrazierBowl17"].properties), 43)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_candle_prop_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 33)
        self.assertEqual(layout.group_child_count, 1)

    @slow_dat_to_ed_test
    def test_full_world_skeleton_can_emit_brazier_objects_from_source_oracle(self):
        terrors = os.path.join(ROOT, "mm9_data", "WORLDS", "1000TERRORS.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "1000TERRORS.ED")
        if not os.path.exists(terrors):
            self.skipTest(f"missing test level: {terrors}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(terrors, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=terrors,
            model_names=["BoardObj1"],
            group_name="GeneratedBrazierProbe",
            include_brazier_objects=True,
            brazier_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 11)
        self.assertEqual(report.object_property_count, 503)
        self.assertTrue(any("Brazier source ED oracle loaded 7" in item for item in report.notes))
        self.assertTrue(any("Generated Brazier object records: 7" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="terrors_brazier_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["Brazier"], 7)
        self.assertNotIn("CandleProp", object_scan.class_counts)
        self.assertNotIn("Fire", object_scan.class_counts)
        self.assertNotIn("TreasureChest", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "Brazier"
        }
        self.assertEqual(props["Brazier46"].property_value("Filename"), "models\\props\\Brazier.abc")
        self.assertEqual(props["Brazier46"].property_value("Skin"), "skins\\props\\Brazier.dtx")
        self.assertEqual(props["Brazier46"].property_value("Pos"), (1185.0, 909.0, -5032.0))
        self.assertEqual(props["Brazier46"].property_value("On"), True)
        self.assertEqual(props["Brazier46"].property_value("SoundRadius"), 200.0)
        self.assertEqual(props["Brazier46"].property_value("SoundFile"), "Sounds\\ambient\\torchlight.wav")
        self.assertEqual(props["Brazier46"].property_value("Fire"), True)
        self.assertEqual(props["Brazier46"].property_value("Visible"), True)
        self.assertEqual(props["Brazier46"].property_value("Solid"), False)
        self.assertEqual(props["Brazier46"].property_value("MoveToFloor"), True)
        self.assertEqual(len(props["Brazier46"].properties), 59)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="terrors_brazier_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 11)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_treasure_chest_objects_from_source_oracle(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedTreasureChestProbe",
            include_treasure_chest_objects=True,
            treasure_chest_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 56)
        self.assertEqual(report.object_count, 11)
        self.assertEqual(report.object_property_count, 531)
        self.assertTrue(any("TreasureChest source ED oracle loaded 7" in item for item in report.notes))
        self.assertTrue(any("1 trigger target reference" in item for item in report.notes))
        self.assertTrue(any("Generated TreasureChest object records: 7" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_treasure_chest_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["TreasureChest"], 7)
        self.assertNotIn("PropDamager", object_scan.class_counts)
        self.assertNotIn("DestructableProp", object_scan.class_counts)
        self.assertNotIn("Fire", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "TreasureChest"
        }
        self.assertEqual(props["TreasureChest1"].property_value("Filename"), "models\\Props\\Chest1.ABC")
        self.assertEqual(props["TreasureChest1"].property_value("Skin"), "Skins\\Props\\Chest1.dtx")
        self.assertEqual(props["TreasureChest1"].property_value("Pos"), (1344.0, -128.0, 480.0))
        self.assertEqual(props["TreasureChest1"].property_value("OpenSoundName"), "Sounds\\Events\\chestopeningwood.wav")
        self.assertEqual(props["TreasureChest1"].property_value("CloseSoundName"), "Sounds\\Events\\chestclosingwood.wav")
        self.assertEqual(props["TreasureChest1"].property_value("Locked"), False)
        self.assertEqual(props["TreasureChest1"].property_value("TriggerTarget"), "TreasureShooterTrigger1")
        self.assertEqual(props["TreasureChest1"].property_value("TreasureLevel"), 2.0)
        self.assertEqual(props["TreasureChest1"].property_value("Solid"), True)
        self.assertEqual(props["TreasureChest1"].property_value("MoveToFloor"), True)
        self.assertEqual(len(props["TreasureChest1"].properties), 63)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_treasure_chest_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 11)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_prop_damager_objects_from_source_oracle(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedPropDamagerProbe",
            include_prop_damager_objects=True,
            prop_damager_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 56)
        self.assertEqual(report.object_count, 10)
        self.assertEqual(report.object_property_count, 378)
        self.assertTrue(any("PropDamager source ED oracle loaded 6" in item for item in report.notes))
        self.assertTrue(any("0 damage trigger target reference" in item for item in report.notes))
        self.assertTrue(any("Generated PropDamager object records: 6" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_prop_damager_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["PropDamager"], 6)
        self.assertNotIn("DestructableProp", object_scan.class_counts)
        self.assertNotIn("TreasureChest", object_scan.class_counts)
        self.assertNotIn("Fire", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "PropDamager"
        }
        self.assertEqual(props["Spikes14"].property_value("Filename"), "models\\props\\SpikePit.abc")
        self.assertEqual(props["Spikes14"].property_value("Skin"), "skins\\props\\SpikePit.dtx")
        self.assertEqual(props["Spikes14"].property_value("Pos"), (1344.0, -395.9999694824219, 708.0))
        self.assertEqual(props["Spikes14"].property_value("DamagerStuff"), 0.0)
        self.assertEqual(props["Spikes14"].property_value("Visible"), True)
        self.assertEqual(props["Spikes14"].property_value("Solid"), False)
        self.assertEqual(props["Spikes14"].property_value("MoveToFloor"), False)
        self.assertEqual(len(props["Spikes14"].properties), 48)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_prop_damager_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 10)
        self.assertEqual(layout.group_child_count, 1)

    def test_full_world_skeleton_can_emit_destructable_prop_objects_from_source_oracle(self):
        bathhouse = os.path.join(ROOT, "mm9_data", "WORLDS", "BATHHOUSE.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BATHHOUSE.ED")
        if not os.path.exists(bathhouse):
            self.skipTest(f"missing test level: {bathhouse}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bathhouse, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bathhouse,
            model_names=["Door5"],
            group_name="GeneratedDestructablePropProbe",
            include_destructable_prop_objects=True,
            destructable_prop_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 25)
        self.assertEqual(report.object_property_count, 1959)
        self.assertTrue(any("DestructableProp source ED oracle loaded 21" in item for item in report.notes))
        self.assertTrue(any("0 damage trigger target reference" in item for item in report.notes))
        self.assertTrue(any("Generated DestructableProp object records: 21" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bathhouse_destructable_prop_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["DestructableProp"], 21)
        self.assertNotIn("PropDamager", object_scan.class_counts)
        self.assertNotIn("TreasureChest", object_scan.class_counts)
        self.assertNotIn("Fire", object_scan.class_counts)
        props = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "DestructableProp"
        }
        self.assertEqual(props["Urn0"].property_value("Filename"), "models\\Props\\UrnGargoyle.abc")
        self.assertEqual(props["Urn0"].property_value("Skin"), "Skins\\Props\\UrnGargoyle.dtx")
        self.assertEqual(props["Urn0"].property_value("Pos"), (-64.0, -80.0, 384.0))
        self.assertEqual(props["Urn0"].property_value("Scale"), 1.5)
        self.assertEqual(props["Urn0"].property_value("HitPoints"), 1.0)
        self.assertEqual(props["Urn0"].property_value("DamageHitPoints"), 1.0)
        self.assertEqual(props["Urn0"].property_value("CanDamage"), True)
        self.assertEqual(props["Urn0"].property_value("DestroyVisible"), True)
        self.assertEqual(props["Urn0"].property_value("DestroySolid"), True)
        self.assertEqual(props["Urn0"].property_value("DestroyGravity"), True)
        self.assertEqual(props["Urn0"].property_value("CustomSound"), "Sounds\\Events\\glass_smash.wav")
        self.assertEqual(props["Urn0"].property_value("ExplodeDamage"), 100.0)
        self.assertEqual(props["Urn0"].property_value("DamageRadius"), 200.0)
        self.assertEqual(props["Urn0"].property_value("Solid"), True)
        self.assertEqual(props["Urn0"].property_value("MoveToFloor"), True)
        self.assertEqual(len(props["Urn0"].properties), 89)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bathhouse_destructable_prop_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 25)
        self.assertEqual(layout.group_child_count, 1)

    @slow_dat_to_ed_test
    def test_full_world_skeleton_can_copy_sky_marker_brushes_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedSkyMarkerProbe",
            include_sky_objects=True,
            sky_source_ed_path=source_ed,
            include_sky_marker_brushes=True,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 24)
        self.assertEqual(report.point_count, 228)
        self.assertEqual(report.polygon_count, 162)
        self.assertEqual(report.object_count, 30)
        self.assertEqual(report.object_property_count, 780)
        self.assertTrue(any("copied 23 Brush record(s) with 156 SkyMarker face(s)" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_sky_marker_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 24)
        self.assertEqual(object_scan.class_counts["SkyPointer"], 1)
        self.assertEqual(object_scan.class_counts["DemoSkyWorldModel"], 1)
        self.assertEqual(object_scan.class_counts["TOD_Sky"], 1)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_sky_marker_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 7)
        self.assertEqual(layout.group_child_count, 24)
        self.assertEqual(layout.brush_names[0], "Brush_MonsterDoor1_0")
        self.assertTrue(layout.brush_names[1].startswith("Brush_SkyMarker_"))

        sky_brush_records = [
            record for record in object_scan.records
            if record.class_name == "Brush"
            and str(record.property_value("Name", "")).startswith("Brush_SkyMarker_")
        ]
        self.assertEqual(len(sky_brush_records), 23)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("SkyPortal")), 4)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("FullyBright")), 4)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("GouraudShade")), 18)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("LightMap")), 14)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("Subdivide")), 14)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("NotAStep")), 1)
        self.assertEqual(
            sorted(record.property_value("DetailLevel") for record in sky_brush_records),
            [0.0] * 12 + [1.0] * 11,
        )

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="bootcamp_sky_marker_probe.ed",
        )
        sky_faces = [
            face for model in scene.models for face in model.faces
            if terrain_semantics.helper_texture_role(face.material_name) == "skyVisibility"
        ]
        self.assertEqual(len(sky_faces), 156)
        self.assertTrue(all("SkyMarker.dtx" in face.material_name for face in sky_faces))
        texture_flag_counts = {}
        for face in sky_faces:
            flags = int(face.extras["texture_flags"])
            texture_flag_counts[flags] = texture_flag_counts.get(flags, 0) + 1
        self.assertEqual(texture_flag_counts, {0: 33, 1: 123})

    @slow_dat_to_ed_test
    def test_full_world_skeleton_can_emit_sky_marker_residue_brushes_from_reference(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedSkyMarkerResidueProbe",
            include_sky_objects=True,
            sky_source_ed_path=source_ed,
            include_sky_marker_residue_brushes=True,
            sky_marker_residue_reference_dat_path=bootcamp,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 24)
        self.assertEqual(report.point_count, 126)
        self.assertEqual(report.polygon_count, 33)
        self.assertEqual(report.object_count, 30)
        self.assertEqual(report.object_property_count, 780)
        self.assertTrue(any("23 diagnostic Brush record(s) with 27 matched SkyMarker face(s)" in item for item in report.notes))
        self.assertTrue(any("No non-oracle SkyMarker residue rule is exact yet" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_sky_marker_residue_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 24)
        self.assertEqual(object_scan.class_counts["SkyPointer"], 1)
        self.assertEqual(object_scan.class_counts["DemoSkyWorldModel"], 1)
        self.assertEqual(object_scan.class_counts["TOD_Sky"], 1)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_sky_marker_residue_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 7)
        self.assertEqual(layout.group_child_count, 24)
        self.assertEqual(layout.brush_names[0], "Brush_MonsterDoor1_0")
        self.assertTrue(layout.brush_names[1].startswith("Brush_SkyMarkerResidue_"))

        sky_brush_records = [
            record for record in object_scan.records
            if record.class_name == "Brush"
            and str(record.property_value("Name", "")).startswith("Brush_SkyMarkerResidue_")
        ]
        self.assertEqual(len(sky_brush_records), 23)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("SkyPortal")), 4)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("FullyBright")), 4)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("GouraudShade")), 18)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("LightMap")), 14)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("Subdivide")), 14)
        self.assertEqual(sum(1 for record in sky_brush_records if record.property_value("NotAStep")), 1)

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="bootcamp_sky_marker_residue_probe.ed",
        )
        sky_faces = [
            face for model in scene.models for face in model.faces
            if terrain_semantics.helper_texture_role(face.material_name) == "skyVisibility"
        ]
        self.assertEqual(len(sky_faces), 27)
        self.assertTrue(all("SkyMarker.dtx" in face.material_name for face in sky_faces))
        texture_flag_counts = {}
        for face in sky_faces:
            flags = int(face.extras["texture_flags"])
            texture_flag_counts[flags] = texture_flag_counts.get(flags, 0) + 1
        self.assertEqual(texture_flag_counts, {1: 27})

    def test_full_world_skeleton_can_emit_collision_helpers_from_source_oracle(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedCollisionHelperProbe",
            include_collision_helper_objects=True,
            collision_helper_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 27)
        self.assertEqual(report.polygon_count, 212)
        self.assertEqual(report.object_count, 56)
        self.assertEqual(report.object_property_count, 1327)
        self.assertTrue(any("Generated collision helper object records: 26" in item for item in report.notes))
        self.assertTrue(any("source ED object matches=26" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_collision_helper_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 27)
        self.assertEqual(object_scan.class_counts["WorldProperties"], 1)
        self.assertEqual(object_scan.class_counts["StartPoint"], 1)
        self.assertEqual(object_scan.class_counts["Light"], 1)
        self.assertEqual(object_scan.class_counts["Ladder"], 3)
        self.assertEqual(object_scan.class_counts["WorldObject"], 3)
        self.assertEqual(object_scan.class_counts["InvisibleBrush"], 8)
        self.assertEqual(object_scan.class_counts["PerceptionBrush"], 12)
        helpers = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name in {"InvisibleBrush", "PerceptionBrush", "Ladder", "WorldObject"}
        }
        self.assertEqual(helpers["InvisibleBrush7"].property_value("Solid"), True)
        self.assertEqual(helpers["PerceptionBrush0"].property_value("PerceptionValue"), 2.0)
        self.assertEqual(helpers["Ladder4"].property_value("SurfaceType"), 9.0)
        self.assertEqual(helpers["LadderBlocker3"].class_name, "WorldObject")

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_collision_helper_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 30)
        self.assertEqual(layout.group_child_count, 27)

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="anskramkeep_collision_helper_probe.ed",
        )
        helper_faces = [
            face for model in scene.models for face in model.faces
            if "invisible.dtx" in face.material_name.lower()
            or "firethrough.dtx" in face.material_name.lower()
            or "sprites" in face.material_name.lower()
        ]
        self.assertGreaterEqual(len(helper_faces), 156)
        self.assertTrue(all(int(face.extras["texture_flags"]) != 0 for face in helper_faces))

    def test_full_world_skeleton_can_emit_collision_helper_objects_without_helper_brushes(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["ExitStairs"],
            group_name="GeneratedCollisionObjectOnlyProbe",
            include_collision_helper_objects=True,
            include_collision_helper_brushes=False,
            collision_helper_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.object_count, 30)
        self.assertTrue(any("Generated collision helper object records: 26" in item for item in report.notes))
        self.assertTrue(any("emitted Brush records=0" in item for item in report.notes))
        self.assertTrue(any("Brush records were intentionally skipped" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="anskramkeep_collision_object_only_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["Ladder"], 3)
        self.assertEqual(object_scan.class_counts["WorldObject"], 3)
        self.assertEqual(object_scan.class_counts["InvisibleBrush"], 8)
        self.assertEqual(object_scan.class_counts["PerceptionBrush"], 12)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_collision_object_only_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.root_child_count, 30)
        self.assertEqual(layout.group_child_count, 1)

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="anskramkeep_collision_object_only_probe.ed",
        )
        helper_faces = [
            face for model in scene.models for face in model.faces
            if "invisible.dtx" in face.material_name.lower()
            or "firethrough.dtx" in face.material_name.lower()
            or "sprites" in face.material_name.lower()
        ]
        self.assertEqual(helper_faces, [])

    def test_full_world_skeleton_can_emit_trigger_helpers_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedTriggerHelperProbe",
            include_trigger_helper_objects=True,
            trigger_helper_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.selected_model_names, ("MonsterDoor1",))
        self.assertEqual(report.model_count, 3)
        self.assertEqual(report.point_count, 24)
        self.assertEqual(report.polygon_count, 18)
        self.assertEqual(report.object_count, 8)
        self.assertEqual(report.object_property_count, 222)
        self.assertTrue(any("Generated trigger helper object records: 2" in item for item in report.notes))
        self.assertTrue(any("source ED object matches=2" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_trigger_helper_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 3)
        self.assertEqual(object_scan.class_counts["WorldProperties"], 1)
        self.assertEqual(object_scan.class_counts["StartPoint"], 1)
        self.assertEqual(object_scan.class_counts["Light"], 1)
        self.assertEqual(object_scan.class_counts["PortalZone"], 2)
        portal_zones = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "PortalZone"
        }
        self.assertEqual(portal_zones["Tavernzone"].property_value("PortalName"), "Tavernportal")
        self.assertEqual(portal_zones["Storezone"].property_value("PortalName"), "Storeportal")
        self.assertAlmostEqual(portal_zones["Tavernzone"].property_value("Pos")[0], 10883.0, places=2)

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_trigger_helper_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.node_layout_kind, "named_group_brush_nodes_with_root_objects")
        self.assertEqual(layout.root_child_count, 6)
        self.assertEqual(layout.group_child_count, 3)
        self.assertEqual(
            layout.brush_names,
            ("Brush_MonsterDoor1_0", "Brush_Tavernzone_1", "Brush_Storezone_2"),
        )

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="bootcamp_trigger_helper_probe.ed",
        )
        helper_faces = [
            face for model in scene.models for face in model.faces
            if "greenscreen.dtx" in face.material_name.lower()
        ]
        self.assertEqual(len(helper_faces), 12)
        self.assertTrue(all(int(face.extras["texture_flags"]) != 0 for face in helper_faces))

    def test_full_world_skeleton_can_emit_trigger_objects_without_helper_brushes(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedTriggerObjectOnlyProbe",
            include_trigger_helper_objects=True,
            include_trigger_helper_brushes=False,
            trigger_helper_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.selected_model_names, ("MonsterDoor1",))
        self.assertEqual(report.model_count, 1)
        self.assertEqual(report.point_count, 8)
        self.assertEqual(report.polygon_count, 6)
        self.assertEqual(report.object_count, 6)
        self.assertTrue(any("Generated trigger helper object records: 2" in item for item in report.notes))
        self.assertTrue(any("emitted Brush records=0" in item for item in report.notes))
        self.assertTrue(any("Brush records were intentionally skipped" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_trigger_object_only_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["Brush"], 1)
        self.assertEqual(object_scan.class_counts["PortalZone"], 2)
        portal_zones = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name == "PortalZone"
        }
        self.assertEqual(portal_zones["Tavernzone"].property_value("PortalName"), "Tavernportal")
        self.assertEqual(portal_zones["Storezone"].property_value("PortalName"), "Storeportal")

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_trigger_object_only_probe.ed",
        )
        self.assertEqual(layout.status, "layout_parsed")
        self.assertEqual(layout.root_child_count, 6)
        self.assertEqual(layout.group_child_count, 1)
        self.assertEqual(layout.brush_names, ("Brush_MonsterDoor1_0",))

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="bootcamp_trigger_object_only_probe.ed",
        )
        helper_faces = [
            face for model in scene.models for face in model.faces
            if "greenscreen.dtx" in face.material_name.lower()
        ]
        self.assertEqual(helper_faces, [])

    def test_full_world_skeleton_uses_dat_native_helper_objects_without_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedDatNativeHelperProbe",
            include_airail_objects=True,
            include_sky_objects=True,
            include_sound_objects=True,
            include_collision_helper_objects=True,
            include_collision_helper_brushes=False,
            include_trigger_helper_objects=True,
            include_trigger_helper_brushes=False,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.model_count, 1)
        self.assertTrue(any("DAT object matches=15" in item for item in report.notes))
        self.assertTrue(any("DAT object matches=2" in item for item in report.notes))
        self.assertTrue(any("AmbientSound DAT object fallback loaded 20" in item for item in report.notes))
        self.assertTrue(any("Sky DAT object fallback loaded 3" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_dat_native_helper_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["AIRail"], 72)
        self.assertEqual(object_scan.class_counts["AmbientSound"], 20)
        self.assertEqual(object_scan.class_counts["PortalZone"], 2)
        self.assertEqual(object_scan.class_counts["TOD_Sky"], 1)
        self.assertEqual(object_scan.class_counts["SkyPointer"], 1)
        self.assertEqual(object_scan.class_counts["DemoSkyWorldModel"], 1)
        self.assertEqual(object_scan.class_counts["InvisibleBrush"], 5)
        self.assertEqual(object_scan.class_counts["PerceptionBrush"], 6)
        self.assertEqual(object_scan.class_counts["Ladder"], 3)
        self.assertEqual(object_scan.class_counts["WorldObject"], 1)
        helper_by_name = {
            str(record.property_value("Name")): record
            for record in object_scan.records
            if record.class_name in {"PortalZone", "AmbientSound", "InvisibleBrush"}
        }
        self.assertEqual(helper_by_name["Tavernzone"].property_value("PortalName"), "Tavernportal")
        self.assertEqual(helper_by_name["InvisibleBrush0"].property_value("Solid"), True)
        self.assertTrue(str(helper_by_name["beachsound1"].property_value("Filename")))

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="bootcamp_dat_native_helper_probe.ed",
        )
        helper_faces = [
            face for model in scene.models for face in model.faces
            if any(
                texture in face.material_name.lower()
                for texture in ("greenscreen.dtx", "invisible.dtx", "firethrough.dtx", "soundonly.dtx", "rail.dtx")
            )
        ]
        self.assertEqual(helper_faces, [])

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
        self.assertEqual(report.point_count, 124)
        self.assertEqual(report.polygon_count, 88)
        self.assertEqual(report.object_count, 8)
        self.assertEqual(report.object_property_count, 202)
        self.assertTrue(any("PhysicsBSP shell patch emitted 4/" in item for item in report.notes))
        self.assertTrue(any("side_wall=2, floor=1, ceiling=1, helper/special=0" in item for item in report.notes))
        for summary in shell_summaries:
            self.assertEqual(summary.status, "written")
            self.assertRegex(
                summary.name,
                r"^PhysicsShellProbe_(?:side_wall|floor|ceiling|helper_special)_\d{4}$",
            )
            self.assertGreaterEqual(summary.point_count, 6)
            self.assertGreaterEqual(summary.polygon_count, 5)

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="physics_shell_probe.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 5)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 88)
        self.assertEqual(scene.metadata["recovered_object_count"], 8)
        invisible_faces = [
            face for model in scene.models for face in model.faces
            if face.material_name == "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx"
        ]
        self.assertGreater(len(invisible_faces), 0)
        self.assertTrue(all(face.extras["texture_flags"] == 1 for face in invisible_faces))

    def test_full_world_skeleton_can_restrict_physics_shell_to_source_polygons(self):
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
            physics_shell_max_polygons=8,
            physics_shell_thickness=16.0,
            physics_shell_source_polygon_indices=(4205, 6861),
        )

        shell_summaries = [
            summary for summary in report.model_summaries
            if summary.name.startswith("PhysicsShellProbe_")
        ]
        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(len(shell_summaries), 2)
        self.assertEqual(
            {
                int(summary.name.rsplit("_", 1)[1])
                for summary in shell_summaries
            },
            {4205, 6861},
        )
        self.assertTrue(any(
            "restricted to requested source polygon indices: 4205, 6861" in item
            for item in report.notes
        ))
        self.assertGreater(len(generated), 0)

    @slow_dat_to_ed_test
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
        self.assertEqual(len(shell_summaries), 757)
        self.assertEqual(report.model_count, 863)
        self.assertEqual(report.point_count, 11737)
        self.assertEqual(report.polygon_count, 10014)
        self.assertEqual(report.object_count, 866)
        self.assertEqual(report.object_property_count, 24226)
        self.assertTrue(any("PhysicsBSP shell patch emitted 757/6442" in item for item in report.notes))
        self.assertTrue(any("864 source polygon(s) in 757 brush(es)" in item for item in report.notes))
        self.assertTrue(any("side_wall=617, floor=169, ceiling=78, helper/special=0" in item for item in report.notes))
        self.assertTrue(any("invalid=8, non-closed=71" in item for item in report.notes))

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="ANSKRAMKEEP_no_helper_physics_shell.ed",
        )
        self.assertEqual(scene.metadata["recovered_brush_count"], 863)
        self.assertEqual(scene.metadata["recovered_polygon_count"], 10014)
        self.assertEqual(scene.metadata["recovered_object_count"], 866)
        self.assertEqual(scene.metadata["object_class_counts"]["Brush"], 863)
        self.assertEqual(sum(len(model.points) for model in scene.mesh_models()), report.point_count)
        self.assertTrue(all(len(model.points) == len(set(model.points)) for model in scene.mesh_models()))
        rail_faces = [
            face for model in scene.models for face in model.faces
            if "rail.dtx" in face.material_name.lower()
        ]
        self.assertEqual(rail_faces, [])

    @slow_dat_to_ed_test
    def test_anskramkeep_stair_assembly_reservation_is_atomic(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        with open(anskramkeep, "rb") as f:
            data = f.read()
        parsed = bsp.parse(data)
        physics = terrain_semantics.model_by_name(parsed.world_models, "PhysicsBSP")
        candidates = terrain_reconstruction.physics_shell_candidates(physics)
        consolidation_index = terrain_reconstruction.build_physics_shell_consolidation_index(
            physics,
            candidates,
        )
        assembly = terrain_reconstruction.detect_physics_shell_stair_assemblies(
            physics,
            candidates,
            consolidation_index=consolidation_index,
        )[3]

        selected_cache = {}
        _generated, selected_report = (
            surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
                data,
                source_path=anskramkeep,
                _preparsed_world=parsed,
                _precomputed_physics_shell_consolidation_index=consolidation_index,
                _physics_shell_analysis_cache=selected_cache,
                model_names=("Innerdoor0",),
                include_physics_shell_patch=True,
                physics_shell_name_prefix="PhysicsShellAtomicStair",
                physics_shell_max_polygons=128,
                physics_shell_generated_face_budget=2048,
                physics_shell_stair_assembly_indices=(3,),
            )
        )
        emitted_indices = {
            int(token)
            for summary in selected_report.model_summaries
            if summary.name.startswith("PhysicsShellAtomicStair_")
            for token in summary.name.rsplit("_", 1)[-1].split("p")
        }
        self.assertEqual(selected_report.physics_shell_selected_stair_assembly_indices, (3,))
        self.assertEqual(selected_report.physics_shell_rejected_stair_assembly_indices, ())
        self.assertTrue(set(assembly.source_polygon_indices).issubset(emitted_indices))
        self.assertTrue(all(
            selected_cache["selection_reasons"][index] == "selected_for_shell_emission"
            for index in assembly.source_polygon_indices
        ))

        rejected_cache = {}
        _generated, rejected_report = (
            surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
                data,
                source_path=anskramkeep,
                _preparsed_world=parsed,
                _precomputed_physics_shell_consolidation_index=consolidation_index,
                _physics_shell_analysis_cache=rejected_cache,
                model_names=("Innerdoor0",),
                include_physics_shell_patch=True,
                physics_shell_name_prefix="PhysicsShellRejectedStair",
                physics_shell_max_polygons=32,
                physics_shell_generated_face_budget=512,
                physics_shell_stair_assembly_indices=(3,),
            )
        )
        rejected_emitted_indices = {
            int(token)
            for summary in rejected_report.model_summaries
            if summary.name.startswith("PhysicsShellRejectedStair_")
            for token in summary.name.rsplit("_", 1)[-1].split("p")
        }
        self.assertEqual(rejected_report.physics_shell_selected_stair_assembly_indices, ())
        self.assertEqual(rejected_report.physics_shell_rejected_stair_assembly_indices, (3,))
        self.assertTrue(set(assembly.source_polygon_indices).isdisjoint(rejected_emitted_indices))
        self.assertTrue(all(
            rejected_cache["selection_reasons"][index] == "rejected_stair_assembly"
            for index in assembly.source_polygon_indices
        ))

    @slow_dat_to_ed_test
    def test_source_door_clearance_bounds_are_derived_from_child_brushes(self):
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        bounds = surrogate_ed._source_door_clearance_bounds_from_source_ed(
            source_ed,
            candidate_names=("Innerdoor0",),
        )

        self.assertEqual(len(bounds), 1)
        bounds_min, bounds_max = bounds[0]
        self.assertLess(bounds_min[0], -384.0)
        self.assertGreater(bounds_max[2], 4144.0)
        self.assertTrue(
            surrogate_ed._physics_shell_group_intersects_clearance_bounds(
                ((-384.0, -192.0, 4112.0), (0.0, 0.0, 4144.0)),
                bounds,
            )
        )
        self.assertFalse(
            surrogate_ed._physics_shell_group_intersects_clearance_bounds(
                ((1000.0, 0.0, 1000.0), (1100.0, 100.0, 1100.0)),
                bounds,
            )
        )

        starting_door_bounds = surrogate_ed._source_door_clearance_bounds_from_source_ed(
            source_ed,
            candidate_names=("DoubleDoorL15", "DoubleDoorR15"),
        )
        self.assertTrue(
            surrogate_ed._physics_shell_group_intersects_clearance_bounds(
                (
                    (-256.0, -192.0, 128.0),
                    (128.0, 0.0, 128.0),
                    (128.0, 0.0, 144.0),
                    (-256.0, -192.0, 144.0),
                ),
                starting_door_bounds,
            )
        )

    @slow_dat_to_ed_test
    def test_anskramkeep_physics_shell_start_point_uses_source_oracle_anchor(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()
        parsed = bsp.parse(data)
        selected_names = terrain_semantics.default_dat_to_ed_model_names(parsed)

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=selected_names,
            group_name="ANSKRAMKEEP_SourceStartAnchor",
            include_physics_shell_patch=True,
            physics_shell_name_prefix="ANSKRAMKEEP_PhysicsShell",
            physics_shell_max_polygons=864,
            physics_shell_thickness=16.0,
            physics_shell_focus_points=((0.0, -104.0, 16.0),),
            physics_shell_focus_radius=512.0,
            physics_shell_focus_budget=512,
            physics_shell_focus_seed_radius=128.0,
            door_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertTrue(any("Source StartPoint oracle loaded 2" in item for item in report.notes))
        self.assertTrue(any("Source StartPoint support Brush copied for Anskramkeepback" in item for item in report.notes))
        self.assertTrue(any("Source StartPoint support Brush appended as an additional generated Brush" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="ANSKRAMKEEP_source_start_anchor.ed",
        )
        start_point = next(record for record in object_scan.records if record.class_name == "StartPoint")
        start_pos = start_point.property_value("Pos")
        self.assertEqual((round(start_pos[0]), round(start_pos[2])), (0, 16))
        self.assertAlmostEqual(start_pos[1], -104.0, places=2)
        support_brush = next(
            record for record in object_scan.records
            if record.class_name == "Brush"
            and str(record.property_value("Name", "")).startswith("Brush_SourceStartSupport_Anskramkeepback_Brush222")
        )
        self.assertTrue(support_brush.property_value("Solid"))
        self.assertTrue(support_brush.property_value("LightMap"))
        self.assertTrue(support_brush.property_value("Subdivide"))

        scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="ANSKRAMKEEP_source_start_anchor.ed",
        )
        support_index = [
            index for index, record in enumerate(record for record in object_scan.records if record.class_name == "Brush")
            if str(record.property_value("Name", "")).startswith("Brush_SourceStartSupport_Anskramkeepback_Brush222")
        ][0]
        support_model = scene.models[support_index]
        self.assertEqual(len(support_model.points), 12)
        self.assertEqual(len(support_model.faces), 8)
        self.assertEqual(
            {face.material_name for face in support_model.faces},
            {"TEXTURES\\A4Drangheim\\floors\\anskramfloor.dtx"},
        )

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

    @slow_dat_to_ed_test
    def test_full_world_skeleton_can_copy_matching_door_objects_from_source_oracle(self):
        bootcamp = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "BOOTCAMP.ED")
        if not os.path.exists(bootcamp):
            self.skipTest(f"missing test level: {bootcamp}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(bootcamp, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=bootcamp,
            model_names=["MonsterDoor1"],
            group_name="GeneratedDoorObjectProbe",
            include_door_objects=True,
            door_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.selected_model_names, ("MonsterDoor1", "MonsterDoor2"))
        self.assertEqual(report.object_count, 7)
        self.assertEqual(report.object_property_count, 282)
        self.assertTrue(any("DoubleDoorName leaf/leaves: MonsterDoor2" in item for item in report.notes))
        self.assertTrue(any("Door source ED oracle loaded 2 matched" in item for item in report.notes))
        self.assertTrue(any("copied source child Brush records for 2/2" in item for item in report.notes))
        self.assertTrue(any("child Brush replacement applied to 2" in item for item in report.notes))
        self.assertTrue(any("Generated Door/RotatingDoor object records: 2" in item for item in report.notes))

        object_scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="bootcamp_door_object_probe.ed",
        )
        self.assertEqual(object_scan.class_counts["RotatingDoor"], 2)
        door = next(
            record for record in object_scan.records
            if record.class_name == "RotatingDoor"
            and record.property_value("Name") == "MonsterDoor1"
        )
        self.assertEqual(door.property_value("Name"), "MonsterDoor1")
        self.assertEqual(door.property_value("DoubleDoorName"), "MonsterDoor2")
        self.assertEqual(door.property_value("Pos"), (8224.0, 561.0, -2108.0))
        self.assertTrue(door.property_value("Visible"))
        self.assertTrue(door.property_value("Solid"))
        pair = next(
            record for record in object_scan.records
            if record.class_name == "RotatingDoor"
            and record.property_value("Name") == "MonsterDoor2"
        )
        self.assertEqual(pair.property_value("DoubleDoorName"), "MonsterDoor1")

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="bootcamp_door_object_probe.ed",
        )
        wrapper = legacy_ed._try_decompress_full_level_wrapper(generated)
        self.assertIsNotNone(wrapper)
        assert wrapper is not None
        parsed, _end = _read_legacy_node_container(
            wrapper["decompressed"],
            layout.node_start,
            include_entry=False,
        )

        def _walk(node):
            yield node
            for child in node["children"]:
                yield from _walk(child)

        door_node = next(
            node for node in _walk(parsed)
            if node["item"]["class_name"] == "RotatingDoor"
            and node["item"]["properties"].get("Name") == "MonsterDoor1"
        )
        self.assertEqual(len(door_node["children"]), 1)
        child_node = door_node["children"][0]
        self.assertEqual(child_node["type"], legacy_ed_writer.NODE_BRUSH)
        self.assertEqual(child_node["item"]["class_name"], "Brush")
        self.assertTrue(child_node["item"]["properties"]["GouraudShade"])
        self.assertTrue(child_node["item"]["properties"]["LightMap"])
        self.assertTrue(child_node["item"]["properties"]["Subdivide"])

        with open(source_ed, "rb") as f:
            source_data = f.read()
        source_layout = legacy_ed.scan_legacy_ed_node_layout(
            source_data,
            source_path=source_ed,
        )
        source_wrapper = legacy_ed._try_decompress_full_level_wrapper(source_data)
        self.assertIsNotNone(source_wrapper)
        assert source_wrapper is not None
        source_parsed, _source_end = _read_legacy_node_container(
            source_wrapper["decompressed"],
            source_layout.node_start,
            include_entry=False,
        )
        source_door_node = next(
            node for node in _walk(source_parsed)
            if node["item"]["class_name"] == "RotatingDoor"
            and node["item"]["properties"].get("Name") == "MonsterDoor1"
        )
        source_child_index = source_door_node["children"][0]["brush_index"]
        generated_scene = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            generated,
            source_path="bootcamp_door_object_probe.ed",
        )
        source_scene = legacy_ed.load_legacy_ed_geometry_scene(source_ed)
        generated_face = generated_scene.models[child_node["brush_index"]].faces[0]
        source_face = source_scene.models[source_child_index].faces[0]
        self.assertEqual(generated_face.extras["uv_p"], source_face.extras["uv_p"])
        self.assertEqual(generated_face.extras["uv_q"], source_face.extras["uv_q"])
        pair_node = next(
            node for node in _walk(parsed)
            if node["item"]["class_name"] == "RotatingDoor"
            and node["item"]["properties"].get("Name") == "MonsterDoor2"
        )
        self.assertEqual(len(pair_node["children"]), 1)
        self.assertEqual(pair_node["children"][0]["type"], legacy_ed_writer.NODE_BRUSH)

    def test_full_world_skeleton_builds_dat_native_rotating_door_with_brush_child(self):
        isle = os.path.join(ROOT, "mm9_data", "WORLDS", "ISLEOFASHES.DAT")
        if not os.path.exists(isle):
            self.skipTest(f"missing test level: {isle}")
        with open(isle, "rb") as handle:
            data = handle.read()

        generated, report = (
            surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
                data,
                source_path=isle,
                model_names=["RotatingDoor8"],
                group_name="DatNativeDoorProbe",
                include_door_objects=True,
            )
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(report.selected_model_names, ("RotatingDoor8",))
        self.assertTrue(
            any(
                "DAT-native movable door fallback loaded 1 matched" in note
                for note in report.notes
            )
        )
        scan = legacy_ed.scan_legacy_ed_object_records(
            generated,
            source_path="isleofashes_dat_native_door_probe.ed",
        )
        self.assertEqual(scan.class_counts["RotatingDoor"], 1)
        door = next(
            record
            for record in scan.records
            if record.class_name == "RotatingDoor"
        )
        self.assertEqual(door.property_value("Name"), "RotatingDoor8")
        self.assertEqual(door.property_value("Pos"), (-1896.0, 138.0, -6610.0))
        self.assertEqual(door.property_value("RotationAngles"), (0.0, 90.0, 0.0))
        self.assertTrue(door.property_value("PushOpen"))
        self.assertFalse(door.property_value("Locked"))

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="isleofashes_dat_native_door_probe.ed",
        )
        wrapper = legacy_ed._try_decompress_full_level_wrapper(generated)
        self.assertIsNotNone(wrapper)
        assert wrapper is not None
        parsed, _end = _read_legacy_node_container(
            wrapper["decompressed"],
            layout.node_start,
            include_entry=False,
        )
        group = parsed["children"][0]
        self.assertEqual(group["item"]["display_name"], "DatNativeDoorProbe")
        self.assertEqual(group["children"], [])

        def _walk(node):
            yield node
            for child in node["children"]:
                yield from _walk(child)

        door_node = next(
            node
            for node in _walk(parsed)
            if node["item"]["class_name"] == "RotatingDoor"
        )
        self.assertEqual(len(door_node["children"]), 1)
        self.assertEqual(
            door_node["children"][0]["type"],
            legacy_ed_writer.NODE_BRUSH,
        )

    def test_adjacent_convex_terrain_group_removes_shared_vertical_wall(self):
        points = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 0.0, 10.0),
            (10.0, 0.0, 0.0),
        )
        first = terrain_reconstruction.TerrainSupportItem(
            10,
            object(),
            (0, 1, 2),
            (points[0], points[1], points[2]),
            (10.0 / 3.0, 0.0, 20.0 / 3.0),
            (0.0, 10.0, 0.0, 10.0),
        )
        second = terrain_reconstruction.TerrainSupportItem(
            11,
            object(),
            (0, 2, 3),
            (points[0], points[2], points[3]),
            (20.0 / 3.0, 0.0, 10.0 / 3.0),
            (0.0, 10.0, 0.0, 10.0),
        )

        groups = surrogate_ed._terrain_support_item_groups(
            (first, second),
            brush_mode="adjacent_convex",
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(tuple(item.polygon_index for item in groups[0]), (10, 11))

        brush, summary = surrogate_ed._terrain_polygon_group_prism_brush(
            None,
            groups[0],
            name="AdjacentTerrainProbe",
            patch_index=0,
            thickness=8.0,
            side_texture="Default",
        )
        self.assertEqual(summary.source_polygon_count, 2)
        self.assertEqual(len(brush.surfaces), 8)
        self.assertEqual(
            surrogate_ed._terrain_group_internal_edge_count(groups[0]),
            1,
        )
        self.assertTrue(surrogate_ed._legacy_brush_faces_enclose_points(brush))

    def test_adaptive_terrain_merges_coplanar_faces_into_one_structural_top(self):
        source_surface = types.SimpleNamespace(
            uv_o=(2.0, 3.0, 4.0),
            uv_p=(0.5, 0.0, 0.0),
            uv_q=(0.0, 0.0, -0.5),
            texture_flags=13,
        )
        terrain_model = types.SimpleNamespace(
            name="TerrainProbe",
            surfaces=(source_surface,),
            texture_name_for=lambda _polygon: (
                "TEXTURES\\A1ISLEOFASHES\\Terrain\\AshCliff.dtx"
            ),
        )
        points = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 0.0, 10.0),
            (10.0, 0.0, 0.0),
        )
        items = (
            terrain_reconstruction.TerrainSupportItem(
                20,
                types.SimpleNamespace(surface_index=0),
                (0, 1, 2),
                (points[0], points[1], points[2]),
                (10.0 / 3.0, 0.0, 20.0 / 3.0),
                (0.0, 10.0, 0.0, 10.0),
            ),
            terrain_reconstruction.TerrainSupportItem(
                21,
                types.SimpleNamespace(surface_index=0),
                (0, 2, 3),
                (points[0], points[2], points[3]),
                (20.0 / 3.0, 0.0, 10.0 / 3.0),
                (0.0, 10.0, 0.0, 10.0),
            ),
        )
        oracle = terrain_reconstruction.TerrainCollisionOracle(
            512.0,
            {},
            0,
        )

        groups = surrogate_ed._terrain_support_item_groups(
            items,
            brush_mode="adaptive_structural",
            terrain_model=terrain_model,
            anchor_points=(),
            radius=4096.0,
            physics_oracle=oracle,
        )
        self.assertEqual(len(groups), 1)
        result = surrogate_ed._adaptive_structural_terrain_prism_brush(
            terrain_model,
            groups[0],
            anchor_points=(),
            radius=4096.0,
            physics_oracle=oracle,
            name="AdaptiveTerrainProbe",
            patch_index=0,
            thickness=8.0,
            side_texture="Default",
        )
        self.assertIsNotNone(result)
        assert result is not None
        brush, summary = result
        self.assertEqual(summary.source_polygon_count, 2)
        self.assertEqual(len(brush.surfaces), 6)
        self.assertEqual(
            brush.surfaces[0].texture_name,
            "TEXTURES\\A1ISLEOFASHES\\Terrain\\AshCliff.dtx",
        )
        self.assertEqual(brush.surfaces[0].uv_o, source_surface.uv_o)
        self.assertEqual(brush.surfaces[0].uv_p, source_surface.uv_p)
        self.assertEqual(brush.surfaces[0].uv_q, source_surface.uv_q)
        self.assertEqual(
            brush.surfaces[0].texture_flags,
            source_surface.texture_flags,
        )
        self.assertTrue(
            any("max source error=0.0000" in note for note in summary.notes)
        )
        self.assertTrue(
            any("visible slabs=0" in note for note in summary.notes)
        )
        self.assertTrue(surrogate_ed._legacy_brush_faces_enclose_points(brush))

    def test_adaptive_terrain_keeps_anchor_patch_top_faces_exact(self):
        points = (
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 0.0, 10.0),
            (10.0, 0.0, 0.0),
        )
        items = (
            terrain_reconstruction.TerrainSupportItem(
                30,
                object(),
                (0, 1, 2),
                (points[0], points[1], points[2]),
                (10.0 / 3.0, 0.0, 20.0 / 3.0),
                (0.0, 10.0, 0.0, 10.0),
            ),
            terrain_reconstruction.TerrainSupportItem(
                31,
                object(),
                (0, 2, 3),
                (points[0], points[2], points[3]),
                (20.0 / 3.0, 0.0, 10.0 / 3.0),
                (0.0, 10.0, 0.0, 10.0),
            ),
        )
        oracle = terrain_reconstruction.TerrainCollisionOracle(
            512.0,
            {},
            0,
        )
        result = surrogate_ed._adaptive_structural_terrain_prism_brush(
            None,
            items,
            anchor_points=((5.0, 0.0, 5.0),),
            radius=4096.0,
            physics_oracle=oracle,
            name="ExactAnchorTerrainProbe",
            patch_index=0,
            thickness=8.0,
            side_texture="Default",
        )

        self.assertIsNotNone(result)
        assert result is not None
        brush, summary = result
        self.assertEqual(summary.source_polygon_count, 2)
        self.assertEqual(len(brush.surfaces), 8)
        self.assertTrue(
            any("preserved without approximation" in note for note in summary.notes)
        )

    @slow_dat_to_ed_test
    def test_full_world_skeleton_copies_door_child_brush_properties_from_node_hierarchy(self):
        anskramkeep = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        source_ed = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.ED")
        if not os.path.exists(anskramkeep):
            self.skipTest(f"missing test level: {anskramkeep}")
        if not os.path.exists(source_ed):
            self.skipTest(f"missing source oracle: {source_ed}")

        with open(anskramkeep, "rb") as f:
            data = f.read()

        generated, report = surrogate_ed.build_full_world_skeleton_surrogate_legacy_ed_bytes_from_dat_bytes(
            data,
            source_path=anskramkeep,
            model_names=["Innerdoor1"],
            group_name="GeneratedAnskramkeepDoorProbe",
            include_door_objects=True,
            door_source_ed_path=source_ed,
        )

        self.assertEqual(report.status, "full_world_skeleton_surrogate_ed_built")
        self.assertEqual(set(report.selected_model_names), {"Innerdoor0", "Innerdoor1"})
        self.assertTrue(any("child Brush replacement applied to 2" in item for item in report.notes))

        layout = legacy_ed.scan_legacy_ed_node_layout(
            generated,
            source_path="anskramkeep_innerdoor_object_probe.ed",
        )
        wrapper = legacy_ed._try_decompress_full_level_wrapper(generated)
        self.assertIsNotNone(wrapper)
        assert wrapper is not None
        parsed, _end = _read_legacy_node_container(
            wrapper["decompressed"],
            layout.node_start,
            include_entry=False,
        )

        def _walk(node):
            yield node
            for child in node["children"]:
                yield from _walk(child)

        door_node = next(
            node for node in _walk(parsed)
            if node["item"]["class_name"] == "Door"
            and node["item"]["properties"].get("Name") == "Innerdoor1"
        )
        self.assertEqual(len(door_node["children"]), 1)
        child_node = door_node["children"][0]
        self.assertEqual(child_node["type"], legacy_ed_writer.NODE_BRUSH)
        self.assertEqual(child_node["item"]["class_name"], "Brush")
        child_properties = child_node["item"]["properties"]
        self.assertTrue(str(child_properties["Name"]).startswith("Brush_Innerdoor1_"))
        self.assertTrue(child_properties["Solid"])
        self.assertTrue(child_properties["GouraudShade"])
        self.assertTrue(child_properties["LightMap"])
        self.assertTrue(child_properties["Subdivide"])
        self.assertFalse(child_properties["Portal"])

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
