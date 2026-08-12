import unittest

import numpy as np

from tests._path import ROOT  # noqa: F401

from core import bsp
from view3d import gl_mesh
from view3d.sky import (
    build_soft_sky_model,
    resolve_sky_scene,
    resolve_soft_sky_texture,
)


class _Object:
    def __init__(self, type_str, **properties):
        self.type_str = type_str
        self.properties = properties

    def get(self, name, default=None):
        return self.properties.get(name, default)


class _TextureCache:
    def __init__(self, names=()):
        self.names = {str(name).replace("/", "\\").casefold() for name in names}

    def has(self, name):
        return str(name).replace("/", "\\").casefold() in self.names


def _surface(flags=0, texture_index=0):
    return bsp.Surface(
        uv_o=(0.0, 0.0, 0.0),
        uv_p=(1.0, 0.0, 0.0),
        uv_q=(0.0, 1.0, 0.0),
        texture_index=texture_index,
        flags=flags,
        texture_flags=0,
    )


class SkySceneTests(unittest.TestCase):
    def test_resolves_order_float_bit_index_and_camera_view_box(self):
        objects = [
            _Object(
                "DemoSkyWorldModel",
                Name="SkyBox0",
                Pos=(256.0, -768.0, -1536.0),
                SkyDims=(128.0, 128.0, 128.0),
                InnerPercentX=0.1,
                InnerPercentY=0.1,
                InnerPercentZ=0.1,
                Index=0,
                Visible=1,
            ),
            _Object("TOD_Sky", Name="TOD_Sky0", Visible=1),
            _Object(
                "SkyPointer",
                Name="SkyPointer0",
                SkyObjectName="TOD_Sky0",
                # DAT LongInt bits for the floating-point value 5.0.
                Index=0x40A00000,
            ),
        ]

        scene = resolve_sky_scene(objects)

        self.assertIsNotNone(scene)
        self.assertEqual([layer.model_name for layer in scene.layers], ["SkyBox0", "TOD_Sky0"])
        self.assertEqual([layer.index for layer in scene.layers], [0.0, 5.0])
        np.testing.assert_allclose(scene.view_min, (243.2, -780.8, -1548.8))
        np.testing.assert_allclose(scene.view_max, (268.8, -755.2, -1523.2))
        np.testing.assert_allclose(
            scene.view_position((50.0, 50.0, 50.0), (0.0, 0.0, 0.0), (100.0, 100.0, 100.0)),
            scene.definition_center,
        )

    def test_hidden_target_removes_pointer_layer(self):
        scene = resolve_sky_scene([
            _Object(
                "DemoSkyWorldModel",
                Name="SkyBox0",
                Pos=(0.0, 0.0, 0.0),
                SkyDims=(32.0, 32.0, 32.0),
            ),
            _Object("TOD_Sky", Name="TOD_Sky0", Visible=0),
            _Object("SkyPointer", Name="SkyPointer0", SkyObjectName="TOD_Sky0"),
        ])

        self.assertEqual([layer.model_name for layer in scene.layers], ["SkyBox0"])

    def test_soft_sky_uses_shipped_fallback_and_builds_five_face_shell(self):
        scene = resolve_sky_scene([
            _Object(
                "WorldProperties",
                SoftSky=r"textures\environmentmaps\clouds\clouds.dtx",
                AllSkyPortals=1,
            ),
            _Object(
                "DemoSkyWorldModel",
                Name="SkyBox0",
                Pos=(0.0, 0.0, 0.0),
                SkyDims=(128.0, 128.0, 128.0),
            ),
        ])
        cache = _TextureCache([r"TEXTURES\Skybox\Clouds1.dtx"])

        texture = resolve_soft_sky_texture(scene, cache)
        model = build_soft_sky_model(scene, texture)

        self.assertEqual(texture, r"TEXTURES\Skybox\Clouds1.dtx")
        self.assertTrue(scene.all_sky_portals)
        self.assertEqual(model.texture_names, [texture])
        self.assertEqual(len(model.polygons), 5)


class SkyPortalMeshTests(unittest.TestCase):
    def test_required_sky_flag_excludes_sky_marker_only_geometry(self):
        model = bsp.WorldModelMesh(
            name="PhysicsBSP",
            min_box=(0.0, 0.0, 0.0),
            max_box=(3.0, 1.0, 0.0),
            translation=(0.0, 0.0, 0.0),
            points=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (2.0, 0.0, 0.0),
                (3.0, 0.0, 0.0),
                (2.0, 1.0, 0.0),
            ],
            polygons=[
                bsp.Polygon([0, 1, 2], surface_index=0, plane_index=0),
                bsp.Polygon([3, 4, 5], surface_index=1, plane_index=0),
            ],
            texture_names=[r"TEXTURES\Skybox\SkyMarker.dtx"],
            surfaces=[
                _surface(flags=(1 << 4)),
                _surface(flags=0),
            ],
        )

        _vertices, indices, _ranges = gl_mesh._triangulate_model(
            model,
            helper_mode="helpers",
            helper_roles={"skyVisibility"},
            required_surface_flags=(1 << 4),
        )

        self.assertEqual(len(indices), 3)


if __name__ == "__main__":
    unittest.main()
