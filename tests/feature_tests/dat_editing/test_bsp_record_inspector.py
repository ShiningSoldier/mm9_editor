import os
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from features.dat_editing import bsp_record_inspector


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class BspRecordInspectorTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data)

    def test_inspects_terrain0_and_physics_bsp_records(self):
        _path, data, _world = self.load_bootcamp()

        report = bsp_record_inspector.inspect_dat(data)

        terrain = report["Terrain0"]
        physics = report["PhysicsBSP"]
        self.assertTrue(terrain.present)
        self.assertTrue(physics.present)
        self.assertGreater(terrain.raw_size, 0)
        self.assertGreater(physics.raw_size, 0)
        self.assertGreater(terrain.point_count, 0)
        self.assertGreater(terrain.polygon_count, 0)
        self.assertGreater(terrain.plane_count, 0)
        self.assertIn("trailing_payload", terrain.section_ranges)
        self.assertEqual(terrain.leaf_count, 0)
        self.assertEqual(terrain.node_count, 0)
        self.assertEqual(terrain.terrain_tail_node_count, 342)
        self.assertEqual(terrain.terrain_tail_root_count, 1)
        self.assertTrue(terrain.terrain_tail_valid_tree)
        self.assertEqual(terrain.terrain_tail_polygon_list_count, 235)
        self.assertEqual(terrain.terrain_tail_render_compact_node_count, 342)
        self.assertEqual(terrain.terrain_tail_render_compact_root_index, 341)
        self.assertTrue(terrain.terrain_tail_render_compact_valid_tree)
        self.assertEqual(terrain.terrain_tail_render_bsp_marker, 0)
        self.assertEqual(terrain.terrain_tail_render_bsp_depth, 3)
        self.assertEqual(terrain.terrain_tail_render_bsp_node_count, 320)
        self.assertEqual(terrain.terrain_tail_render_bsp_polygon_list_count, 246)
        self.assertTrue(terrain.terrain_tail_render_bsp_valid_tree)
        self.assertEqual(terrain.terrain_tail_render_chunk_count, 20)
        self.assertEqual(terrain.terrain_tail_render_terminal_chunk_count, 1)
        self.assertEqual(terrain.terrain_tail_render_chunk_compact_node_total, 9245)
        self.assertEqual(terrain.terrain_tail_render_chunk_bsp_node_total, 8903)
        self.assertEqual(terrain.terrain_tail_render_chunk_polygon_list_total, 4387)
        self.assertTrue(terrain.terrain_tail_render_chunk_chain_valid)
        self.assertEqual(terrain.terrain_tail_render_unknown_payload_size, 0)
        self.assertTrue(terrain.terrain_tail_render_fully_decoded)
        self.assertEqual(len(terrain.terrain_tail_render_chunks), 20)
        self.assertEqual(terrain.terrain_tail_render_chunks[0].source_node_table_name, "terrain_tail_nodes")
        self.assertFalse(terrain.terrain_tail_render_chunks[0].terminal)
        self.assertEqual(terrain.terrain_tail_render_chunks[0].header_flags, (1, 1, 1))
        self.assertEqual(terrain.terrain_tail_render_chunks[0].compact_node_count, 342)
        self.assertEqual(terrain.terrain_tail_render_chunks[0].bsp_node_count, 320)
        self.assertTrue(terrain.terrain_tail_render_chunks[-1].terminal)
        self.assertEqual(terrain.terrain_tail_render_chunks[-1].bsp_node_count, 0)
        self.assertEqual(terrain.plane_relationship.reference_mode, "indexed")
        self.assertTrue(terrain.plane_relationship.polygon_records_use_plane_table)
        self.assertEqual(terrain.plane_relationship.out_of_range_polygon_count, 0)
        self.assertEqual(terrain.plane_relationship.referenced_plane_count, terrain.plane_count)
        self.assertEqual(terrain.plane_relationship.unused_plane_count, 0)
        self.assertEqual(terrain.lightmapped_polygon_count, 0)
        self.assertEqual(terrain.lightmap_extra_data_polygon_count, 0)
        self.assertIn("terrain_tail_nodes", terrain.section_ranges)
        self.assertIn("terrain_tail_polygon_list", terrain.section_ranges)
        self.assertIn("terrain_tail_render_payload", terrain.section_ranges)
        self.assertIn("terrain_tail_render_header", terrain.section_ranges)
        self.assertIn("terrain_tail_render_compact_nodes", terrain.section_ranges)
        self.assertIn("terrain_tail_render_bsp_header", terrain.section_ranges)
        self.assertIn("terrain_tail_render_bsp_nodes", terrain.section_ranges)
        self.assertIn("terrain_tail_render_bsp_polygon_list", terrain.section_ranges)
        self.assertIn("terrain_tail_render_chunks", terrain.section_ranges)
        self.assertIn("terrain_tail_render_unknown_payload", terrain.section_ranges)
        self.assertEqual(
            terrain.section_ranges["terrain_tail_render_unknown_payload"][0],
            terrain.section_ranges["terrain_tail_render_unknown_payload"][1],
        )
        self.assertGreater(physics.point_count, 0)
        self.assertGreater(physics.polygon_count, 0)
        self.assertIn("nodes", physics.section_ranges)
        self.assertGreater(physics.node_count, 0)
        self.assertEqual(physics.bsp_node_root_count, 1)
        self.assertTrue(physics.bsp_node_valid_tree)
        self.assertEqual(physics.bsp_node_referenced_polygon_count, physics.polygon_count)
        self.assertEqual(physics.physics_block_dimensions, (10, 2, 8))
        self.assertEqual(physics.physics_block_cell_count, 160)
        self.assertEqual(physics.physics_block_nonempty_cell_count, 136)
        self.assertEqual(physics.physics_block_empty_cell_count, 24)
        self.assertEqual(physics.physics_block_record_count, 31147)
        self.assertEqual(physics.physics_block_max_cell_node_count, 8227)
        self.assertEqual(physics.physics_block_valid_cell_tree_count, 136)
        self.assertEqual(physics.physics_block_invalid_cell_tree_count, 0)
        self.assertEqual(physics.physics_block_referenced_node_count, physics.node_count)
        self.assertEqual(physics.physics_block_duplicate_node_reference_count, 12665)
        text = bsp_record_inspector.format_inspection_report(report)
        self.assertIn("Terrain0", text)
        self.assertIn("PhysicsBSP", text)
        self.assertIn("terrain tail: nodes=342", text)
        self.assertIn("terrain render tail: compact_nodes=342", text)
        self.assertIn("terrain render BSP: marker=0, depth=3", text)
        self.assertIn("terrain render chunks: chunks=20, terminal=1", text)
        self.assertIn("fully_decoded=True, unknown_tail_bytes=0", text)
        self.assertIn("plane relationship: mode=indexed", text)
        self.assertIn("BSP node table: roots=1", text)
        self.assertIn("physics block table: dims=(10, 2, 8)", text)

    def test_bootcamp_world_tree_layout_is_decoded(self):
        _path, _data, world = self.load_bootcamp()

        tree = world.world_tree

        self.assertIsNotNone(tree)
        self.assertEqual(tree.min_box, (4608.0, -128.0, -4352.0))
        self.assertEqual(tree.max_box, (23552.0, 2112.0, 11264.0))
        self.assertEqual(tree.declared_node_count, 1061)
        self.assertEqual(tree.decoded_node_count, 1061)
        self.assertEqual(tree.internal_node_count, 265)
        self.assertEqual(tree.leaf_node_count, 796)
        self.assertEqual(tree.max_depth, 5)
        self.assertEqual(tree.dummy_terrain_depth, 3)
        self.assertEqual(tree.layout_start, 177)
        self.assertEqual(tree.layout_end, 310)
        self.assertEqual(tree.byte_count, 133)
        self.assertEqual(tree.bit_count, 1061)
        self.assertTrue(tree.valid_node_count)
        self.assertFalse(tree.depth_limit_exceeded)

    def test_decodes_terrain_polygon_lightmap_metadata(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "ISLEOFASHES.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()

        report = bsp_record_inspector.inspect_dat(data, model_names=["Terrain3"])
        terrain = report["Terrain3"]

        self.assertTrue(terrain.present)
        self.assertEqual(terrain.polygon_count, 836)
        self.assertEqual(terrain.lightmapped_polygon_count, 110)
        self.assertEqual(terrain.lightmap_extra_data_polygon_count, 110)
        self.assertEqual(terrain.lightmap_extra_data_value_count, 110)
        self.assertGreater(terrain.lightmap_pixel_count, 0)
        self.assertGreaterEqual(terrain.max_lightmap_width, 9)
        self.assertGreaterEqual(terrain.max_lightmap_height, 9)
        text = bsp_record_inspector.format_inspection_report(report)
        self.assertIn("polygon lightmaps: polygons=110", text)

    def test_physics_bsp_derived_data_golden_across_shipped_levels(self):
        cases = {
            "AFTERWORLD.DAT": {
                "points": 2542,
                "polygons": 1274,
                "nodes": 3553,
                "dims": (2, 1, 2),
                "cell_count": 4,
                "nonempty": 4,
                "empty": 0,
                "compact_nodes": 3816,
                "max_cell_nodes": 3550,
                "duplicate_refs": 263,
            },
            "ARSLEGARDCITY.DAT": {
                "points": 12625,
                "polygons": 6414,
                "nodes": 15739,
                "dims": (5, 1, 6),
                "cell_count": 30,
                "nonempty": 23,
                "empty": 7,
                "compact_nodes": 30683,
                "max_cell_nodes": 3338,
                "duplicate_refs": 14944,
            },
            "DRANGHEIMCITY.DAT": {
                "points": 12806,
                "polygons": 6470,
                "nodes": 20562,
                "dims": (4, 1, 4),
                "cell_count": 16,
                "nonempty": 16,
                "empty": 0,
                "compact_nodes": 36022,
                "max_cell_nodes": 7928,
                "duplicate_refs": 15460,
            },
            "FROSGARDCITY.DAT": {
                "points": 11726,
                "polygons": 6739,
                "nodes": 16733,
                "dims": (16, 4, 9),
                "cell_count": 576,
                "nonempty": 416,
                "empty": 160,
                "compact_nodes": 49519,
                "max_cell_nodes": 3560,
                "duplicate_refs": 32786,
            },
        }
        for filename, expected in cases.items():
            path = os.path.join(DATA_ROOT, "WORLDS", filename)
            if not os.path.exists(path):
                self.skipTest(f"missing shipped level fixture: {path}")
            with self.subTest(filename=filename):
                with open(path, "rb") as f:
                    data = f.read()
                physics = bsp_record_inspector.inspect_dat(data, model_names=("PhysicsBSP",))["PhysicsBSP"]

                self.assertTrue(physics.present)
                self.assertEqual(physics.raw_error, "")
                self.assertEqual(physics.point_count, expected["points"])
                self.assertEqual(physics.polygon_count, expected["polygons"])
                self.assertEqual(physics.node_count, expected["nodes"])
                self.assertEqual(physics.bsp_node_root_count, 1)
                self.assertTrue(physics.bsp_node_valid_tree)
                self.assertEqual(physics.bsp_node_referenced_polygon_count, expected["polygons"])
                self.assertEqual(physics.physics_block_dimensions, expected["dims"])
                self.assertEqual(physics.physics_block_cell_count, expected["cell_count"])
                self.assertEqual(physics.physics_block_nonempty_cell_count, expected["nonempty"])
                self.assertEqual(physics.physics_block_empty_cell_count, expected["empty"])
                self.assertEqual(physics.physics_block_record_count, expected["compact_nodes"])
                self.assertEqual(physics.physics_block_max_cell_node_count, expected["max_cell_nodes"])
                self.assertEqual(physics.physics_block_valid_cell_tree_count, expected["nonempty"])
                self.assertEqual(physics.physics_block_invalid_cell_tree_count, 0)
                self.assertEqual(physics.physics_block_referenced_node_count, expected["nodes"])
                self.assertEqual(physics.physics_block_duplicate_node_reference_count, expected["duplicate_refs"])

    def test_terrain0_derived_data_golden_across_shipped_levels(self):
        cases = {
            "AFTERWORLD.DAT": {
                "points": 3891,
                "polygons": 6362,
                "planes": 5188,
                "tail_nodes": 39,
                "tail_polygons": 28,
                "tail_list": 28,
                "chunks": 64,
                "compact_total": 12448,
                "bsp_total": 12409,
                "polygon_lists": 7846,
            },
            "ARSLEGARDCITY.DAT": {
                "points": 414,
                "polygons": 680,
                "planes": 545,
                "tail_nodes": 77,
                "tail_polygons": 57,
                "tail_list": 57,
                "chunks": 11,
                "compact_total": 1419,
                "bsp_total": 1342,
                "polygon_lists": 788,
            },
            "DRANGHEIMCITY.DAT": {
                "points": 1232,
                "polygons": 1637,
                "planes": 1145,
                "tail_nodes": 604,
                "tail_polygons": 200,
                "tail_list": 200,
                "chunks": 16,
                "compact_total": 3401,
                "bsp_total": 2797,
                "polygon_lists": 1646,
            },
            "FROSGARDCITY.DAT": {
                "points": 2769,
                "polygons": 4133,
                "planes": 3809,
                "tail_nodes": 13,
                "tail_polygons": 13,
                "tail_list": 13,
                "chunks": 70,
                "compact_total": 9804,
                "bsp_total": 9791,
                "polygon_lists": 5067,
            },
        }
        for filename, expected in cases.items():
            path = os.path.join(DATA_ROOT, "WORLDS", filename)
            if not os.path.exists(path):
                self.skipTest(f"missing shipped level fixture: {path}")
            with self.subTest(filename=filename):
                with open(path, "rb") as f:
                    data = f.read()
                terrain = bsp_record_inspector.inspect_dat(data, model_names=("Terrain0",))["Terrain0"]

                self.assertTrue(terrain.present)
                self.assertEqual(terrain.raw_error, "")
                self.assertEqual(terrain.point_count, expected["points"])
                self.assertEqual(terrain.polygon_count, expected["polygons"])
                self.assertEqual(terrain.plane_count, expected["planes"])
                self.assertEqual(terrain.terrain_tail_node_count, expected["tail_nodes"])
                self.assertEqual(terrain.terrain_tail_referenced_polygon_count, expected["tail_polygons"])
                self.assertEqual(terrain.terrain_tail_polygon_list_count, expected["tail_list"])
                self.assertTrue(terrain.terrain_tail_valid_tree)
                self.assertEqual(terrain.terrain_tail_render_chunk_count, expected["chunks"])
                self.assertEqual(terrain.terrain_tail_render_terminal_chunk_count, 1)
                self.assertEqual(terrain.terrain_tail_render_chunk_compact_node_total, expected["compact_total"])
                self.assertEqual(terrain.terrain_tail_render_chunk_bsp_node_total, expected["bsp_total"])
                self.assertEqual(terrain.terrain_tail_render_chunk_polygon_list_total, expected["polygon_lists"])
                self.assertTrue(terrain.terrain_tail_render_chunk_chain_valid)
                self.assertEqual(terrain.terrain_tail_render_unknown_payload_size, 0)
                self.assertTrue(terrain.terrain_tail_render_fully_decoded)
                self.assertEqual(len(terrain.terrain_tail_render_chunks), expected["chunks"])
                self.assertEqual(terrain.terrain_tail_render_chunks[0].source_node_table_name, "terrain_tail_nodes")
                self.assertEqual(terrain.terrain_tail_render_chunks[0].header_flags, (1, 1, 1))
                self.assertFalse(terrain.terrain_tail_render_chunks[0].terminal)
                self.assertTrue(terrain.terrain_tail_render_chunks[-1].terminal)
                self.assertEqual(terrain.terrain_tail_render_chunks[-1].bsp_node_count, 0)
                self.assertEqual(terrain.plane_relationship.reference_mode, "indexed")
                self.assertTrue(terrain.plane_relationship.polygon_records_use_plane_table)
                self.assertEqual(terrain.plane_relationship.out_of_range_polygon_count, 0)
                self.assertEqual(terrain.plane_relationship.referenced_plane_count, terrain.plane_count)
                self.assertEqual(terrain.plane_relationship.unused_plane_count, 0)


if __name__ == "__main__":
    unittest.main()
