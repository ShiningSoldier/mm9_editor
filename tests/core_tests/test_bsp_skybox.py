import unittest
from core import bsp


class BspSkyboxTests(unittest.TestCase):
    def test_skybox_detection(self):
        skyboxes = [
            bsp.WorldModelMesh("SkyBox0", (0,0,0), (0,0,0), (0,0,0)),
            bsp.WorldModelMesh("TOD_Sky0", (0,0,0), (0,0,0), (0,0,0)),
            bsp.WorldModelMesh("demosky", (0,0,0), (0,0,0), (0,0,0)),
            bsp.WorldModelMesh("skybox_east", (0,0,0), (0,0,0), (0,0,0)),
            bsp.WorldModelMesh("main_skybox", (0,0,0), (0,0,0), (0,0,0)),
            bsp.WorldModelMesh("sky_box_mesh", (0,0,0), (0,0,0), (0,0,0)),
        ]
        non_skyboxes = [
            bsp.WorldModelMesh("Terrain0", (0,0,0), (0,0,0), (0,0,0)),
            bsp.WorldModelMesh("PhysicsBSP", (0,0,0), (0,0,0), (0,0,0)),
            bsp.WorldModelMesh("Door32", (0,0,0), (0,0,0), (0,0,0)),
        ]

        for mesh in skyboxes:
            with self.subTest(name=mesh.name):
                self.assertTrue(mesh.is_skybox())

        for mesh in non_skyboxes:
            with self.subTest(name=mesh.name):
                self.assertFalse(mesh.is_skybox())


if __name__ == "__main__":
    unittest.main()
