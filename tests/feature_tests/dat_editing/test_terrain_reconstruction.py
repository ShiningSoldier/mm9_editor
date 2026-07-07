import inspect
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
