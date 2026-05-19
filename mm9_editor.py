#!/usr/bin/env python3
"""Compatibility launcher for the MM9 editor application."""

from app.editor import *  # noqa: F401,F403
from app.editor import main as _main


if __name__ == "__main__":
    raise SystemExit(_main())

