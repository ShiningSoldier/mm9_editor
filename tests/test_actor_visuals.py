import os
import tempfile
import unittest

import _path_setup  # noqa: F401
import mm9_patch as patcher

import catalog
from actor_visuals import parse_actor_visual_tables, resolve_actor_visual


TABLE_HEADER = (
    "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
    "Type/Picture\n"
)


class ActorVisualTests(unittest.TestCase):
    def test_matches_direct_monster_name_with_spaced_words(self):
        visuals = parse_actor_visual_tables([
            ("MONSTERS.TXT", TABLE_HEADER + "116\tBlack Wolf\twolf.abc\twolfblack.dtx\t\t\tWolf C\n"),
        ])

        visual = resolve_actor_visual(visuals, "BlackWolf", "BlackWolf3")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.model, "models\\wolf.abc")
        self.assertEqual(visual.skins, ("skins\\wolfblack.dtx",))

    def test_matches_civilian_type_picture_without_peasant_or_variant(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "50\tCommoner\tPeasantM2.abc\tPeasantM2a.dtx\t\t\tPeasant Human2 MaleA A\n"
                + "51\tCommoner\tPeasantM7.abc\tPeasantM7b.dtx\t\t\tPeasant Human2 MaleA B\n",
            ),
        ])

        visual = resolve_actor_visual(
            visuals, "CommonerHuman2MaleA", "CommonerHuman2MaleA0")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.model, "models\\PeasantM2.abc")
        self.assertEqual(visual.skins, ("skins\\PeasantM2a.dtx",))

    def test_monsters_table_overrides_actor_placeholders(self):
        visuals = parse_actor_visual_tables([
            ("ACTOR.TXT", TABLE_HEADER + "57\tCommoner\tsheep.abc\t\t\t\tPeasant Half-Orc Male A\n"),
            ("MONSTERS.TXT", TABLE_HEADER + "57\tCommoner\tPeasantHOM1.ABC\tPeasantHOM1a.dtx\t\t\tPeasant Half-Orc Male A\n"),
        ])

        visual = resolve_actor_visual(
            visuals, "CommonerHalfOrcMale", "CommonerHalfOrcMale0")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.model, "models\\PeasantHOM1.ABC")
        self.assertEqual(visual.skins, ("skins\\PeasantHOM1a.dtx",))

    def test_catalog_uses_actor_visual_model_before_dat_filename(self):
        visuals = parse_actor_visual_tables([
            ("MONSTERS.TXT", TABLE_HEADER + "115\tRed Wolf\twolf.abc\tWolfred.dtx\t\t\tWolf B\n"),
        ])
        obj = patcher.WorldObject("RedWolf", [
            patcher.Property("Name", 0, 0, "RedWolf6"),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            patcher.Property("Filename", 0, 0, "models\\sheep.abc"),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "MOUNTAINPASS.DAT")
            patcher.World(
                patcher.Header(patcher.DAT_VERSION, patcher.HEADER_SIZE, patcher.HEADER_SIZE, (0,) * 8),
                b"",
                [obj],
                b"",
            ).save(path)

            cat = catalog.build_catalog(tmp, actor_visuals=visuals)

        self.assertIn("models\\wolf.abc", cat["classes"]["RedWolf"]["filenames"])
        self.assertNotIn("models\\sheep.abc", cat["classes"]["RedWolf"]["filenames"])
        self.assertIn("skins\\wolfred.dtx", cat["classes"]["RedWolf"]["skins"])
        self.assertIn("models\\wolf.abc", cat["filenames"])


if __name__ == "__main__":
    unittest.main()
