#!/usr/bin/env python3
"""Compatibility wrapper for core.rezmgr."""

from core import rezmgr as _impl

globals().update({
    name: getattr(_impl, name)
    for name in dir(_impl)
    if not (name.startswith("__") and name.endswith("__"))
})

_main = _impl.main


if __name__ == "__main__":
    raise SystemExit(_main())
