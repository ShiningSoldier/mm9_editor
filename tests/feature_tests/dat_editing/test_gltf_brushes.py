import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import geometry_scene
from features.dat_editing import gltf_brushes
from features.dat_editing import legacy_ed
from features.dat_editing import legacy_ed_writer
from features.dat_editing import mesh_topology
from features.dat_editing import uv_projection


FRONT_TEXTURE = "TEXTURES\\Test\\Front.dtx"
EXTRAS_TEXTURE = "TEXTURES\\Test\\FromExtras.dtx"
MAP_TEXTURE = "TEXTURES\\Test\\FromMap.dtx"
BACK_TEXTURE = "TEXTURES\\Test\\Back.dtx"
SIDE_TEXTURE = "TEXTURES\\Test\\Side.dtx"
FALLBACK_TEXTURE = "TEXTURES\\Test\\Fallback.dtx"


def _tetrahedron_scene(*, uvs=True, material=None):
    points = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ]
    triangles = [
        (0, 2, 1),
        (0, 1, 3),
        (0, 3, 2),
        (1, 2, 3),
    ]
    faces = [
        geometry_scene.GeometryFace(
            vertex_indices=list(triangle),
            material_name="Material",
            uv_coords=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)] if uvs else [None, None, None],
            extras={"primitive_index": face_index % 2},
        )
        for face_index, triangle in enumerate(triangles)
    ]
    return geometry_scene.GeometryScene(
        source_path="tetra.gltf",
        models=[geometry_scene.GeometryModel(name="Tetra Mesh", points=points, faces=faces)],
        materials=[material or geometry_scene.GeometryMaterial("Material", FRONT_TEXTURE)],
    )


def _open_square_scene():
    return geometry_scene.GeometryScene(
        source_path="square.gltf",
        models=[geometry_scene.GeometryModel(
            name="Open Square",
            points=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 1.0, 0.0),
                (0.0, 1.0, 0.0),
            ],
            faces=[
                geometry_scene.GeometryFace(
                    [0, 1, 2], "Material", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
                ),
                geometry_scene.GeometryFace(
                    [0, 2, 3], "Material", [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
                ),
            ],
        )],
        materials=[geometry_scene.GeometryMaterial("Material", FRONT_TEXTURE)],
    )


def _analyze(scene):
    return mesh_topology.analyze_geometry_scene(scene)


def _dimensions(*textures):
    return {texture: (128, 128) for texture in textures}


