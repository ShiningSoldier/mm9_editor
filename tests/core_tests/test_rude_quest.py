import os
import tempfile
import unittest


from tests._path import ROOT  # noqa: F401

from core import rezmgr
from core import rude
from core import rude_quest
from core import project as project_model
from tests.core_tests.test_game_resources import write_minimal_rez


def choice(
    state_id,
    branch_id,
    action,
    *,
    required=(),
    forbidden=(),
    granted=(),
    removed=(),
    player_text="Choice",
):
    def slots(values, size=5):
        values = tuple(values)
        return values + (0,) * (size - len(values))

    return rude.RudeChoice(
        npc_nbr=42,
        state_id=state_id,
        branch_id=branch_id,
        player_text=player_text,
        npc_response="Response",
        action=rude.RudeAction(action),
        conditions=rude.RudeKeyConditions(
            required=slots(required),
            forbidden=slots(forbidden),
        ),
        effects=rude.RudeKeyEffects(
            granted=slots(granted),
            removed=slots(removed),
        ),
    )


def dialogue(*choices):
    return rude.RudeDialogue(
        rude.RudeDialogueMetadata(42, "Quest NPC", 10, "Hello"),
        choices,
    )


class QuestKeyIndexTests(unittest.TestCase):
    def test_script_index_resolves_literals_local_values_and_uncertain_values(self):
        source = """
        #number KEY_MAIN = 101
        #number dynamicKey
        dynamicKey = 202
        set dynamicKey, 203
        HasKey KEY_MAIN, g_ntemp
        GiveKey, 404
        TakeKey dynamicKey
        HasKey runtimeParam, g_ntemp
        ; GiveKey 999
        """
        index = rude_quest.QuestKeyIndex()

        rude_quest.index_script_text(index, "SCRIPTS/QUEST", source)

        self.assertEqual(
            [usage.role for usage in index.usage_for(101)],
            [rude_quest.QuestKeyRole.SCRIPT_CHECK],
        )
        self.assertEqual(index.usage_for(404)[0].role, rude_quest.QuestKeyRole.SCRIPT_GRANT)
        self.assertFalse(index.usage_for(202)[0].certain)
        self.assertFalse(index.usage_for(203)[0].certain)
        self.assertNotIn(999, index.used_keys)
        self.assertEqual(len(index.unresolved_script_usages), 1)
        self.assertEqual(index.unresolved_script_usages[0].operand, "runtimeParam")

    def test_dialogue_index_marks_native_effect_slots_as_ambiguous(self):
        model = dialogue(
            choice(
                10,
                1,
                20,
                required=(1,),
                forbidden=(2,),
                granted=(3,),
                removed=(4,),
            ),
            choice(20, 1, -4, granted=(2016,)),
        )
        index = rude_quest.QuestKeyIndex()

        rude_quest.index_dialogue(index, model)

        self.assertEqual(index.usage_for(1)[0].role, rude_quest.QuestKeyRole.RUDE_REQUIRED)
        self.assertEqual(index.usage_for(3)[0].role, rude_quest.QuestKeyRole.RUDE_GRANTED)
        native = index.usage_for(2016)[0]
        self.assertEqual(native.role, rude_quest.QuestKeyRole.RUDE_NATIVE_EFFECT)
        self.assertFalse(native.certain)

    def test_archive_index_uses_project_dialogue_override_and_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            rude_rez = os.path.join(tmp, "RUDE.REZ")
            scripts_rez = os.path.join(tmp, "SCRIPTS.REZ")
            source_dialogue = dialogue(choice(10, 1, -1, granted=(111,)))
            override_dialogue = dialogue(choice(10, 1, -1, granted=(222,)))
            write_minimal_rez(
                rude_rez,
                {
                    "RUDE/NPCNAME": b'42,"Quest NPC"\r\n',
                    "RUDE/TOPBLURB": b'42,10,"Hello"\r\n',
                    "RUDE/NPC42": source_dialogue.to_bytes(),
                },
                resource_type=rezmgr._restype_for_filename("NPC.RUDE"),
            )
            write_minimal_rez(
                scripts_rez,
                {"SCRIPTS/QUEST": b"HasKey 222, g_ntemp\r\nTakeKey 333\r\n"},
            )

            index = rude_quest.build_quest_key_index(
                rude_rez,
                scripts_rez,
                dialogue_overrides={42: override_dialogue},
            )

            self.assertNotIn(111, index.used_keys)
            self.assertEqual(len(index.usage_for(222)), 2)
            self.assertEqual(index.usage_for(333)[0].role, rude_quest.QuestKeyRole.SCRIPT_REMOVE)
            self.assertEqual(index.rude_resource_count, 1)
            self.assertEqual(index.script_resource_count, 1)


