#!/usr/bin/env python3
"""Compatibility launcher for the catalog builder CLI."""

from catalog.builder import main


if __name__ == "__main__":
    raise SystemExit(main())
