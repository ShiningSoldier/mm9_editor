import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import geometry_scene, mesh_topology


def _scene(points, triangles, *, name="Mesh", model_extras=None):
    faces = []
    for face_index, triangle in enumerate(triangles):
        faces.append(geometry_scene.GeometryFace(
            vertex_indices=list(triangle),
            material_name=f"Material{face_index % 2}",
            uv_coords=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            extras={"primitive_index": face_index % 2, "triangle_index": face_index},
        ))
    model = geometry_scene.GeometryModel(
        name=name,
        points=list(points),
        faces=faces,
        extras=dict(model_extras or {}),
    )
    return geometry_scene.GeometryScene(
        source_path="synthetic.gltf",
        models=[model],
        metadata={"selected_scene_index": 2},
    )


def _tetrahedron(offset=(0.0, 0.0, 0.0)):
    ox, oy, oz = offset
    points = [
        (ox + 0.0, oy + 0.0, oz + 0.0),
        (ox + 1.0, oy + 0.0, oz + 0.0),
        (ox + 0.0, oy + 1.0, oz + 0.0),
        (ox + 0.0, oy + 0.0, oz + 1.0),
    ]
    # Consistently outward.
    triangles = [
        (0, 2, 1),
        (0, 1, 3),
        (0, 3, 2),
        (1, 2, 3),
    ]
    return points, triangles


def _edge_directions(component):
    uses = {}
    for face in component.faces:
        a, b, c = face.vertex_indices
        for start, end in ((a, b), (b, c), (c, a)):
            uses.setdefault(tuple(sorted((start, end))), []).append((start, end))
    return uses