class DialogueValidationTests(unittest.TestCase):
    def test_validation_reports_unreachable_missing_and_impossible_paths(self):
        model = dialogue(
            choice(10, 1, 20),
            choice(20, 1, 404),
            choice(30, 1, -1, required=(7,), forbidden=(7,)),
        )

        report = rude_quest.validate_dialogue(model)
        codes = {issue.code for issue in report.issues}

        self.assertIn("MISSING_STATE_TARGET", codes)
        self.assertIn("IMPOSSIBLE_KEY_CONDITION", codes)
        self.assertIn("UNREACHABLE_STATE", codes)
        self.assertIn("NO_TERMINAL_PATH", codes)
        self.assertEqual(report.reachable_states, frozenset({10, 20}))
        self.assertEqual(report.unreachable_states, frozenset({30}))

    def test_validation_is_action_aware_for_native_actions(self):
        model = dialogue(
            choice(10, 1, -4),
            choice(10, 2, -11, required=(455,), removed=(456,)),
            choice(10, 3, -13),
        )

        codes = {issue.code for issue in rude_quest.validate_dialogue(model).issues}

        self.assertIn("SKILL_TRAINING_PARAMETER_MISSING", codes)
        self.assertIn("DISMISS_KEY_MISMATCH", codes)
        self.assertIn("UNKNOWN_NATIVE_ACTION", codes)
        self.assertNotIn("MISSING_STATE_TARGET", codes)

    def test_valid_skill_training_parameter_is_not_treated_as_missing_state(self):
        model = dialogue(choice(10, 1, -4, granted=(2016,)))

        report = rude_quest.validate_dialogue(model)

        self.assertFalse(report.errors)
        self.assertEqual(report.states_with_terminal_path, frozenset({10}))

    def test_special_table_actions_are_not_validated_as_dialogue_transitions(self):
        metadata = rude.RudeDialogueMetadata(998, "Auto Notes", 998, "")
        model = rude.RudeDialogue(metadata, [
            rude.RudeChoice(
                npc_nbr=998,
                state_id=998,
                branch_id=1,
                player_text="Trainer note",
                npc_response="blank",
                action=rude.RudeAction(0),
                conditions=rude.RudeKeyConditions(required=(2001, 0, 0, 0, 0)),
            ),
            rude.RudeChoice(
                npc_nbr=998,
                state_id=2,
                branch_id=1,
                player_text="Unused stock row",
                npc_response="blank",
                action=rude.RudeAction.close(),
            ),
        ])

        report = rude_quest.validate_dialogue(model)
        codes = [issue.code for issue in report.issues]

        self.assertNotIn("MISSING_STATE_TARGET", codes)
        self.assertNotIn("NO_TERMINAL_PATH", codes)
        self.assertEqual(report.unreachable_states, frozenset({2}))
        self.assertEqual(report.issues[0].severity, rude_quest.QuestIssueSeverity.INFO)


class SpecialEntryTests(unittest.TestCase):
    def _special(self, npc_nbr):
        metadata = rude.RudeDialogueMetadata(
            npc_nbr,
            "Quest Notes" if npc_nbr == 997 else "Awards",
            npc_nbr,
            "",
        )
        return rude.RudeDialogue(metadata, [rude.RudeChoice(
            npc_nbr=npc_nbr,
            state_id=npc_nbr,
            branch_id=3,
            player_text="Existing",
            npc_response="blank",
            action=rude.RudeAction.state(npc_nbr),
        )])

    def test_append_quest_note_uses_stock_shape_and_next_branch(self):
        model = self._special(997)

        entry = rude_quest.append_quest_note(
            model,
            "Find the relic",
            "The relic is hidden in the keep.",
            required_keys=(5001,),
            forbidden_keys=(5002,),
        )

        self.assertEqual(entry.branch_id, 4)
        self.assertEqual(entry.state_id, 997)
        self.assertEqual(entry.action.target_state, 997)
        self.assertEqual(entry.conditions.required_keys, (5001,))
        self.assertEqual(entry.conditions.forbidden_keys, (5002,))

    def test_append_award_requires_a_key_and_uses_blank_response(self):
        model = self._special(999)
        with self.assertRaisesRegex(ValueError, "requires at least one"):
            rude_quest.append_award(model, "Won", required_keys=())

        entry = rude_quest.append_award(model, "Recovered the relic", (5002,))

        self.assertEqual(entry.npc_response, "blank")
        self.assertEqual(entry.action.target_state, 999)

    def test_quest_note_helper_writes_a_runtime_typed_archive_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_rez = os.path.join(tmp, "game", "data", "RUDE.REZ")
            work_dir = os.path.join(tmp, "output")
            source = self._special(997)
            write_minimal_rez(
                source_rez,
                {
                    "RUDE/NPCNAME": b'997,"Quest Notes"\r\n',
                    "RUDE/TOPBLURB": b'997,997,"Journal"\r\n',
                    "RUDE/NPC997": source.to_bytes(),
                },
                resource_type=rezmgr._restype_for_filename("NPC.RUDE"),
            )
            project = project_model.Project(
                rude_rez_path=source_rez,
                work_dir=work_dir,
            )
            asset = project.open_rude_asset(997)
            rude_quest.append_quest_note(
                asset.dialogue,
                "Find the relic",
                "Search Anskram Keep.",
                required_keys=(5001,),
            )

            plan = project.save_plan()
            project.execute(plan)

            output_rez = os.path.join(
                work_dir, plan.batch_id, "data", "RUDE.REZ")
            with rezmgr.RezReader(output_rez) as reader:
                entry = reader.find("RUDE/NPC997.RUDE")
                output = reader.extract_to_bytes("RUDE/NPC997")
            self.assertEqual(entry.type_str, "RUDE")
            reparsed = rude.RudeDialogue.from_bytes(asset.metadata, output)
            self.assertEqual(reparsed.choices_in_file_order[-1].player_text, "Find the relic")
            self.assertEqual(
                reparsed.choices_in_file_order[-1].conditions.required_keys,
                (5001,),
            )


if __name__ == "__main__":
    unittest.main()
