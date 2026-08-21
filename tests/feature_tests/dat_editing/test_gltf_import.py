import base64
import copy
import json
import os
import struct
import tempfile
import unittest

from tests._path import ROOT  # noqa: F401

from features.dat_editing import geometry_scene, gltf_export, gltf_import, mesh_topology


def _triangle_payload(*, strided=False, indices=(0, 1, 2), include_uv=True):
    positions = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    raw = bytearray()
    if strided:
        raw.extend(b"\0" * 4)
        for x, y, z in positions:
            raw.extend(struct.pack("<4f", x, y, z, 99.0))
        position_length = 4 + 16 * len(positions)
    else:
        for point in positions:
            raw.extend(struct.pack("<3f", *point))
        position_length = 12 * len(positions)

    views = [{
        "buffer": 0,
        "byteOffset": 0,
        "byteLength": position_length,
    }]
    if strided:
        views[0]["byteStride"] = 16
    accessors = [{
        "bufferView": 0,
        "componentType": 5126,
        "count": 3,
        "type": "VEC3",
    }]
    if strided:
        accessors[0]["byteOffset"] = 4

    if include_uv:
        while len(raw) % 4:
            raw.append(0)
        uv_offset = len(raw)
        raw.extend(struct.pack("<6f", 0.0, 0.0, 1.0, 0.0, 0.0, 1.0))
        views.append({
            "buffer": 0,
            "byteOffset": uv_offset,
            "byteLength": 24,
        })
        accessors.append({
            "bufferView": len(views) - 1,
            "componentType": 5126,
            "count": 3,
            "type": "VEC2",
        })

    index_accessor = None
    if indices is not None:
        while len(raw) % 4:
            raw.append(0)
        index_offset = len(raw)
        raw.extend(struct.pack("<" + "H" * len(indices), *indices))
        views.append({
            "buffer": 0,
            "byteOffset": index_offset,
            "byteLength": 2 * len(indices),
        })
        accessors.append({
            "bufferView": len(views) - 1,
            "componentType": 5123,
            "count": len(indices),
            "type": "SCALAR",
        })
        index_accessor = len(accessors) - 1
    return bytes(raw), views, accessors, index_accessor


def _triangle_document(
    uri,
    raw,
    views,
    accessors,
    index_accessor,
    *,
    include_uv=True,
):
    attributes = {"POSITION": 0}
    if include_uv:
        attributes["TEXCOORD_0"] = 1
    primitive = {
        "attributes": attributes,
        "material": 0,
    }
    if index_accessor is not None:
        primitive["indices"] = index_accessor
    buffer = {"byteLength": len(raw)}
    if uri is not None:
        buffer["uri"] = uri
    return {
        "asset": {"version": "2.0", "generator": "small-test-fixture"},
        "scene": 0,
        "scenes": [{"name": "Main", "nodes": [0]}],
        "nodes": [{"name": "Triangle", "mesh": 0}],
        "meshes": [{"name": "TriangleMesh", "primitives": [primitive]}],
        "materials": [{
            "name": "Stone",
            "extras": {"MM9_texture": "TEXTURES\\World\\Stone.dtx"},
        }],
        "buffers": [buffer],
        "bufferViews": views,
        "accessors": accessors,
    }


def _write_gltf(directory, document, raw=None, *, name="scene.gltf"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="\n") as target:
        json.dump(document, target)
    if raw is not None:
        with open(os.path.join(directory, "scene.bin"), "wb") as target:
            target.write(raw)
    return path


def _write_glb(directory, document, binary, *, name="scene.glb"):
    encoded_json = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded_json += b" " * ((-len(encoded_json)) % 4)
    padded_binary = binary + b"\0" * ((-len(binary)) % 4)
    total_length = 12 + 8 + len(encoded_json) + 8 + len(padded_binary)
    data = (
        struct.pack("<III", 0x46546C67, 2, total_length)
        + struct.pack("<II", len(encoded_json), 0x4E4F534A)
        + encoded_json
        + struct.pack("<II", len(padded_binary), 0x004E4942)
        + padded_binary
    )
    path = os.path.join(directory, name)
    with open(path, "wb") as target:
        target.write(data)
    return path


