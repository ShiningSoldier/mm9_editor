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

    def test_height_hint_and_ray_origin_keep_existing_precedence(self):
        points = []
        polygons = []
        for y in (2.0, 5.0, 50.0):
            first = len(points)
            points.extend([
                (-10.0, y, -10.0),
                (10.0, y, -10.0),
                (10.0, y, 10.0),
            ])
            polygons.append(Polygon([first, first + 2, first + 1], 0, 0))
        model = WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-10.0, 2.0, -10.0),
            max_box=(10.0, 50.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=points,
            polygons=polygons,
            surfaces=[_surface(1)],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        self.assertEqual(
            raycast_floor_y(
                world,
                0.0,
                0.0,
                y_hint_min=4.5,
                y_hint_max=5.5,
                y_above=100.0,
            ),
            5.0,
        )
        self.assertEqual(
            raycast_floor_y(world, 0.0, 0.0, y_above=9.0),
            5.0,
        )
        self.assertEqual(
            raycast_floor_y(world, 0.0, 0.0, y_above=5.0),
            2.0,
        )

    def test_index_is_reused_and_filter_modes_are_cached_separately(self):
        model = WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-10.0, 5.0, -10.0),
            max_box=(10.0, 5.0, 10.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-10.0, 5.0, -10.0),
                (10.0, 5.0, -10.0),
                (10.0, 5.0, 10.0),
            ],
            polygons=[Polygon([0, 2, 1], 0, 0)],
            surfaces=[_surface(1)],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        self.assertEqual(raycast_floor_y(world, 0.0, 0.0), 5.0)
        unfiltered = world._floor_raycast_indexes[(False, False)]
        self.assertEqual(raycast_floor_y(world, 0.0, 0.0), 5.0)
        self.assertIs(
            world._floor_raycast_indexes[(False, False)],
            unfiltered,
        )

        self.assertEqual(
            raycast_floor_y(world, 0.0, 0.0, solid_only=True),
            5.0,
        )
        self.assertEqual(
            set(world._floor_raycast_indexes),
            {(False, False), (True, False)},
        )

    def test_triangle_crossing_grid_boundary_is_found(self):
        model = WorldModelMesh(
            name="PhysicsBSP",
            min_box=(255.0, 7.0, -1.0),
            max_box=(257.0, 7.0, 1.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (255.0, 7.0, -1.0),
                (257.0, 7.0, 1.0),
                (257.0, 7.0, -1.0),
            ],
            polygons=[Polygon([0, 1, 2], 0, 0)],
            surfaces=[_surface(1)],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        self.assertEqual(raycast_floor_y(world, 256.0, 0.0), 7.0)

    def test_huge_triangle_uses_bounded_overflow_path(self):
        model = WorldModelMesh(
            name="PhysicsBSP",
            min_box=(-1.0e6, 3.0, -1.0e6),
            max_box=(1.0e6, 3.0, 1.0e6),
            translation=(0.0, 0.0, 0.0),
            points=[
                (-1.0e6, 3.0, -1.0e6),
                (1.0e6, 3.0, 1.0e6),
                (1.0e6, 3.0, -1.0e6),
            ],
            polygons=[Polygon([0, 1, 2], 0, 0)],
            surfaces=[_surface(1)],
        )
        world = BspWorld(version=66, world_info="", world_models=[model])

        self.assertEqual(raycast_floor_y(world, 0.0, 0.0), 3.0)
        index = world._floor_raycast_indexes[(False, False)]
        self.assertEqual(len(index.cells), 0)
        self.assertEqual(len(index.overflow), 1)
