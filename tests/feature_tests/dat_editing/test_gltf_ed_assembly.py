import struct
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import geometry_scene
from features.dat_editing import gltf_brushes
from features.dat_editing import gltf_ed_assembly
from features.dat_editing import legacy_ed
from features.dat_editing import mesh_topology


TEXTURE = "TEXTURES\\Test\\Phase6.dtx"


def _tetrahedron_scene():
    triangles = (
        (0, 2, 1),
        (0, 1, 3),
        (0, 3, 2),
        (1, 2, 3),
    )
    return geometry_scene.GeometryScene(
        source_path="phase6.gltf",
        models=[geometry_scene.GeometryModel(
            name="Phase 6 Mesh",
            points=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
            ],
            faces=[
                geometry_scene.GeometryFace(
                    list(indices),
                    "Material",
                    [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                )
                for indices in triangles
            ],
        )],
        materials=[geometry_scene.GeometryMaterial("Material", TEXTURE)],
    )


def _open_triangle_scene():
    return geometry_scene.GeometryScene(
        source_path="blocked.gltf",
        models=[geometry_scene.GeometryModel(
            name="Open",
            points=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            faces=[geometry_scene.GeometryFace(
                [0, 1, 2],
                "Material",
                [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            )],
        )],
        materials=[geometry_scene.GeometryMaterial("Material", TEXTURE)],
    )


def _plan(scene):
    return gltf_brushes.build_gltf_brush_plan(
        scene,
        mesh_topology.analyze_geometry_scene(scene),
        texture_dimensions={TEXTURE: (128, 128)},
    )


class GltfEdAssemblyTests(unittest.TestCase):
    def test_ready_plan_builds_deterministic_named_group_prefab_and_round_trips(self):
        plan = _plan(_tetrahedron_scene())

        first = gltf_ed_assembly.assemble_gltf_ed(
            plan,
            output_mode=gltf_ed_assembly.PREFAB,
            group_name="Imported Phase 6",
        )
        second = gltf_ed_assembly.assemble_gltf_ed(
            plan,
            output_mode=gltf_ed_assembly.PREFAB,
            group_name="Imported Phase 6",
        )

        self.assertEqual(first.status, "ready_prefab")
        self.assertEqual(first.ed_bytes, second.ed_bytes)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(struct.unpack_from("<I", first.ed_bytes, 0)[0], 1249)
        self.assertEqual(first.group_name, "Imported_Phase_6")
        self.assertEqual(first.wrapper_kind, "uncompressed_named_group_prefab")
        self.assertEqual(first.brush_count, 1)
        self.assertEqual(first.surface_count, 4)
        self.assertEqual(first.point_count, 4)
        self.assertEqual(first.node_count, 3)
        self.assertEqual(first.object_count, 1)
        self.assertEqual(first.validation.writer, "pass")
        self.assertEqual(first.validation.reader_roundtrip, "pass")
        self.assertEqual(first.validation.node_layout_kind, "named_group_brush_nodes")
        self.assertIsNone(first.scaffold)
        self.assertEqual(first.node_assignments, ())
        self.assertNotIn("ed_bytes", first.to_dict()["output"])

        analysis = legacy_ed.analyze_legacy_ed_bytes(first.ed_bytes)
        self.assertEqual(analysis.node_layout.brush_names, plan.brush_names)
        self.assertEqual(analysis.geometry_scene.models[0].faces[0].material_name, TEXTURE)

    def test_full_world_adds_deterministic_scaffold_ids_and_zlib_wrapper(self):
        plan = _plan(_tetrahedron_scene())

        result = gltf_ed_assembly.assemble_gltf_ed(
            plan,
            output_mode=gltf_ed_assembly.FULL_WORLD,
            group_name="ImportedWorld",
        )

        self.assertEqual(result.status, "ready_full_world")
        self.assertEqual(result.ed_bytes[4], 1)
        self.assertEqual(result.wrapper_kind, "zlib_blocked_full_level")
        self.assertEqual(result.wrapper_block_count, 1)
        self.assertEqual(result.wrapper_block_size, 50000)
        self.assertEqual(result.node_count, 6)
        self.assertEqual(result.object_count, 4)
        self.assertEqual(result.validation.reader_roundtrip, "pass")
        self.assertEqual(
            result.validation.node_layout_kind,
            "named_group_brush_nodes_with_root_objects",
        )
        self.assertEqual(
            tuple(item.node_id for item in result.node_assignments),
            (1, 2, 3, 4, 5, 6),
        )
        self.assertEqual(result.scaffold.world_properties_position, (0.5, 513.0, 0.5))
        self.assertEqual(result.scaffold.start_point_position, (0.5, 65.0, 0.5))
        self.assertEqual(result.scaffold.light_position, (0.5, 257.0, 0.5))
        self.assertIn("PBlockSize 2048", result.scaffold.infostring)
        self.assertIn("minimal_full_world_scaffold", {item.code for item in result.cautions})

        analysis = legacy_ed.analyze_legacy_ed_bytes(result.ed_bytes)
        self.assertEqual(analysis.geometry_scene.metadata["declared_brush_count"], 1)
        self.assertEqual(
            analysis.object_scan.class_counts,
            {"Brush": 1, "WorldProperties": 1, "StartPoint": 1, "Light": 1},
        )
        self.assertEqual(analysis.node_tree.node_name, "Container")
        self.assertEqual(analysis.node_tree.children[0].node_name, "ImportedWorld")

    def test_generic_assembly_sanitizes_and_suffixes_colliding_brush_names(self):
        brush = _plan(_tetrahedron_scene()).write_ready_brushes[0]

        result = gltf_ed_assembly.assemble_ed_document(
            (brush, brush),
            brush_names=("Same name!", "Same name?"),
            brush_ids=("first", "second"),
        )

        self.assertEqual(result.status, "ready_prefab")
        self.assertEqual(result.brush_names, ("Same_name", "Same_name_2"))
        brush_map = tuple(item for item in result.name_map if item.kind == "brush")
        self.assertEqual(tuple(item.source_id for item in brush_map), ("first", "second"))
        self.assertEqual(
            legacy_ed.scan_legacy_ed_node_layout(result.ed_bytes).brush_names,
            result.brush_names,
        )

    def test_blocked_brush_plan_never_reaches_writer(self):
        plan = _plan(_open_triangle_scene())
        self.assertEqual(plan.status, "blocked")

        result = gltf_ed_assembly.assemble_gltf_ed(plan)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.ed_bytes, b"")
        self.assertEqual(result.validation.writer, "not_run")
        self.assertEqual(result.validation.reader_roundtrip, "not_run")
        self.assertEqual({item.code for item in result.blockers}, {"brush_plan_blocked"})

    def test_empty_input_blocks_and_invalid_options_fail_before_assembly(self):
        empty = gltf_ed_assembly.assemble_ed_document(())
        self.assertEqual(empty.status, "blocked")
        self.assertEqual({item.code for item in empty.blockers}, {"no_brushes"})

        with self.assertRaisesRegex(ValueError, "output_mode"):
            gltf_ed_assembly.assemble_ed_document((), output_mode="raw")
        with self.assertRaisesRegex(ValueError, "brush_names"):
            gltf_ed_assembly.assemble_ed_document(
                (_plan(_tetrahedron_scene()).write_ready_brushes[0],),
                brush_names=("one", "two"),
            )


if __name__ == "__main__":
    unittest.main()
