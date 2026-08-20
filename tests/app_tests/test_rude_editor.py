import os
import tempfile
import unittest
from unittest import mock


from tests._path import ROOT  # noqa: F401

from app import editor as editor_app
from core import project as project_model
from core import rezmgr
from core import rude
from core import rude_quest
from core import rude_script
from tests.core_tests.test_game_resources import write_minimal_rez
from ui import rude_editor
from ui import rude_script_editor


def _row(npc_nbr, state_id, branch_id, action=-1):
    return (
        f'{npc_nbr},{state_id},{branch_id},"Choice","Response",{action},'
        + ",".join(["0"] * 24)
        + "\r\n"
    ).encode("latin-1")


class _FakeRudeWindow:
    created = []

    def __init__(
        self,
        parent,
        project,
        asset,
        *,
        on_changed=None,
        on_open_related=None,
        dialogue_overrides_provider=None,
    ):
        self.parent = parent
        self.project = project
        self.asset = asset
        self.on_changed = on_changed
        self.on_open_related = on_open_related
        self.dialogue_overrides_provider = dialogue_overrides_provider
        self.bindings = []
        self.__class__.created.append(self)

    def bind(self, sequence, callback, add=None):
        self.bindings.append((sequence, callback, add))


class RudeEditorHelperTests(unittest.TestCase):
    def test_fixed_slots_accept_compact_input_and_pad_with_zeroes(self):
        self.assertEqual(
            rude_editor.parse_slot_values("12, 34; 56", 5, "Keys"),
            (12, 34, 56, 0, 0),
        )
        self.assertEqual(rude_editor.parse_slot_values("", 4, "Reserved"), (0,) * 4)
        with self.assertRaisesRegex(ValueError, "at most 2"):
            rude_editor.parse_slot_values("1 2 3", 2, "Keys")
        with self.assertRaisesRegex(ValueError, "integers"):
            rude_editor.parse_slot_values("1 nope", 5, "Keys")

    def test_mock_key_parser_deduplicates_sorts_and_ignores_zero(self):
        keys = rude_editor.parse_key_set("9, 1; 9 0")
        self.assertEqual(keys, {1, 9})
        self.assertEqual(rude_editor.format_key_set(keys), "1, 9")

    def test_graph_layout_and_action_labels_are_deterministic(self):
        self.assertEqual(
            rude_editor.graph_layout([10, 20, 30, 40], columns=3),
            {10: (90, 70), 20: (260, 70), 30: (430, 70), 40: (90, 190)},
        )
        self.assertEqual(
            rude_editor.action_description(rude.RudeAction(-5)),
            "Travel",
        )
        self.assertEqual(
            rude_editor.action_description(rude.RudeAction(-13)),
            "unknown native action -13",
        )

    def test_validation_and_key_usage_formatters_include_locations(self):
        issue = rude_quest.QuestValidationIssue(
            rude_quest.QuestIssueSeverity.ERROR,
            "MISSING_STATE_TARGET",
            "Transition targets missing state 20",
            state_id=10,
            branch_id=2,
        )
        usage = rude_quest.QuestKeyUsage(
            key_id=2016,
            role=rude_quest.QuestKeyRole.RUDE_NATIVE_EFFECT,
            source="RUDE/NPC42",
            line_number=1,
            detail="skill parameter",
            npc_nbr=42,
            state_id=10,
            branch_id=2,
            certain=False,
        )

        self.assertIn("state 10 branch 2", rude_editor.format_validation_issue(issue))
        formatted_usage = rude_editor.format_key_usage(usage)
        self.assertIn("NPC42 state 10 branch 2", formatted_usage)
        self.assertIn("possible/parameter", formatted_usage)

    def test_script_reward_and_world_change_fields_are_strict_and_ordered(self):
        self.assertEqual(
            rude_script_editor.parse_item_ids("42, 7; 42"),
            (42, 7, 42),
        )
        changes = rude_script_editor.parse_world_changes(
            "QuestDoor, unlock\nTownExit trigger"
        )
        self.assertEqual(
            [(item.object_name, item.message) for item in changes],
            [("QuestDoor", "unlock"), ("TownExit", "trigger")],
        )
        self.assertEqual(
            rude_script_editor.format_world_changes(changes),
            "QuestDoor, unlock\nTownExit, trigger",
        )
        with self.assertRaisesRegex(ValueError, "positive"):
            rude_script_editor.parse_item_ids("42, 0")
        with self.assertRaisesRegex(ValueError, "line 1"):
            rude_script_editor.parse_world_changes("Door;bad, open")


