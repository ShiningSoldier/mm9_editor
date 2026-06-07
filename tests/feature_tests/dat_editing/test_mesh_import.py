import os
import json
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import bsp
from core import rezmgr as mm9_rezmgr
from core import project as P
from core import project_io
from features.dat_editing import bsp_compile
from features.dat_editing import geometry_scene
from features.dat_editing import export_roundtrip, mesh_import
from mm9_patcher.mm9_patch import Header, World
from tests.core_tests.test_game_resources import write_minimal_rez


DATA_ROOT = os.path.join(ROOT, "mm9_data")


class MeshImportTests(unittest.TestCase):
    def load_bootcamp(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        with open(path, "rb") as f:
            data = f.read()
        return path, data, bsp.parse(data), World.load(path)

    def exported_one_model(self, tmp):
        path, data, bsp_world, _world = self.load_bootcamp()
        model = next((m for m in bsp_world.world_models if m.polygons and m.points), None)
        if model is None:
            self.skipTest("BOOTCAMP has no importable BSP model")
        result = export_roundtrip.export_roundtrip(
            bsp_world,
            data,
            tmp,
            source_path=path,
            base_name="MeshImportSource",
            selected_model_names=[model.name],
        )
        return path, data, bsp_world, model, result

    def test_builds_preview_mesh_from_exported_obj_and_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, _data, target_bsp, source_model, exported = self.exported_one_model(tmp)
            plan = mesh_import.build_mesh_import_plan(
                target_bsp,
                exported.obj_path,
                exported.meta_path,
                new_name="ImportedMeshPreview",
            )

            self.assertEqual(plan.new_name, "ImportedMeshPreview")
            self.assertEqual(len(plan.models), 1)
            imported = plan.models[0].mesh
            self.assertEqual(imported.name, "ImportedMeshPreview")
            self.assertEqual(len(imported.polygons), len(source_model.polygons))
            self.assertEqual(imported.texture_names, source_model.texture_names)
            self.assertIsNone(imported.raw_start)
            self.assertEqual(plan.collision_mode, "none")

            preview = mesh_import.build_preview_bsp(target_bsp, [plan])
            self.assertIsNotNone(preview.model_by_name("ImportedMeshPreview"))

    def test_imported_obj_uvs_are_converted_to_bsp_surface_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, "uv_quad.obj")
            meta_path = os.path.join(tmp, "uv_quad.datmeta.json")
            with open(obj_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "o UVMesh\n"
                    "v 0 0 0\n"
                    "v 128 0 0\n"
                    "v 128 0 128\n"
                    "v 0 0 128\n"
                    "vt 0 0\n"
                    "vt 1 0\n"
                    "vt 1 1\n"
                    "vt 0 1\n"
                    "usemtl Floor\n"
                    "f 1/1 2/2 3/3 4/4\n"
                )
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({
                    "kind": "mm9_dat_geometry_roundtrip",
                    "source": {},
                    "coordinate_system": {
                        "export_to_dat_matrix": [
                            [1.0, 0.0, 0.0, 0.0],
                            [0.0, 1.0, 0.0, 0.0],
                            [0.0, 0.0, 1.0, 0.0],
                            [0.0, 0.0, 0.0, 1.0],
                        ],
                    },
                    "materials": [
                        {"material_name": "Floor", "texture_name": "TEXTURES\\World\\Floor.dtx"},
                    ],
                    "models": [{"name": "Template"}],
                }, f)

            plan = mesh_import.build_mesh_import_plan(
                bsp.BspWorld(version=66, world_info="test"),
                obj_path,
                meta_path,
                new_name="ImportedUVQuad",
            )

            mesh = plan.models[0].mesh
            surface = mesh.surfaces[mesh.polygons[0].surface_index]
            self.assertEqual(getattr(surface, "mm9_uv_method", ""), "dedit_opq")
            expected_uvs = {
                0: (0.0, 0.0),
                1: (1.0, 0.0),
                2: (1.0, 1.0),
                3: (0.0, 1.0),
            }
            for point_index, expected_uv in expected_uvs.items():
                actual_uv = surface.compute_uv(mesh.points[point_index])
                self.assertAlmostEqual(actual_uv[0], expected_uv[0], places=4)
                self.assertAlmostEqual(actual_uv[1], expected_uv[1], places=4)

    def test_mesh_import_can_generate_box_collision_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            _path, _data, target_bsp, _source_model, exported = self.exported_one_model(tmp)
            plan = mesh_import.build_mesh_import_plan(
                target_bsp,
                exported.obj_path,
                exported.meta_path,
                new_name="ImportedMeshPreview",
                collision_mode="box_approx",
                collision_thickness=16.0,
            )

            self.assertEqual(plan.collision_mode, "box_approx")
            self.assertEqual(len(plan.models), 2)
            visible, collision = plan.models
            self.assertEqual(visible.role, "visible")
            self.assertEqual(collision.role, "collision_box")
            self.assertEqual(collision.name, "ImportedMeshPreview_Collision")
            self.assertEqual(len(collision.mesh.polygons), 6)
            horizontal_sizes = [
                collision.mesh.max_box[0] - collision.mesh.min_box[0],
                collision.mesh.max_box[2] - collision.mesh.min_box[2],
            ]
            self.assertAlmostEqual(min(horizontal_sizes), 16.0, places=3)

    def test_mesh_import_can_generate_per_face_collision_slabs(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, "slab_quad.obj")
            meta_path = os.path.join(tmp, "slab_quad.datmeta.json")
            self._write_quad_obj(obj_path, object_name="SlabMesh")
            self._write_basic_meta(meta_path)

            plan = mesh_import.build_mesh_import_plan(
                bsp.BspWorld(version=66, world_info="test"),
                obj_path,
                meta_path,
                new_name="ImportedSlabMesh",
                collision_mode="face_slabs",
                collision_thickness=12.0,
            )

            self.assertEqual(plan.collision_mode, "face_slabs")
            self.assertEqual(mesh_import.role_counts(plan.models), {"visible": 1, "collision_slab": 1})
            visible, collision = plan.models
            self.assertEqual(visible.role, "visible")
            self.assertEqual(collision.role, "collision_slab")
            self.assertEqual(collision.name, "ImportedSlabMesh_CollisionFace1")
            self.assertEqual(collision.mesh.texture_names, [mesh_import.COLLISION_TEXTURE])
            self.assertAlmostEqual(collision.mesh.max_box[1] - collision.mesh.min_box[1], 12.0, places=3)

    def test_explicit_collision_obj_node_imports_as_hidden_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, "explicit_collision.obj")
            meta_path = os.path.join(tmp, "explicit_collision.datmeta.json")
            self._write_quad_obj(obj_path, object_name="CollisionHull")
            self._write_basic_meta(meta_path)

            plan = mesh_import.build_mesh_import_plan(
                bsp.BspWorld(version=66, world_info="test"),
                obj_path,
                meta_path,
                new_name="ImportedHull",
                collision_mode="none",
            )

            self.assertEqual(len(plan.models), 1)
            collision = plan.models[0]
            self.assertEqual(collision.role, "collision_explicit")
            self.assertEqual(collision.name, "ImportedHull_Collision")
            self.assertTrue(mesh_import.is_collision_model(collision))
            self.assertEqual(collision.mesh.texture_names, [mesh_import.COLLISION_TEXTURE])
            self.assertIn("Roles: collision_explicit=1", mesh_import.import_summary(plan))

    def test_collision_metadata_marks_model_as_collision_only(self):
        model = geometry_scene.GeometryModel(
            name="Blocker",
            extras={"role": "collision"},
        )

        self.assertEqual(mesh_import._parsed_model_role(model), "collision_only")

    def test_compiled_mesh_record_roundtrips_through_bsp_parser(self):
        model = bsp.WorldModelMesh(
            name="CompiledQuad",
            min_box=(0.0, 0.0, 0.0),
            max_box=(10.0, 0.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 0.0, 10.0)],
            polygons=[bsp.Polygon([0, 1, 2, 3], 0, 0)],
            texture_names=["TEXTURES\\World\\Floor.dtx"],
            surfaces=[bsp.Surface((0, 0, 0), (1, 0, 0), (0, 0, 1), 0, 0, 0)],
        )

        record = bsp_compile.compile_world_model_record(model)
        patched = bsp_compile.patch_next_world_item(record, 1234)

        self.assertEqual(int.from_bytes(patched[:4], "little"), 1234)
        self.assertGreater(len(patched), 36)

    def test_minimal_bsp_compiler_handles_stage2_geometry_fixtures(self):
        fixtures = [
            self._fixture_concave_ngon(),
            self._fixture_triangulated_wall(),
            self._fixture_ramp(),
            self._fixture_stairs(),
            self._fixture_tiny_coordinates(),
            self._fixture_large_coordinates(),
        ]

        for model in fixtures:
            with self.subTest(model=model.name):
                diagnostics = bsp_compile.analyze_model(model)
                record = bsp_compile.compile_world_model_record(model)

                self.assertGreater(len(record.raw_bytes), 128)
                self.assertEqual(diagnostics.min_box, self._bounds(model.points)[0])
                self.assertEqual(diagnostics.max_box, self._bounds(model.points)[1])
                self.assertEqual(diagnostics.polygon_count, len(model.polygons))
                self.assertEqual(len(diagnostics.polygons), len(model.polygons))
                for polygon_diag, polygon in zip(diagnostics.polygons, model.polygons):
                    expected_center = self._polygon_center(model.points, polygon.vertex_indices)
                    for actual, expected in zip(polygon_diag.center, expected_center):
                        self.assertAlmostEqual(actual, expected, places=5)
                    self.assertAlmostEqual(self._length(polygon_diag.plane_normal), 1.0, places=5)
                    self.assertEqual(polygon_diag.surface_index, polygon.surface_index)
                    self.assertEqual(polygon_diag.texture_index, model.surfaces[polygon.surface_index].texture_index)

    def test_mesh_import_rejects_degenerate_polygons_before_preview_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, "degenerate.obj")
            meta_path = os.path.join(tmp, "degenerate.datmeta.json")
            with open(obj_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "o BadFace\n"
                    "v 0 0 0\n"
                    "v 10 0 0\n"
                    "v 20 0 0\n"
                    "usemtl Floor\n"
                    "f 1 2 3\n"
                )
            self._write_basic_meta(meta_path)

            with self.assertRaisesRegex(ValueError, "degenerate.*plane normal"):
                mesh_import.build_mesh_import_plan(
                    bsp.BspWorld(version=66, world_info="test"),
                    obj_path,
                    meta_path,
                    new_name="BadFaceImport",
                )

    def test_mesh_import_reports_bad_uv_text_before_preview_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, "bad_uv.obj")
            meta_path = os.path.join(tmp, "bad_uv.datmeta.json")
            with open(obj_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "o BadUV\n"
                    "v 0 0 0\n"
                    "v 128 0 0\n"
                    "v 0 0 128\n"
                    "vt nan 0\n"
                    "vt 1 0\n"
                    "vt 0 1\n"
                    "usemtl Floor\n"
                    "f 1/1 2/2 3/3\n"
                )
            self._write_basic_meta(meta_path)

            with self.assertRaisesRegex(ValueError, "texture coordinates must be finite"):
                mesh_import.build_mesh_import_plan(
                    bsp.BspWorld(version=66, world_info="test"),
                    obj_path,
                    meta_path,
                    new_name="BadUVImport",
                )

    def test_box_collision_for_flat_import_has_valid_thickness(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, "flat_wall.obj")
            meta_path = os.path.join(tmp, "flat_wall.datmeta.json")
            with open(obj_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "o FlatWall\n"
                    "v 0 0 0\n"
                    "v 0 128 0\n"
                    "v 0 0 128\n"
                    "usemtl Wall\n"
                    "f 1 2 3\n"
                )
            self._write_basic_meta(meta_path)

            plan = mesh_import.build_mesh_import_plan(
                bsp.BspWorld(version=66, world_info="test"),
                obj_path,
                meta_path,
                new_name="FlatWallImport",
                collision_mode="box_approx",
            )
            collision = next(item for item in plan.models if item.role == "collision_box")

            self.assertGreater(collision.mesh.max_box[0] - collision.mesh.min_box[0], 0.0)
            bsp_compile.analyze_model(collision.mesh)

    def test_compiler_reports_bad_source_opq(self):
        model = self._fixture_ramp()
        model.surfaces[0].uv_p = (0.0, 0.0, 0.0)

        with self.assertRaisesRegex(ValueError, "degenerate uv_p"):
            bsp_compile.analyze_model(model)

    def test_project_op_materializes_controller_and_preview_bsp(self):
        _path, data, original_bsp, _world = self.load_bootcamp()
        original_model = next((m for m in original_bsp.world_models if m.polygons and m.points), None)
        if original_model is None:
            self.skipTest("BOOTCAMP has no importable BSP model")
        with tempfile.TemporaryDirectory() as tmp:
            exported = export_roundtrip.export_roundtrip(
                original_bsp,
                data,
                tmp,
                base_name="MeshImportSource",
                selected_model_names=[original_model.name],
            )
            source_rez = os.path.join(tmp, "game", "data", "WORLDS.REZ")
            write_minimal_rez(source_rez, {"WORLDS/BOOTCAMP": data})

            project = P.Project(work_dir=os.path.join(tmp, "out"))
            level = P.LevelEdit(
                path=f"{source_rez}::WORLDS/BOOTCAMP",
                source_kind=P.SOURCE_REZ,
                rez_path=source_rez,
                rez_vpath="WORLDS/BOOTCAMP",
                world=World.load(os.path.join(DATA_ROOT, "WORLDS", "BOOTCAMP.DAT")),
            )
            level._raw_bytes = data
            project.levels.append(level)
            baseline_count = len(level.materialize().objects)
            level.append_op(P.ImportMeshBspOp(
                obj_path=exported.obj_path,
                meta_path=exported.meta_path,
                new_name="ImportedMeshPreview",
                collision_mode="box_approx",
            ))

            materialized = level.materialize()
            self.assertEqual(len(materialized.objects), baseline_count + 2)
            self.assertEqual(materialized.objects[-2].get("Name"), "ImportedMeshPreview")
            self.assertEqual(materialized.objects[-2].type_str, "WorldObject")
            self.assertEqual(materialized.objects[-1].get("Name"), "ImportedMeshPreview_Collision")
            self.assertEqual(materialized.objects[-1].type_str, "InvisibleBrush")
            self.assertEqual(materialized.objects[-1].get("Visible"), 0)

            preview = level.preview_bsp()
            self.assertIsNotNone(preview.model_by_name("ImportedMeshPreview"))
            self.assertIsNotNone(preview.model_by_name("ImportedMeshPreview_Collision"))
            self.assertEqual(len(preview.world_models), len(original_bsp.world_models) + 2)

            plan = project.save_plan()
            self.assertEqual(plan.dats[0].stats()["mesh_imports"], 1)
            self.assertEqual(plan.dats[0].stats()["mesh_bsp_models"], 2)
            self.assertTrue(any("experimental minimal BSP compiler" in warning for warning in plan.dats[0].validation_warnings))
            project.execute(plan)
            manifest_path = os.path.join(tmp, "out", plan.batch_id, "manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            mesh_details = manifest["dats"][0]["geometry_edits"]["mesh_imports"][0]
            self.assertEqual(mesh_details["new_name"], "ImportedMeshPreview")
            self.assertEqual(mesh_details["visible_model_count"], 1)
            self.assertEqual(mesh_details["collision_model_count"], 1)
            self.assertEqual(mesh_details["role_counts"], {"visible": 1, "collision_box": 1})
            self.assertEqual(mesh_details["models"][0]["name"], "ImportedMeshPreview")
            self.assertIn("uv_method_counts", mesh_details["models"][0])
            self.assertEqual(mesh_details["models"][1]["role"], "collision_box")

            output_rez = os.path.join(tmp, "out", plan.batch_id, "data", "WORLDS.REZ")
            with mm9_rezmgr.RezReader(output_rez) as reader:
                changed = reader.extract_to_bytes("WORLDS/BOOTCAMP")

        changed_world = self._world_from_bytes(changed)
        changed_bsp = bsp.parse(changed)
        imported = changed_bsp.model_by_name("ImportedMeshPreview")
        collision = changed_bsp.model_by_name("ImportedMeshPreview_Collision")
        self.assertIsNotNone(imported)
        self.assertIsNotNone(collision)
        self.assertTrue(any(obj.get("Name") == "ImportedMeshPreview" for obj in changed_world.objects))
        self.assertTrue(any(obj.get("Name") == "ImportedMeshPreview_Collision"
                            and obj.type_str == "InvisibleBrush"
                            for obj in changed_world.objects))
        self.assertEqual(len(changed_bsp.world_models), len(original_bsp.world_models) + 2)
        self.assertEqual(len(imported.polygons), len(original_model.polygons))

    def test_collision_controller_validation_requires_hidden_invisiblebrush(self):
        model = self._make_model("ValidationMesh", [
            (0.0, 0.0, 0.0),
            (64.0, 0.0, 0.0),
            (0.0, 0.0, 64.0),
        ], [[0, 1, 2]])
        plan = mesh_import.MeshBspImportPlan(
            obj_path="C:/tmp/source.obj",
            meta_path="",
            new_name="ValidationMesh",
            target_pos=(0.0, 0.0, 0.0),
            target_yaw=0.0,
            original_center=(0.0, 0.0, 0.0),
            collision_mode="none",
            models=[
                mesh_import.ImportedMeshModel(
                    name="ValidationMesh_Collision",
                    mesh=model,
                    role="collision_explicit",
                )
            ],
        )
        bad_object = WorldObjectStub("ValidationMesh_Collision", "WorldObject", 1)

        warnings = mesh_import.validate_collision_controllers([plan], [bad_object])

        self.assertTrue(any("hidden InvisibleBrush" in warning for warning in warnings))

    def test_dat_write_geometry_risk_report_summarizes_mesh_imports(self):
        visible = self._make_model("RiskVisible", [
            (0.0, 0.0, 0.0),
            (64.0, 0.0, 0.0),
            (0.0, 0.0, 64.0),
        ], [[0, 1, 2]])
        setattr(visible.polygons[0], "mm9_source_face", {"source_format": "gltf"})
        collision = self._make_model("RiskVisible_Collision", [
            (0.0, 0.0, 0.0),
            (64.0, 0.0, 0.0),
            (0.0, 0.0, 64.0),
        ], [[0, 1, 2]])
        plan = mesh_import.MeshBspImportPlan(
            obj_path="C:/tmp/generic.gltf",
            meta_path="",
            new_name="RiskVisible",
            target_pos=(0.0, 0.0, 0.0),
            target_yaw=0.0,
            original_center=(0.0, 0.0, 0.0),
            collision_mode="box_approx",
            models=[
                mesh_import.ImportedMeshModel(
                    name="RiskVisible",
                    mesh=visible,
                    role="visible",
                ),
                mesh_import.ImportedMeshModel(
                    name="RiskVisible_Collision",
                    mesh=collision,
                    role="collision_box",
                ),
            ],
            source_format="gltf",
            metadata_source="missing",
        )
        dat_write = P.DatWrite(
            source_path="C:/tmp/LEVEL.DAT",
            output_path="C:/tmp/out/WORLDS.REZ",
            ops_summary=[],
            materialized=World(Header(66, 44, 44, tuple([0] * 8)), b"", [], b""),
            mesh_imports=[plan],
        )

        report = "\n".join(dat_write.geometry_risk_report())

        self.assertIn("1 visible model(s), 1 collision/helper model(s)", report)
        self.assertIn("mesh roles: collision_box=1, visible=1", report)
        self.assertIn("mesh UV methods: fixture=2", report)
        self.assertIn("mesh source formats: gltf=1", report)
        self.assertIn("generic glTF lacks MM9 DAT metadata", report)
        self.assertIn("cannot be treated as a full-level DAT round trip", report)

    def test_project_io_roundtrips_mesh_import_op(self):
        op = P.ImportMeshBspOp(
            obj_path="C:/tmp/level_geometry.obj",
            meta_path="C:/tmp/level_geometry.datmeta.json",
            new_name="ImportedMeshPreview",
            target_pos=(1.0, 2.0, 3.0),
            target_yaw=0.25,
            collision_mode="box_approx",
            collision_thickness=24.0,
            collision_segment_length=128.0,
        )

        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertIsInstance(restored, P.ImportMeshBspOp)
        self.assertEqual(restored.obj_path, op.obj_path)
        self.assertEqual(restored.meta_path, op.meta_path)
        self.assertEqual(restored.new_name, op.new_name)
        self.assertEqual(restored.target_pos, (1.0, 2.0, 3.0))
        self.assertEqual(restored.target_yaw, 0.25)
        self.assertEqual(restored.collision_mode, "box_approx")
        self.assertEqual(restored.collision_thickness, 24.0)
        self.assertEqual(restored.collision_segment_length, 128.0)

    def _world_from_bytes(self, data: bytes) -> World:
        fd, path = tempfile.mkstemp(suffix=".DAT")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(data)
            return World.load(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _write_basic_meta(self, path, models=None):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "kind": "mm9_dat_geometry_roundtrip",
                "source": {},
                "coordinate_system": {
                    "export_to_dat_matrix": [
                        [1.0, 0.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0, 0.0],
                        [0.0, 0.0, 1.0, 0.0],
                        [0.0, 0.0, 0.0, 1.0],
                    ],
                },
                "materials": [
                    {"material_name": "Floor", "texture_name": "TEXTURES\\World\\Floor.dtx"},
                ],
                "models": models or [{"name": "Template"}],
            }, f)

    def _write_quad_obj(self, path, object_name="Quad"):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                f"o {object_name}\n"
                "v 0 0 0\n"
                "v 128 0 0\n"
                "v 128 0 128\n"
                "v 0 0 128\n"
                "vt 0 0\n"
                "vt 1 0\n"
                "vt 1 1\n"
                "vt 0 1\n"
                "usemtl Floor\n"
                "f 1/1 2/2 3/3 4/4\n"
            )

    def _fixture_concave_ngon(self):
        points = [
            (0.0, 0.0, 0.0),
            (96.0, 0.0, 0.0),
            (96.0, 0.0, 96.0),
            (48.0, 0.0, 48.0),
            (0.0, 0.0, 96.0),
        ]
        return self._make_model("ConcaveNgon", points, [[0, 1, 2, 3, 4]])

    def _fixture_triangulated_wall(self):
        points = [
            (0.0, 0.0, 0.0),
            (0.0, 96.0, 0.0),
            (0.0, 96.0, 128.0),
            (0.0, 0.0, 128.0),
        ]
        return self._make_model("TriangulatedVerticalWall", points, [[0, 1, 2], [0, 2, 3]])

    def _fixture_ramp(self):
        points = [
            (0.0, 0.0, 0.0),
            (128.0, 0.0, 0.0),
            (128.0, 48.0, 128.0),
            (0.0, 48.0, 128.0),
        ]
        return self._make_model("Ramp", points, [[0, 1, 2, 3]])

    def _fixture_stairs(self):
        points = [
            (0.0, 0.0, 0.0), (64.0, 0.0, 0.0), (64.0, 0.0, 32.0), (0.0, 0.0, 32.0),
            (0.0, 16.0, 32.0), (64.0, 16.0, 32.0), (64.0, 16.0, 64.0), (0.0, 16.0, 64.0),
            (0.0, 32.0, 64.0), (64.0, 32.0, 64.0), (64.0, 32.0, 96.0), (0.0, 32.0, 96.0),
        ]
        return self._make_model("Stairs", points, [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]])

    def _fixture_tiny_coordinates(self):
        points = [
            (0.001, 0.0, 0.001),
            (0.101, 0.0, 0.001),
            (0.001, 0.0, 0.101),
        ]
        return self._make_model("TinyCoordinates", points, [[0, 1, 2]])

    def _fixture_large_coordinates(self):
        base = 250000.0
        points = [
            (base, 10000.0, -base),
            (base + 512.0, 10000.0, -base),
            (base + 512.0, 10128.0, -base + 512.0),
            (base, 10128.0, -base + 512.0),
        ]
        return self._make_model("LargeCoordinates", points, [[0, 1, 2, 3]])

    def _make_model(self, name, points, polygons):
        min_box, max_box = self._bounds(points)
        surfaces = []
        poly_records = []
        for index, indices in enumerate(polygons):
            surfaces.append(bsp.Surface(
                uv_o=points[indices[0]],
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 0.0, 1.0),
                texture_index=0,
                flags=0,
                texture_flags=0,
            ))
            setattr(surfaces[-1], "mm9_uv_method", "fixture")
            poly_records.append(bsp.Polygon(indices, index, 0))
        return bsp.WorldModelMesh(
            name=name,
            min_box=min_box,
            max_box=max_box,
            translation=(0.0, 0.0, 0.0),
            points=points,
            polygons=poly_records,
            texture_names=["TEXTURES\\World\\Floor.dtx"],
            surfaces=surfaces,
        )

    def _bounds(self, points):
        return (
            (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
            (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
        )

    def _polygon_center(self, points, indices):
        count = float(len(indices))
        return (
            sum(points[index][0] for index in indices) / count,
            sum(points[index][1] for index in indices) / count,
            sum(points[index][2] for index in indices) / count,
        )

    def _length(self, value):
        return (value[0] * value[0] + value[1] * value[1] + value[2] * value[2]) ** 0.5


class WorldObjectStub:
    def __init__(self, name, type_str, visible):
        self.type_str = type_str
        self._values = {
            "Name": name,
            "Visible": visible,
        }

    def get(self, name, default=None):
        return self._values.get(name, default)


if __name__ == "__main__":
    unittest.main()
