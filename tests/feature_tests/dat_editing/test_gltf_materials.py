import struct
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import geometry_scene
from features.dat_editing import gltf_brushes
from features.dat_editing import gltf_materials
from features.dat_editing import mesh_topology
from features.dat_editing import uv_projection


EXTRAS_TEXTURE = "TEXTURES\\Test\\Extras.dtx"
MAP_TEXTURE = "TEXTURES\\Test\\Map.dtx"
NAMED_TEXTURE = "TEXTURES\\Test\\Named.dtx"
DIRECT_TEXTURE = "TEXTURES\\Test\\Direct.dtx"
FALLBACK_TEXTURE = "TEXTURES\\Test\\Fallback.dtx"
HELPER_TEXTURE = "TEXTURES\\LevelTextures\\Misc\\Invisible.dtx"


def _dtx_header(width=256, height=64, *, version=-5, mip_count=4, pixel_format=4):
    payload = bytearray(gltf_materials.DTX_HEADER_SIZE)
    struct.pack_into("<i", payload, 4, version)
    struct.pack_into("<4H", payload, 8, width, height, mip_count, 0)
    struct.pack_into("<H", payload, 26, pixel_format)
    return bytes(payload)


def _triangle_scene(material, *, uvs=None):
    return geometry_scene.GeometryScene(
        source_path="material.gltf",
        models=[geometry_scene.GeometryModel(
            name="Triangle",
            points=[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
            faces=[geometry_scene.GeometryFace(
                [0, 1, 2],
                material.name,
                uvs if uvs is not None else [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            )],
        )],
        materials=[material],
    )


def _tetrahedron_scene(material):
    points = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ]
    triangles = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    return geometry_scene.GeometryScene(
        source_path="material_tetra.gltf",
        models=[geometry_scene.GeometryModel(
            "Tetra",
            points,
            [
                geometry_scene.GeometryFace(
                    list(face), material.name, [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
                )
                for face in triangles
            ],
        )],
        materials=[material],
    )


def _component_and_face(scene):
    component = mesh_topology.analyze_geometry_scene(scene).components[0]
    return component, component.faces[0]


class GltfMaterialTests(unittest.TestCase):
    def test_reads_dimensions_from_minimal_dtx_header(self):
        info = gltf_materials.parse_dtx_texture_info(
            _dtx_header(512, 128, version=-4, mip_count=3, pixel_format=6),
            source="Synthetic.dtx",
        )

        self.assertEqual(info.source, "Synthetic.dtx")
        self.assertEqual(info.version, -4)
        self.assertEqual((info.width, info.height), (512, 128))
        self.assertEqual(info.mip_count, 3)
        self.assertEqual(info.pixel_format, 6)
        with self.assertRaisesRegex(ValueError, "expected at least 164"):
            gltf_materials.parse_dtx_texture_info(b"short")

    def test_material_resolution_precedence_is_deterministic(self):
        materials = [
            geometry_scene.GeometryMaterial(
                "Extras",
                EXTRAS_TEXTURE,
                extras={"gltf_extras": {"MM9_texture": EXTRAS_TEXTURE}},
            ),
            geometry_scene.GeometryMaterial(
                "Mapped",
                "",
                extras={"source_name": "OriginalMapped"},
            ),
            geometry_scene.GeometryMaterial(NAMED_TEXTURE, NAMED_TEXTURE),
            geometry_scene.GeometryMaterial("Direct", DIRECT_TEXTURE),
        ]
        scene = geometry_scene.GeometryScene(source_path="precedence.gltf", materials=materials)
        sizes = {
            EXTRAS_TEXTURE: (32, 32),
            MAP_TEXTURE: (64, 64),
            NAMED_TEXTURE: (128, 128),
            DIRECT_TEXTURE: (256, 256),
            FALLBACK_TEXTURE: (16, 16),
        }
        converter = gltf_materials.MaterialUvConverter(
            scene,
            material_map={"Extras": MAP_TEXTURE, "OriginalMapped": MAP_TEXTURE},
            fallback_texture=FALLBACK_TEXTURE,
            texture_dimensions=sizes,
        )

        extras, _ = converter.resolve_material_texture("Extras")
        mapped, _ = converter.resolve_material_texture("Mapped")
        named, _ = converter.resolve_material_texture(NAMED_TEXTURE)
        direct, _ = converter.resolve_material_texture("Direct")
        fallback, fallback_diagnostics = converter.resolve_material_texture("Unknown")

        self.assertEqual((extras.texture_name, extras.resolution_source), (EXTRAS_TEXTURE, "extras"))
        self.assertEqual((mapped.texture_name, mapped.resolution_source), (MAP_TEXTURE, "material_map"))
        self.assertEqual((named.texture_name, named.resolution_source), (NAMED_TEXTURE, "material_name"))
        self.assertEqual((direct.texture_name, direct.resolution_source), (DIRECT_TEXTURE, "scene_material"))
        self.assertEqual((fallback.texture_name, fallback.resolution_source), (FALLBACK_TEXTURE, "fallback"))
        self.assertIn("fallback_material_texture", {item.code for item in fallback_diagnostics})

    def test_face_uvs_use_dtx_dimensions_and_report_opq_method_counts(self):
        material = geometry_scene.GeometryMaterial(
            "Material",
            DIRECT_TEXTURE,
            extras={
                "source_index": 7,
                "source_name": "AuthoredMaterial",
                "ignored_pbr_fields": ["pbrMetallicRoughness", "doubleSided"],
            },
        )
        scene = _triangle_scene(material)
        component, face = _component_and_face(scene)
        converter = gltf_materials.MaterialUvConverter(
            scene,
            texture_bytes_lookup=lambda _name: _dtx_header(256, 64),
        )

        resolved, diagnostics = converter.resolve_face(component, face)
        report = converter.report()

        self.assertEqual(diagnostics, ())
        self.assertIsNotNone(resolved)
        self.assertEqual((resolved.texture.width, resolved.texture.height), (256.0, 64.0))
        self.assertEqual(resolved.texture.dimension_source, "dtx_header")
        self.assertEqual(resolved.uv_method, "dedit_uv_to_opq")
        expected = uv_projection.dedit_uv_to_opq(
            [component.points[index] for index in face.vertex_indices],
            [value for value in face.uv_coords if value is not None],
            tex_width=256,
            tex_height=64,
        )
        self.assertEqual((resolved.uv_o, resolved.uv_p, resolved.uv_q), expected)
        summary = report.materials[0]
        self.assertEqual(summary.source_material_index, 7)
        self.assertEqual(summary.source_material_name, "AuthoredMaterial")
        self.assertEqual(summary.surface_count, 1)
        self.assertEqual(summary.uv_method_counts, {"dedit_uv_to_opq": 1})
        self.assertIn("ignored_gltf_pbr_fields", {item.code for item in summary.notes})

    def test_invalid_dtx_header_is_a_blocker_even_with_dimension_fallback(self):
        scene = _triangle_scene(geometry_scene.GeometryMaterial("Material", DIRECT_TEXTURE))
        component, face = _component_and_face(scene)
        converter = gltf_materials.MaterialUvConverter(
            scene,
            texture_bytes_lookup=lambda _name: b"not-a-dtx",
            fallback_texture_size=(128, 128),
        )

        resolved, diagnostics = converter.resolve_face(component, face)

        self.assertIsNone(resolved)
        self.assertIn("invalid_dtx_header", {item.code for item in diagnostics})
        self.assertEqual(converter.report().status, "blocked")

    def test_generated_helper_surface_gets_projection_and_texture_flags(self):
        scene = geometry_scene.GeometryScene(source_path="generated.gltf")
        converter = gltf_materials.MaterialUvConverter(
            scene,
            texture_dimensions={HELPER_TEXTURE: (128, 128)},
        )

        resolved, diagnostics = converter.resolve_generated_surface(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            HELPER_TEXTURE,
            resolution_source="slab_side_option",
            component_id="component",
            source_face_index=3,
        )

        self.assertEqual(diagnostics, ())
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.uv_method, gltf_materials.WORLD_ALIGNED_PROJECTION)
        self.assertEqual(resolved.texture_flags, 1)
        report = converter.report()
        self.assertEqual(report.generated_surface_count, 1)
        self.assertEqual(report.generated_uv_method_counts, {"world_aligned": 1})

    def test_brush_planner_uses_phase5_dtx_lookup_and_exports_material_report(self):
        material = geometry_scene.GeometryMaterial("Material", DIRECT_TEXTURE)
        scene = _tetrahedron_scene(material)

        plan = gltf_brushes.build_gltf_brush_plan(
            scene,
            mesh_topology.analyze_geometry_scene(scene),
            texture_bytes_lookup=lambda _name: _dtx_header(320, 80),
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.material_uv_report.status, "ready")
        summary = plan.material_uv_report.materials[0]
        self.assertEqual((summary.texture_width, summary.texture_height), (320.0, 80.0))
        self.assertEqual(summary.dimension_source, "dtx_header")
        self.assertEqual(summary.uv_method_counts, {"dedit_uv_to_opq": 4})
        self.assertEqual(plan.to_dict()["materials"][0]["resolved_texture_name"], DIRECT_TEXTURE)


if __name__ == "__main__":
    unittest.main()
