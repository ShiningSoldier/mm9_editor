import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401
import _path_setup  # noqa: F401
import mm9_patch as patcher

import catalog
from catalog.actor_visuals import object_actor_keys, parse_actor_visual_tables, resolve_actor_visual
from view3d.gl_object_models import (
    _civilian_appearance_key,
    _civilian_preview_model,
    _object_model_filename,
    _object_skin_names,
)


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
        self.assertEqual(visual.accessory_skins, ())

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

    def test_compressed_human1_civilian_class_resolves_actor_visual(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "29\tCommoner\tPeasantF1.abc\tPeasantF1b.dtx\t\t\tPeasant Human1 FemaleA A\n"
                + "30\tCommoner\tPeasantF4.abc\tPeasantF4a.dtx\t\t\tPeasant Human1 FemaleA B\n",
            ),
        ])

        visual_a = resolve_actor_visual(
            visuals, "CommonerHumanFemaleA", "Prop28")
        visual_b = resolve_actor_visual(
            visuals, "CommonerHumanFemaleB", "CommonerHumanFemaleB0")

        self.assertIsNotNone(visual_a)
        self.assertEqual(visual_a.number, "29")
        self.assertEqual(visual_a.model, "models\\PeasantF1.abc")
        self.assertEqual(visual_a.skins, ("skins\\PeasantF1b.dtx",))
        self.assertIsNotNone(visual_b)
        self.assertEqual(visual_b.number, "30")

    def test_guberland_prop28_uses_actor_visual_not_tree_fallback(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "29\tCommoner\tPeasantF1.abc\tPeasantF1b.dtx\t\t\tPeasant Human1 FemaleA A\n",
            ),
        ])
        obj = patcher.WorldObject("CommonerHumanFemaleA", [
            patcher.Property("Name", 0, 0, "Prop28"),
            patcher.Property(
                "Filename", 0, 0,
                "models/props/PlantsandTrees/Tree04.abc",
            ),
        ])

        self.assertEqual(
            _object_model_filename(obj, visuals),
            "models\\PeasantF1.abc",
        )
        self.assertEqual(
            _object_skin_names(obj, visuals),
            ["skins\\PeasantF1b.dtx"],
        )

    def test_civilian_role_and_variant_choose_exact_actor_row(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "3\tTown\tPeasantDF4.abc\tPeasantDF4b.dtx\t\t\tPeasant Dwarf FemaleB A\n"
                + "4\tTown\tPeasantDF3.abc\tPeasantDF3a.dtx\t\t\tPeasant Dwarf FemaleB B\n"
                + "63\tShopkeeper\tPeasantHOF1.ABC\tPeasantHOF1b.dtx\t\t\tPeasant Half-Orc Female C\n"
                + "74\tTownsfolk Child\tPeasantGirl1e.ABC\tPeasantgirl1.dtx\t\t\tPeasant Elf Child B\n",
            ),
        ])

        town = resolve_actor_visual(
            visuals, "TownDwarfFemaleB", "TownDwarfFemaleB0")
        half_orc = resolve_actor_visual(
            visuals, "ShopKeeperHalfOrcFemaleA", "ShopKeeperHalfOrcFemaleA0")
        child = resolve_actor_visual(
            visuals, "TownsfolkChildElfChildA", "TownsfolkChildElfChildA0")

        self.assertIsNotNone(town)
        self.assertEqual(town.number, "4")
        self.assertIsNotNone(half_orc)
        self.assertEqual(half_orc.number, "63")
        self.assertIsNotNone(child)
        self.assertEqual(child.number, "74")

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

    def test_actor_visual_preserves_secondary_accessory_skins(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx\t\tLizard-Orc C\n",
            ),
        ])

        visual = resolve_actor_visual(visuals, "LizardOrcMage", "LizardOrcMage0")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.model, "models\\lizardorc.abc")
        self.assertEqual(visual.skins, ("skins\\LizardOrc.dtx",))
        self.assertEqual(visual.accessory_skins, ("skins\\LizOrcCutlass.dtx",))
        self.assertEqual(
            visual.to_json()["accessory_skins"],
            ["skins\\LizOrcCutlass.dtx"],
        )

    def test_accountant_resolves_to_row_217_with_honk_hat(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "186\tHonk\thonkfemale.abc\thonkf1.dtx\thonkhat.dtx\t\tHonk Worshipper A\n"
                + "216\tHonk\thonkmale.abc\thonkm2.dtx\t\t\tHonk Worshipper2 A\n"
                + "217\tElder Honk\thonkfemale.abc\thonkf3.dtx\thonkhat.dtx\t\tHonk Worshipper2 B\n",
            ),
        ])

        visual = resolve_actor_visual(visuals, "Honk", "Accountant")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.number, "217")
        self.assertEqual(visual.model, "models\\honkfemale.abc")
        self.assertEqual(visual.skins, ("skins\\honkf3.dtx",))
        self.assertEqual(visual.accessory_skins, ("skins\\honkhat.dtx",))
        self.assertIn("MONSTERS.TXT:217", visual.quirk)

    def test_lomm_orc_lizardorc_variant_prefers_appended_row(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "189\tLizard-Orc\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx\t\tLizard-Orc A\n"
                + "304\tLoMM Orc\tOrcMM9.abc\tOrc.dtx\t\t\tLoMM Orc\n",
            ),
        ])

        visual = resolve_actor_visual(visuals, "LizardOrc", "LoMMOrc1")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.number, "304")
        self.assertEqual(visual.model, "models\\OrcMM9.abc")
        self.assertEqual(visual.skins, ("skins\\Orc.dtx",))
        self.assertTrue(visual.editor_preview_only)
        self.assertTrue(visual.to_json()["editor_preview_only"])
        self.assertIn("editor-preview-only", visual.quirk)

    def test_lomm_orc_lizardorc_variant_has_editor_fallback_visual(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "189\tLizard-Orc\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx\t\tLizard-Orc A\n",
            ),
        ])

        visual = resolve_actor_visual(visuals, "LizardOrc", "LoMMOrc1")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.number, "304")
        self.assertEqual(visual.model, "models\\OrcMM9.abc")
        self.assertEqual(visual.skins, ("skins\\Orc.dtx",))
        self.assertIn("LoMM Orc", visual.quirk)
        self.assertTrue(visual.editor_preview_only)

    def test_lomm_orc_lizardorc_mage_variant_prefers_appended_row(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\tLizOrcCutlass.dtx\t\tLizard-Orc C\n"
                + "304\tLoMM Orc\tOrcMM9.abc\tOrc.dtx\t\t\tLoMM Orc\n",
            ),
        ])

        visual = resolve_actor_visual(visuals, "LizardOrcMage", "LoMMOrc1")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.number, "304")
        self.assertEqual(visual.model, "models\\OrcMM9.abc")
        self.assertEqual(visual.skins, ("skins\\Orc.dtx",))
        self.assertTrue(visual.editor_preview_only)

    def test_script_rule_maps_placeholder_actor_to_explicit_row(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "301\tEbora\tebora.abc\tebora.dtx\t\t\tEbora\n",
            ),
        ])

        visual = resolve_actor_visual(
            visuals,
            "SuccEbora",
            "BathingGuest7",
            r"scripts\eborabath.scr",
        )

        self.assertIsNotNone(visual)
        self.assertEqual(visual.number, "301")
        self.assertEqual(visual.model, "models\\ebora.abc")
        self.assertEqual(visual.skins, ("skins\\ebora.dtx",))
        self.assertFalse(visual.editor_preview_only)
        self.assertIn("eborabath.scr", visual.quirk)

    def test_explicit_row_rule_preserves_accessory_skins(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "304\tLoMM Orc\tOrcMM9.abc\tOrc.dtx\tOrcAxe.dtx\tOrcShield.dtx\tLoMM Orc\n",
            ),
        ])

        visual = resolve_actor_visual(visuals, "LizardOrcMage", "LoMMOrc1")

        self.assertIsNotNone(visual)
        self.assertEqual(visual.skins, ("skins\\Orc.dtx",))
        self.assertEqual(
            visual.accessory_skins,
            ("skins\\OrcAxe.dtx", "skins\\OrcShield.dtx"),
        )
        self.assertEqual(
            visual.all_skins,
            ("skins\\Orc.dtx", "skins\\OrcAxe.dtx", "skins\\OrcShield.dtx"),
        )

    def test_custom_visual_rule_dict_maps_imported_variant_to_row(self):
        custom_rule = {
            "type_str": "LizardOrcMage",
            "object_name": "ImportedOrcCaptain",
            "script_name": r"scripts\mm9ed_debug_actor.scr",
            "source_file": "MONSTERS.TXT",
            "source_row": "304",
            "comment": "Local import test mapping for LoMM Orc Captain.",
            "editor_preview_only": True,
        }
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "304\tLoMM Orc Captain\tOrcMM9.abc\tOrc.dtx\tOrcAxe.dtx\t\tLoMM Orc Captain\n",
            ),
        ], visual_rules=[custom_rule])

        visual = resolve_actor_visual(
            visuals,
            "LizardOrcMage",
            "ImportedOrcCaptain",
            r"scripts\MM9ED_DEBUG_ACTOR.scr",
            visual_rules=[custom_rule],
        )

        self.assertIsNotNone(visual)
        self.assertEqual(visual.number, "304")
        self.assertEqual(visual.model, "models\\OrcMM9.abc")
        self.assertEqual(visual.all_skins, ("skins\\Orc.dtx", "skins\\OrcAxe.dtx"))
        self.assertTrue(visual.editor_preview_only)
        self.assertIn("Local import test mapping", visual.quirk)

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

    def test_catalog_marks_preview_only_actor_visual_sources(self):
        visuals = parse_actor_visual_tables([
            (
                "MONSTERS.TXT",
                TABLE_HEADER
                + "304\tLoMM Orc\tOrcMM9.abc\tOrc.dtx\t\t\tLoMM Orc\n",
            ),
        ])
        obj = patcher.WorldObject("LizardOrcMage", [
            patcher.Property("Name", 0, 0, "LoMMOrc1"),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
            patcher.Property("Filename", 0, 0, "models\\lizardorc.abc"),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "BOOTCAMP.DAT")
            patcher.World(
                patcher.Header(patcher.DAT_VERSION, patcher.HEADER_SIZE, patcher.HEADER_SIZE, (0,) * 8),
                b"",
                [obj],
                b"",
            ).save(path)

            cat = catalog.build_catalog(tmp, actor_visuals=visuals)

        self.assertIn(
            "MONSTERS.TXT:304:editor-preview-only",
            cat["classes"]["LizardOrcMage"]["actor_visual_sources"],
        )

    # ------------------------------------------------------------------
    # Regression: class name must take priority over a misleading object
    # name (BOOTCAMP.DAT ShopkeeperElfFemaleA1 / ShopkeeperDwarfMaleA).
    # ------------------------------------------------------------------

    def test_class_name_takes_priority_over_misleading_object_name_in_lookup(self):
        """object_actor_keys must try the class before the object name."""
        # Canonical class key first; object-name keys remain fallbacks.
        keys = object_actor_keys("ShopkeeperDwarfMaleA", "ShopkeeperElfFemaleA1")
        self.assertEqual(keys[0], "shopkeeperdwarfmaleca",
                         "canonical class key must be the first lookup candidate")
        self.assertLess(
            keys.index("shopkeeperdwarfmalea"),
            keys.index("shopkeeperelffemaleca"),
            "all class keys must precede object-name fallback keys",
        )

    def test_appearance_key_prefers_class_over_misleading_object_name(self):
        """_civilian_appearance_key must return the class, not the wrong name."""
        key = _civilian_appearance_key("ShopkeeperDwarfMaleA", "ShopkeeperElfFemaleA1")
        self.assertEqual(key, "ShopkeeperDwarfMaleA",
                         "class encodes the real race/gender; the object name is wrong")

    def test_misnamed_shopkeeper_resolves_to_dwarf_male_model(self):
        """End-to-end: ShopkeeperDwarfMaleA class must render as a dwarf male."""
        key = _civilian_appearance_key("ShopkeeperDwarfMaleA", "ShopkeeperElfFemaleA1")
        model = _civilian_preview_model(key)
        self.assertIn("Dwarf", model,
                      f"expected a dwarf model, got {model!r}")
        self.assertNotIn("Female", model,
                         f"must not resolve to a female model, got {model!r}")

    def test_generic_class_still_uses_descriptive_object_name(self):
        """When the class carries no race/gender info the object name is used."""
        # "Commoner" alone has no race/gender word → fall through to name.
        key = _civilian_appearance_key("Commoner", "CommonerDwarfFemaleB2")
        self.assertEqual(key, "CommonerDwarfFemaleB2")

    def test_non_civilian_class_falls_back_to_civilian_object_name(self):
        """A non-civilian class should not block the object-name fallback."""
        key = _civilian_appearance_key("SomeMonster", "CommonerHumanFemaleA1")
        self.assertEqual(key, "CommonerHumanFemaleA1")


if __name__ == "__main__":
    unittest.main()
