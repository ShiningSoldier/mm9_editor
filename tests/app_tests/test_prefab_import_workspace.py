import os
import tempfile
import types
import unittest

from app import editor as editor_app
from features.prefabs import behavioral as prefab_behavioral
from features.prefabs.inspector import (
    PrefabInspection,
    PrefabModelInfo,
    PrefabObjectInfo,
)
from features.prefabs.graph import (
    PrefabAnalysis,
    PrefabDiagnostic,
    PrefabGraph,
    PrefabReference,
    DiagnosticSeverity,
    SupportState,
)
from ui.prefab_import_workspace import (
    PrefabImportRequest,
    available_placement_anchors,
    build_import_request,
    discover_prefab_files,
    format_workspace_summary,
    required_binding_targets,
)
from mm9_patcher import mm9_patch as patcher
from tests.feature_tests.prefabs._fixtures import write_minimal_dat


def _model(name="Brush1"):
    return PrefabModelInfo(
        index=0,
        name=name,
        role="geometry",
        polygon_count=6,
        point_count=8,
        texture_count=1,
        min_box=(0.0, 0.0, 0.0),
        max_box=(16.0, 32.0, 16.0),
    )


class PrefabImportWorkspaceSemanticsTests(unittest.TestCase):
    def test_discovers_nested_ed_and_dat_prefabs_with_relative_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Furniture"))
            os.makedirs(os.path.join(tmp, "Doors"))
            for path in (
                os.path.join(tmp, "Furniture", "Chair.ed"),
                os.path.join(tmp, "Doors", "A1_Door.DAT"),
                os.path.join(tmp, "notes.txt"),
            ):
                with open(path, "wb") as handle:
                    handle.write(b"fixture")

            all_files = discover_prefab_files(tmp)
            filtered = discover_prefab_files(tmp, "furniture/chair")

        self.assertEqual(len(all_files), 2)
        self.assertEqual([os.path.basename(path) for path in all_files], ["A1_Door.DAT", "Chair.ed"])
        self.assertEqual([os.path.basename(path) for path in filtered], ["Chair.ed"])

    def test_build_request_validates_and_types_all_workspace_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Chair.ed")
            with open(path, "wb") as handle:
                handle.write(b"fixture")

            request = build_import_request(
                prefab_path=path,
                new_name="ImportedChair_2",
                collision_mode="box_approx",
                collision_thickness="12",
                collision_segment_length="256",
                placement_anchor="bottom_center",
                browser_root=tmp,
            )

        self.assertEqual(request.new_name, "ImportedChair_2")
        self.assertEqual(request.collision_thickness, 12.0)
        self.assertEqual(request.collision_segment_length, 256.0)

    def test_build_request_rejects_names_that_backend_would_silently_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Chair.ed")
            with open(path, "wb") as handle:
                handle.write(b"fixture")
            with self.assertRaisesRegex(ValueError, "letters, digits, and underscores"):
                build_import_request(
                    prefab_path=path,
                    new_name="Imported Chair",
                    collision_mode="none",
                    collision_thickness="8",
                    collision_segment_length="512",
                    placement_anchor="bottom_center",
                    browser_root=tmp,
                )

    def test_request_preserves_explicit_behavioral_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Barrel.ed")
            with open(path, "wb") as handle:
                handle.write(b"fixture")
            request = build_import_request(
                prefab_path=path,
                new_name="ImportedBarrel",
                collision_mode="none",
                collision_thickness="8",
                collision_segment_length="512",
                placement_anchor="original_origin",
                browser_root=tmp,
                import_mode="behavioral",
                external_bindings={"DoorPortal": "<omit>"},
            )
        self.assertEqual(request.import_mode, "behavioral")
        self.assertEqual(request.external_bindings, {"DoorPortal": "<omit>"})

    def test_binding_targets_include_external_objects_and_all_portal_names(self):
        graph = PrefabGraph(
            "source",
            "legacy_ed",
            1249,
            "hash",
            references=(
                PrefabReference(0, "TargetName1", "Local", "local", 1),
                PrefabReference(0, "TargetName2", "LevelTarget", "external"),
                PrefabReference(0, "PortalName", "DoorPortal", "local"),
            ),
        )
        analysis = PrefabAnalysis(
            graph,
            SupportState.STATIC_READY,
            SupportState.ACTION_REQUIRED,
        )

        self.assertEqual(
            required_binding_targets(analysis),
            (("DoorPortal", "portal"), ("LevelTarget", "object")),
        )

    def test_summary_and_anchor_options_expose_behavior_limit_inline(self):
        info = PrefabInspection(
            path=r"C:\PreFabs\Doors\A1_Door.ed",
            file_size=100,
            version=1249,
            object_data_pos=0,
            render_data_pos=0,
            object_count=2,
            model_count=1,
            objects=[
                PrefabObjectInfo(0, "Brush", "Brush1", position=(0.0, 0.0, 0.0)),
                PrefabObjectInfo(1, "RotatingDoor", "Door1", position=(8.0, 0.0, 0.0)),
            ],
            models=[_model()],
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(16.0, 32.0, 16.0),
            source_format="legacy_ed",
            has_authored_collision=True,
        )

        summary = format_workspace_summary(info)

        self.assertIn("DEdit ED source", summary)
        self.assertIn("RotatingDoor=1", summary)
        self.assertIn("Static geometry mode will not import", summary)
        self.assertIn("controller_pivot", available_placement_anchors(info))

        brush_only = PrefabInspection(
            path=r"C:\PreFabs\Furniture\Chair.ed",
            file_size=100,
            version=1249,
            object_data_pos=0,
            render_data_pos=0,
            object_count=1,
            model_count=1,
            objects=[PrefabObjectInfo(0, "Brush", "Brush1", position=(0.0, 0.0, 0.0))],
            models=[_model()],
            source_format="legacy_ed",
        )
        self.assertNotIn("controller_pivot", available_placement_anchors(brush_only))

    def test_summary_exposes_independent_static_and_behavioral_states(self):
        info = PrefabInspection(
            path=r"C:\PreFabs\Doors\A1_Door.ed",
            file_size=100,
            version=1249,
            object_data_pos=0,
            render_data_pos=0,
            object_count=1,
            model_count=1,
            objects=[PrefabObjectInfo(0, "RotatingDoor", "Door1")],
            models=[_model("Door1")],
        )
        graph = PrefabGraph("source", "legacy_ed", 1249, "hash")
        analysis = PrefabAnalysis(
            graph,
            SupportState.STATIC_READY,
            SupportState.BLOCKED,
            (PrefabDiagnostic(
                "behavioral_brush_ownership_unresolved",
                DiagnosticSeverity.BLOCKING,
                "Brush ownership is unresolved.",
            ),),
        )

        summary = format_workspace_summary(info, analysis)

        self.assertIn("Static import: Static ready", summary)
        self.assertIn("Full behavior: Blocked", summary)
        self.assertIn("Brush ownership is unresolved", summary)


