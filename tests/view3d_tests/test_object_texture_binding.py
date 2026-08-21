import os
import sys
import math
import unittest

import numpy as np


from tests._path import ROOT  # noqa: F401

from view3d.gl_object_models import (
    _floor_y_override,
    _light_dir_for_model,
    _object_model_filename,
    _object_skin_names,
    _object_is_visible,
    _object_yaw,
    _rotation_y,
    _resolve_skin_for_piece,
    _split_skin_names,
    surface_placement_y,
)
from catalog.actor_visuals import parse_actor_visual_tables


class FakeTextureCache:
    def __init__(self, names):
        self.names = {self._key(name) for name in names}

    @staticmethod
    def _key(name):
        norm = str(name).replace("\\", "/").upper().lstrip("/")
        if norm.startswith("SKINS/"):
            norm = norm[len("SKINS/"):]
        if not norm.endswith(".DTX"):
            norm += ".DTX"
        return norm

    def has(self, name):
        key = self._key(name)
        base = key.rsplit("/", 1)[-1]
        return key in self.names or base in self.names


class FakeObject:
    def __init__(self, type_str, **props):
        self.type_str = type_str
        self.props = props

    def get(self, name, default=None):
        return self.props.get(name, default)


class ObjectTextureBindingTests(unittest.TestCase):
    def setUp(self):
        self.cache = FakeTextureCache([
            "BeldSword.dtx",
            "ClanShield1.dtx",
            "ClanSoldier1.dtx",
            "Krohn.dtx",
            "KrohnSpear.dtx",
            "KrohnSword.dtx",
            "LizOrcCutlass.dtx",
            "LizardOrc.dtx",
            "Body.dtx",
            "PeasantM1A.dtx",
            "PeasantM2A.dtx",
            "PeasantM7A.dtx",
            "PeasantF2A.dtx",
            "Imp1.dtx",
            "ImpTrident.dtx",
            "Yanmir.dtx",
            "YanmirMallet.dtx",
            "Zombie1.dtx",
            "Colloidal1.dtx",
            "Colloidal2.dtx",
            "Colloidal3.dtx",
            "Orbus1.dtx",
            "Orbus2.dtx",
            "Orbus3.dtx",
            "SkeletonWar1.dtx",
            "SkeletonWar2.dtx",
            "SkeletonScimitar.dtx",
            "ebora.dtx",
            "Siren1.dtx",
        ])
        header = (
            "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
            "Type/Picture\n"
        )
        self.actor_visuals = parse_actor_visual_tables([
            ("MONSTERS.TXT", header
             + "228\tEye\tEvileyeTerror.abc\tOrbus1.dtx\t\t\tBeholder A\n"
             + "230\tOculus\tEvileyeTerror.abc\tOrbus3.dtx\t\t\tBeholder C\n"
             + "234\tColloidal Soldier\tColloidalWarrior.abc\tColloidal1.dtx\t\t\tStrange Beast A\n"
             + "236\tColloidal Guardian\tColloidalWarrior.abc\tColloidal3.dtx\t\t\tStrange Beast C\n"
             + "301\tEbora\tebora.abc\tebora.dtx\t\t\tEbora\n"
             + "302\tConcubine\tebora.abc\tSiren1.dtx\t\t\tConcubine\n"),
        ])

    def resolve(self, piece, index, count, model, object_type="", skin="", appearance_key=""):
        return _resolve_skin_for_piece(
            piece,
            index,
            count,
            _split_skin_names(skin),
            model,
            object_type=object_type,
            appearance_key=appearance_key,
            skin_cache=self.cache,
        )

    def test_explicit_multiskin_mapping_still_wins(self):
        skin = "skins\\ClanSoldier1.dtx; skins\\BeldSword.dtx; skins\\Clanshield1.dtx"
        self.assertEqual(
            self.resolve("sword", 2, 4, "models\\clansoldier.abc", skin=skin),
            "skins\\BeldSword.dtx",
        )
        self.assertEqual(
            self.resolve("shield", 3, 4, "models\\clansoldier.abc", skin=skin),
            "skins\\Clanshield1.dtx",
        )

    def test_missing_skin_uses_model_and_accessory_defaults(self):
        self.assertEqual(
            self.resolve("domehelm", 0, 4, "models\\clansoldier.abc", "ClanSoldier"),
            "clansoldier1.dtx",
        )
        self.assertEqual(
            self.resolve("shield", 3, 4, "models\\clansoldier.abc", "ClanSoldier"),
            "ClanShield1.dtx",
        )
        self.assertEqual(
            self.resolve("Cutlass", 1, 2, "models\\lizardorc.abc", "LizardOrc"),
            "LizOrcCutlass.dtx",
        )

    def test_missing_skin_infers_common_character_variants(self):
        self.assertEqual(
            self.resolve("spear", 3, 4, "models\\Krohn.abc", "Krohn"),
            "KrohnSpear.dtx",
        )
        self.assertEqual(
            self.resolve("right arm", 1, 2, "models\\Zombie.abc", "Rotter"),
            "Zombie1.dtx",
        )
        self.assertEqual(
            self.resolve("ngon", 1, 2, "models\\PeasantMale.abc", "ShopkeeperHuman2MaleA"),
            "PeasantM1A.dtx",
        )
        self.assertEqual(
            self.resolve("ngon_s", 0, 2, "models\\PeasantMS2.ABC", "ShopkeeperHuman2MaleA"),
            "PeasantM2A.dtx",
        )

    def test_civilian_placeholder_uses_name_for_preview_appearance(self):
        obj = FakeObject(
            "ShopkeeperHuman2FemaleA",
            Name="ShopkeeperElfFemaleA2",
            Filename="models\\PeasantMale.abc",
        )
        self.assertEqual(_object_model_filename(obj), "models\\PeasantFemale.abc")
        self.assertEqual(
            self.resolve(
                "longhair",
                1,
                2,
                "models\\PeasantFemale.abc",
                "ShopkeeperHuman2FemaleA",
                appearance_key="ShopkeeperElfFemaleA2",
            ),
            "PeasantF2A.dtx",
        )

    def test_missing_commoner_model_uses_civilian_preview_model(self):
        male = FakeObject(
            "CommonerElfMaleA",
            Name="CommonerElfMaleA1",
            Filename="models\\CommonerElfMaleA.abc",
        )
        female = FakeObject(
            "CommonerHumanFemaleA",
            Name="CommonerHumanFemaleA2",
            Filename="models\\CommonerHumanFemaleA.abc",
        )
        child = FakeObject(
            "CommonerChildElfChildA",
            Name="CommonerElfFemaleA4",
            Filename="models\\CommonerElfFemaleA.abc",
        )

        self.assertEqual(_object_model_filename(male), "models\\PeasantMale.abc")
        self.assertEqual(_object_model_filename(female), "models\\PeasantFemale.abc")
        self.assertEqual(_object_model_filename(child), "models\\PeasantChildGirl.abc")

    def test_move_to_floor_uses_bsp_floor_for_render_y(self):
        from core import bsp
        import numpy as np
        from mm9_patcher.mm9_patch import World
        from view3d.abc_loader import load_abc

        repo_root = ROOT
        dat_path = os.path.join(repo_root, "mm9_data", "WORLDS", "STURMFORDCITY.DAT")
        if not os.path.exists(dat_path):
            self.skipTest(f"missing test level: {dat_path}")

        def mesh_for(filename, bake=True):
            rel = filename.replace("/", "\\")
            if rel.lower().startswith("models\\"):
                rel = rel[7:]
            model_path = os.path.join(repo_root, "mm9_data", "MODELS", *rel.split("\\"))
            model = load_abc(model_path, bake_static_bind_pose=bake)
            if model is None:
                self.skipTest(f"missing test model: {model_path}")

            class FakeMesh:
                pass

            tri_positions = []
            for piece in model.pieces:
                for tri in piece.triangles:
                    tri_positions.append([
                        piece.vertices[ref.vertex_index].pos for ref in tri.refs
                    ])
            mesh = FakeMesh()
            mesh.tri_positions = np.array(tri_positions, dtype=np.float32)
            mesh.model_user_dims = model.default_user_dims()
            return mesh

        world = World.load(dat_path)
        bsp_world = bsp.parse_path(dat_path)

        self.assertAlmostEqual(
            _floor_y_override(
                next(o for o in world.objects if o.get("Name") == "Pew1"),
                mesh_for("models\\Props\\pewNS.ABC", bake=False),
                bsp_world=bsp_world,
            ),
            7745.1,
            places=4,
        )
        self.assertAlmostEqual(
            _floor_y_override(
                next(o for o in world.objects if o.get("Name") == "Magi"),
                mesh_for("models\\PeasantF6.ABC"),
                bsp_world=bsp_world,
            ),
            7746.1,
            places=4,
        )
        self.assertAlmostEqual(
            _floor_y_override(
                next(o for o in world.objects if o.get("Name") == "Target1"),
                mesh_for("models\\Props\\Training_Archery1.ABC", bake=False),
                bsp_world=bsp_world,
            ),
            7741.6,
            places=4,
        )

    def test_static_no_animation_prop_uses_floor_anchored_dat_position(self):
        from core.bsp import BspWorld, Polygon, Surface, WorldModelMesh

        surface = Surface(
            uv_o=(0.0, 0.0, 0.0),
            uv_p=(1.0, 0.0, 0.0),
            uv_q=(0.0, 0.0, 1.0),
            texture_index=0,
            flags=1,
            texture_flags=0,
        )
        model = WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-10.0, 0.0, -10.0),
            max_box=(10.0, 0.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-10.0, 0.0, -10.0),
                (10.0, 0.0, 10.0),
                (10.0, 0.0, -10.0),
            ],
            polygons=[Polygon([0, 1, 2], 0, 0)],
            surfaces=[surface],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        class FakeMesh:
            tri_positions = np.array([[[-1.0, -2.0, -1.0],
                                       [1.0, -2.0, -1.0],
                                       [0.0, 3.0, 1.0]]], dtype=np.float32)
            model_user_dims = (1.0, 2.0, 1.0)
            model_no_animation = True

        prop = FakeObject(
            "Prop",
            Pos=(0.0, 0.0, 0.0),
            MoveToFloor=0,
            Scale=1.0,
        )

        self.assertEqual(_floor_y_override(prop, FakeMesh(), world), 2.0)

    def test_bottom_pivot_prop_preserves_dat_ground_reference_without_bsp(self):
        class FakeMesh:
            model_bottom_pivot_offset_y = 279.630981

        prop = FakeObject(
            "Prop",
            Pos=(10475.0, 574.0, -1501.0),
            MoveToFloor=0,
            Scale=1.4,
        )

        self.assertAlmostEqual(
            _floor_y_override(prop, FakeMesh(), bsp_world=None),
            965.4833734,
            places=5,
        )

    def test_move_to_floor_takes_precedence_over_source_pivot(self):
        from core.bsp import BspWorld, Polygon, Surface, WorldModelMesh

        surface = Surface(
            uv_o=(0.0, 0.0, 0.0),
            uv_p=(1.0, 0.0, 0.0),
            uv_q=(0.0, 0.0, 1.0),
            texture_index=0,
            flags=1,
            texture_flags=0,
        )
        model = WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-10.0, 0.0, -10.0),
            max_box=(10.0, 0.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-10.0, 0.0, -10.0),
                (10.0, 0.0, 10.0),
                (10.0, 0.0, -10.0),
            ],
            polygons=[Polygon([0, 1, 2], 0, 0)],
            surfaces=[surface],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        class FakeMesh:
            model_bottom_pivot_offset_y = 100.0
            model_user_dims = (1.0, 2.0, 1.0)

        prop = FakeObject(
            "Prop",
            Pos=(0.0, 10.0, 0.0),
            MoveToFloor=1,
            Scale=1.0,
        )

        self.assertAlmostEqual(_floor_y_override(prop, FakeMesh(), world), 2.1)

        placed_y = surface_placement_y(prop, FakeMesh(), 0.0)
        placed = FakeObject(
            "Prop",
            Pos=(0.0, placed_y, 0.0),
            MoveToFloor=1,
            Scale=1.0,
        )
        self.assertAlmostEqual(_floor_y_override(placed, FakeMesh(), world), placed_y)

    def test_surface_placement_lifts_move_to_floor_origin_by_runtime_dims(self):
        class FakeMesh:
            model_user_dims = (12.0, 34.0, 12.0)

        actor = FakeObject(
            "CommonerHuman2MaleA",
            MoveToFloor=1,
            Scale=1.5,
        )

        self.assertAlmostEqual(
            surface_placement_y(actor, FakeMesh(), 700.0),
            751.1,
        )

    def test_surface_placement_keeps_exact_y_without_move_to_floor(self):
        class FakeMesh:
            model_user_dims = (12.0, 34.0, 12.0)

        prop = FakeObject("Prop", MoveToFloor=0, Scale=2.0)

        self.assertEqual(surface_placement_y(prop, FakeMesh(), 700.0), 700.0)

    def test_surface_placement_clearance_avoids_coincident_unknown_model(self):
        actor = FakeObject("UnknownActor", MoveToFloor=1)

        self.assertAlmostEqual(surface_placement_y(actor, None, 700.0), 700.1)

    def test_object_model_yaw_uses_game_model_basis(self):
        self.assertAlmostEqual(_rotation_y((0.0, math.pi, 0.0, 0.0)), math.pi)
        self.assertAlmostEqual(
            _object_yaw(FakeObject(
                "Prop",
                Filename="models\\Props\\Cabinet05NS.ABC",
                Rotation=(0.0, math.pi, 0.0, 0.0),
            )),
            math.pi * 0.5,
        )
        self.assertAlmostEqual(
            _object_yaw(FakeObject(
                "Prop",
                Filename="models\\Props\\Chest1.ABC",
                Rotation=(0.0, 0.0, 0.0, 0.0),
            )),
            0.0,
        )

    def test_object_light_direction_accounts_for_rotation_and_reflection(self):
        angle = math.pi * 0.5
        c = math.cos(angle)
        s = math.sin(angle)
        rotation = np.array([
            [c, 0.0, s],
            [0.0, 1.0, 0.0],
            [-s, 0.0, c],
        ], dtype=np.float32)
        reflection = np.diag([-1.0, 1.0, 1.0]).astype(np.float32)
        model = np.eye(4, dtype=np.float32)
        model[:3, :3] = reflection @ rotation * 2.0
        world_light = np.array((0.4, 0.9, 0.3), dtype=np.float64)

        local_light = np.array(
            _light_dir_for_model(tuple(world_light), model),
            dtype=np.float64,
        )
        local_normal = np.array((0.25, 0.5, 0.75), dtype=np.float64)
        local_normal /= np.linalg.norm(local_normal)
        normal_matrix = np.linalg.inv(model[:3, :3]).T
        display_normal = normal_matrix @ local_normal
        display_normal /= np.linalg.norm(display_normal)
        world_light /= np.linalg.norm(world_light)

        self.assertAlmostEqual(float(np.linalg.norm(local_light)), 1.0)
        self.assertAlmostEqual(
            float(np.dot(local_normal, local_light)),
            float(np.dot(display_normal, world_light)),
            places=6,
        )

    def test_model_skin_beats_generic_body_piece_skin(self):
        self.assertEqual(
            self.resolve("body", 2, 3, "models\\Yanmir.abc", "Yanmir"),
            "Yanmir.dtx",
        )
        self.assertEqual(
            self.resolve("staff", 0, 3, "models\\Yanmir.abc", "Yanmir"),
            "YanmirMallet.dtx",
        )
        self.assertEqual(
            self.resolve("body", 0, 3, "models\\Imp.abc", "Imp"),
            "Imp1.dtx",
        )
        self.assertEqual(
            self.resolve("trident", 2, 3, "models\\Imp.abc", "Imp"),
            "ImpTrident.dtx",
        )

    def test_colloidal_warrior_uses_actor_table_skin_name(self):
        self.assertEqual(
            self.resolve("Bip01 Head", 8, 56, "models\\ColloidalWarrior.abc", "ColloidalWarrior"),
            "Colloidal2.dtx",
        )
        self.assertEqual(
            self.resolve("Bip01 Head", 8, 56, "models\\ColloidalWarrior.abc", "ColloidalSoldier"),
            "Colloidal1.dtx",
        )
        self.assertEqual(
            self.resolve("Bip01 Head", 8, 56, "models\\ColloidalWarrior.abc", "ColloidalGuardian"),
            "Colloidal3.dtx",
        )

    def test_beholder_variants_use_actor_table_skin_names(self):
        self.assertEqual(
            self.resolve("d_evileye", 0, 4, "models\\EvileyeTerror.abc", "Eye"),
            "Orbus1.dtx",
        )
        self.assertEqual(
            self.resolve("d_evileye", 0, 4, "models\\EvileyeTerror.abc", "Orbus"),
            "Orbus2.dtx",
        )
        self.assertEqual(
            self.resolve("d_evileye", 0, 4, "models\\EvileyeTerror.abc", "Oculus"),
            "Orbus3.dtx",
        )

    def test_lichlab_oculus_typo_uses_real_evileye_model(self):
        obj = FakeObject(
            "Oculus",
            Name="Oculus11",
            Filename="models\\EvilEyeTerrorTerror.abc",
        )

        self.assertEqual(
            _object_model_filename(obj, actor_visuals=self.actor_visuals),
            "models\\EvileyeTerror.abc",
        )
        self.assertEqual(
            _object_skin_names(obj, actor_visuals=self.actor_visuals),
            ["skins\\Orbus3.dtx"],
        )

    def test_actor_table_accessory_skin_is_available_for_piece_binding(self):
        header = (
            "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
            "Type/Picture\n"
        )
        actor_visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                header
                + "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx\t\tLizard-Orc C\n",
            ),
        ])
        obj = FakeObject(
            "LizardOrcMage",
            Name="LizardOrcMage0",
            Filename="models\\Skeleton.abc",
        )

        skins = _object_skin_names(obj, actor_visuals=actor_visuals)

        self.assertEqual(
            skins,
            ["skins\\LizardOrc.dtx", "skins\\LizOrcCutlass.dtx"],
        )
        self.assertEqual(
            _resolve_skin_for_piece(
                "Cutlass",
                1,
                2,
                skins,
                _object_model_filename(obj, actor_visuals=actor_visuals),
                "LizardOrcMage",
                skin_cache=self.cache,
            ),
            "skins\\LizOrcCutlass.dtx",
        )

    def test_skeleton_master_uses_actor_table_body_and_weapon_skins(self):
        self.assertEqual(
            self.resolve(
                "d_skeletonwarrior",
                2,
                4,
                "models\\SkeletonWarrior.abc",
                "SkeletonMaster",
            ),
            "SkeletonWar1.dtx",
        )
        self.assertEqual(
            self.resolve(
                "Scimitar",
                3,
                4,
                "models\\SkeletonWarrior.abc",
                "SkeletonMaster",
            ),
            "SkeletonScimitar.dtx",
        )

    def test_colloidal_variants_use_actor_table_shared_warrior_mesh(self):
        soldier = FakeObject(
            "ColloidalSoldier",
            Name="ColloidalSoldier0",
            Filename="models\\ColloidalSoldier.abc",
        )
        guardian = FakeObject(
            "ColloidalGuardian",
            Name="ColloidalGuardian0",
            Filename="models\\ColloidalGuardian.abc",
        )

        self.assertEqual(
            _object_model_filename(soldier, actor_visuals=self.actor_visuals),
            "models\\ColloidalWarrior.abc",
        )
        self.assertEqual(
            _object_skin_names(soldier, actor_visuals=self.actor_visuals),
            ["skins\\Colloidal1.dtx"],
        )
        self.assertEqual(
            _object_model_filename(guardian, actor_visuals=self.actor_visuals),
            "models\\ColloidalWarrior.abc",
        )
        self.assertEqual(
            _object_skin_names(guardian, actor_visuals=self.actor_visuals),
            ["skins\\Colloidal3.dtx"],
        )
        self.assertEqual(
            self.resolve(
                "Bip01 Head",
                8,
                56,
                _object_model_filename(guardian, actor_visuals=self.actor_visuals),
                "JellySpore",
                skin=";".join(_object_skin_names(guardian, actor_visuals=self.actor_visuals)),
            ),
            "skins\\Colloidal3.dtx",
        )

    def test_ebora_script_model_overrides_honk_placeholder(self):
        ebora = FakeObject(
            "SuccEbora",
            Name="BathingGuest7",
            Filename="models\\Honk.abc",
            ScriptName="scripts\\eborabath.scr",
        )
        self.assertEqual(
            _object_model_filename(ebora, actor_visuals=self.actor_visuals),
            "models\\ebora.abc",
        )
        self.assertEqual(
            _object_skin_names(ebora, actor_visuals=self.actor_visuals),
            ["skins\\ebora.dtx"],
        )
        self.assertEqual(
            self.resolve(
                "Ebora",
                0,
                1,
                _object_model_filename(ebora, actor_visuals=self.actor_visuals),
                "SuccEbora",
                skin=";".join(_object_skin_names(ebora, actor_visuals=self.actor_visuals)),
            ),
            "skins\\ebora.dtx",
        )

    def test_concubine_script_model_uses_deterministic_siren_preview(self):
        concubine = FakeObject(
            "Concubine",
            Name="Concubine1",
            Filename="models\\Honk.abc",
            ScriptName="scripts\\eboraconcubine.scr",
        )
        self.assertEqual(
            _object_model_filename(concubine, actor_visuals=self.actor_visuals),
            "models\\ebora.abc",
        )
        self.assertEqual(
            _object_skin_names(concubine, actor_visuals=self.actor_visuals),
            ["skins\\Siren1.dtx"],
        )
        self.assertEqual(
            self.resolve(
                "Ebora",
                0,
                1,
                _object_model_filename(concubine, actor_visuals=self.actor_visuals),
                "Concubine",
                skin=";".join(_object_skin_names(concubine, actor_visuals=self.actor_visuals)),
            ),
            "skins\\Siren1.dtx",
        )

    def test_invisible_objects_do_not_get_model_previews(self):
        visible = FakeObject("ForestGiant", Visible=1)
        hidden = FakeObject("ForestGiant", Visible=0)
        self.assertTrue(_object_is_visible(visible))
        self.assertFalse(_object_is_visible(hidden))


if __name__ == "__main__":
    unittest.main()