class GltfBrushTests(unittest.TestCase):
    def test_strict_convex_builds_one_writer_ready_brush_and_round_trips(self):
        material = geometry_scene.GeometryMaterial(
            "Material",
            EXTRAS_TEXTURE,
            extras={
                "resolution_source": "extras",
                "gltf_extras": {"MM9_texture": EXTRAS_TEXTURE},
            },
        )
        scene = _tetrahedron_scene(material=material)

        plan = gltf_brushes.build_gltf_brush_plan(
            scene,
            _analyze(scene),
            material_map={"Material": MAP_TEXTURE},
            texture_dimensions=_dimensions(EXTRAS_TEXTURE),
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(len(plan.write_ready_brushes), 1)
        self.assertEqual(plan.estimated_brush_count, 1)
        self.assertEqual(plan.estimated_surface_count, 4)
        planned = plan.planned_brushes[0]
        self.assertEqual(planned.output_classification, "exact")
        self.assertEqual(len(planned.brush.points), 4)
        self.assertEqual(len(planned.brush.surfaces), 4)
        self.assertEqual({surface.texture_name for surface in planned.brush.surfaces}, {EXTRAS_TEXTURE})
        self.assertEqual(
            {surface.texture_resolution_source for surface in planned.surfaces},
            {"extras"},
        )
        first_face = _analyze(scene).components[0].faces[0]
        expected_opq = uv_projection.dedit_uv_to_opq(
            [plan.planned_brushes[0].brush.points[index] for index in first_face.vertex_indices],
            [value for value in first_face.uv_coords if value is not None],
            tex_width=128,
            tex_height=128,
        )
        self.assertEqual(planned.brush.surfaces[0].uv_o, expected_opq[0])
        self.assertTrue(all(
            sum(surface.plane_normal[axis] * point[axis] for axis in range(3))
            <= surface.plane_dist + 1.0e-6
            for surface in planned.brush.surfaces
            for point in planned.brush.points
        ))

        encoded = legacy_ed_writer.build_named_group_prefab(
            plan.write_ready_brushes,
            group_name="Imported",
            brush_names=plan.brush_names,
        )
        recovered = legacy_ed.legacy_ed_bytes_to_geometry_scene(
            encoded,
            source_path="phase4.ed",
        )
        self.assertEqual(len(recovered.mesh_models()), 1)
        self.assertEqual(len(recovered.mesh_models()[0].faces), 4)
        self.assertEqual(
            {face.material_name for face in recovered.mesh_models()[0].faces},
            {EXTRAS_TEXTURE},
        )

    def test_strict_convex_blocks_open_component_without_selecting_slab(self):
        scene = _open_square_scene()

        plan = gltf_brushes.build_gltf_brush_plan(
            scene,
            _analyze(scene),
            texture_dimensions=_dimensions(FRONT_TEXTURE),
        )

        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.write_ready_brushes, ())
        self.assertEqual(plan.planned_brushes, ())
        self.assertIn("blocked_open", {item.code for item in plan.blockers})

    def test_triangle_slab_builds_one_closed_five_surface_brush_per_triangle(self):
        scene = _open_square_scene()

        plan = gltf_brushes.build_gltf_brush_plan(
            scene,
            _analyze(scene),
            geometry_policy=gltf_brushes.TRIANGLE_SLAB,
            slab_thickness=0.25,
            slab_back_texture=BACK_TEXTURE,
            slab_side_texture=SIDE_TEXTURE,
            texture_dimensions=_dimensions(FRONT_TEXTURE, BACK_TEXTURE, SIDE_TEXTURE),
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.estimated_brush_count, 2)
        self.assertEqual(plan.estimated_surface_count, 10)
        self.assertEqual(len(plan.write_ready_brushes), 2)
        component = plan.components[0]
        self.assertEqual(component.output_classification, "approximated")
        self.assertAlmostEqual(component.nominal_added_volume, 0.25)
        for planned in plan.planned_brushes:
            self.assertEqual(len(planned.brush.points), 6)
            self.assertEqual(len(planned.brush.surfaces), 5)
            self.assertEqual(
                tuple(surface.role for surface in planned.surfaces),
                ("front", "generated_back", "generated_side", "generated_side", "generated_side"),
            )
            self.assertEqual(planned.brush.surfaces[0].texture_name, FRONT_TEXTURE)
            self.assertEqual(planned.brush.surfaces[1].texture_name, BACK_TEXTURE)
            self.assertEqual(
                {surface.texture_name for surface in planned.brush.surfaces[2:]},
                {SIDE_TEXTURE},
            )
            self.assertAlmostEqual(max(point[2] for point in planned.brush.points), 0.0)
            self.assertAlmostEqual(min(point[2] for point in planned.brush.points), -0.25)

        encoded = legacy_ed_writer.build_named_group_prefab(
            plan.write_ready_brushes,
            brush_names=plan.brush_names,
        )
        recovered = legacy_ed.legacy_ed_bytes_to_geometry_scene(encoded, source_path="slabs.ed")
        self.assertEqual(len(recovered.mesh_models()), 2)
        self.assertTrue(all(len(model.faces) == 5 for model in recovered.mesh_models()))

    def test_slab_options_are_explicit_and_must_survive_point_welding(self):
        scene = _open_square_scene()
        topology = _analyze(scene)

        with self.assertRaisesRegex(ValueError, "slab_thickness"):
            gltf_brushes.build_gltf_brush_plan(
                scene,
                topology,
                geometry_policy=gltf_brushes.TRIANGLE_SLAB,
                slab_back_texture=BACK_TEXTURE,
                slab_side_texture=SIDE_TEXTURE,
            )
        with self.assertRaisesRegex(ValueError, "slab_back_texture"):
            gltf_brushes.build_gltf_brush_plan(
                scene,
                topology,
                geometry_policy=gltf_brushes.TRIANGLE_SLAB,
                slab_thickness=1.0,
                slab_side_texture=SIDE_TEXTURE,
            )

        too_thin = gltf_brushes.build_gltf_brush_plan(
            scene,
            topology,
            geometry_policy=gltf_brushes.TRIANGLE_SLAB,
            slab_thickness=0.01,
            slab_back_texture=BACK_TEXTURE,
            slab_side_texture=SIDE_TEXTURE,
            texture_dimensions=_dimensions(FRONT_TEXTURE, BACK_TEXTURE, SIDE_TEXTURE),
        )
        self.assertEqual(too_thin.status, "blocked")
        self.assertIn("slab_thickness_within_weld_tolerance", {item.code for item in too_thin.blockers})

    def test_unresolved_material_blocks_but_explicit_fallback_is_reported(self):
        unresolved = geometry_scene.GeometryMaterial("Material", "")
        scene = _tetrahedron_scene(material=unresolved)
        topology = _analyze(scene)

        blocked = gltf_brushes.build_gltf_brush_plan(
            scene,
            topology,
            texture_dimensions=_dimensions(FALLBACK_TEXTURE),
        )
        ready = gltf_brushes.build_gltf_brush_plan(
            scene,
            topology,
            fallback_texture=FALLBACK_TEXTURE,
            texture_dimensions=_dimensions(FALLBACK_TEXTURE),
        )

        self.assertIn("unresolved_material_texture", {item.code for item in blocked.blockers})
        self.assertEqual(blocked.write_ready_brushes, ())
        self.assertEqual(ready.status, "ready")
        self.assertIn("fallback_material_texture", {item.code for item in ready.cautions})
        self.assertEqual(
            {surface.texture_name for surface in ready.write_ready_brushes[0].surfaces},
            {FALLBACK_TEXTURE},
        )

    def test_texture_dimensions_require_real_values_or_explicit_fallback(self):
        scene = _tetrahedron_scene()
        topology = _analyze(scene)

        blocked = gltf_brushes.build_gltf_brush_plan(scene, topology)
        ready = gltf_brushes.build_gltf_brush_plan(
            scene,
            topology,
            fallback_texture_size=(128, 128),
        )

        self.assertIn("missing_texture_dimensions", {item.code for item in blocked.blockers})
        self.assertEqual(ready.status, "ready")
        self.assertIn("fallback_texture_dimensions", {item.code for item in ready.cautions})

    def test_missing_uvs_require_explicit_default_projection(self):
        scene = _tetrahedron_scene(uvs=False)
        topology = _analyze(scene)

        blocked = gltf_brushes.build_gltf_brush_plan(
            scene,
            topology,
            texture_dimensions=_dimensions(FRONT_TEXTURE),
        )
        ready = gltf_brushes.build_gltf_brush_plan(
            scene,
            topology,
            texture_dimensions=_dimensions(FRONT_TEXTURE),
            default_uv_projection=gltf_brushes.WORLD_ALIGNED_PROJECTION,
        )

        self.assertIn("missing_uv_projection", {item.code for item in blocked.blockers})
        self.assertEqual(ready.status, "ready")
        self.assertIn("default_uv_projection", {item.code for item in ready.cautions})
        self.assertEqual(
            {surface.uv_method for surface in ready.planned_brushes[0].surfaces},
            {gltf_brushes.WORLD_ALIGNED_PROJECTION},
        )

    def test_global_budgets_block_all_write_ready_brushes(self):
        scene = _open_square_scene()

        plan = gltf_brushes.build_gltf_brush_plan(
            scene,
            _analyze(scene),
            geometry_policy=gltf_brushes.TRIANGLE_SLAB,
            slab_thickness=1.0,
            slab_back_texture=BACK_TEXTURE,
            slab_side_texture=SIDE_TEXTURE,
            texture_dimensions=_dimensions(FRONT_TEXTURE, BACK_TEXTURE, SIDE_TEXTURE),
            max_brushes=1,
        )

        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.planned_brushes, ())
        self.assertEqual(plan.write_ready_brushes, ())
        self.assertIn("brush_budget_exceeded", {item.code for item in plan.blockers})
        self.assertFalse(plan.to_dict()["budgets"]["brushes_pass"])

    def test_blocked_topology_cannot_be_downgraded_to_slabs(self):
        points = [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, -1.0, 0.0),
            (0.0, 0.0, 1.0),
        ]
        scene = geometry_scene.GeometryScene(
            source_path="nonmanifold.gltf",
            models=[geometry_scene.GeometryModel(
                "Nonmanifold",
                points,
                [
                    geometry_scene.GeometryFace(list(face), "Material", [(0, 0), (1, 0), (0, 1)])
                    for face in ((0, 1, 2), (1, 0, 3), (0, 1, 4))
                ],
            )],
            materials=[geometry_scene.GeometryMaterial("Material", FRONT_TEXTURE)],
        )

        plan = gltf_brushes.build_gltf_brush_plan(
            scene,
            _analyze(scene),
            geometry_policy=gltf_brushes.TRIANGLE_SLAB,
            slab_thickness=1.0,
            slab_back_texture=BACK_TEXTURE,
            slab_side_texture=SIDE_TEXTURE,
            texture_dimensions=_dimensions(FRONT_TEXTURE, BACK_TEXTURE, SIDE_TEXTURE),
        )

        self.assertEqual(plan.status, "blocked")
        self.assertIn("topology_not_slab_safe", {item.code for item in plan.blockers})
        self.assertEqual(plan.write_ready_brushes, ())


if __name__ == "__main__":
    unittest.main()
