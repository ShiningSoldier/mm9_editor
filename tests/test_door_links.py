import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import bsp
import door_links
from mm9_patcher.mm9_patch import World


DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mm9_data")


class DoorLinkTests(unittest.TestCase):
    def load_sturmford(self):
        path = os.path.join(DATA_ROOT, "WORLDS", "STURMFORDCITY.DAT")
        if not os.path.exists(path):
            self.skipTest(f"missing test level: {path}")
        return World.load(path), bsp.parse_path(path)

    def test_links_door_controller_to_same_named_bsp_submodel(self):
        world, bsp_world = self.load_sturmford()

        link = door_links.find_physical_door_link(world.objects, bsp_world, "Door32")
        self.assertIsNotNone(link)
        self.assertEqual(link.name, "Door32")
        self.assertEqual(link.class_name, "Door")
        self.assertFalse(link.is_rotating)
        self.assertEqual(link.model.name, "Door32")
        self.assertIsNone(link.pair_model)

    def test_links_rotating_double_door_pair(self):
        world, bsp_world = self.load_sturmford()

        right = door_links.find_physical_door_link(world.objects, bsp_world, "ChurchdoorR")
        self.assertIsNotNone(right)
        self.assertEqual(right.class_name, "RotatingDoor")
        self.assertTrue(right.is_rotating)
        self.assertEqual(right.model.name, "ChurchdoorR")
        self.assertEqual(right.pair_name, "ChurchdoorL")
        self.assertTrue(right.is_paired)
        self.assertIsNotNone(right.pair_object_index)
        self.assertEqual(right.pair_model.name, "ChurchdoorL")

        left = door_links.find_physical_door_link(world.objects, bsp_world, "ChurchdoorL")
        self.assertIsNotNone(left)
        self.assertEqual(left.pair_name, "ChurchdoorR")
        self.assertTrue(left.is_paired)

    def test_all_links_have_door_objects_and_bsp_models(self):
        world, bsp_world = self.load_sturmford()

        links = door_links.build_physical_door_links(world.objects, bsp_world)
        self.assertGreaterEqual(len(links), 40)
        for link in links:
            with self.subTest(name=link.name):
                self.assertIn(link.class_name, door_links.DOOR_CLASSES)
                self.assertEqual(link.name.lower(), link.model.name.lower())


if __name__ == "__main__":
    unittest.main()
