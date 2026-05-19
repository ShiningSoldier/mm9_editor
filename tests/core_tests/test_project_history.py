import os
import sys
import unittest


from tests._path import ROOT  # noqa: F401

from core import project as P
import mm9_patch as patcher


def make_object(name, pos=(0.0, 0.0, 0.0)):
    return patcher.WorldObject("TestObject", [
        patcher.Property("Name", 0, 0, name),
        patcher.Property("Pos", 1, 0, tuple(pos)),
        patcher.Property("Rotation", 7, 0, (0.0, 0.0, 0.0, 1.0)),
    ])


def make_level(*names):
    header = patcher.Header(66, 0, 0, (0,) * 8)
    world = patcher.World(
        header=header,
        pre_objects=b"",
        objects=[make_object(name) for name in names],
        render_data=b"",
    )
    return P.LevelEdit(path="dummy.dat", world=world)


class LevelHistoryTests(unittest.TestCase):
    def test_undo_redo_moves_ops_between_stacks(self):
        level = make_level("A")
        edit = P.EditOp(target_index=0, overrides={"Name": "B"})

        level.append_op(edit)
        self.assertEqual(level.undo_last_op(), edit)
        self.assertEqual(level.ops, [])
        self.assertEqual(level.redo_ops, [edit])

        self.assertEqual(level.redo_last_op(), edit)
        self.assertEqual(level.ops, [edit])
        self.assertEqual(level.redo_ops, [])

    def test_new_op_clears_redo(self):
        level = make_level("A")
        level.append_op(P.EditOp(target_index=0, overrides={"Name": "B"}))
        level.undo_last_op()

        level.append_op(P.EditOp(target_index=0, overrides={"Name": "C"}))

        self.assertEqual(level.redo_ops, [])
        self.assertEqual(level.materialize().objects[0].get("Name"), "C")

    def test_move_coalescing_does_not_mutate_baseline(self):
        level = make_level("A")

        first = level.coalesce_move_op(0, new_pos=(1.0, 2.0, 3.0))
        second = level.coalesce_move_op(0, new_pos=(4.0, 5.0, 6.0))

        self.assertIs(first, second)
        self.assertEqual(len(level.ops), 1)
        self.assertEqual(level.world.objects[0].get("Pos"), (0.0, 0.0, 0.0))
        self.assertEqual(level.materialize().objects[0].get("Pos"), (4.0, 5.0, 6.0))

        level.undo_last_op()
        self.assertEqual(level.materialize().objects[0].get("Pos"), (0.0, 0.0, 0.0))

    def test_materialized_index_maps_through_pending_delete(self):
        level = make_level("A", "B", "C")
        level.append_op(P.DeleteOp(target_index=1))

        self.assertEqual(level.materialized_existing_indices(), [0, 2])
        self.assertEqual(level.existing_index_for_materialized(1), 2)

        level.append_op(P.AddOp(template=make_object("D")))
        names = [obj.get("Name") for obj in level.materialize().objects]

        self.assertEqual(names, ["A", "C", "D"])
        self.assertEqual(level.add_offset_for_materialized(2), 0)


if __name__ == "__main__":
    unittest.main()
