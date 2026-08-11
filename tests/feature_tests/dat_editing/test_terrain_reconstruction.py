import inspect
import os
import types
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import compiler_strategy, surrogate_ed, terrain_reconstruction


class TerrainReconstructionTests(unittest.TestCase):
    def test_surrogate_and_compiler_use_public_terrain_reconstruction_helpers(self):
        self.assertFalse(hasattr(surrogate_ed, "_canonical_terrain_polygon_indices"))
        self.assertFalse(hasattr(surrogate_ed, "_simplify_collinear_terrain_polygon_indices"))
        self.assertIn("terrain_reconstruction", inspect.getsource(compiler_strategy))
        self.assertIn("terrain_reconstruction", inspect.getsource(surrogate_ed))

    def test_canonical_indices_prefers_unique_trailing_boundary_loop(self):
        self.assertEqual(
            terrain_reconstruction.canonical_terrain_polygon_indices(
                (10, 11, 12, 13, 10, 11, 12, 13)
            ),
            (10, 11, 12, 13),
        )

    def test_canonical_indices_keeps_last_occurrence_when_no_unique_suffix_exists(self):
        self.assertEqual(
            terrain_reconstruction.canonical_terrain_polygon_indices((1, 2, 1, 3)),
            (2, 1, 3),
        )

    def test_simplify_collinear_indices_removes_duplicate_and_straight_boundary_points(self):
        points = (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        )

        self.assertEqual(
            terrain_reconstruction.simplify_collinear_terrain_polygon_indices(
                (0, 1, 2, 3, 4, 5),
                points,
            ),
            (0, 2, 3, 5),
        )

    def test_normalizes_terrain_support_modes(self):
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_selection_mode("component-radius"),
            "connected_radius",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_selection_mode("footprint"),
            "bounds",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_selection_mode(
                "anchor-neighborhood-budget"
            ),
            "playable_anchor_budget",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_selection_mode(
                "playable-region-allocation"
            ),
            "playable_area_budget",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_selection_mode(
                "adaptive-region-allocation"
            ),
            "adaptive_playable_area_budget",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_brush_mode("triangle-ngons"),
            "triangulated_ngons",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_brush_mode("cell-prisms"),
            "paired_triangles",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_brush_mode("single"),
            "single_polygon",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_brush_mode(
                "continuous-regions"
            ),
            "adjacent_convex",
        )
        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_brush_mode(
                "terrain-compression"
            ),
            "adaptive_structural",
        )

    def test_physics_bsp_terrain_oracle_indexes_height_without_emitting_geometry(self):
        physics_model = types.SimpleNamespace(
            points=(
                (0.0, 12.0, 0.0),
                (0.0, 12.0, 10.0),
                (10.0, 12.0, 10.0),
                (10.0, 12.0, 0.0),
            ),
            polygons=(
                types.SimpleNamespace(
                    vertex_indices=(0, 1, 2, 3),
                ),
            ),
        )

        oracle = terrain_reconstruction.build_terrain_collision_oracle(
            physics_model,
            cell_size=64.0,
        )

        self.assertEqual(oracle.triangle_count, 2)
        self.assertAlmostEqual(
            terrain_reconstruction.terrain_collision_oracle_floor_y(
                oracle,
                5.0,
                5.0,
                source_y=15.0,
                max_vertical_distance=8.0,
            ),
            12.0,
        )
        self.assertIsNone(
            terrain_reconstruction.terrain_collision_oracle_floor_y(
                oracle,
                20.0,
                20.0,
                source_y=15.0,
                max_vertical_distance=8.0,
            )
        )
        self.assertFalse(hasattr(oracle, "brushes"))

    def test_classifies_walkable_upward_facing_vertices(self):
        normal = terrain_reconstruction.polygon_normal((
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 10.0),
            (10.0, 0.0, 10.0),
            (10.0, 0.0, 0.0),
        ))

        terrain = types.SimpleNamespace(
            points=(
                (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0),
                (10.0, 0.0, 10.0),
                (0.0, 0.0, 10.0),
                (0.0, 10.0, 0.0),
                (10.0, 10.0, 0.0),
            ),
            polygons=(
                types.SimpleNamespace(vertex_indices=(0, 3, 2, 1)),
                types.SimpleNamespace(vertex_indices=(0, 1, 5, 4)),
            ),
        )

        self.assertAlmostEqual(normal[1], 1.0)
        self.assertEqual(terrain_reconstruction.walkable_vertex_indices(terrain), {0, 1, 2, 3})

    def test_samples_and_hits_xz_polygons(self):
        square = (
            (0.0, 0.0),
            (4.0, 0.0),
            (4.0, 4.0),
            (0.0, 4.0),
        )

        self.assertTrue(terrain_reconstruction.point_in_xz_polygon(2.0, 2.0, square))
        self.assertTrue(terrain_reconstruction.point_in_xz_polygon(0.0, 2.0, square))
        self.assertFalse(terrain_reconstruction.point_in_xz_polygon(5.0, 2.0, square))
        self.assertEqual(terrain_reconstruction.xz_polygon_bounds(square), (0.0, 4.0, 0.0, 4.0))
        self.assertEqual(
            terrain_reconstruction.xz_polygon_interior_sample_points(square, 2),
            ((1.0, 1.0), (3.0, 1.0), (1.0, 3.0), (3.0, 3.0)),
        )
        self.assertEqual(
            terrain_reconstruction.xz_rect_sample_points(0.0, 4.0, 10.0, 14.0, 2),
            ((1.0, 11.0), (3.0, 11.0), (1.0, 13.0), (3.0, 13.0)),
        )

    def test_vec3_bounds_overshoot_and_classification_helpers(self):
        points = (
            (-1.0, 2.0, 4.0),
            (3.0, -2.0, 10.0),
            (2.0, 5.0, -4.0),
        )

        self.assertEqual(
            terrain_reconstruction.vec3_bounds(points),
            ((-1.0, -2.0, -4.0), (3.0, 5.0, 10.0)),
        )
        self.assertEqual(
            terrain_reconstruction.expanded_vec3_bounds(
                (0.0, 0.0, 0.0),
                (2.0, 2.0, 2.0),
                points,
            ),
            ((-1.0, -2.0, -4.0), (3.0, 5.0, 10.0)),
        )
        self.assertAlmostEqual(
            terrain_reconstruction.vec3_distance((0.0, 0.0, 0.0), (3.0, 4.0, 12.0)),
            13.0,
        )
        self.assertEqual(
            terrain_reconstruction.vec3_dot((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)),
            32.0,
        )
        self.assertEqual(
            terrain_reconstruction.point_box_overshoot(
                (4.0, -3.0, 1.0),
                (0.0, 0.0, 0.0),
                (2.0, 2.0, 2.0),
            ),
            3.0,
        )
        self.assertEqual(
            terrain_reconstruction.bounds_box_overshoot(
                (-2.0, 0.0, 0.0),
                (2.0, 6.0, 2.0),
                (0.0, 0.0, 0.0),
                (4.0, 4.0, 4.0),
            ),
            2.0,
        )
        self.assertEqual(terrain_reconstruction.classification_sign(0.6, 0.5), 1)
        self.assertEqual(terrain_reconstruction.classification_sign(-0.6, 0.5), -1)
        self.assertEqual(terrain_reconstruction.classification_sign(0.4, 0.5), 0)
        self.assertEqual(
            terrain_reconstruction.points_classification_signs(
                ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (-2.0, 0.0, 0.0)),
                (1.0, 0.0, 0.0),
                0.0,
                0.5,
            ),
            (0, 1, -1),
        )
        self.assertEqual(
            terrain_reconstruction.edited_points_classification_signs(
                ((0.0, 0.0, 0.0),),
                ((2.0, 0.0, 0.0),),
                (1.0, 0.0, 0.0),
                0.0,
                0.5,
            ),
            ((1,), 2.0),
        )

    def test_polygon_area_and_plane_helpers(self):
        square = (
            (0.0, 2.0, 0.0),
            (0.0, 2.0, 4.0),
            (4.0, 2.0, 4.0),
            (4.0, 2.0, 0.0),
        )
        line_like = (
            (0.0, 5.0, 0.0),
            (1.0, 5.0, 0.0),
            (2.0, 5.0, 0.0),
        )

        normal, distance = terrain_reconstruction.polygon_plane(square, (0, 1, 2, 3))
        fallback_normal, fallback_distance = terrain_reconstruction.polygon_plane(line_like, (0, 1, 2))

        self.assertAlmostEqual(terrain_reconstruction.polygon_area(square), 16.0)
        self.assertEqual(normal, (0.0, 1.0, 0.0))
        self.assertEqual(distance, 2.0)
        self.assertEqual(fallback_normal, (0.0, 1.0, 0.0))
        self.assertEqual(fallback_distance, 5.0)

    def test_physics_shell_roles_classify_orientation_and_helpers(self):
        model = types.SimpleNamespace(
            points=(
                (0.0, 2.0, 0.0),
                (0.0, 2.0, 4.0),
                (4.0, 2.0, 4.0),
                (4.0, 2.0, 0.0),
                (0.0, 0.0, 0.0),
                (0.0, 4.0, 0.0),
                (0.0, 4.0, 4.0),
                (0.0, 0.0, 4.0),
            ),
            polygons=(
                types.SimpleNamespace(vertex_indices=(0, 1, 2, 3), surface_index=0),
                types.SimpleNamespace(vertex_indices=(3, 2, 1, 0), surface_index=0),
                types.SimpleNamespace(vertex_indices=(4, 5, 6, 7), surface_index=0),
                types.SimpleNamespace(vertex_indices=(0, 1, 2, 3), surface_index=1),
                types.SimpleNamespace(vertex_indices=(0, 1), surface_index=0),
            ),
            surfaces=(
                types.SimpleNamespace(texture_index=0),
                types.SimpleNamespace(texture_index=1),
            ),
            texture_names=(
                "TEXTURES\\LevelTextures\\Stone\\wall.dtx",
                "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx",
            ),
        )

        roles = terrain_reconstruction.physics_shell_source_polygon_roles(model)
        candidates = terrain_reconstruction.physics_shell_candidates(model)

        self.assertEqual(
            roles,
            {
                0: "floor",
                1: "ceiling",
                2: "side_wall",
                3: "helper/special",
                4: "degenerate",
            },
        )
        self.assertEqual([candidate.role for candidate in candidates], ["floor", "ceiling", "side_wall", "helper/special"])
        self.assertEqual([candidate.generated_face_count for candidate in candidates], [6, 6, 6, 6])

        batches = terrain_reconstruction.physics_shell_role_index_batches(
            model,
            max_indices_per_batch=2,
        )
        self.assertEqual(
            [(batch.role, batch.batch_index, batch.polygon_indices) for batch in batches],
            [
                ("floor", 0, (0,)),
                ("ceiling", 0, (1,)),
                ("side_wall", 0, (2,)),
                ("helper/special", 0, (3,)),
            ],
        )
        self.assertEqual([batch.generated_face_count for batch in batches], [6, 6, 6, 6])
        face_limited = terrain_reconstruction.physics_shell_role_index_batches(
            model,
            max_indices_per_batch=32,
            max_generated_faces_per_batch=6,
        )
        self.assertTrue(face_limited)
        self.assertTrue(all(batch.generated_face_count <= 6 for batch in face_limited))
        with self.assertRaises(ValueError):
            terrain_reconstruction.physics_shell_role_index_batches(model, max_indices_per_batch=0)

    def test_physics_shell_quality_filter_simplifies_or_rejects_warning_prone_slabs(self):
        model = types.SimpleNamespace(
            points=(
                (0.0, 2.0, 0.0),
                (4.0, 2.0, 0.0),
                (4.01, 2.0, 0.0),
                (4.0, 2.0, 4.0),
                (0.0, 2.0, 4.0),
                (10.0, 0.0, 0.0),
                (10.1, 0.0, 0.0),
                (10.0, 0.0, 0.1),
                (20.0, 0.0, 0.0),
                (24.0, 0.0, 0.0),
                (24.0, 0.0, 4.0),
                (20.0, 1.0, 4.0),
            ),
            polygons=(
                types.SimpleNamespace(vertex_indices=(0, 1, 2, 3, 4), surface_index=0),
                types.SimpleNamespace(vertex_indices=(5, 6, 7), surface_index=0),
                types.SimpleNamespace(vertex_indices=(8, 9, 10, 11), surface_index=0),
            ),
            surfaces=(types.SimpleNamespace(texture_index=0),),
            texture_names=("TEXTURES\\LevelTextures\\Stone\\wall.dtx",),
        )

        roles = terrain_reconstruction.physics_shell_source_polygon_roles(model)
        candidates = terrain_reconstruction.physics_shell_candidates(model)

        self.assertEqual(roles[0], "ceiling")
        self.assertEqual(roles[1], "degenerate")
        self.assertEqual(roles[2], "degenerate")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].indices, (0, 1, 3, 4))
        self.assertEqual(candidates[0].generated_face_count, 6)
        self.assertFalse(
            terrain_reconstruction.physics_shell_slab_quality_ok(
                candidates[0].points,
                thickness=0.5,
            )
        )

    def test_balanced_physics_shell_selection_prioritizes_walls_before_helper_fill(self):
        def candidate(index: int, area: float, role: str) -> terrain_reconstruction.PhysicsShellCandidate:
            return terrain_reconstruction.PhysicsShellCandidate(
                polygon_index=index,
                polygon=types.SimpleNamespace(vertex_indices=(0, 1, 2)),
                indices=(0, 1, 2),
                points=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                area=area,
                role=role,
                generated_face_count=5,
            )

        candidates = (
            candidate(0, 1000.0, "helper/special"),
            candidate(1, 90.0, "side_wall"),
            candidate(2, 80.0, "side_wall"),
            candidate(3, 70.0, "floor"),
            candidate(4, 60.0, "ceiling"),
            candidate(5, 50.0, "floor"),
        )

        selected = terrain_reconstruction.balanced_physics_shell_candidates(candidates, 4)

        self.assertEqual([item.polygon_index for item in selected], [1, 2, 3, 4])
        self.assertEqual([item.role for item in selected], ["side_wall", "side_wall", "floor", "ceiling"])
        self.assertEqual(
            terrain_reconstruction.budgeted_balanced_physics_shell_source_polygon_count(
                candidates,
                requested_source_polygon_count=4,
                generated_polygon_budget=15,
            ),
            3,
        )

    def test_physics_shell_consolidation_merges_adjacent_coplanar_triangles(self):
        def candidate(index, indices, points):
            return terrain_reconstruction.PhysicsShellCandidate(
                polygon_index=index,
                polygon=types.SimpleNamespace(vertex_indices=indices),
                indices=indices,
                points=points,
                area=0.5,
                role="floor",
                generated_face_count=5,
            )

        candidates = (
            candidate(10, (0, 1, 2), ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0))),
            candidate(11, (0, 2, 3), ((0.0, 0.0, 0.0), (1.0, 0.0, 1.0), (0.0, 0.0, 1.0))),
        )

        groups = terrain_reconstruction.consolidated_physics_shell_candidate_groups(None, candidates)

        self.assertEqual(len(groups), 1)
        self.assertEqual([item.polygon_index for item in groups[0].candidates], [10, 11])
        self.assertEqual(len(groups[0].points), 4)
        self.assertEqual(groups[0].generated_face_count, 6)

    def test_physics_shell_consolidation_grows_a_convex_four_triangle_group(self):
        points = (
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
            (1.0, 0.0, 1.0),
        )
        triangles = ((0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4))
        candidates = tuple(
            terrain_reconstruction.PhysicsShellCandidate(
                polygon_index=index,
                polygon=types.SimpleNamespace(vertex_indices=indices),
                indices=indices,
                points=tuple(points[item] for item in indices),
                area=1.0,
                role="floor",
                generated_face_count=5,
            )
            for index, indices in enumerate(triangles)
        )

        groups = terrain_reconstruction.consolidated_physics_shell_candidate_groups(None, candidates)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].candidates), 4)
        self.assertEqual(len(groups[0].points), 4)
        self.assertEqual(groups[0].generated_face_count, 6)

    def test_cost_aware_consolidation_grows_an_exact_convex_eight_triangle_group(self):
        outer = (
            (3.0, 0.0, 0.0),
            (2.0, 0.0, 2.0),
            (0.0, 0.0, 3.0),
            (-2.0, 0.0, 2.0),
            (-3.0, 0.0, 0.0),
            (-2.0, 0.0, -2.0),
            (0.0, 0.0, -3.0),
            (2.0, 0.0, -2.0),
        )
        points = outer + ((0.0, 0.0, 0.0),)
        triangles = tuple(
            (index, (index + 1) % len(outer), len(outer))
            for index in range(len(outer))
        )
        candidates = tuple(
            terrain_reconstruction.PhysicsShellCandidate(
                polygon_index=index,
                polygon=types.SimpleNamespace(vertex_indices=indices),
                indices=indices,
                points=tuple(points[item] for item in indices),
                area=terrain_reconstruction.polygon_area(
                    tuple(points[item] for item in indices)
                ),
                role="floor",
                generated_face_count=5,
            )
            for index, indices in enumerate(triangles)
        )

        plan = terrain_reconstruction.build_physics_shell_packing_plan(
            None,
            candidates,
            source_polygon_limit=len(candidates),
        )

        self.assertEqual(plan.source_polygon_count, len(candidates))
        self.assertEqual(plan.generated_brush_count, 1)
        self.assertEqual(plan.generated_face_count, len(outer) + 2)
        self.assertEqual(len(plan.groups[0].points), len(outer))

    def test_cost_aware_consolidation_does_not_fill_a_large_concave_gap(self):
        # A fan triangulates a concave pentagon.  The convex hull would fill
        # the inward notch, so only safe groups up to four source triangles
        # may be consolidated.
        outer = (
            (0.0, 0.0, 0.0),
            (4.0, 0.0, 0.0),
            (4.0, 0.0, 4.0),
            (2.0, 0.0, 2.0),
            (0.0, 0.0, 4.0),
        )
        points = outer + ((2.0, 0.0, 1.0),)
        triangles = tuple(
            (index, (index + 1) % len(outer), len(outer))
            for index in range(len(outer))
        )
        candidates = tuple(
            terrain_reconstruction.PhysicsShellCandidate(
                polygon_index=index + 30,
                polygon=types.SimpleNamespace(vertex_indices=indices),
                indices=indices,
                points=tuple(points[item] for item in indices),
                area=terrain_reconstruction.polygon_area(
                    tuple(points[item] for item in indices)
                ),
                role="floor",
                generated_face_count=5,
            )
            for index, indices in enumerate(triangles)
        )

        plan = terrain_reconstruction.build_physics_shell_packing_plan(
            None,
            candidates,
            source_polygon_limit=5,
        )

        self.assertEqual(plan.source_polygon_count, 5)
        self.assertGreater(plan.generated_brush_count, 1)
        self.assertLessEqual(max(len(group.candidates) for group in plan.groups), 4)

    def test_physics_shell_consolidation_rejects_concave_pair(self):
        first = terrain_reconstruction.PhysicsShellCandidate(
            10, types.SimpleNamespace(vertex_indices=(0, 1, 2)), (0, 1, 2),
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            1.0, "floor", 5,
        )
        second = terrain_reconstruction.PhysicsShellCandidate(
            11, types.SimpleNamespace(vertex_indices=(0, 1, 3)), (0, 1, 3),
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (3.0, 0.0, 3.0)),
            1.5, "floor", 5,
        )

        groups = terrain_reconstruction.consolidated_physics_shell_candidate_groups(None, (first, second))

        self.assertEqual(len(groups), 2)

    def test_physics_shell_packing_plan_reuses_regions_and_respects_face_budget(self):
        candidates = (
            terrain_reconstruction.PhysicsShellCandidate(
                10,
                types.SimpleNamespace(vertex_indices=(0, 1, 2)),
                (0, 1, 2),
                ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, 2.0)),
                2.0,
                "floor",
                5,
            ),
            terrain_reconstruction.PhysicsShellCandidate(
                11,
                types.SimpleNamespace(vertex_indices=(0, 2, 3)),
                (0, 2, 3),
                ((0.0, 0.0, 0.0), (2.0, 0.0, 2.0), (0.0, 0.0, 2.0)),
                2.0,
                "floor",
                5,
            ),
            terrain_reconstruction.PhysicsShellCandidate(
                20,
                types.SimpleNamespace(vertex_indices=(4, 5, 6)),
                (4, 5, 6),
                ((10.0, 0.0, 0.0), (10.0, 20.0, 0.0), (10.0, 0.0, 20.0)),
                200.0,
                "side_wall",
                5,
            ),
        )

        index = terrain_reconstruction.build_physics_shell_consolidation_index(
            None,
            candidates,
        )
        self.assertEqual(
            terrain_reconstruction.consolidated_physics_shell_candidate_groups(
                None,
                candidates,
            ),
            terrain_reconstruction.consolidated_physics_shell_candidate_groups(
                None,
                candidates,
                consolidation_index=index,
            ),
        )

        tight = terrain_reconstruction.build_physics_shell_packing_plan(
            None,
            candidates,
            source_polygon_limit=3,
            generated_face_budget=6,
            consolidation_index=index,
        )
        self.assertEqual(tight.source_polygon_count, 1)
        self.assertEqual(tight.generated_brush_count, 1)
        self.assertEqual(tight.generated_face_count, 5)
        self.assertEqual(tight.role_counts, (("side_wall", 1),))
        self.assertEqual(tight.groups[0].candidates[0].polygon_index, 20)

        roomy = terrain_reconstruction.build_physics_shell_packing_plan(
            None,
            candidates,
            source_polygon_limit=3,
            generated_face_budget=11,
            consolidation_index=index,
        )
        self.assertEqual(roomy.source_polygon_count, 3)
        self.assertEqual(roomy.generated_brush_count, 2)
        self.assertEqual(roomy.generated_face_count, 11)
        self.assertEqual(dict(roomy.role_counts), {"floor": 2, "side_wall": 1})

        protected = terrain_reconstruction.build_physics_shell_packing_plan(
            None,
            candidates,
            source_polygon_limit=3,
            protected_bounds=(((9.0, -1.0, -1.0), (11.0, 21.0, 21.0)),),
            consolidation_index=index,
        )
        self.assertEqual(protected.source_polygon_count, 2)
        self.assertEqual(protected.protected_polygon_indices, (20,))

    def test_cost_aware_packing_accepts_role_and_playable_importance_overrides(self):
        def candidate(index, role, center):
            x, z = center
            points = (
                (x, 0.0, z),
                (x + 2.0, 0.0, z),
                (x, 0.0, z + 2.0),
            )
            indices = (index * 3, index * 3 + 1, index * 3 + 2)
            return terrain_reconstruction.PhysicsShellCandidate(
                index,
                types.SimpleNamespace(vertex_indices=indices),
                indices,
                points,
                2.0,
                role,
                5,
            )

        candidates = (
            candidate(1, "side_wall", (0.0, 0.0)),
            candidate(2, "floor", (20.0, 20.0)),
            candidate(3, "floor", (100.0, 100.0)),
        )
        role_override = terrain_reconstruction.build_physics_shell_packing_plan(
            None,
            candidates,
            source_polygon_limit=1,
            generated_face_budget=5,
            role_weights={"side_wall": 1.0, "floor": 20.0},
        )
        self.assertEqual(role_override.groups[0].candidates[0].polygon_index, 2)

        playable_override = terrain_reconstruction.build_physics_shell_packing_plan(
            None,
            candidates[1:],
            source_polygon_limit=1,
            generated_face_budget=5,
            playable_importance_points=((20.0, 0.0, 20.0),),
            playable_importance_radius=16.0,
            playable_importance_weight=10.0,
        )
        self.assertEqual(playable_override.groups[0].candidates[0].polygon_index, 2)
        self.assertEqual(
            dict(terrain_reconstruction.normalized_physics_shell_role_weights({"floor": 20.0}))["floor"],
            20.0,
        )

    def test_packing_comparison_uses_common_budgets_and_reports_value_gain(self):
        def candidate(index, role, x, area):
            points = ((x, 0.0, 0.0), (x + 2.0, 0.0, 0.0), (x, 0.0, 2.0))
            indices = (index * 3, index * 3 + 1, index * 3 + 2)
            return terrain_reconstruction.PhysicsShellCandidate(
                index,
                types.SimpleNamespace(vertex_indices=indices),
                indices,
                points,
                area,
                role,
                5,
            )

        candidates = (
            candidate(1, "side_wall", 0.0, 20.0),
            candidate(2, "floor", 20.0, 10.0),
            candidate(3, "floor", 40.0, 9.0),
        )
        comparison = terrain_reconstruction.compare_physics_shell_packing_plans(
            None,
            candidates,
            source_polygon_limit=1,
            generated_face_budget=5,
            role_weights={"side_wall": 1.0, "floor": 10.0},
        )

        self.assertEqual(comparison.balanced.source_polygon_count, 1)
        self.assertEqual(comparison.cost_aware.source_polygon_count, 1)
        self.assertLessEqual(comparison.balanced.generated_face_count, 5)
        self.assertLessEqual(comparison.cost_aware.generated_face_count, 5)
        self.assertEqual(
            comparison.balanced.groups[0].candidates[0].polygon_index,
            1,
        )
        self.assertEqual(
            comparison.cost_aware.groups[0].candidates[0].polygon_index,
            2,
        )
        self.assertGreater(comparison.weighted_value_delta, 0.0)
        self.assertEqual(comparison.preferred_validation_mode, "cost_aware")
        self.assertTrue(comparison.protected_sets_match)

    def test_balanced_physics_shell_selection_prefers_connected_structural_neighborhood(self):
        def candidate(
            index: int,
            area: float,
            role: str,
            indices: tuple[int, int, int],
        ) -> terrain_reconstruction.PhysicsShellCandidate:
            return terrain_reconstruction.PhysicsShellCandidate(
                polygon_index=index,
                polygon=types.SimpleNamespace(vertex_indices=indices),
                indices=indices,
                points=tuple((float(value), 0.0, float(value)) for value in indices),
                area=area,
                role=role,
                generated_face_count=5,
            )

        local_room = (
            candidate(10, 90.0, "side_wall", (0, 1, 2)),
            candidate(11, 20.0, "floor", (2, 3, 0)),
            candidate(12, 19.0, "ceiling", (3, 4, 2)),
        )
        distant_support = (
            candidate(20, 1000.0, "floor", (100, 101, 102)),
            candidate(21, 900.0, "ceiling", (102, 103, 100)),
            candidate(22, 850.0, "helper/special", (100, 103, 104)),
        )

        selected = terrain_reconstruction.balanced_physics_shell_candidates(
            local_room + distant_support,
            3,
        )

        self.assertEqual([item.polygon_index for item in selected], [10, 11, 12])
        self.assertEqual([item.role for item in selected], ["side_wall", "floor", "ceiling"])

    def test_focused_physics_shell_selection_keeps_connected_local_faces(self):
        def candidate(
            index: int,
            role: str,
            vertex_indices: tuple[int, int, int],
            center_x: float,
        ) -> terrain_reconstruction.PhysicsShellCandidate:
            return terrain_reconstruction.PhysicsShellCandidate(
                polygon_index=index,
                polygon=types.SimpleNamespace(vertex_indices=vertex_indices),
                indices=vertex_indices,
                points=(
                    (center_x - 1.0, 0.0, 0.0),
                    (center_x, 0.0, 1.0),
                    (center_x + 1.0, 0.0, 0.0),
                ),
                area=10.0,
                role=role,
                generated_face_count=5,
            )

        connected_room = (
            candidate(10, "side_wall", (0, 1, 2), 0.0),
            candidate(11, "floor", (2, 3, 0), 50.0),
            candidate(12, "ceiling", (3, 4, 2), 80.0),
        )
        disconnected_nearby = candidate(20, "side_wall", (100, 101, 102), 40.0)
        distant = candidate(30, "side_wall", (200, 201, 202), 500.0)

        selection = terrain_reconstruction.focused_balanced_physics_shell_candidates(
            connected_room + (disconnected_nearby, distant),
            3,
            focus_points=((0.0, 0.0, 0.0),),
            focus_radius=100.0,
            focus_budget=3,
        )

        self.assertEqual(selection.anchor_candidate_count, 1)
        self.assertEqual(selection.focus_component_count, 1)
        self.assertEqual(selection.focus_candidate_count, 3)
        self.assertEqual(selection.focus_selected_count, 3)
        self.assertEqual([item.polygon_index for item in selection.selected], [10, 11, 12])

    def test_detects_connected_tread_riser_stair_assembly(self):
        def tread(index, x, y):
            indices = (index * 3, index * 3 + 1, index * 3 + 2)
            return terrain_reconstruction.PhysicsShellCandidate(
                index,
                types.SimpleNamespace(vertex_indices=indices),
                indices,
                ((x, y, 0.0), (x + 4.0, y, 0.0), (x + 4.0, y, 8.0), (x, y, 8.0)),
                32.0,
                "floor",
                6,
            )

        def riser(index, x, low_y, high_y):
            indices = (index * 3, index * 3 + 1, index * 3 + 2)
            return terrain_reconstruction.PhysicsShellCandidate(
                index,
                types.SimpleNamespace(vertex_indices=indices),
                indices,
                ((x, low_y, 0.0), (x, high_y, 0.0), (x, high_y, 8.0), (x, low_y, 8.0)),
                64.0,
                "side_wall",
                6,
            )

        candidates = (
            tread(1, 0.0, 0.0),
            tread(2, 4.0, 8.0),
            tread(3, 8.0, 16.0),
            tread(4, 12.0, 24.0),
            riser(10, 4.0, 0.0, 8.0),
            riser(11, 8.0, 8.0, 16.0),
            riser(12, 12.0, 16.0, 24.0),
        )

        assemblies = terrain_reconstruction.detect_physics_shell_stair_assemblies(
            None,
            candidates,
        )

        self.assertEqual(len(assemblies), 1)
        assembly = assemblies[0]
        self.assertEqual(assembly.tread_polygon_indices, (1, 2, 3, 4))
        self.assertEqual(assembly.riser_polygon_indices, (10, 11, 12))
        self.assertEqual(assembly.elevation_levels, (0.0, 8.0, 16.0, 24.0))
        self.assertEqual(assembly.step_count, 3)
        self.assertEqual(assembly.confidence, "high")
        diagnostics = tuple(
            compiler_strategy.PhysicsShellSourcePolygonDiagnostic(
                source_polygon_index=index,
                role=("floor" if index < 10 else "side_wall"),
                status="emitted_ed",
                reason="emitted_shell_brush_provenance",
                generated_brush_names=(f"PhysicsShell_{index}",),
                compiled_match_count=1,
            )
            for index in assembly.source_polygon_indices
        )
        report = compiler_strategy.PhysicsShellSourceCoverageReport(
            status="physics_shell_source_coverage_has_gaps",
            source_dat_path="source.dat",
            generated_ed_path="generated.ed",
            compiled_dat_path="compiled.dat",
            source_polygon_diagnostics=diagnostics,
            stair_assemblies=assemblies,
        )
        manifest = compiler_strategy._physics_shell_source_coverage_manifest(report)
        assembly_manifest = manifest["stair_assemblies"][0]
        self.assertTrue(assembly_manifest["emission_complete"])
        self.assertTrue(assembly_manifest["compiled_retention_complete"])
        self.assertEqual(
            assembly_manifest["compiled_retained_polygon_count"],
            len(assembly.source_polygon_indices),
        )
        self.assertIn(
            "stair assemblies: count=1",
            compiler_strategy.format_physics_shell_source_coverage_report(report),
        )

    def test_stair_detector_rejects_two_level_curb_and_large_rise(self):
        def floor(index, x, y):
            indices = (index * 3, index * 3 + 1, index * 3 + 2)
            return terrain_reconstruction.PhysicsShellCandidate(
                index,
                types.SimpleNamespace(vertex_indices=indices),
                indices,
                ((x, y, 0.0), (x + 4.0, y, 0.0), (x, y, 4.0)),
                8.0,
                "floor",
                5,
            )

        self.assertEqual(
            terrain_reconstruction.detect_physics_shell_stair_assemblies(
                None,
                (floor(1, 0.0, 0.0), floor(2, 4.0, 8.0)),
            ),
            (),
        )
        self.assertEqual(
            terrain_reconstruction.detect_physics_shell_stair_assemblies(
                None,
                (
                    floor(1, 0.0, 0.0),
                    floor(2, 4.0, 48.0),
                    floor(3, 8.0, 96.0),
                ),
            ),
            (),
        )

    def test_anskramkeep_startpoint_focus_reserves_connected_stairwell_neighborhood(self):
        from core import bsp
        from features.dat_editing import terrain_semantics

        path = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            parsed = bsp.parse(f.read())
        physics = terrain_semantics.model_by_name(parsed.world_models, "PhysicsBSP")
        candidates = terrain_reconstruction.physics_shell_candidates(physics)

        selection = terrain_reconstruction.focused_balanced_physics_shell_candidates(
            candidates,
            864,
            focus_points=((0.0, -104.0, 16.0),),
            focus_radius=512.0,
            focus_budget=512,
        )

        focused = selection.selected[:selection.focus_selected_count]
        self.assertEqual(selection.anchor_candidate_count, 16)
        self.assertEqual(selection.focus_component_count, 1)
        self.assertEqual(selection.focus_candidate_count, 350)
        self.assertEqual(selection.focus_selected_count, 350)
        self.assertEqual(
            {role: sum(item.role == role for item in focused)
             for role in ("floor", "ceiling", "side_wall")},
            {"floor": 52, "ceiling": 59, "side_wall": 239},
        )

    def test_anskramkeep_stair_detector_finds_high_confidence_assemblies(self):
        from core import bsp
        from features.dat_editing import terrain_semantics

        path = os.path.join(ROOT, "mm9_data", "WORLDS", "ANSKRAMKEEP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            parsed = bsp.parse(f.read())
        physics = terrain_semantics.model_by_name(parsed.world_models, "PhysicsBSP")
        candidates = terrain_reconstruction.physics_shell_candidates(physics)
        index = terrain_reconstruction.build_physics_shell_consolidation_index(
            physics,
            candidates,
        )

        assemblies = terrain_reconstruction.detect_physics_shell_stair_assemblies(
            physics,
            candidates,
            consolidation_index=index,
        )

        high_confidence = [item for item in assemblies if item.confidence == "high"]
        self.assertGreaterEqual(len(assemblies), 10)
        self.assertGreaterEqual(len(high_confidence), 4)
        self.assertTrue(any(item.step_count >= 12 for item in high_confidence))
        self.assertTrue(all(item.tread_polygon_indices for item in assemblies))

    def test_builds_terrain_coverage_items_with_texture_filtering(self):
        terrain = types.SimpleNamespace(
            points=(
                (0.0, 0.0, 0.0),
                (4.0, 0.0, 0.0),
                (4.0, 0.0, 4.0),
                (0.0, 0.0, 4.0),
                (10.0, 0.0, 0.0),
                (14.0, 0.0, 0.0),
                (14.0, 0.0, 4.0),
                (10.0, 0.0, 4.0),
            ),
            polygons=(
                types.SimpleNamespace(vertex_indices=(0, 3, 2, 1), surface_index=0),
                types.SimpleNamespace(vertex_indices=(4, 5, 6, 7), surface_index=1),
                types.SimpleNamespace(vertex_indices=(0, 1, 2), surface_index=99),
            ),
            surfaces=(
                types.SimpleNamespace(texture_index=0),
                types.SimpleNamespace(texture_index=1),
            ),
            texture_names=(
                "TEXTURES\\LevelTextures\\Terrain\\grass.dtx",
                "TEXTURES\\LevelTextures\\Terrain\\sand.dtx",
            ),
        )

        items = terrain_reconstruction.terrain_coverage_items(
            terrain,
            ignored_textures=("textures\\leveltextures\\terrain\\sand.dtx",),
        )
        cutout_items = terrain_reconstruction.terrain_coverage_items(
            terrain,
            ignored_textures=("textures\\leveltextures\\terrain\\sand.dtx",),
            require_texture=False,
        )

        self.assertEqual([item.polygon_index for item in items], [0])
        self.assertEqual(items[0].bounds_min, (0.0, 0.0, 0.0))
        self.assertEqual(items[0].bounds_max, (4.0, 0.0, 4.0))
        self.assertEqual(items[0].xz_points, ((0.0, 0.0), (0.0, 4.0), (4.0, 4.0), (4.0, 0.0)))
        self.assertEqual([item.polygon_index for item in cutout_items], [0, 2])
        self.assertEqual(cutout_items[1].texture_name, "")

    def test_builds_generated_terrain_coverage_items_from_scene_faces(self):
        scene = types.SimpleNamespace(
            models=(
                types.SimpleNamespace(
                    points=(
                        (0.0, 0.0, 0.0),
                        (4.0, 0.0, 0.0),
                        (4.0, 0.0, 4.0),
                        (0.0, 0.0, 4.0),
                        (10.0, 0.0, 0.0),
                        (14.0, 0.0, 0.0),
                        (14.0, 0.0, 4.0),
                    ),
                    faces=(
                        types.SimpleNamespace(vertex_indices=(0, 1, 2, 3), material_name="grass.dtx"),
                        types.SimpleNamespace(vertex_indices=(4, 5, 6), material_name="sand.dtx"),
                        types.SimpleNamespace(vertex_indices=(0, 1), material_name="grass.dtx"),
                        types.SimpleNamespace(vertex_indices=(0, 1, 2), material_name="stone.dtx"),
                    ),
                ),
            ),
        )

        items = terrain_reconstruction.generated_terrain_coverage_items(
            scene,
            source_texture_names=("grass.dtx", "sand.dtx"),
            ignored_textures=("sand.dtx",),
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].texture_name, "grass.dtx")
        self.assertEqual(items[0].min_x, 0.0)
        self.assertEqual(items[0].max_x, 4.0)
        self.assertEqual(items[0].min_z, 0.0)
        self.assertEqual(items[0].max_z, 4.0)
        self.assertEqual(items[0].xz_points, ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)))

    def test_hits_generated_and_source_coverage_points(self):
        polygon = (
            (0.0, 0.0),
            (4.0, 0.0),
            (4.0, 4.0),
            (0.0, 4.0),
        )
        generated_items = (
            terrain_reconstruction.GeneratedTerrainCoverageItem(
                min_x=0.0,
                max_x=4.0,
                min_z=0.0,
                max_z=4.0,
                texture_name="grass.dtx",
                xz_points=polygon,
            ),
        )
        terrain_items = (
            terrain_reconstruction.TerrainCoverageItem(
                polygon_index=7,
                bounds_min=(0.0, 0.0, 0.0),
                bounds_max=(4.0, 0.0, 4.0),
                texture_name="grass.dtx",
                xz_points=polygon,
            ),
        )

        self.assertTrue(terrain_reconstruction.generated_coverage_point_hit(2.0, 2.0, generated_items))
        self.assertFalse(terrain_reconstruction.generated_coverage_point_hit(5.0, 2.0, generated_items))
        self.assertEqual(
            terrain_reconstruction.terrain_coverage_point_texture_hit(2.0, 2.0, terrain_items),
            "grass.dtx",
        )
        self.assertIsNone(
            terrain_reconstruction.terrain_coverage_point_texture_hit(5.0, 2.0, terrain_items)
        )

    def test_builds_and_clusters_cutout_model_footprints(self):
        def model(name, min_x, max_x, min_z, max_z):
            return types.SimpleNamespace(
                name=name,
                points=(
                    (float(min_x), 0.0, float(min_z)),
                    (float(max_x), 0.0, float(min_z)),
                    (float(max_x), 0.0, float(max_z)),
                    (float(min_x), 0.0, float(max_z)),
                ),
                polygons=(types.SimpleNamespace(vertex_indices=(0, 1, 2, 3)),),
                is_skybox=lambda: False,
            )

        models = (
            model("Terrain0", 0.0, 100.0, 0.0, 100.0),
            model("HouseA", 0.0, 32.0, 0.0, 32.0),
            model("HouseB", 40.0, 72.0, 0.0, 32.0),
            model("WaterPlane", 200.0, 300.0, 0.0, 100.0),
            model("Tower", 300.0, 360.0, 0.0, 60.0),
        )

        infos = terrain_reconstruction.terrain_cutout_model_infos(
            models,
            include_skyboxes=False,
            min_model_footprint_area=1024.0,
        )
        clusters = terrain_reconstruction.terrain_cutout_model_clusters(
            infos,
            cluster_gap=16.0,
            min_cluster_footprint_area=1024.0,
        )

        self.assertEqual([item.name for item in infos], ["HouseA", "HouseB", "Tower"])
        self.assertEqual([item.model_index for item in infos], [1, 2, 4])
        self.assertEqual([[item.name for item in cluster] for cluster in clusters], [["HouseA", "HouseB"], ["Tower"]])
        self.assertEqual(terrain_reconstruction.terrain_cutout_cluster_xz_bounds(clusters[0]), (0.0, 72.0, 0.0, 32.0))
        self.assertTrue(terrain_reconstruction.terrain_cutout_blocked_model_name("PhysicsBSP", include_skyboxes=False))
        self.assertTrue(terrain_reconstruction.terrain_cutout_blocked_model_name("SkyBox_Main", include_skyboxes=False))
        self.assertFalse(terrain_reconstruction.terrain_cutout_blocked_model_name("SkyBox_Main", include_skyboxes=True))

    def test_builds_and_selects_terrain_support_items(self):
        terrain = types.SimpleNamespace(
            points=(
                (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0),
                (10.0, 0.0, 10.0),
                (0.0, 0.0, 10.0),
                (30.0, 0.0, 0.0),
                (40.0, 0.0, 0.0),
                (40.0, 0.0, 10.0),
                (30.0, 0.0, 10.0),
            ),
            polygons=(
                types.SimpleNamespace(vertex_indices=(0, 3, 2, 1)),
                types.SimpleNamespace(vertex_indices=(4, 5, 6, 7)),
            ),
        )

        items = terrain_reconstruction.terrain_support_items(terrain)
        selected = terrain_reconstruction.select_terrain_support_items(
            items,
            anchor_points=((2.0, 0.0, 2.0), (8.0, 0.0, 8.0)),
            margin=0.0,
        )
        placement = terrain_reconstruction.terrain_support_start_placement(
            selected,
            anchor_points=((2.0, 0.0, 2.0), (8.0, 0.0, 8.0)),
            margin=0.0,
        )

        self.assertEqual([item.polygon_index for item in items], [0, 1])
        self.assertEqual([item.polygon_index for item in selected], [0])
        self.assertGreater(terrain_reconstruction.terrain_support_start_score(selected[0]), 0.0)
        self.assertEqual(placement.center, (5.0, 0.0, 5.0))
        self.assertEqual(placement.top_y, 0.0)

    def test_budgeted_connected_terrain_support_limits_compact_component(self):
        terrain = types.SimpleNamespace(
            points=(
                (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0),
                (20.0, 0.0, 0.0),
                (30.0, 0.0, 0.0),
                (0.0, 0.0, 10.0),
                (10.0, 0.0, 10.0),
                (20.0, 0.0, 10.0),
                (30.0, 0.0, 10.0),
                (100.0, 0.0, 0.0),
                (110.0, 0.0, 0.0),
                (110.0, 0.0, 10.0),
                (100.0, 0.0, 10.0),
            ),
            polygons=(
                types.SimpleNamespace(vertex_indices=(0, 4, 5, 1)),
                types.SimpleNamespace(vertex_indices=(1, 5, 6, 2)),
                types.SimpleNamespace(vertex_indices=(2, 6, 7, 3)),
                types.SimpleNamespace(vertex_indices=(8, 11, 10, 9)),
            ),
        )

        items = terrain_reconstruction.terrain_support_items(terrain)
        selected = terrain_reconstruction.select_terrain_support_items(
            items,
            anchor_points=((2.0, 0.0, 2.0), (8.0, 0.0, 8.0)),
            margin=0.0,
            selection_mode="budgeted-connected-radius",
            radius=1000.0,
            max_items=2,
        )

        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_selection_mode("budgeted-connected-radius"),
            "connected_budget",
        )
        self.assertEqual([item.polygon_index for item in selected], [0, 1])

    def test_multi_anchor_terrain_support_spreads_budget_across_components(self):
        terrain = types.SimpleNamespace(
            points=(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 0.0, 10.0),
                (100.0, 0.0, 0.0), (110.0, 0.0, 0.0), (110.0, 0.0, 10.0), (100.0, 0.0, 10.0),
            ),
            polygons=(
                types.SimpleNamespace(vertex_indices=(0, 3, 2, 1)),
                types.SimpleNamespace(vertex_indices=(4, 7, 6, 5)),
            ),
        )
        items = terrain_reconstruction.terrain_support_items(terrain)
        selected = terrain_reconstruction.select_terrain_support_items(
            items,
            anchor_points=((5.0, 0.0, 5.0), (105.0, 0.0, 5.0)),
            margin=0.0,
            selection_mode="multi-anchor-budget",
            radius=32.0,
            max_items=2,
        )

        self.assertEqual(
            terrain_reconstruction.normalize_terrain_support_selection_mode("multi-anchor-budget"),
            "multi_anchor_budget",
        )
        self.assertEqual([item.polygon_index for item in selected], [0, 1])

    def test_playable_area_allocation_clusters_anchors_and_weights_surface(self):
        points = tuple(
            (float(x), 0.0, float(z))
            for z in (0, 10)
            for x in (0, 10, 20, 30, 40)
        ) + (
            (1000.0, 0.0, 0.0),
            (1010.0, 0.0, 0.0),
            (1000.0, 0.0, 10.0),
            (1010.0, 0.0, 10.0),
        )
        indices = (
            (0, 5, 6, 1),
            (1, 6, 7, 2),
            (2, 7, 8, 3),
            (3, 8, 9, 4),
            (10, 12, 13, 11),
        )
        items = tuple(
            terrain_reconstruction.TerrainSupportItem(
                polygon_index,
                types.SimpleNamespace(vertex_indices=polygon_indices),
                polygon_indices,
                tuple(points[index] for index in polygon_indices),
                (
                    sum(points[index][0] for index in polygon_indices) / 4.0,
                    0.0,
                    sum(points[index][2] for index in polygon_indices) / 4.0,
                ),
                (
                    min(points[index][0] for index in polygon_indices),
                    max(points[index][0] for index in polygon_indices),
                    min(points[index][2] for index in polygon_indices),
                    max(points[index][2] for index in polygon_indices),
                ),
            )
            for polygon_index, polygon_indices in enumerate(indices)
        )
        anchors = (
            (5.0, 0.0, 5.0),
            (15.0, 0.0, 5.0),
            (1005.0, 0.0, 5.0),
        )

        areas = terrain_reconstruction.playable_terrain_area_allocations(
            items,
            anchors,
            margin=0.0,
            radius=32.0,
            total_polygon_budget=4,
        )
        selected = terrain_reconstruction.select_terrain_support_items(
            items,
            anchors,
            margin=0.0,
            selection_mode="playable-area-budget",
            radius=32.0,
            max_items=4,
        )

        self.assertEqual(len(areas), 2)
        self.assertEqual(
            tuple(area.anchor_count for area in areas),
            (2, 1),
        )
        self.assertEqual(
            tuple(area.candidate_polygon_count for area in areas),
            (4, 1),
        )
        self.assertEqual(
            tuple(area.allocated_polygon_budget for area in areas),
            (3, 1),
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(
            sum(item.polygon_index < 4 for item in selected),
            3,
        )
        self.assertIn(4, {item.polygon_index for item in selected})

    def test_triangulates_convex_and_degenerate_boundaries(self):
        square = (
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 0.0, 2.0),
            (0.0, 0.0, 2.0),
        )
        line_like_quad = (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (3.0, 0.0, 0.0),
        )

        self.assertEqual(
            terrain_reconstruction.triangulate_polygon_vertex_offsets(square),
            ((3, 0, 1), (1, 2, 3)),
        )
        self.assertEqual(
            terrain_reconstruction.triangulate_polygon_vertex_offsets(line_like_quad),
            ((0, 1, 2), (0, 2, 3)),
        )

    def test_triangulates_terrain_support_item(self):
        source = terrain_reconstruction.TerrainSupportItem(
            42,
            types.SimpleNamespace(surface_index=7),
            (10, 11, 12, 13),
            (
                (0.0, 0.0, 0.0),
                (2.0, 0.0, 0.0),
                (2.0, 0.0, 2.0),
                (0.0, 0.0, 2.0),
            ),
            (1.0, 0.0, 1.0),
            (0.0, 2.0, 0.0, 2.0),
        )

        triangles = terrain_reconstruction.triangulated_terrain_support_items(source)

        self.assertEqual(len(triangles), 2)
        self.assertEqual([item.polygon_index for item in triangles], [42, 42])
        self.assertEqual([item.indices for item in triangles], [(13, 10, 11), (11, 12, 13)])
        self.assertEqual(triangles[0].bounds, (0.0, 2.0, 0.0, 2.0))


if __name__ == "__main__":
    unittest.main()
