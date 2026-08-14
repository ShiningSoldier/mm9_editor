import struct
import sys
import types
import unittest
from unittest import mock

import numpy as np


from tests._path import ROOT  # noqa: F401

from view3d import dtx


def _dxt_blob(pixel_format=4, width=8, height=8, mip_count=3):
    data = bytearray(164)
    struct.pack_into("<H", data, 8, width)
    struct.pack_into("<H", data, 10, height)
    struct.pack_into("<H", data, 12, mip_count)
    struct.pack_into("<H", data, 26, pixel_format)
    mip_w, mip_h = width, height
    for level in range(mip_count):
        size = dtx._mip0_size(pixel_format, mip_w, mip_h)
        data.extend(bytes([level + 1]) * size)
        mip_w = max(1, mip_w // 2)
        mip_h = max(1, mip_h // 2)
    return bytes(data)


class _FakeGL:
    GL_TEXTURE_2D = 1
    GL_TEXTURE_MIN_FILTER = 2
    GL_TEXTURE_MAG_FILTER = 3
    GL_LINEAR_MIPMAP_LINEAR = 4
    GL_LINEAR = 5
    GL_TEXTURE_WRAP_S = 6
    GL_TEXTURE_WRAP_T = 7
    GL_REPEAT = 8
    GL_TEXTURE_MAX_LEVEL = 9
    GL_RGBA = 10
    GL_BGRA = 11
    GL_UNSIGNED_BYTE = 12
    GL_NO_ERROR = 0
    GL_COMPRESSED_RGBA_S3TC_DXT1_EXT = 0x83F1
    GL_COMPRESSED_RGBA_S3TC_DXT5_EXT = 0x83F3

    def __init__(self, reject_compressed=False):
        self.reject_compressed = reject_compressed
        self.next_texture = 1
        self.error = 0
        self.compressed_calls = []
        self.tex_image_calls = []
        self.parameter_calls = []
        self.deleted = []
        self.generated = 0

    def glGenTextures(self, _count):
        texture = self.next_texture
        self.next_texture += 1
        return texture

    def glBindTexture(self, *_args):
        pass

    def glTexParameteri(self, *args):
        self.parameter_calls.append(args)

    def glCompressedTexImage2D(self, *args):
        self.compressed_calls.append(args)
        if self.reject_compressed:
            self.error = 0x0500

    def glGetError(self):
        error = self.error
        self.error = 0
        return error

    def glDeleteTextures(self, textures):
        self.deleted.append(list(textures))

    def glTexImage2D(self, *args):
        self.tex_image_calls.append(args)

    def glGenerateMipmap(self, _target):
        self.generated += 1


class DtxUploadTests(unittest.TestCase):
    def _open_gl_module(self, fake_gl):
        module = types.ModuleType("OpenGL")
        module.GL = fake_gl
        return module

    def test_compressed_upload_uses_authored_mip_chain(self):
        fake_gl = _FakeGL()
        blob = _dxt_blob()

        with mock.patch.dict(
            sys.modules,
            {"OpenGL": self._open_gl_module(fake_gl)},
        ), mock.patch(
            "view3d.dtx._decode_dxt1_rgba",
            side_effect=AssertionError("CPU decoder should not run"),
        ):
            texture = dtx.load_dtx_bytes(blob)

        self.assertEqual(texture, 1)
        self.assertEqual(
            [(call[1], call[3], call[4]) for call in fake_gl.compressed_calls],
            [(0, 8, 8), (1, 4, 4), (2, 2, 2)],
        )
        self.assertEqual(fake_gl.tex_image_calls, [])
        self.assertEqual(fake_gl.generated, 0)
        self.assertIn(
            (fake_gl.GL_TEXTURE_2D, fake_gl.GL_TEXTURE_MAX_LEVEL, 2),
            fake_gl.parameter_calls,
        )

    def test_driver_rejection_retries_with_cpu_rgba_upload(self):
        fake_gl = _FakeGL(reject_compressed=True)
        blob = _dxt_blob(mip_count=1)

        with mock.patch.dict(
            sys.modules,
            {"OpenGL": self._open_gl_module(fake_gl)},
        ), mock.patch(
            "view3d.dtx._decode_dxt1_rgba",
            return_value=np.zeros((8, 8, 4), dtype=np.uint8),
        ) as decode:
            texture = dtx.load_dtx_bytes(blob)

        self.assertEqual(texture, 2)
        self.assertEqual(fake_gl.deleted, [[1]])
        self.assertEqual(len(fake_gl.tex_image_calls), 1)
        self.assertEqual(fake_gl.generated, 1)
        decode.assert_called_once()


if __name__ == "__main__":
    unittest.main()
