import json
import os
import struct
import tempfile
import unittest
import zlib

from tests._path import ROOT  # noqa: F401

from features.model_conversion import abc_gltf_export, abc_obj_export, dtx_png_export, skin_resolver
from view3d.abc_loader import load_abc


MODELS = os.path.join(ROOT, "mm9_data", "MODELS")
SKINS = os.path.join(ROOT, "mm9_data", "SKINS")
CATALOG = os.path.join(ROOT, "catalog", "data", "catalog.json")


def _glb_json(path):
    with open(path, "rb") as stream:
        data = stream.read()
    magic, version, declared_length = struct.unpack_from("<III", data, 0)
    json_length, json_kind = struct.unpack_from("<II", data, 12)
    assert magic == 0x46546C67 and version == 2 and declared_length == len(data)
    assert json_kind == 0x4E4F534A
    return json.loads(data[20 : 20 + json_length].decode("utf-8").rstrip())


def _png_alpha_values(png_data):
    offset = 8
    idat = bytearray()
    width = height = 0
    while offset < len(png_data):
        size = struct.unpack_from(">I", png_data, offset)[0]
        kind = png_data[offset + 4 : offset + 8]
        payload = png_data[offset + 8 : offset + 8 + size]
        if kind == b"IHDR":
            width, height = struct.unpack_from(">II", payload, 0)
        elif kind == b"IDAT":
            idat.extend(payload)
        offset += 12 + size
    raw = zlib.decompress(bytes(idat))
    values = []
    stride = width * 4 + 1
    for row in range(height):
        scanline = raw[row * stride : (row + 1) * stride]
        assert scanline[0] == 0
        values.extend(scanline[4::4])
    return values