class GltfImportTests(unittest.TestCase):
    def test_reads_the_small_geometry_scene_exporter_output(self):
        source_scene = geometry_scene.GeometryScene(
            source_path="synthetic.ed",
            models=[geometry_scene.GeometryModel(
                name="ExportedTriangle",
                points=[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
                faces=[geometry_scene.GeometryFace(
                    vertex_indices=[0, 1, 2],
                    material_name="Floor",
                    uv_coords=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                )],
            )],
            materials=[geometry_scene.GeometryMaterial(
                name="Floor",
                texture_name="TEXTURES\\World\\Floor.dtx",
            )],
        )
        with tempfile.TemporaryDirectory() as tmp:
            exported = gltf_export.export_geometry_scene_gltf(
                source_scene,
                tmp,
                base_name="ReaderRoundTrip",
            )
            loaded = gltf_import.load_gltf_geometry_scene(exported.gltf_path)
            topology = mesh_topology.analyze_geometry_scene(loaded)

        self.assertEqual(len(loaded.models), 1)
        self.assertEqual(loaded.models[0].points[:3], source_scene.models[0].points)
        self.assertEqual(loaded.models[0].faces[0].vertex_indices, [0, 1, 2])
        self.assertEqual(loaded.materials[0].texture_name, "TEXTURES\\World\\Floor.dtx")
        self.assertEqual(topology.components[0].classification, mesh_topology.SLAB_CANDIDATE)

    def test_loads_selected_scene_with_nested_transform_stride_uv_and_mirrored_winding(self):
        raw, views, accessors, index_accessor = _triangle_payload(strided=True)
        document = _triangle_document(
            "scene.bin", raw, views, accessors, index_accessor
        )
        document["scene"] = 1
        document["scenes"] = [
            {"name": "Ignored", "nodes": [0]},
            {"name": "Selected", "nodes": [1]},
        ]
        document["nodes"] = [
            {"name": "UnselectedTriangle", "mesh": 0},
            {"name": "Parent", "translation": [10.0, 0.0, 0.0], "children": [2]},
            {
                "name": "MirroredTriangle",
                "mesh": 0,
                "translation": [0.0, 2.0, 0.0],
                "scale": [-1.0, 1.0, 1.0],
                "extras": {"purpose": "reader-test"},
            },
        ]
        document["materials"][0]["pbrMetallicRoughness"] = {"metallicFactor": 0.25}
        document["materials"][0]["doubleSided"] = True

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_gltf(tmp, document, raw)
            scene = gltf_import.load_gltf_geometry_scene(path)

        self.assertEqual([model.name for model in scene.models], ["MirroredTriangle"])
        model = scene.models[0]
        self.assertEqual(model.points, [(10.0, 2.0, 0.0), (9.0, 2.0, 0.0), (10.0, 3.0, 0.0)])
        self.assertEqual(model.faces[0].vertex_indices, [0, 2, 1])
        self.assertEqual(model.faces[0].uv_coords, [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0)])
        self.assertTrue(model.extras["winding_reversed"])
        self.assertEqual(model.extras["node_path"], ["Parent[1]", "MirroredTriangle[2]"])
        self.assertEqual(scene.materials[0].texture_name, "TEXTURES\\World\\Stone.dtx")
        self.assertEqual(
            scene.materials[0].extras["ignored_pbr_fields"],
            ["doubleSided", "pbrMetallicRoughness"],
        )
        self.assertEqual(scene.metadata["selected_scene_index"], 1)
        self.assertEqual(scene.metadata["inventory"]["selected_node_count"], 2)
        self.assertEqual(scene.metadata["inventory"]["triangle_count"], 1)

    def test_loads_glb_and_creates_one_model_for_each_mesh_instance(self):
        raw, views, accessors, _index_accessor = _triangle_payload(
            indices=None, include_uv=False
        )
        document = _triangle_document(
            None, raw, views, accessors, None, include_uv=False
        )
        document["nodes"] = [
            {"name": "Shared", "mesh": 0, "translation": [1.0, 0.0, 0.0]},
            {"name": "Shared", "mesh": 0, "translation": [3.0, 0.0, 0.0]},
        ]
        document["scenes"][0]["nodes"] = [0, 1]

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_glb(tmp, document, raw)
            scene = gltf_import.load_gltf_geometry_scene(path)

        self.assertEqual([model.name for model in scene.models], ["Shared", "Shared_2"])
        self.assertEqual(scene.models[0].points[0], (1.0, 0.0, 0.0))
        self.assertEqual(scene.models[1].points[0], (3.0, 0.0, 0.0))
        self.assertEqual(scene.metadata["format"], "glb")
        self.assertEqual(scene.metadata["inventory"]["mesh_instance_count"], 2)

    def test_loads_base64_buffer_and_uses_the_only_scene_when_root_scene_is_absent(self):
        raw, views, accessors, index_accessor = _triangle_payload(include_uv=False)
        uri = "data:application/octet-stream;base64," + base64.b64encode(raw).decode("ascii")
        document = _triangle_document(
            uri, raw, views, accessors, index_accessor, include_uv=False
        )
        document.pop("scene")
        document["meshes"][0]["primitives"][0].pop("material")
        document["nodes"][0]["matrix"] = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            5.0, 6.0, 7.0, 1.0,
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_gltf(tmp, document)
            scene = gltf_import.load_gltf_geometry_scene(path)

        self.assertEqual(scene.metadata["selected_scene_index"], 0)
        self.assertEqual(scene.models[0].points[0], (5.0, 6.0, 7.0))
        self.assertEqual(scene.models[0].faces[0].material_name, "Default")
        self.assertEqual(scene.models[0].faces[0].uv_coords, [None, None, None])
        self.assertIn("Default", [material.name for material in scene.materials])
        self.assertNotIn("base64", scene.metadata["source"]["buffers"][0]["uri"].split(",", 1)[-1])

    def test_reports_ignored_static_attributes_without_reading_them(self):
        raw, views, accessors, index_accessor = _triangle_payload(include_uv=False)
        document = _triangle_document(
            "scene.bin", raw, views, accessors, index_accessor, include_uv=False
        )
        document["meshes"][0]["primitives"][0]["attributes"]["NORMAL"] = 999

        with tempfile.TemporaryDirectory() as tmp:
            path = _write_gltf(tmp, document, raw)
            scene = gltf_import.load_gltf_geometry_scene(path)

        self.assertIn("attribute:NORMAL", scene.metadata["ignored_features"])

    def test_rejects_unsupported_or_invalid_selected_geometry_with_stable_codes(self):
        raw, views, accessors, index_accessor = _triangle_payload()
        base = _triangle_document(
            "scene.bin", raw, views, accessors, index_accessor
        )

        cases = []

        ambiguous = copy.deepcopy(base)
        ambiguous.pop("scene")
        ambiguous["scenes"].append({"nodes": [0]})
        cases.append(("ambiguous_scene_selection", ambiguous, raw))

        sparse = copy.deepcopy(base)
        sparse["accessors"][0]["sparse"] = {}
        cases.append(("unsupported_sparse_accessor", sparse, raw))

        normalized_uv = copy.deepcopy(base)
        normalized_uv["accessors"][1]["normalized"] = True
        cases.append(("unsupported_normalized_accessor", normalized_uv, raw))

        strip = copy.deepcopy(base)
        strip["meshes"][0]["primitives"][0]["mode"] = 5
        cases.append(("unsupported_primitive_mode", strip, raw))

        skinned = copy.deepcopy(base)
        skinned["nodes"][0]["skin"] = 0
        cases.append(("unsupported_skinning", skinned, raw))

        morphed = copy.deepcopy(base)
        morphed["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
        cases.append(("unsupported_morph_targets", morphed, raw))

        animated = copy.deepcopy(base)
        animated["animations"] = [{
            "channels": [{"target": {"node": 0, "path": "translation"}}]
        }]
        cases.append(("unsupported_animation", animated, raw))

        singular = copy.deepcopy(base)
        singular["nodes"][0]["scale"] = [0.0, 1.0, 1.0]
        cases.append(("singular_mesh_transform", singular, raw))

        cyclic = copy.deepcopy(base)
        cyclic["nodes"][0]["children"] = [0]
        cases.append(("node_cycle", cyclic, raw))

        short_declaration = copy.deepcopy(base)
        short_declaration["buffers"][0]["byteLength"] -= 1
        cases.append(("buffer_view_out_of_range", short_declaration, raw))

        for expected_code, document, payload in cases:
            with self.subTest(expected_code):
                with tempfile.TemporaryDirectory() as tmp:
                    path = _write_gltf(tmp, document, payload)
                    with self.assertRaises(gltf_import.GltfImportError) as caught:
                        gltf_import.load_gltf_geometry_scene(path)
                self.assertEqual(caught.exception.code, expected_code)
                self.assertIn(f"[{expected_code}]", str(caught.exception))

    def test_rejects_out_of_range_indices(self):
        raw, views, accessors, index_accessor = _triangle_payload(indices=(0, 1, 7))
        document = _triangle_document(
            "scene.bin", raw, views, accessors, index_accessor
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_gltf(tmp, document, raw)
            with self.assertRaises(gltf_import.GltfImportError) as caught:
                gltf_import.load_gltf_geometry_scene(path)
        self.assertEqual(caught.exception.code, "index_out_of_range")

    def test_rejects_external_buffer_path_escape(self):
        raw, views, accessors, index_accessor = _triangle_payload()
        document = _triangle_document(
            "../outside.bin", raw, views, accessors, index_accessor
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_gltf(tmp, document)
            with self.assertRaises(gltf_import.GltfImportError) as caught:
                gltf_import.load_gltf_geometry_scene(path)
        self.assertEqual(caught.exception.code, "unsafe_buffer_path")

    def test_rejects_glb_with_mismatched_declared_length(self):
        raw, views, accessors, _index_accessor = _triangle_payload(
            indices=None, include_uv=False
        )
        document = _triangle_document(
            None, raw, views, accessors, None, include_uv=False
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_glb(tmp, document, raw)
            with open(path, "r+b") as target:
                target.seek(8)
                target.write(struct.pack("<I", os.path.getsize(path) - 4))
            with self.assertRaises(gltf_import.GltfImportError) as caught:
                gltf_import.load_gltf_geometry_scene(path)
        self.assertEqual(caught.exception.code, "invalid_glb_length")


if __name__ == "__main__":
    unittest.main()
