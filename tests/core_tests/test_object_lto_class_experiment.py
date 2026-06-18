import json
import os
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401
import _path_setup  # noqa: F401

import mm9_patch as patcher
from core import install_manager
from core.rezmgr import RezReader
from tests.core_tests.test_game_resources import write_minimal_rez
from tools import object_lto_class_experiment


TABLE_HEADER = (
    "Number\tMonster Name\tModelName\tSkinName\tSkinName2\tSkinName3\t"
    "Type/Picture\tScriptName\tBaseName\tIsMonster\n"
)


def _prop(name, type_id, value, source_class="LizardOrc"):
    return {
        "name": name,
        "source_class": source_class,
        "type_id": type_id,
        "type": "string" if type_id == 0 else ("vector" if type_id == 1 else "rotation"),
        "flags": 0,
        "flag_names": [],
        "group": 0,
        "hidden_in_dedit": False,
        "default_value": value,
        "default_raw": {"vector": [0, 0, 0], "float": 0, "string": value if type_id == 0 else None},
    }


def _class(name, parent, hierarchy, properties, declared=None):
    return {
        "name": name,
        "parent": parent,
        "hierarchy": hierarchy,
        "flags": 0,
        "flag_names": [],
        "hidden_in_dedit": False,
        "runtime_loadable": True,
        "class_object_size": 2988,
        "declared_properties": declared or [],
        "properties": properties,
    }


def _dump(classes):
    return {
        "schema": "mm9_editor.object_lto_dump.v1",
        "object_lto_path": "object.lto",
        "server_object_version": 1,
        "class_count": len(classes),
        "classes": classes,
    }


def _world_bytes():
    world = patcher.World(
        patcher.Header(patcher.DAT_VERSION, patcher.HEADER_SIZE, patcher.HEADER_SIZE, (0,) * 8),
        b"",
        [patcher.WorldObject("StartPoint", [
            patcher.Property("Name", 0, 0, "StartPoint0"),
            patcher.Property("Pos", 1, 0, (0.0, 0.0, 0.0)),
        ])],
        b"",
    )
    fd, path = tempfile.mkstemp(suffix=".DAT")
    os.close(fd)
    try:
        world.save(path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.remove(path)


class ObjectLtoClassExperimentTests(unittest.TestCase):
    def _write_game(self, root):
        data = os.path.join(root, "data")
        os.makedirs(data, exist_ok=True)
        table = (
            TABLE_HEADER
            + "191\tLizard-Orc Mage\tlizardorc.abc\tLizardOrc.dtx\t"
            + "LizOrcCutlass.dtx\t\tLizard-Orc C\tbaserange.scr\tLizardOrc\t1\n"
            + "121\tLoMM Orc Mage\tOrcMM9.abc\tOrc.dtx\t\t\tLoMMOrcMage\tbaserange.scr\tLoMMOrcMage\t1\n"
        )
        write_minimal_rez(os.path.join(data, "DATA.REZ"), {
            "DATA/ACTOR": table.encode("latin-1"),
            "DATA/MONSTERS": table.encode("latin-1"),
        })
        write_minimal_rez(os.path.join(data, "WORLDS.REZ"), {
            "WORLDS/BOOTCAMP": _world_bytes(),
        })

    def _write_dump(self, path, include_candidate):
        props = [
            _prop("Name", 0, "noname", "BaseClass"),
            _prop("Pos", 1, [0, 0, 0], "BaseClass"),
            _prop("Rotation", 7, [0, 0, 0, 0], "BaseClass"),
            _prop("Filename", 0, r"models\lizardorc.abc"),
            _prop("ScriptName", 0, "", "ObjectBase"),
        ]
        parent = _class(
            "LizardOrcMage",
            "LizardOrc",
            ["BaseClass", "ObjectBase", "ModelObject", "Actor", "AIBase", "LizardOrc", "LizardOrcMage"],
            props,
        )
        classes = [parent]
        if include_candidate:
            classes.append(_class(
                "LoMMOrcMage",
                "LizardOrcMage",
                parent["hierarchy"] + ["LoMMOrcMage"],
                props,
            ))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_dump(classes), fh)

    def test_missing_candidate_class_blocks_placement_but_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9 = os.path.join(tmp, "mm9")
            out = os.path.join(tmp, "out")
            dump = os.path.join(tmp, "object_lto.json")
            self._write_game(mm9)
            self._write_dump(dump, include_candidate=False)

            result = object_lto_class_experiment.build_object_lto_class_experiment(
                mm9_root=mm9,
                output_dir=out,
                class_name="LoMMOrcMage",
                parent_class="LizardOrcMage",
                target_row="121",
                level_name="BOOTCAMP",
                object_name="Stage5LoMMOrc1",
                pos=(1.0, 2.0, 3.0),
                yaw=0.0,
                filename=r"models\OrcMM9.abc",
                script_name="",
                object_lto_dump=dump,
            )

            self.assertEqual(result["status"], "blocked")
            self.assertIn("candidate class 'LoMMOrcMage'", result["validation_errors"][0])
            self.assertEqual(result["archives"], [])
            self.assertTrue(os.path.isfile(result["manifest"]))
            self.assertFalse(os.path.exists(os.path.join(out, "data", "WORLDS.REZ")))

    def test_existing_candidate_class_writes_throwaway_worlds_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            mm9 = os.path.join(tmp, "mm9")
            out = os.path.join(tmp, "out")
            dump = os.path.join(tmp, "object_lto.json")
            self._write_game(mm9)
            self._write_dump(dump, include_candidate=True)

            result = object_lto_class_experiment.build_object_lto_class_experiment(
                mm9_root=mm9,
                output_dir=out,
                class_name="LoMMOrcMage",
                parent_class="LizardOrcMage",
                target_row="121",
                level_name="BOOTCAMP",
                object_name="Stage5LoMMOrc1",
                pos=(1.0, 2.0, 3.0),
                yaw=0.25,
                filename=r"models\OrcMM9.abc",
                script_name=r"scripts\MM9ED_DEBUG_ACTOR.scr",
                object_lto_dump=dump,
            )

            output_rez = os.path.join(out, "data", "WORLDS.REZ")
            self.assertEqual(result["status"], "ready-to-place")
            self.assertIn(output_rez, install_manager.archives_to_install(out))
            self.assertTrue(result["actor_row_selection"]["target_row_matches_table_name_heuristic"])
            self.assertFalse(result["actor_row_selection"]["target_row_known_selected_by_candidate_class"])
            self.assertEqual(
                os.path.basename(result["placement"]["changed_entries"][0]),
                "BOOTCAMP.DAT",
            )

            with RezReader(output_rez) as reader:
                data = reader.extract_to_bytes("WORLDS/BOOTCAMP")
            fd, path = tempfile.mkstemp(suffix=".DAT")
            os.close(fd)
            try:
                with open(path, "wb") as fh:
                    fh.write(data)
                world = patcher.World.load(path)
            finally:
                os.remove(path)

            placed = world.objects[-1]
            self.assertEqual(placed.type_str, "LoMMOrcMage")
            self.assertEqual(placed.get("Name"), "Stage5LoMMOrc1")
            self.assertEqual(placed.get("Filename"), r"models\OrcMM9.abc")
            self.assertEqual(placed.get("ScriptName"), r"scripts\MM9ED_DEBUG_ACTOR.scr")


if __name__ == "__main__":
    unittest.main()
