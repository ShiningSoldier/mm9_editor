import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from core import project as P
from core import project_io
from features.prefabs.resource_backed import find_resource_backed_candidates
from mm9_patcher.mm9_patch import Header, Property, World, WorldObject
from ui.prefab_import_workspace import build_import_request


class ResourceBackedPrefabTests(unittest.TestCase):
    def test_bookcase_name_resolves_only_observed_prop_variants(self):
        catalog = {
            "filenames": {
                r"models\props\bookcase02ew.abc": {
                    "uses": 45,
                    "classes": ["DestructableProp", "Prop"],
                    "levels": ["BOOTCAMP.DAT"],
                },
                r"models\bookcasekeeper.abc": {
                    "uses": 100,
                    "classes": ["CommonerHumanFemaleA"],
                    "levels": ["TOWN.DAT"],
                },
                r"models\props\chair01.abc": {
                    "uses": 80,
                    "classes": ["Prop"],
                    "levels": ["BOOTCAMP.DAT"],
                },
            },
            "model_variants": {
                r"models\props\bookcase02ew.abc": [{
                    "name": "Prop",
                    "skins": [r"skins\props\bookcase02.dtx"],
                    "source_keys": ["BOOTCAMP.DAT:Bookcase57"],
                }],
                r"models\bookcasekeeper.abc": [{
                    "name": "CommonerHumanFemaleA",
                    "skins": [r"skins\human.dtx"],
                }],
                r"models\props\chair01.abc": [{
                    "name": "Prop",
                    "skins": [r"skins\props\chair01.dtx"],
                }],
            },
        }

        candidates = find_resource_backed_candidates("Furniture/Bookcase.ed", catalog)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].target_class, "Prop")
        self.assertEqual(candidates[0].model_path, r"models\props\bookcase02ew.abc")
        self.assertEqual(candidates[0].skin_paths, (r"skins\props\bookcase02.dtx",))

    def test_resource_operation_is_a_normal_object_add_and_round_trips(self):
        template = WorldObject("Prop", [
            Property("Name", 0, 0, "Prop"),
            Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 0.0)),
            Property("Filename", 0, 0, ""),
            Property("Skin", 0, 0, ""),
            Property("MoveToFloor", 5, 0, 1),
        ])
        op = P.ImportResourcePrefabOp(
            template=template,
            overrides={
                "Name": "ImportedBookcase",
                "Pos": [10.0, 20.0, 30.0],
                "Filename": r"models\props\bookcase02ew.abc",
                "Skin": r"skins\props\bookcase02.dtx",
            },
            prefab_path=r"C:\PreFabs\Furniture\Bookcase.ed",
            candidate_id="candidate",
            model_path=r"models\props\bookcase02ew.abc",
            skin_paths=(r"skins\props\bookcase02.dtx",),
            source_fingerprint="sha256",
        )
        world = World(Header(66, 0, 0, (0,) * 8), b"", [], b"")

        created = op.apply_to(world)
        restored = project_io.dict_to_op(project_io.op_to_dict(op))

        self.assertEqual(created.type_str, "Prop")
        self.assertEqual(created.get("Name"), "ImportedBookcase")
        self.assertEqual(created.get("Filename"), r"models\props\bookcase02ew.abc")
        self.assertEqual(len(world.objects), 1)
        self.assertIsInstance(restored, P.ImportResourcePrefabOp)
        self.assertEqual(restored.candidate_id, "candidate")
        self.assertEqual(restored.skin_paths, (r"skins\props\bookcase02.dtx",))

    def test_workspace_request_preserves_explicit_resource_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Bookcase.ed")
            with open(path, "wb") as handle:
                handle.write(b"fixture")
            request = build_import_request(
                prefab_path=path,
                new_name="ImportedBookcase",
                collision_mode="none",
                collision_thickness="8",
                collision_segment_length="512",
                placement_anchor="bottom_center",
                browser_root=tmp,
                import_mode="resource",
                resource_candidate_id="candidate",
                resource_class="Prop",
                resource_model=r"models\props\bookcase02ew.abc",
                resource_skins=(r"skins\props\bookcase02.dtx",),
            )

        self.assertEqual(request.import_mode, "resource")
        self.assertEqual(request.resource_class, "Prop")
        self.assertEqual(request.resource_skins, (r"skins\props\bookcase02.dtx",))


if __name__ == "__main__":
    unittest.main()
