import csv
import io
import unittest


from tests._path import ROOT  # noqa: F401

from core import rude


def dialogue_row(
    npc_nbr,
    state_id,
    branch_id,
    player_text,
    npc_response,
    action,
    trailing=None,
):
    trailing = list(trailing if trailing is not None else [0] * 24)
    if len(trailing) != 24:
        raise AssertionError("test RUDE row requires 24 trailing columns")
    quoted_player = '"' + player_text.replace('"', '""') + '"'
    quoted_response = '"' + npc_response.replace('"', '""') + '"'
    return ",".join([
        str(npc_nbr),
        str(state_id),
        str(branch_id),
        quoted_player,
        quoted_response,
        str(action),
        *(str(value) for value in trailing),
    ])


class RudeModelTests(unittest.TestCase):
    def setUp(self):
        self.metadata = rude.RudeDialogueMetadata(
            npc_nbr=42,
            name="Test NPC",
            initial_state=10,
            opening_blurb="Hello",
        )

    def test_dialogue_round_trip_preserves_all_columns_and_source_order(self):
        trailing = [
            101, 901, 102, 902, 103, 903, 104, 904, 105,
            201, 202, 203, 204, 205,
            301, 302, 303, 304, 305,
            401, 402, 403, 404, 405,
        ]
        source = "\r\n".join([
            dialogue_row(
                42, 10, 2, 'Ask, now', 'He said "yes".', -5, trailing),
            dialogue_row(42, 20, 1, "Middle", "Still here", -1),
            dialogue_row(42, 10, 1, "First", "Back again", -13),
        ]) + "\r\n"

        dialogue = rude.RudeDialogue.parse(self.metadata, source)

        self.assertEqual(dialogue.to_text(), source)
        self.assertEqual([state.state_id for state in dialogue.states], [10, 20])
        self.assertEqual(
            [choice.branch_id for choice in dialogue.state(10).choices],
            [2, 1],
        )
        self.assertEqual(
            [choice.state_id for choice in dialogue.choices_in_file_order],
            [10, 20, 10],
        )

        choice = dialogue.choices_in_file_order[0]
        self.assertEqual(choice.conditions.required, (101, 102, 103, 104, 105))
        self.assertEqual(choice.conditions.reserved, (901, 902, 903, 904))
        self.assertEqual(choice.effects.granted, (201, 202, 203, 204, 205))
        self.assertEqual(choice.conditions.forbidden, (301, 302, 303, 304, 305))
        self.assertEqual(choice.effects.removed, (401, 402, 403, 404, 405))
        self.assertEqual(choice.action.native_action, rude.RudeNativeAction.TRAVEL)
        self.assertTrue(choice.conditions.matches({101, 102, 103, 104, 105}))
        self.assertFalse(choice.conditions.matches({101, 102, 103, 104, 105, 301}))

    def test_actions_preserve_known_and_unknown_runtime_values(self):
        state = rude.RudeAction(17)
        close = rude.RudeAction(-1)
        known = rude.RudeAction(-14)
        unknown = rude.RudeAction(-13)

        self.assertEqual(state.kind, rude.RudeActionKind.STATE)
        self.assertEqual(state.target_state, 17)
        self.assertEqual(close.kind, rude.RudeActionKind.CLOSE)
        self.assertEqual(known.native_action, rude.RudeNativeAction.PROMOTION)
        self.assertEqual(unknown.kind, rude.RudeActionKind.NATIVE)
        self.assertIsNone(unknown.native_action)
        self.assertEqual(unknown.value, -13)

    def test_metadata_catalog_is_lossless_and_upserts_in_place(self):
        npcname = '1,"Yrsa"\r\n3,"Sven"\r\n2,"Forad"\r\n'
        topblurb = (
            '1,1,"Hello"\r\n'
            '3,30,"Welcome"\r\n'
            '2,2,"Greetings"\r\n'
        )
        catalog = rude.RudeMetadataCatalog.parse(npcname, topblurb)

        self.assertEqual(catalog.to_npcname_text(), npcname)
        self.assertEqual(catalog.to_topblurb_text(), topblurb)
        self.assertEqual(catalog.metadata_for(3).initial_state, 30)

        catalog.upsert(rude.RudeDialogueMetadata(
            npc_nbr=3,
            name='Sven, the "Bold"',
            initial_state=31,
            opening_blurb='A "new" greeting',
        ))
        catalog.upsert(rude.RudeDialogueMetadata(
            npc_nbr=437,
            name="New NPC",
            initial_state=7,
            opening_blurb="Fresh dialogue",
        ))

        names_out = catalog.to_npcname_text()
        blurbs_out = catalog.to_topblurb_text()
        self.assertEqual(
            [entry.npc_nbr for entry in catalog.names],
            [1, 3, 2, 437],
        )
        self.assertNotIn("\n", names_out.replace("\r\n", ""))
        self.assertNotIn("\n", blurbs_out.replace("\r\n", ""))

        reparsed = rude.RudeMetadataCatalog.parse(names_out, blurbs_out)
        self.assertEqual(reparsed.metadata_for(3).name, 'Sven, the "Bold"')
        self.assertEqual(reparsed.metadata_for(3).initial_state, 31)
        self.assertEqual(reparsed.metadata_for(437).initial_state, 7)

    def test_metadata_preserves_multiline_csv_and_latin1_controls(self):
        npcname = '42,"Test NPC"\r\n'
        topblurb = '42,10,"First line\r\nsecond line with \x85 control"\r\n'

        catalog = rude.RudeMetadataCatalog.parse(npcname, topblurb)

        self.assertEqual(catalog.to_bytes(), (
            npcname.encode("latin-1"),
            topblurb.encode("latin-1"),
        ))
        self.assertEqual(
            catalog.metadata_for(42).opening_blurb,
            "First line\r\nsecond line with \x85 control",
        )

    def test_editing_and_reordering_keep_valid_ordered_rows(self):
        source = "\r\n".join([
            dialogue_row(42, 10, 2, "Second", "Two", 10),
            dialogue_row(42, 20, 1, "Other state", "Other", -1),
            dialogue_row(42, 10, 1, "First", "One", 10),
        ]) + "\r\n"
        dialogue = rude.RudeDialogue.parse(self.metadata, source)
        dialogue.choices_in_file_order[0].player_text = 'Edited, with "quotes"'
        dialogue.reorder_choice(10, 0, 1)

        output = dialogue.to_text()
        rows = list(csv.reader(io.StringIO(output, newline="")))
        self.assertTrue(all(len(row) == 30 for row in rows))
        self.assertEqual([int(row[1]) for row in rows], [10, 20, 10])
        self.assertEqual([int(row[2]) for row in rows], [1, 1, 2])
        self.assertEqual(rows[2][3], 'Edited, with "quotes"')

    def test_simple_dialogue_uses_model_defaults_without_losing_quotes(self):
        metadata = rude.RudeDialogueMetadata(
            npc_nbr=437,
            name="New NPC",
            initial_state=12,
            opening_blurb="Hello",
        )
        dialogue = rude.make_simple_dialogue(
            metadata,
            [('Ask "why"?', "Because, traveler.")],
        )
        reparsed = rude.RudeDialogue.from_bytes(metadata, dialogue.to_bytes())

        state = reparsed.state(12)
        self.assertEqual(len(state.choices), 2)
        self.assertEqual(state.choices[0].player_text, 'Ask "why"?')
        self.assertEqual(state.choices[0].action.target_state, 12)
        self.assertEqual(state.choices[1].action.kind, rude.RudeActionKind.CLOSE)

    def test_simulator_filters_choices_and_updates_mock_party_keys(self):
        gated = [0] * 24
        gated[0] = 1       # required key 1 (column 6)
        gated[9] = 2       # grant key 2 (column 15)
        gated[14] = 9      # forbid key 9 (column 20)
        gated[19] = 1      # remove key 1 (column 25)
        needs_two = [0] * 24
        needs_two[0] = 2
        source = "\r\n".join([
            dialogue_row(42, 10, 1, "Proceed", "Advanced", 20, gated),
            dialogue_row(42, 10, 2, "Leave", "Closed", -1),
            dialogue_row(42, 20, 1, "Travel", "Opening travel", -5, needs_two),
        ]) + "\r\n"
        dialogue = rude.RudeDialogue.parse(self.metadata, source)
        simulator = rude.RudeSimulator(dialogue, active_keys={1})

        self.assertEqual(
            [choice.branch_id for choice in simulator.available_choices],
            [1, 2],
        )
        first = simulator.choose(0)
        self.assertEqual(first.current_state, 20)
        self.assertEqual(first.active_keys, frozenset({2}))
        self.assertEqual(first.granted_keys, (2,))
        self.assertEqual(first.removed_keys, (1,))
        self.assertEqual(
            [choice.branch_id for choice in simulator.available_choices],
            [1],
        )

        terminal = simulator.choose(0)
        self.assertTrue(terminal.terminal)
        self.assertEqual(terminal.action.native_action, rude.RudeNativeAction.TRAVEL)
        self.assertEqual(simulator.available_choices, ())

        simulator.reset(active_keys=set())
        self.assertEqual(
            [choice.branch_id for choice in simulator.available_choices],
            [2],
        )
        self.assertEqual(
            simulator.choose(0).action.kind,
            rude.RudeActionKind.CLOSE,
        )

    def test_state_mutations_keep_graph_edges_and_order_consistent(self):
        source = "\r\n".join([
            dialogue_row(42, 10, 1, "To twenty", "Go", 20),
            dialogue_row(42, 20, 1, "Back", "Return", 10),
            dialogue_row(42, 10, 2, "Close", "Bye", -1),
        ]) + "\r\n"
        dialogue = rude.RudeDialogue.parse(self.metadata, source)

        dialogue.rename_state(10, 30)

        self.assertEqual(dialogue.metadata.initial_state, 30)
        self.assertEqual([state.state_id for state in dialogue.states], [30, 20])
        self.assertEqual(dialogue.state(20).choices[0].action.target_state, 30)
        self.assertEqual(
            [(edge.source_state, edge.target_state) for edge in dialogue.graph_edges],
            [(30, 20), (20, 30), (30, None)],
        )
        removed = dialogue.remove_choice(30, 1)
        self.assertEqual(removed.branch_id, 2)
        self.assertEqual(dialogue.next_branch_id(30), 2)
        dialogue.remove_state(20)
        self.assertEqual([state.state_id for state in dialogue.states], [30])

    def test_invalid_column_count_reports_resource_and_line(self):
        with self.assertRaisesRegex(
                rude.RudeFormatError, r"NPC42 line 1: expected 30 columns"):
            rude.RudeDialogue.parse(self.metadata, "42,10,1\r\n")


if __name__ == "__main__":
    unittest.main()
