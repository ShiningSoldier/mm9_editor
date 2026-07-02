import types
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import terrain_semantics


class TerrainSemanticsTests(unittest.TestCase):
    def test_identifies_compiled_world_model_roles_by_name(self):
        self.assertTrue(terrain_semantics.is_terrain_name("Terrain0"))
        self.assertTrue(terrain_semantics.is_terrain_name("terrain_tail_01"))
        self.assertTrue(terrain_semantics.is_physics_bsp_name("PhysicsBSP"))
        self.assertTrue(terrain_semantics.is_vis_bsp_name("VisBSP"))

        self.assertFalse(terrain_semantics.is_terrain_name("WorldObject12"))
        self.assertFalse(terrain_semantics.is_physics_bsp_name("Terrain0"))
        self.assertFalse(terrain_semantics.is_vis_bsp_name("PhysicsBSP"))

    def test_terrain_model_names_prefers_terrain0(self):
        bsp_world = types.SimpleNamespace(world_models=[
            types.SimpleNamespace(name="Terrain1"),
            types.SimpleNamespace(name="WorldObject12"),
            types.SimpleNamespace(name="Terrain0"),
            types.SimpleNamespace(name="Terrain2"),
        ])

        self.assertEqual(
            terrain_semantics.terrain_model_names(bsp_world),
            ["Terrain0", "Terrain1", "Terrain2"],
        )

    def test_model_by_name_matches_case_insensitively(self):
        terrain = types.SimpleNamespace(name="Terrain0")
        physics = types.SimpleNamespace(name="PhysicsBSP")

        self.assertIs(
            terrain_semantics.model_by_name([terrain, physics], "terrain0"),
            terrain,
        )
        self.assertIs(
            terrain_semantics.model_by_name([terrain, physics], "PHYSICSBSP"),
            physics,
        )
        self.assertIsNone(terrain_semantics.model_by_name([terrain, physics], "Missing"))

    def test_default_dat_to_ed_model_names_skips_system_and_helper_models(self):
        class FakeModel:
            def __init__(self, name, *, skybox=False, points=True, polygons=True, texture=""):
                self.name = name
                self.points = [(0.0, 0.0, 0.0)] if points else []
                self.polygons = [object()] if polygons else []
                self._skybox = skybox
                self._texture = texture

            def is_skybox(self):
                return self._skybox

            def texture_name_for(self, _polygon):
                return self._texture

        world = types.SimpleNamespace(world_models=[
            FakeModel("Terrain0"),
            FakeModel("PhysicsBSP"),
            FakeModel("VisBSP"),
            FakeModel("SkyBox0", skybox=True),
            FakeModel("EmptyThing", points=False),
            FakeModel("AITrk0", texture="TEXTURES\\LevelTextures\\Misc\\rail.dtx"),
            FakeModel("WorldObject1"),
            FakeModel("MonsterDoor1"),
        ])

        self.assertEqual(
            terrain_semantics.default_dat_to_ed_model_names(world),
            ("WorldObject1", "MonsterDoor1"),
        )


if __name__ == "__main__":
    unittest.main()
