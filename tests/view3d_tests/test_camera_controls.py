import unittest

import numpy as np

from tests._path import ROOT  # noqa: F401

from core import bsp
from view3d.camera import Camera
from view3d import gl_mesh, gl_view
from view3d.coords import game_to_display_bounds


def _mesh(
    name: str,
    lo: tuple[float, float, float],
    hi: tuple[float, float, float],
    texture: str = r"TEXTURES\World\stone.dtx",
) -> bsp.WorldModelMesh:
    surface = bsp.Surface(
        uv_o=(0.0, 0.0, 0.0),
        uv_p=(1.0, 0.0, 0.0),
        uv_q=(0.0, 1.0, 0.0),
        texture_index=0,
        flags=0,
        texture_flags=0,
    )
    return bsp.WorldModelMesh(
        name=name,
        min_box=lo,
        max_box=hi,
        translation=(0.0, 0.0, 0.0),
        points=[lo, (hi[0], lo[1], lo[2]), (lo[0], hi[1], hi[2])],
        polygons=[bsp.Polygon([0, 1, 2], surface_index=0, plane_index=0)],
        texture_names=[texture],
        surfaces=[surface],
    )


class CameraStateTests(unittest.TestCase):
    def test_return_to_orbit_restores_useful_pivot_distance(self):
        camera = Camera()
        camera.eye = np.array([10.0, 20.0, 110.0], dtype=np.float32)
        camera.target = np.array([10.0, 20.0, 10.0], dtype=np.float32)

        camera.set_mode("fly")
        camera.fly_rotate(35.0, -12.0)
        heading = camera.target - camera.eye
        heading /= np.linalg.norm(heading)
        camera.fly_dolly(250.0)
        eye_before_orbit = camera.eye.copy()

        camera.set_mode("orbit")

        orbit_arm = camera.target - camera.eye
        self.assertAlmostEqual(float(np.linalg.norm(orbit_arm)), 100.0, places=4)
        np.testing.assert_allclose(camera.eye, eye_before_orbit, atol=1.0e-5)
        np.testing.assert_allclose(
            orbit_arm / np.linalg.norm(orbit_arm),
            heading,
            atol=1.0e-5,
        )

    def test_fly_dolly_moves_eye_and_target_together(self):
        camera = Camera()
        camera.set_mode("fly")
        eye_before = camera.eye.copy()
        target_before = camera.target.copy()

        camera.fly_dolly(125.0)

        np.testing.assert_allclose(
            camera.eye - eye_before,
            camera.target - target_before,
            atol=1.0e-5,
        )
        self.assertAlmostEqual(
            float(np.linalg.norm(camera.eye - eye_before)),
            125.0,
            places=4,
        )

    def test_elapsed_fly_time_uses_real_delta_and_caps_stalls(self):
        self.assertEqual(gl_view._fly_elapsed_seconds(None, 4.0), 0.0)
        self.assertAlmostEqual(gl_view._fly_elapsed_seconds(4.0, 4.025), 0.025)
        self.assertEqual(gl_view._fly_elapsed_seconds(4.0, 5.0), 0.1)
        self.assertEqual(gl_view._fly_elapsed_seconds(5.0, 4.0), 0.0)


class CameraFitTests(unittest.TestCase):
    def test_normal_bounds_ignore_helper_polygons_inside_visible_model(self):
        surfaces = [
            bsp.Surface(
                uv_o=(0.0, 0.0, 0.0),
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 1.0, 0.0),
                texture_index=0,
                flags=0,
                texture_flags=0,
            ),
            bsp.Surface(
                uv_o=(0.0, 0.0, 0.0),
                uv_p=(1.0, 0.0, 0.0),
                uv_q=(0.0, 1.0, 0.0),
                texture_index=0,
                flags=(1 << 4),
                texture_flags=0,
            ),
        ]
        model = bsp.WorldModelMesh(
            name="PhysicsBSP",
            min_box=(0.0, 0.0, 0.0),
            max_box=(10_000.0, 10_000.0, 10_000.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0),
                (0.0, 10.0, 10.0),
                (9_000.0, 9_000.0, 9_000.0),
                (10_000.0, 9_000.0, 9_000.0),
                (9_000.0, 10_000.0, 10_000.0),
            ],
            polygons=[
                bsp.Polygon([0, 1, 2], surface_index=0, plane_index=0),
                bsp.Polygon([3, 4, 5], surface_index=1, plane_index=0),
            ],
            texture_names=[r"TEXTURES\World\stone.dtx"],
            surfaces=surfaces,
        )
        world = bsp.BspWorld(version=66, world_info="", world_models=[model])

        bounds = gl_mesh.normal_render_world_bounds(world)

        self.assertEqual(bounds, ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)))

    def test_normal_bounds_exclude_sky_visibility_and_helper_models(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("Terrain0", (-100.0, -20.0, -30.0), (300.0, 80.0, 70.0)),
                _mesh("SkyBox0", (-9000.0, -9000.0, -9000.0), (9000.0, 9000.0, 9000.0)),
                _mesh("VisBSP", (-8000.0, -8000.0, -8000.0), (8000.0, 8000.0, 8000.0)),
                _mesh(
                    "Fence_Collision",
                    (-7000.0, -7000.0, -7000.0),
                    (7000.0, 7000.0, 7000.0),
                    r"TEXTURES\LevelTextures\Misc\Firethrough.dtx",
                ),
                _mesh("AIBarrier51", (-6000.0, -6000.0, -6000.0), (6000.0, 6000.0, 6000.0)),
            ],
        )

        bounds = gl_mesh.normal_render_world_bounds(
            world,
            hidden_helper_model_names={"aibarrier51"},
        )

        self.assertEqual(
            bounds,
            ((-100.0, -20.0, -30.0), (300.0, 80.0, 70.0)),
        )

    def test_viewport_fit_reflects_game_x_bounds(self):
        world = bsp.BspWorld(
            version=66,
            world_info="",
            world_models=[
                _mesh("Terrain0", (100.0, -20.0, -30.0), (300.0, 80.0, 70.0)),
            ],
        )

        bounds = gl_mesh.normal_render_world_bounds(world)
        self.assertIsNotNone(bounds)
        fitted = game_to_display_bounds(*bounds)
        self.assertEqual(
            fitted,
            ((-300.0, -20.0, -30.0), (-100.0, 80.0, 70.0)),
        )


class ViewportInputOwnershipTests(unittest.TestCase):
    def test_global_key_is_rejected_when_another_control_has_focus(self):
        accepted = gl_view._should_accept_viewport_key(
            "w",
            direct_to_canvas=False,
            focus_known=True,
            focus_in_viewport=False,
            pointer_over_canvas=True,
        )

        self.assertFalse(accepted)

    def test_global_key_can_fall_back_to_pointer_when_tk_has_no_focus(self):
        accepted = gl_view._should_accept_viewport_key(
            "w",
            direct_to_canvas=False,
            focus_known=False,
            focus_in_viewport=False,
            pointer_over_canvas=True,
        )

        self.assertTrue(accepted)

    def test_profile_key_is_not_a_user_viewport_binding(self):
        accepted = gl_view._should_accept_viewport_key(
            "p",
            direct_to_canvas=True,
            focus_known=True,
            focus_in_viewport=True,
            pointer_over_canvas=True,
        )

        self.assertFalse(accepted)


if __name__ == "__main__":
    unittest.main()