class RudeEditorCommandTests(unittest.TestCase):
    def setUp(self):
        _FakeRudeWindow.created.clear()

    def _app(self, rude_rez):
        app = object.__new__(editor_app.EditorApp)
        app.root = object()
        app.project = project_model.Project(rude_rez_path=rude_rez)
        app._rude_editor_windows = {}
        app._update_history_menu = lambda: None
        return app

    def test_command_opens_special_asset_without_a_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            rude_rez = os.path.join(tmp, "RUDE.REZ")
            write_minimal_rez(
                rude_rez,
                {
                    "RUDE/NPCNAME": b'997,"Quest Notes"\r\n',
                    "RUDE/TOPBLURB": b'997,997,"Journal"\r\n',
                    "RUDE/NPC997": _row(997, 997, 1),
                },
                resource_type=rezmgr._restype_for_filename("NPC.RUDE"),
            )
            app = self._app(rude_rez)

            with mock.patch("ui.rude_editor.RudeEditorWindow", _FakeRudeWindow):
                editor_app.EditorApp.cmd_rude_dialogue_editor(app, 997)

            self.assertIn(997, app.project.rude_assets)
            self.assertEqual(_FakeRudeWindow.created[0].asset.npc_nbr, 997)
            self.assertIsNone(getattr(app, "active", None))

    def test_command_can_create_an_independent_asset(self):
        class FakeMessagebox:
            @staticmethod
            def askyesno(*_args, **_kwargs):
                return True

            @staticmethod
            def showerror(title, body, **_kwargs):
                raise AssertionError(f"unexpected error: {title}: {body}")

        class FakeSimpledialog:
            @staticmethod
            def askstring(*_args, **_kwargs):
                return "Standalone NPC"

        with tempfile.TemporaryDirectory() as tmp:
            rude_rez = os.path.join(tmp, "RUDE.REZ")
            write_minimal_rez(
                rude_rez,
                {
                    "RUDE/NPCNAME": b'1,"Existing"\r\n',
                    "RUDE/TOPBLURB": b'1,1,"Hello"\r\n',
                    "RUDE/NPC1": _row(1, 1, 1),
                },
                resource_type=rezmgr._restype_for_filename("NPC.RUDE"),
            )
            app = self._app(rude_rez)

            with (
                mock.patch.object(editor_app, "messagebox", FakeMessagebox, create=True),
                mock.patch.object(editor_app, "simpledialog", FakeSimpledialog, create=True),
                mock.patch("ui.rude_editor.RudeEditorWindow", _FakeRudeWindow),
            ):
                editor_app.EditorApp.cmd_rude_dialogue_editor(app, 437)

            asset = app.project.rude_assets[437]
            self.assertTrue(asset.is_new)
            self.assertEqual(asset.metadata.name, "Standalone NPC")
            self.assertEqual(asset.metadata.initial_state, 437)
            self.assertEqual(len(asset.dialogue.state(437).choices), 1)
            self.assertEqual(asset.dialogue.state(437).choices[0].action.value, -1)


class DialogueScriptAttachmentTests(unittest.TestCase):
    @staticmethod
    def _object(*, npc_nbr=437, script_name=""):
        from mm9_patcher import mm9_patch as patcher
        return patcher.WorldObject("CAIHuman", [
            patcher.Property("NPCNbr", 3, 0, npc_nbr),
            patcher.Property("ScriptName", 0, 0, script_name),
        ])

    @staticmethod
    def _asset(*, npc_nbr=437, base_path=""):
        return rude_script.DialogueScriptAssetEdit(
            rude_script.DialogueScriptIntegration(
                npc_nbr=npc_nbr,
                base_virtual_path=base_path,
                base_source_text=(":Main\nExit\n" if base_path else ""),
                hooks=[rude_script.RudeExitHook(
                    completion_key=6001,
                    completion_sound=rude_script.DEFAULT_COMPLETION_SOUND,
                )],
            )
        )

    def _app(self, obj):
        class Level:
            def editor_materialize(self):
                return type("World", (), {"objects": [obj]})()

        app = object.__new__(editor_app.EditorApp)
        app.active = Level()
        app._selected_world_index = 0
        app.props_panel = type("Panel", (), {"current_obj": obj})()
        app._attached = []
        app._on_property_edited = (
            lambda name, value: app._attached.append((name, value)))
        return app

    def test_attach_accepts_matching_npc_with_no_existing_script(self):
        app = self._app(self._object())
        asset = self._asset()

        attached = editor_app.EditorApp._attach_dialogue_script_to_selected(
            app, asset)

        self.assertTrue(attached)
        self.assertEqual(
            app._attached,
            [("ScriptName", r"SCRIPTS\MM9EDITOR\NPC437_RUDE.SCR")],
        )

    def test_attach_refuses_to_drop_an_unintegrated_existing_script(self):
        class FakeMessagebox:
            errors = []

            @classmethod
            def showerror(cls, title, body, **_kwargs):
                cls.errors.append((title, body))

        app = self._app(self._object(script_name="NPC437.SCR"))
        asset = self._asset()
        with mock.patch.object(editor_app, "messagebox", FakeMessagebox, create=True):
            attached = editor_app.EditorApp._attach_dialogue_script_to_selected(
                app, asset)

        self.assertFalse(attached)
        self.assertEqual(app._attached, [])
        self.assertIn("not integrated", FakeMessagebox.errors[0][0].lower())


if __name__ == "__main__":
    unittest.main()
