"""
view3d
======

Optional PyOpenGL-based 3-D viewer for the MM9 Mod Editor.

Usage::

    from view3d import View3D, OPENGL_AVAILABLE

    if OPENGL_AVAILABLE:
        viewer = View3D(parent, on_select=callback)
    else:
        # show install prompt

Install dependencies::

    pip install PyOpenGL PyOpenGL_accelerate pyopengltk

The package degrades gracefully when dependencies are missing:
OPENGL_AVAILABLE is False and View3D renders a placeholder label.
"""

from view3d.gl_view import View3D, OPENGL_AVAILABLE

__all__ = ["View3D", "OPENGL_AVAILABLE"]