class MeshTopologyTests(unittest.TestCase):
    def test_closed_tetrahedron_is_exact_convex_and_outward(self):
        points, triangles = _tetrahedron()
        scene = _scene(
            points,
            triangles,
            model_extras={"scene_node_index": 7, "mesh_index": 3},
        )

        report = mesh_topology.analyze_geometry_scene(scene)

        self.assertEqual(report.status, "ready_strict_convex")
        self.assertTrue(report.strict_convex_ready)
        self.assertEqual(report.classification_counts, {"exact_convex": 1})
        component = report.components[0]
        self.assertEqual(component.classification, mesh_topology.EXACT_CONVEX)
        self.assertEqual(component.topology_status, "closed_manifold")
        self.assertEqual(component.convexity_status, "convex")
        self.assertEqual(component.scene_index, 2)
        self.assertEqual(component.scene_node_index, 7)
        self.assertEqual(component.mesh_index, 3)
        self.assertEqual(component.primitive_indices, (0, 1))
        self.assertEqual(component.boundary_edge_count, 0)
        self.assertEqual(component.nonmanifold_edge_count, 0)
        self.assertAlmostEqual(component.signed_volume, 1.0 / 6.0)
        for uses in _edge_directions(component).values():
            self.assertEqual(len(uses), 2)
            self.assertEqual(uses[0], tuple(reversed(uses[1])))

    def test_welded_face_vertices_form_one_closed_component(self):
        canonical_points, canonical_triangles = _tetrahedron()
        points = []
        triangles = []
        for canonical_triangle in canonical_triangles:
            first = len(points)
            points.extend(canonical_points[index] for index in canonical_triangle)
            triangles.append((first, first + 1, first + 2))

        component = mesh_topology.analyze_geometry_scene(_scene(points, triangles)).components[0]

        self.assertEqual(component.classification, mesh_topology.EXACT_CONVEX)
        self.assertEqual(component.source_point_count, 12)
        self.assertEqual(component.welded_point_count, 4)
        self.assertEqual(component.source_triangle_count, 4)

    def test_disconnected_solids_are_split_deterministically(self):
        left_points, left_triangles = _tetrahedron()
        right_points, right_triangles = _tetrahedron((3.0, 0.0, 0.0))
        offset = len(left_points)
        triangles = left_triangles + [tuple(index + offset for index in face) for face in right_triangles]

        report = mesh_topology.analyze_geometry_scene(_scene(left_points + right_points, triangles))

        self.assertEqual(report.component_count, 2)
        self.assertEqual(
            tuple(item.component_id for item in report.components),
            ("model_0000_component_0000", "model_0000_component_0001"),
        )
        self.assertTrue(all(item.classification == mesh_topology.EXACT_CONVEX for item in report.components))

    def test_inconsistent_but_orientable_winding_is_repaired(self):
        points, triangles = _tetrahedron()
        triangles[2] = tuple(reversed(triangles[2]))

        component = mesh_topology.analyze_geometry_scene(_scene(points, triangles)).components[0]

        self.assertEqual(component.classification, mesh_topology.EXACT_CONVEX)
        self.assertGreater(component.inconsistent_edge_count, 0)
        self.assertGreater(component.winding_flip_count, 0)
        self.assertIn("winding_repaired", {item.code for item in component.notes})
        for uses in _edge_directions(component).values():
            self.assertEqual(uses[0], tuple(reversed(uses[1])))

    def test_inward_closed_winding_is_reversed_outward(self):
        points, triangles = _tetrahedron()
        inward = [tuple(reversed(triangle)) for triangle in triangles]

        component = mesh_topology.analyze_geometry_scene(_scene(points, inward)).components[0]

        self.assertEqual(component.classification, mesh_topology.EXACT_CONVEX)
        self.assertTrue(component.global_winding_reversed)
        self.assertEqual(component.winding_flip_count, 4)
        self.assertAlmostEqual(component.signed_volume, 1.0 / 6.0)

    def test_open_manifold_is_only_a_slab_candidate(self):
        scene = _scene(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
            [(0, 1, 2), (0, 2, 3)],
        )

        report = mesh_topology.analyze_geometry_scene(scene)
        component = report.components[0]

        self.assertEqual(report.status, "analyzed_with_slab_candidates")
        self.assertFalse(report.strict_convex_ready)
        self.assertEqual(component.classification, mesh_topology.SLAB_CANDIDATE)
        self.assertEqual(component.topology_status, "open_manifold")
        self.assertEqual(component.boundary_edge_count, 4)
        self.assertEqual(component.convexity_status, "not_applicable_open")
        self.assertIn("open_surface", {item.code for item in component.cautions})

    def test_nonmanifold_edge_is_blocked(self):
        scene = _scene(
            [
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, -1.0, 0.0),
                (0.0, 0.0, 1.0),
            ],
            [(0, 1, 2), (1, 0, 3), (0, 1, 4)],
        )

        report = mesh_topology.analyze_geometry_scene(scene)
        component = report.components[0]

        self.assertEqual(report.status, "blocked")
        self.assertEqual(component.classification, mesh_topology.BLOCKED_NON_MANIFOLD)
        self.assertEqual(component.nonmanifold_edge_count, 1)
        self.assertIn("nonmanifold_edges", {item.code for item in report.blockers})

    def test_closed_inward_dent_is_blocked_as_concave(self):
        points = [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (1.0, 0.0, 1.0),
            (1.0, 1.0, 1.0),
            (0.0, 1.0, 1.0),
            (0.5, 0.5, 0.4),
        ]
        triangles = [
            (0, 2, 1), (0, 3, 2),
            (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6),
            (3, 0, 4), (3, 4, 7),
            (4, 5, 8), (5, 6, 8), (6, 7, 8), (7, 4, 8),
        ]

        component = mesh_topology.analyze_geometry_scene(_scene(points, triangles)).components[0]

        self.assertEqual(component.classification, mesh_topology.BLOCKED_CONCAVE)
        self.assertEqual(component.topology_status, "closed_manifold")
        self.assertEqual(component.convexity_status, "concave")
        self.assertGreater(component.convexity_violation_count, 0)
        self.assertGreater(component.max_convexity_violation, 0.0)

    def test_degenerate_face_is_preserved_as_blocked_component(self):
        scene = _scene(
            [(0.0, 0.0, 0.0), (0.005, 0.0, 0.0), (1.0, 0.0, 0.0)],
            [(0, 1, 2)],
        )

        report = mesh_topology.analyze_geometry_scene(scene)
        component = report.components[0]

        self.assertEqual(report.source_triangle_count, 1)
        self.assertEqual(component.source_face_indices, (0,))
        self.assertEqual(component.classification, mesh_topology.BLOCKED_INVALID)
        self.assertEqual(component.faces, ())
        self.assertIn("face_collapsed_after_weld", {item.code for item in component.blockers})

    def test_duplicate_triangle_is_reported(self):
        points, triangles = _tetrahedron()
        triangles.append(triangles[0])

        component = mesh_topology.analyze_geometry_scene(_scene(points, triangles)).components[0]

        self.assertEqual(component.classification, mesh_topology.BLOCKED_INVALID)
        self.assertEqual(component.duplicate_face_count, 1)
        self.assertIn("duplicate_faces", {item.code for item in component.blockers})

    def test_empty_scene_and_invalid_options_fail_closed(self):
        empty = geometry_scene.GeometryScene(source_path="empty.gltf")

        report = mesh_topology.analyze_geometry_scene(empty)

        self.assertEqual(report.status, "blocked")
        self.assertEqual(report.component_count, 0)
        self.assertIn("empty_geometry_scene", {item.code for item in report.blockers})
        self.assertEqual(report.to_dict()["inventory"]["components"], 0)
        with self.assertRaisesRegex(ValueError, "weld_tolerance"):
            mesh_topology.analyze_geometry_scene(empty, weld_tolerance=float("nan"))


if __name__ == "__main__":
    unittest.main()
