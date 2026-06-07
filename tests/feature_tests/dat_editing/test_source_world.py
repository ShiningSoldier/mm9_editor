import os
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import source_world


MINIMAL_LTA = r'''
; tiny DEdit-style source world fixture
( world
  ( header
    ( versioncode 2 )
    ( infostring "fixture world" )
  )
  ( polyhedronlist (
    ( polyhedron
      ( color 128 64 32 )
      ( pointlist
        ( 0 0 0 255 255 255 255 )
        ( 128 0 0 255 255 255 255 )
        ( 128 0 128 255 255 255 255 )
        ( 0 0 128 255 255 255 255 )
      )
      ( polylist (
        ( editpoly
          ( f 0 1 2 3 )
          ( n 0 1 0 )
          ( dist 0 )
          ( textureinfo
            ( 0 0 0 )
            ( 1 0 0 )
            ( 0 0 1 )
            ( sticktopoly 1 )
            ( name "TEXTURES\World\Floor.dtx" )
          )
          ( flags solid shadow )
          ( shade 0 0 0 )
          ( physicsmaterial "Stone" )
          ( surfacekey "FloorSurface" )
          ( textures ( ) )
        )
      ) )
    )
  ) )
  ( globalproplist (
    ( proplist ( ) )
  ) )
  ( nodehierarchy
    ( worldnode
      ( type null )
      ( label "Root" )
      ( nodeid 1 )
      ( flags ( worldroot expanded ) )
      ( properties ( propid 0 ) )
      ( childlist (
        ( worldnode
          ( type brush )
          ( brushindex 0 )
          ( label "FloorBrush" )
          ( nodeid 2 )
          ( flags ( ) )
          ( properties ( propid 0 ) )
        )
      ) )
    )
  )
  ( navigatorposlist ( ) )
)
'''


class SourceWorldTests(unittest.TestCase):
    def test_lta_text_to_geometry_scene_extracts_brushes_and_opq(self):
        scene = source_world.lta_text_to_geometry_scene(MINIMAL_LTA, source_path="fixture.lta")

        self.assertEqual(scene.metadata["kind"], "lithtech_lta_source_world")
        self.assertEqual(scene.metadata["versioncode"], "2")
        self.assertEqual(scene.metadata["infostring"], "fixture world")
        self.assertEqual(scene.metadata["brush_count"], 1)
        self.assertEqual(scene.source_path, os.path.abspath("fixture.lta"))
        self.assertEqual(scene.material_texture_map()["TEXTURES\\World\\Floor.dtx"], "TEXTURES\\World\\Floor.dtx")

        model = scene.mesh_models()[0]
        self.assertEqual(model.name, "FloorBrush")
        self.assertEqual(model.extras["brush_index"], 0)
        self.assertEqual(model.extras["color"], [128.0, 64.0, 32.0])
        self.assertEqual(model.points[2], (128.0, 0.0, 128.0))

        face = model.faces[0]
        self.assertEqual(face.vertex_indices, [0, 1, 2, 3])
        self.assertEqual(face.material_name, "TEXTURES\\World\\Floor.dtx")
        self.assertEqual(face.uv_coords, [None, None, None, None])
        self.assertEqual(face.extras["normal"], [0.0, 1.0, 0.0])
        self.assertEqual(face.extras["dist"], 0.0)
        self.assertEqual(face.extras["uv_o"], [0.0, 0.0, 0.0])
        self.assertEqual(face.extras["uv_p"], [1.0, 0.0, 0.0])
        self.assertEqual(face.extras["uv_q"], [0.0, 0.0, 1.0])
        self.assertEqual(face.extras["physics_material"], "Stone")
        self.assertEqual(face.extras["surface_key"], "FloorSurface")
        self.assertEqual(face.extras["surface_flags"], ["solid", "shadow"])

    def test_load_lta_geometry_scene_rejects_compressed_ltc_for_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "world.ltc")
            with open(path, "w", encoding="utf-8") as f:
                f.write(MINIMAL_LTA)

            with self.assertRaisesRegex(ValueError, "compressed .ltc"):
                source_world.load_lta_geometry_scene(path)

    def test_lta_text_to_geometry_scene_reports_missing_world(self):
        with self.assertRaises(source_world.LtaParseError):
            source_world.lta_text_to_geometry_scene("( header ( versioncode 2 ) )")


if __name__ == "__main__":
    unittest.main()
