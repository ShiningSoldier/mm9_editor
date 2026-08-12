import os
import io
import tempfile
import unittest
from contextlib import redirect_stdout

from tests._path import ROOT  # noqa: F401

from catalog import ensure_lomm_catalog
from catalog.builder import main as catalog_main
from tests.app_tests.test_editor_resource_workflow import make_world_bytes
from tests.core_tests.test_game_resources import write_minimal_rez


class LommCatalogTests(unittest.TestCase):
    def _minimal_lomm_install(self, root: str) -> str:
        lomm_root = os.path.join(root, "Legends of Might and Magic")
        data_dir = os.path.join(lomm_root, "Data")
        write_minimal_rez(
            os.path.join(data_dir, "worlds.rez"),
            {
                "WORLDS/ISLEOFFIRE": make_world_bytes(
                    "DragonRed", filename=r"models\DragonRed.abc"
                ),
            },
        )
        write_minimal_rez(
            os.path.join(data_dir, "SKINS.REZ"),
            {
                "SKINS/DRAGONRED.DTX": b"skin",
                "SKINS/PRINCESSBLUE.DTX": b"blue",
                "SKINS/PRINCESSGOLD.DTX": b"gold",
                "SKINS/PRINCESSPINK.DTX": b"pink",
            },
        )
        write_minimal_rez(
            os.path.join(data_dir, "MODELS.REZ"),
            {
                "MODELS/DRAGONRED.ABC": b"dragon-model",
                "MODELS/PRINCESS.ABC": b"princess-model",
            },
        )
        return lomm_root

    def test_missing_catalog_is_built_atomically_from_lomm_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            lomm_root = self._minimal_lomm_install(tmp)
            output = os.path.join(tmp, "catalogs", "catalog_lomm.json")

            catalog, generated = ensure_lomm_catalog(lomm_root, output)

            self.assertTrue(generated)
            self.assertTrue(os.path.isfile(output))
            self.assertEqual(catalog["game"], "lomm")
            self.assertEqual(catalog["summary"]["total_levels"], 1)
            self.assertEqual(
                catalog["model_variants"][r"models\dragonred.abc"][0]["skins"],
                [r"skins\dragonred.dtx"],
            )
            self.assertIn(r"models\princess.abc", catalog["model_resources"])
            self.assertEqual(
                [
                    row["skins"][0]
                    for row in catalog["model_variants"][r"models\princess.abc"]
                ],
                [
                    r"skins\princessblue.dtx",
                    r"skins\princessgold.dtx",
                    r"skins\princesspink.dtx",
                ],
            )
            leftovers = [
                name for name in os.listdir(os.path.dirname(output))
                if name.startswith(".catalog_")
            ]
            self.assertEqual(leftovers, [])

    def test_existing_catalog_is_not_rebuilt_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "catalog_lomm.json")
            original = {
                "game": "lomm",
                "classes": {},
                "model_variants": {},
                "summary": {"total_levels": 7},
            }
            from catalog import save_catalog
            save_catalog(original, output)

            catalog, generated = ensure_lomm_catalog(
                os.path.join(tmp, "missing-install"),
                output,
            )

            self.assertFalse(generated)
            self.assertEqual(catalog["summary"]["total_levels"], 7)

    def test_build_lomm_cli_uses_install_root_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            lomm_root = self._minimal_lomm_install(tmp)
            output = os.path.join(tmp, "catalog_lomm.json")

            with redirect_stdout(io.StringIO()):
                result = catalog_main([
                    "build-lomm",
                    lomm_root,
                    "--out",
                    output,
                ])

            self.assertEqual(result, 0)
            with open(output, "r", encoding="utf-8") as stream:
                import json
                catalog = json.load(stream)
            self.assertEqual(catalog["game"], "lomm")
            self.assertEqual(catalog.get("actor_visuals"), {})
            self.assertEqual(
                catalog["model_variants"][r"models\dragonred.abc"][0]["skins"],
                [r"skins\dragonred.dtx"],
            )


if __name__ == "__main__":
    unittest.main()
