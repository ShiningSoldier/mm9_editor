import unittest


from tests._path import ROOT  # noqa: F401

from core.bsp import (
    BspWorld,
    Polygon,
    Surface,
    WorldModelMesh,
    raycast_floor_y,
)


def _surface(flags, texture_index=0):
    return Surface(
        uv_o=(0.0, 0.0, 0.0),
        uv_p=(1.0, 0.0, 0.0),
        uv_q=(0.0, 0.0, 1.0),
        texture_index=texture_index,
        flags=flags,
        texture_flags=0,
    )


class BspFloorRaycastTests(unittest.TestCase):
    def test_solid_only_ignores_higher_nonsolid_polygon(self):
        model = WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-10.0, 5.0, -10.0),
            max_box=(10.0, 10.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-10.0, 10.0, -10.0),
                (10.0, 10.0, -10.0),
                (10.0, 10.0, 10.0),
                (-10.0, 5.0, -10.0),
                (10.0, 5.0, -10.0),
                (10.0, 5.0, 10.0),
            ],
            polygons=[
                Polygon([0, 2, 1], 0, 0),
                Polygon([3, 5, 4], 1, 0),
            ],
            surfaces=[_surface(0), _surface(1)],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        self.assertEqual(raycast_floor_y(world, 0.0, 0.0, y_above=20.0), 10.0)
        self.assertEqual(
            raycast_floor_y(world, 0.0, 0.0, y_above=20.0, solid_only=True),
            5.0,
        )

    def test_support_only_ignores_solid_ai_rail(self):
        model = WorldModelMesh(
            name="AITrk66",
            min_box=(-10.0, 5.0, -10.0),
            max_box=(10.0, 8.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-10.0, 8.0, -10.0),
                (10.0, 8.0, -10.0),
                (10.0, 8.0, 10.0),
                (-10.0, 5.0, -10.0),
                (10.0, 5.0, -10.0),
                (10.0, 5.0, 10.0),
            ],
            polygons=[
                Polygon([0, 2, 1], 0, 0),
                Polygon([3, 5, 4], 1, 0),
            ],
            texture_names=[
                r"LevelTextures\Misc\Rail.dtx",
                r"LevelTextures\Terrain\Grass.dtx",
            ],
            surfaces=[_surface(1, 0), _surface(1, 1)],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        self.assertEqual(
            raycast_floor_y(world, 0.0, 0.0, y_above=20.0, solid_only=True),
            8.0,
        )
        self.assertEqual(
            raycast_floor_y(
                world,
                0.0,
                0.0,
                y_above=20.0,
                solid_only=True,
                support_only=True,
            ),
            5.0,
        )
