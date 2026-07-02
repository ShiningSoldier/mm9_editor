import copy
import os
import struct
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from features.dat_editing import (
    bsp_record_inspector,
    terrain_bsp_patch,
    terrain_reconstruction,
)


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class TerrainBspPatchTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data)

    def test_diff_identifies_point_only_terrain_patch(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        physics = world.model_by_name("PhysicsBSP")
        if terrain is None or physics is None:
            self.skipTest("BOOTCAMP is missing Terrain0 or PhysicsBSP")

        edited = copy.deepcopy(terrain)
        edited.points[81] = (
            terrain.points[81][0] + 1.0,
            terrain.points[81][1],
            terrain.points[81][2],
        )
        raw = world.raw_model_bytes(data, terrain)
        patched = terrain_bsp_patch.patch_terrain_model_points_only(raw, terrain, edited)
        changed = bytearray(data)
        changed[terrain.raw_start:terrain.raw_end] = patched

        report = bsp_record_inspector.diff_dat_records(data, bytes(changed))
        by_name = {item.name: item for item in report.model_diffs}
        terrain_diff = by_name["Terrain0"]
        physics_diff = by_name["PhysicsBSP"]

        self.assertTrue(terrain_diff.comparable)
        self.assertGreater(terrain_diff.byte_diff_count, 0)
        self.assertEqual(terrain_diff.unknown_structural_changed_bytes, 0)
        self.assertEqual(terrain_diff.moved_points.changed_count, 1)
        self.assertEqual(terrain_diff.moved_points.changed_indices, [81])
        self.assertEqual(terrain_diff.changed_planes.changed_count, 0)
        self.assertEqual(terrain_diff.changed_polygon_centers.changed_count, 0)
        self.assertEqual(terrain_diff.changed_point_normals.changed_count, 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_nodes"), 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_polygon_list"), 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_render_payload"), 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_render_compact_nodes"), 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_render_bsp_nodes"), 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_render_bsp_polygon_list"), 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_render_unknown_payload"), 0)
        self.assertEqual(physics_diff.byte_diff_count, 0)
        self.assertEqual(physics_diff.moved_points.changed_count, 0)

        text = bsp_record_inspector.format_diff_report(report)
        self.assertIn("unknown/structural=0", text)
        self.assertIn("moved points: 1/", text)

    def test_diff_flags_generic_recomputed_terrain_derivatives(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        if terrain is None:
            self.skipTest("BOOTCAMP is missing Terrain0")

        edited = copy.deepcopy(terrain)
        edited.points[81] = (
            terrain.points[81][0] + 1.0,
            terrain.points[81][1],
            terrain.points[81][2],
        )
        raw = world.raw_model_bytes(data, terrain)
        patched = terrain_bsp_patch.patch_model_record(raw, terrain, edited)
        changed = bytearray(data)
        changed[terrain.raw_start:terrain.raw_end] = patched

        report = bsp_record_inspector.diff_dat_records(data, bytes(changed), model_names=("Terrain0",))
        terrain_diff = report.model_diffs[0]

        self.assertTrue(terrain_diff.comparable)
        self.assertEqual(terrain_diff.moved_points.changed_count, 1)
        self.assertGreater(terrain_diff.changed_planes.changed_count, 0)
        self.assertGreater(terrain_diff.changed_polygon_centers.changed_count, 0)
        self.assertGreater(terrain_diff.changed_point_normals.changed_count, 0)

    def test_experimental_terrain_section_bounds_patch_updates_render_headers(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        if terrain is None:
            self.skipTest("BOOTCAMP is missing Terrain0")

        raw = world.raw_model_bytes(data, terrain)
        layout = bsp_record_inspector._decode_world_bsp_layout(raw, terrain)
        node_range = layout.section_ranges["terrain_tail_nodes"]
        polygon_indices = terrain_bsp_patch._terrain_section_node_polygon_indices(
            raw,
            node_range,
            includes_count=True,
        )
        if not polygon_indices:
            self.skipTest("BOOTCAMP Terrain0 has no decoded terrain-tail polygons")

        edited = copy.deepcopy(terrain)
        vertex_index = terrain.polygons[polygon_indices[0]].vertex_indices[0]
        section_min_y = min(
            terrain.points[index][1]
            for polygon_index in polygon_indices
            for index in terrain.polygons[polygon_index].vertex_indices
        )
        edited.points[vertex_index] = (
            terrain.points[vertex_index][0],
            section_min_y - 16.0,
            terrain.points[vertex_index][2],
        )

        patched = terrain_bsp_patch.patch_terrain_model_points_and_section_bounds(raw, terrain, edited)
        self.assertEqual(len(patched), len(raw))

        changed = bytearray(data)
        changed[terrain.raw_start:terrain.raw_end] = patched
        report = bsp_record_inspector.diff_dat_records(data, bytes(changed), model_names=("Terrain0",))
        terrain_diff = report.model_diffs[0]

        self.assertTrue(terrain_diff.comparable)
        self.assertEqual(terrain_diff.moved_points.changed_count, 1)
        self.assertEqual(terrain_diff.changed_planes.changed_count, 0)
        self.assertGreater(terrain_diff.changed_polygon_centers.changed_count, 0)
        self.assertGreater(terrain_diff.changed_point_normals.changed_count, 0)
        self.assertGreater(terrain_diff.section_changed_bytes.get("terrain_tail_render_header", 0), 0)
        self.assertGreater(terrain_diff.section_changed_bytes.get("terrain_tail_render_chunks", 0), 0)
        self.assertEqual(terrain_diff.section_changed_bytes.get("terrain_tail_render_unknown_payload"), 0)
        self.assertGreater(terrain_diff.terrain_render_header_changed_bytes, 0)
        self.assertGreater(
            terrain_diff.terrain_render_header_changed_sections.get("terrain_tail_render_header", 0),
            0,
        )
        self.assertTrue(terrain_diff.changed.terrain_tail_render_chunk_chain_valid)
        self.assertEqual(terrain_diff.unknown_structural_changed_bytes, 0)
        text = bsp_record_inspector.format_diff_report(report)
        self.assertIn("changed render header bytes:", text)
        self.assertIn("terrain_tail_render_header=", text)

    def test_experimental_terrain_section_bounds_expand_root_and_chunk_headers(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        if terrain is None:
            self.skipTest("BOOTCAMP is missing Terrain0")

        raw = world.raw_model_bytes(data, terrain)
        layout = bsp_record_inspector._decode_world_bsp_layout(raw, terrain)
        pairs = terrain_bsp_patch._terrain_render_header_node_ranges(layout)
        targets = []
        root = next((pair for pair in pairs if pair[0] == "terrain_tail_render_header"), None)
        child = next((pair for pair in pairs if pair[0].startswith("terrain_tail_render_chunk_")), None)
        if root:
            targets.append(root)
        if child:
            targets.append(child)
        if len(targets) < 2:
            self.skipTest("BOOTCAMP Terrain0 did not expose both root and chunk render headers")

        for header_name, node_name in targets:
            with self.subTest(header_name=header_name):
                header_range = layout.section_ranges[header_name]
                node_range = layout.section_ranges[node_name]
                polygon_indices = terrain_bsp_patch._terrain_section_node_polygon_indices(
                    raw,
                    node_range,
                    includes_count=(node_name == "terrain_tail_nodes"),
                )
                polygon_indices = [
                    index for index in polygon_indices
                    if 0 <= index < len(terrain.polygons)
                ]
                if not polygon_indices:
                    self.skipTest(f"{header_name} has no decoded polygon indices")

                section_points = [
                    terrain.points[vertex_index]
                    for polygon_index in polygon_indices
                    for vertex_index in terrain.polygons[polygon_index].vertex_indices
                    if 0 <= vertex_index < len(terrain.points)
                ]
                if not section_points:
                    self.skipTest(f"{header_name} has no decoded section points")
                old_size = struct.unpack_from("<3f", raw, header_range[0] + 12)
                old_min = struct.unpack_from("<3f", raw, header_range[0] + 24)
                section_min_y = min(point[1] for point in section_points)

                edited = copy.deepcopy(terrain)
                vertex_index = terrain.polygons[polygon_indices[0]].vertex_indices[0]
                new_y = float(section_min_y) - 16.0
                edited.points[vertex_index] = (
                    terrain.points[vertex_index][0],
                    new_y,
                    terrain.points[vertex_index][2],
                )

                patched = terrain_bsp_patch.patch_terrain_model_points_and_section_bounds(raw, terrain, edited)
                new_size = struct.unpack_from("<3f", patched, header_range[0] + 12)
                new_min = struct.unpack_from("<3f", patched, header_range[0] + 24)

                self.assertLess(new_min[1], old_min[1])
                self.assertAlmostEqual(new_min[1], new_y, places=3)
                self.assertGreater(new_size[1], old_size[1])

    def test_terrain_render_classification_audit_flags_split_crossing(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        if terrain is None:
            self.skipTest("BOOTCAMP is missing Terrain0")

        raw = world.raw_model_bytes(data, terrain)
        no_op = terrain_bsp_patch.audit_terrain_render_classification(raw, terrain, copy.deepcopy(terrain))
        self.assertGreater(no_op.checked_node_table_count, 0)
        self.assertGreater(no_op.checked_polygon_reference_count, 0)
        self.assertEqual(no_op.changed_center_reference_count, 0)
        self.assertEqual(no_op.changed_vertex_reference_count, 0)

        edited = self._classification_crossing_edit(raw, terrain)
        crossing = terrain_bsp_patch.audit_terrain_render_classification(raw, terrain, edited)

        self.assertGreater(crossing.changed_center_reference_count, 0)
        self.assertGreater(crossing.changed_vertex_reference_count, 0)
        self.assertGreater(crossing.max_center_distance_delta, 0.0)
        self.assertGreater(crossing.max_vertex_distance_delta, 0.0)
        self.assertTrue(crossing.examples)

    def test_point_only_terrain_edit_blocks_dirty_render_classification(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        if terrain is None:
            self.skipTest("BOOTCAMP is missing Terrain0")

        raw = world.raw_model_bytes(data, terrain)
        edited = self._classification_crossing_edit(raw, terrain)
        plan = terrain_bsp_patch.VertexEditPlan(
            source_path="dirty_render_source",
            metadata_path="dirty_render_metadata",
            models=[
                terrain_bsp_patch.VertexEditedModel(
                    name="Terrain0",
                    source_model=terrain,
                    edited_model=edited,
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "decoded render BSP classification changed"):
            terrain_bsp_patch.validate_terrain_vertex_edit_safety(
                [plan],
                source_bsp=world,
                source_dat=data,
            )

        risk = terrain_bsp_patch.audit_terrain_derived_data_risks(
            data,
            world,
            [plan],
        )[0]
        self.assertTrue(risk.render_topology_rebuild_required)
        self.assertEqual(risk.visibility_culling_risk, "blocked")
        self.assertTrue(risk.blockers)

    def test_experimental_render_topology_rebuild_blocks_new_split_spans_without_source_occurrences(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        if terrain is None:
            self.skipTest("BOOTCAMP is missing Terrain0")

        raw = world.raw_model_bytes(data, terrain)
        edited = self._classification_crossing_edit(raw, terrain)
        dirty = terrain_bsp_patch.audit_terrain_render_classification(raw, terrain, edited)
        self.assertGreater(dirty.changed_center_reference_count, 0)
        self.assertGreater(dirty.changed_vertex_reference_count, 0)
        errors = terrain_bsp_patch._terrain_render_topology_rebuild_errors(raw, terrain, edited)
        self.assertTrue(any("newly spans a render split" in error for error in errors))

        with self.assertRaisesRegex(ValueError, "newly spans a render split"):
            terrain_bsp_patch.patch_terrain_model_points_section_bounds_and_render_topology(
                raw,
                terrain,
                edited,
            )

    def test_repeated_polygon_rebuild_reuses_existing_split_side_occurrences(self):
        source = self._synthetic_split_span_model(source_spans=True)
        edited = copy.deepcopy(source)
        nodes = [
            (0, 1, 2),
            (1, -2, -1),
            (1, -2, -1),
        ]

        rebuilt = terrain_bsp_patch._rebuild_terrain_node_table(nodes, source, edited)

        self.assertEqual(rebuilt[0], (0, 1, 2))
        self.assertEqual(rebuilt[1][0], 1)
        self.assertEqual(rebuilt[2][0], 1)

    def test_repeated_polygon_rebuild_blocks_new_span_without_existing_occurrence(self):
        source = self._synthetic_split_span_model(source_spans=False)
        edited = copy.deepcopy(source)
        edited.points[4] = (1.0, 1.0, 0.0)
        nodes = [
            (0, 1, -1),
            (1, -2, -1),
        ]

        errors = terrain_bsp_patch._terrain_node_table_rebuild_errors(
            "synthetic_nodes",
            nodes,
            source,
            edited,
        )

        self.assertTrue(any("newly spans a render split" in error for error in errors))

    def test_shipped_terrain_render_split_spanning_polygons_are_repeated(self):
        _path, data, world = self.load_bootcamp()
        terrain = world.model_by_name("Terrain0")
        if terrain is None:
            self.skipTest("BOOTCAMP is missing Terrain0")

        raw = world.raw_model_bytes(data, terrain)
        audit = terrain_bsp_patch.audit_terrain_render_split_spans(raw, terrain)

        self.assertEqual(audit.checked_node_table_count, 20)
        self.assertEqual(audit.checked_node_count, 9245)
        self.assertEqual(audit.checked_polygon_reference_count, 131207)
        self.assertEqual(audit.spanning_reference_count, 13317)
        self.assertEqual(audit.spanning_polygon_count, 2122)
        self.assertEqual(audit.touching_reference_count, 57209)
        self.assertEqual(audit.touching_polygon_count, 3923)
        self.assertEqual(audit.duplicate_node_reference_count, 4623)
        self.assertEqual(audit.repeated_node_polygon_count, 2188)
        self.assertEqual(audit.repeated_spanning_polygon_count, audit.spanning_polygon_count)
        self.assertGreater(audit.repeated_touching_polygon_count, 0)
        self.assertTrue(audit.examples)
        self.assertEqual(
            terrain_bsp_patch._terrain_render_topology_rebuild_errors(raw, terrain, copy.deepcopy(terrain)),
            [],
        )

    def _classification_crossing_edit(self, raw, terrain):
        layout = bsp_record_inspector._decode_world_bsp_layout(raw, terrain)
        for _name, node_range, includes_count in terrain_bsp_patch._terrain_render_bsp_node_table_ranges(layout):
            nodes = terrain_bsp_patch._terrain_section_nodes(raw, node_range, includes_count=includes_count)
            descendants = {}
            for splitter_polygon_index, side0, side1 in nodes:
                normal, distance = terrain_bsp_patch.plane_for_polygon(
                    terrain.points,
                    terrain.polygons[splitter_polygon_index],
                )
                for child_index in (side0, side1):
                    if child_index < 0:
                        continue
                    for polygon_index in terrain_bsp_patch._terrain_node_descendant_polygons(nodes, child_index, descendants):
                        center = terrain_bsp_patch.polygon_center(terrain.points, terrain.polygons[polygon_index])
                        signed_distance = terrain_reconstruction.vec3_dot(normal, center) - distance
                        if abs(signed_distance) < 16.0:
                            continue
                        edited = copy.deepcopy(terrain)
                        delta = -2.0 * signed_distance
                        for vertex_index in terrain.polygons[polygon_index].vertex_indices:
                            point = terrain.points[vertex_index]
                            edited.points[vertex_index] = (
                                point[0] + normal[0] * delta,
                                point[1] + normal[1] * delta,
                                point[2] + normal[2] * delta,
                            )
                        return edited
        raise AssertionError("could not find a BOOTCAMP Terrain0 polygon to move across a render split")

    def _synthetic_split_span_model(self, *, source_spans):
        points = [
            (0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (-1.0, 0.0, 0.0),
            (1.0 if source_spans else -1.0, 1.0, 0.0),
            (-1.0, 0.0, 1.0),
        ]
        return bsp.WorldModelMesh(
            "Terrain0",
            (-1.0, 0.0, 0.0),
            (1.0, 1.0, 1.0),
            (0.0, 0.0, 0.0),
            points=points,
            polygons=[
                bsp.Polygon([0, 1, 2], 0, 0),
                bsp.Polygon([3, 4, 5], 0, 0),
            ],
        )




if __name__ == "__main__":
    unittest.main()