class AbcGltfExportTests(unittest.TestCase):
    def setUp(self):
        self.guard = os.path.join(MODELS, "GUARD.ABC")
        self.guard3 = os.path.join(SKINS, "GUARD3.DTX")
        self.guard_pole2 = os.path.join(SKINS, "GUARDPOLE2.DTX")
        if not all(os.path.isfile(path) for path in (self.guard, self.guard3, self.guard_pole2, CATALOG)):
            self.skipTest("extracted GUARD model/skins or catalog are unavailable")

    def test_unused_dtx_alpha_is_made_opaque(self):
        with open(self.guard3, "rb") as stream:
            converted = dtx_png_export.dtx_to_png_bytes(stream.read())
        self.assertIsNotNone(converted)
        _fmt, width, height, useful_alpha, png_data = converted
        self.assertEqual((width, height), (256, 256))
        self.assertFalse(useful_alpha)
        self.assertEqual(set(_png_alpha_values(png_data)), {255})

    def test_piece_named_skin_resolution(self):
        model = load_abc(self.guard, bake_static_bind_pose=True)
        result = skin_resolver.resolve_model_skins(
            model,
            explicit_skins=[f"guard={self.guard3}", f"pole={self.guard_pole2}"],
        )
        self.assertEqual([piece.piece_name for piece in result.pieces], ["guard", "pole"])
        self.assertEqual([os.path.basename(piece.skin_path) for piece in result.pieces], ["GUARD3.DTX", "GUARDPOLE2.DTX"])
        self.assertEqual(result.warnings, [])

    def test_one_skin_broadcast_is_reported(self):
        model = load_abc(self.guard, bake_static_bind_pose=True)
        result = skin_resolver.resolve_model_skins(model, explicit_skins=[self.guard3])
        self.assertTrue(any("broadcast" in warning for warning in result.warnings))
        self.assertEqual(len({piece.skin_path for piece in result.pieces}), 1)

    def test_catalog_guard_variants_are_deduplicated(self):
        model = load_abc(self.guard, bake_static_bind_pose=True)
        variants, warnings = skin_resolver.catalog_variants_for_model(model, self.guard, CATALOG, SKINS)
        self.assertEqual([variant.name for variant in variants], ["Guard A", "Guard B", "Guard C"])
        self.assertEqual(
            [[os.path.basename(path).upper() for path in variant.skin_paths] for variant in variants],
            [
                ["GUARD1.DTX", "GUARDPOLE.DTX"],
                ["GUARD2.DTX", "GUARDPOLE.DTX"],
                ["GUARD3.DTX", "GUARDPOLE2.DTX"],
            ],
        )
        self.assertEqual(warnings, [])

    def test_game_neutral_model_variants_are_resolved_before_legacy_actor_table(self):
        model = load_abc(self.guard, bake_static_bind_pose=True)
        with tempfile.TemporaryDirectory() as tmp:
            skins_root = os.path.join(tmp, "SKINS")
            os.makedirs(skins_root)
            for name in ("BODYA.DTX", "POLEA.DTX", "BODYB.DTX", "POLEB.DTX"):
                with open(os.path.join(skins_root, name), "wb") as stream:
                    stream.write(b"test")
            catalog_path = os.path.join(tmp, "catalog.json")
            with open(catalog_path, "w", encoding="utf-8") as stream:
                json.dump({
                    "model_variants": {
                        r"models\guard.abc": [
                            {
                                "name": "LoMM A",
                                "skins": [r"skins\bodya.dtx", r"skins\polea.dtx"],
                                "source_keys": ["object.lto:GuardA"],
                            },
                            {
                                "name": "LoMM B",
                                "skins": [r"skins\bodyb.dtx", r"skins\poleb.dtx"],
                                "source_keys": ["asset-name:GuardB"],
                            },
                        ],
                    },
                    "actor_visuals": {},
                }, stream)

            variants, warnings = skin_resolver.catalog_variants_for_model(
                model, self.guard, catalog_path, skins_root
            )

        self.assertEqual([variant.name for variant in variants], ["LoMM A", "LoMM B"])
        self.assertEqual(
            [variant.source_keys for variant in variants],
            [("object.lto:GuardA",), ("asset-name:GuardB",)],
        )
        self.assertEqual(warnings, [])

    def test_self_contained_glb_has_normals_bounds_and_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = abc_gltf_export.export_abc_to_gltf(
                self.guard,
                tmp,
                base_name="GuardVariants",
                write_glb=True,
                skins_root=SKINS,
                catalog_path=CATALOG,
                all_variants=True,
            )
            self.assertTrue(os.path.isfile(result.glb_path))
            self.assertEqual(result.variant_names, ("Guard A", "Guard B", "Guard C"))
            self.assertEqual(result.triangle_count, 718)
            self.assertEqual(result.texture_count, 5)
            self.assertEqual([name for name in os.listdir(tmp) if name.lower().endswith(".png")], [])

            gltf = _glb_json(result.glb_path)
            self.assertNotIn("uri", gltf["buffers"][0])
            self.assertEqual(gltf["extensionsUsed"], ["KHR_materials_variants"])
            self.assertEqual(
                [value["name"] for value in gltf["extensions"]["KHR_materials_variants"]["variants"]],
                ["Guard A", "Guard B", "Guard C"],
            )
            self.assertTrue(all("bufferView" in image and "uri" not in image for image in gltf["images"]))
            self.assertTrue(all(material["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0] for material in gltf["materials"]))
            for mesh in gltf["meshes"]:
                primitive = mesh["primitives"][0]
                self.assertEqual(set(primitive["attributes"]), {"POSITION", "NORMAL", "TEXCOORD_0"})
                position = gltf["accessors"][primitive["attributes"]["POSITION"]]
                self.assertEqual(len(position["min"]), 3)
                self.assertEqual(len(position["max"]), 3)
                mappings = primitive["extensions"]["KHR_materials_variants"]["mappings"]
                self.assertEqual([item["variants"] for item in mappings], [[0], [1], [2]])

    def test_separate_gltf_writes_bin_and_png_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = abc_gltf_export.export_abc_to_gltf(
                self.guard,
                tmp,
                base_name="GuardC",
                skin_paths=[f"guard={self.guard3}", f"pole={self.guard_pole2}"],
            )
            self.assertTrue(os.path.isfile(result.gltf_path))
            self.assertTrue(os.path.isfile(result.bin_path))
            with open(result.gltf_path, "r", encoding="utf-8") as stream:
                gltf = json.load(stream)
            self.assertEqual(gltf["buffers"][0]["uri"], "GuardC.bin")
            self.assertTrue(all(os.path.isfile(os.path.join(tmp, image["uri"])) for image in gltf["images"]))

    def test_geometry_only_obj_compatibility_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = abc_obj_export.export_abc_to_obj(self.guard, tmp, base_name="GuardObj")
            self.assertTrue(os.path.isfile(result.obj_path))
            self.assertTrue(os.path.isfile(result.mtl_path))
            self.assertEqual(result.piece_count, 2)
            self.assertEqual(result.triangle_count, 718)
            with open(result.obj_path, "r", encoding="utf-8") as stream:
                text = stream.read()
            self.assertIn("o guard", text)
            self.assertIn("o pole", text)


if __name__ == "__main__":
    unittest.main()