class EditorPrefabWorkspaceIntegrationTests(unittest.TestCase):
    def test_editor_starts_resource_backed_prop_placement_from_same_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Bookcase.ed")
            with open(path, "wb") as handle:
                handle.write(b"source")
            request = PrefabImportRequest(
                prefab_path=path,
                new_name="ImportedBookcase",
                collision_mode="none",
                collision_thickness=8.0,
                collision_segment_length=512.0,
                placement_anchor="bottom_center",
                browser_root=tmp,
                import_mode="resource",
                resource_candidate_id="candidate",
                resource_class="Prop",
                resource_model=r"models\props\bookcase02ew.abc",
                resource_skins=(r"skins\props\bookcase02.dtx",),
            )
            template = patcher.WorldObject("Prop", [
                patcher.Property("Name", 0, 0, "Bookcase57"),
                patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
                patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
                patcher.Property("Filename", 0, 0, r"models\props\bookcase02ew.abc"),
                patcher.Property("Skin", 0, 0, r"skins\props\bookcase02.dtx"),
                patcher.Property("MoveToFloor", 5, 0, 1),
            ])

            class FakeLevel:
                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[])

                def preview_bsp(self):
                    return self.get_bsp()

                def editor_materialize(self):
                    return types.SimpleNamespace(objects=[])

            class FakeWorkspace:
                @staticmethod
                def ask(_parent, **kwargs):
                    kwargs["validate_request"](request)
                    return request

            class FakeView:
                def __init__(self):
                    self.values = []

                def set_place_mode(self, value):
                    self.values.append(value)

            globals_ = editor_app.EditorApp.cmd_import_static_prefab_bsp.__globals__
            old_workspace = globals_.get("PrefabImportWorkspace")
            try:
                globals_["PrefabImportWorkspace"] = FakeWorkspace
                app = object.__new__(editor_app.EditorApp)
                app.root = object()
                app.active = FakeLevel()
                app.catalog = {"classes": {}, "filenames": {}, "model_variants": {}}
                app.resources = types.SimpleNamespace(exists=lambda _path: True)
                app.project = types.SimpleNamespace(levels=[])
                app._find_template_for_filename = lambda _path, class_name=None: template
                app.cfg = types.SimpleNamespace(editor_dir=tmp)
                app.editor_settings = {}
                app.settings_path = ""
                app.view3d = FakeView()

                editor_app.EditorApp.cmd_import_static_prefab_bsp(app)
            finally:
                globals_["PrefabImportWorkspace"] = old_workspace

        self.assertEqual(app._pending_kind, "import_resource_prefab")
        self.assertEqual(app._pending_resource_model, request.resource_model)
        self.assertEqual(app._pending_resource_skins, request.resource_skins)
        self.assertEqual(app.view3d.values, [True])

    def test_editor_uses_one_workspace_result_to_start_viewport_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Chair.ed")
            with open(path, "wb") as handle:
                handle.write(b"fixture")
            request = PrefabImportRequest(
                prefab_path=path,
                new_name="ImportedChair",
                collision_mode="none",
                collision_thickness=12.0,
                collision_segment_length=256.0,
                placement_anchor="bottom_center",
                browser_root=tmp,
                import_mode="preview",
            )
            calls = {"workspace": 0, "validated": 0, "place_mode": []}
            target_bsp = object()

            class FakeLevel:
                def get_bsp(self):
                    return target_bsp

                def preview_bsp(self):
                    return target_bsp

                def editor_materialize(self):
                    return types.SimpleNamespace(objects=[])

                def source_bytes(self):
                    return b"target dat"

            class FakeWorkspace:
                @staticmethod
                def ask(_parent, **kwargs):
                    calls["workspace"] += 1
                    self.assertEqual(kwargs["suggest_name"](path), "ImportedChair")
                    kwargs["validate_request"](request)
                    return request

            class FakeView:
                def set_place_mode(self, value):
                    calls["place_mode"].append(value)

            globals_ = editor_app.EditorApp.cmd_import_static_prefab_bsp.__globals__
            old_workspace = globals_.get("PrefabImportWorkspace")
            old_template = globals_.get("class_template_from_catalog")
            old_suggest = editor_app.prefab_import.suggest_import_name
            old_build = editor_app.prefab_import.build_static_import_plan
            try:
                globals_["PrefabImportWorkspace"] = FakeWorkspace
                globals_["class_template_from_catalog"] = lambda _catalog, name: f"{name} template"
                editor_app.prefab_import.suggest_import_name = (
                    lambda _bsp, _path, _names: "ImportedChair"
                )

                def _build(*_args, **kwargs):
                    calls["validated"] += 1
                    self.assertEqual(kwargs["new_name"], "ImportedChair")
                    self.assertEqual(kwargs["collision_mode"], "none")
                    self.assertTrue(kwargs["allow_generated_bsp"])

                editor_app.prefab_import.build_static_import_plan = _build
                app = object.__new__(editor_app.EditorApp)
                app.root = object()
                app.active = FakeLevel()
                app.catalog = {}
                app.cfg = types.SimpleNamespace(editor_dir=tmp)
                app.editor_settings = {}
                app.settings_path = ""
                app.view3d = FakeView()

                editor_app.EditorApp.cmd_import_static_prefab_bsp(app)
            finally:
                globals_["PrefabImportWorkspace"] = old_workspace
                globals_["class_template_from_catalog"] = old_template
                editor_app.prefab_import.suggest_import_name = old_suggest
                editor_app.prefab_import.build_static_import_plan = old_build

        self.assertEqual(calls["workspace"], 1)
        self.assertEqual(calls["validated"], 1)
        self.assertEqual(calls["place_mode"], [True])
        self.assertEqual(app._pending_kind, "import_prefab_bsp")
        self.assertEqual(app._pending_prefab_path, path)
        self.assertEqual(app._pending_prefab_collision_thickness, 12.0)
        self.assertTrue(app._pending_prefab_preview_only)

    def test_editor_accepts_promoted_object_only_behavioral_prefab(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ObjectOnly.dat")
            write_minimal_dat(path, [], [patcher.WorldObject("Prop", [
                patcher.Property("Name", 0, 0, "Prop1"),
                patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
                patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            ])])
            expected_fingerprint = prefab_behavioral.load_prefab_graph(path).source_fingerprint
            request = PrefabImportRequest(
                prefab_path=path,
                new_name="ImportedProp",
                collision_mode="none",
                collision_thickness=8.0,
                collision_segment_length=512.0,
                placement_anchor="original_origin",
                browser_root=tmp,
                import_mode="behavioral",
            )

            class FakeLevel:
                def get_bsp(self):
                    return types.SimpleNamespace(world_models=[])

                def preview_bsp(self):
                    return self.get_bsp()

                def editor_materialize(self):
                    return types.SimpleNamespace(objects=[])

            class FakeWorkspace:
                @staticmethod
                def ask(_parent, **kwargs):
                    analysis = kwargs["analyze_prefab"](path)
                    self.assertEqual(
                        analysis.behavioral_state,
                        SupportState.BEHAVIORAL_READY,
                    )
                    kwargs["validate_request"](request)
                    return request

            class FakeView:
                def __init__(self):
                    self.values = []

                def set_place_mode(self, value):
                    self.values.append(value)

            template_props = [
                {"name": "Name", "code": 0, "flags": 0, "value": ""},
                {"name": "Pos", "code": 1, "flags": 0, "value": [0.0, 0.0, 0.0]},
                {"name": "Rotation", "code": 7, "flags": 0, "value": [0.0, 0.0, 0.0, 0.0]},
            ]
            catalog = {"classes": {"Prop": {"object_lto": {
                "template_properties": template_props,
            }}}}
            globals_ = editor_app.EditorApp.cmd_import_static_prefab_bsp.__globals__
            old_workspace = globals_.get("PrefabImportWorkspace")
            try:
                globals_["PrefabImportWorkspace"] = FakeWorkspace
                app = object.__new__(editor_app.EditorApp)
                app.root = object()
                app.active = FakeLevel()
                app.catalog = catalog
                app.resources = types.SimpleNamespace(exists=lambda _path: True)
                app.cfg = types.SimpleNamespace(editor_dir=tmp)
                app.editor_settings = {}
                app.settings_path = ""
                app.view3d = FakeView()

                editor_app.EditorApp.cmd_import_static_prefab_bsp(app)
            finally:
                globals_["PrefabImportWorkspace"] = old_workspace

        self.assertEqual(app._pending_kind, "import_behavioral_prefab")
        self.assertEqual(app._pending_behavioral_fingerprint, expected_fingerprint)
        self.assertIn("Prop", app._pending_behavioral_templates)
        self.assertEqual(app.view3d.values, [True])


if __name__ == "__main__":
    unittest.main()
