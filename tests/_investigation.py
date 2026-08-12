"""Opt-in marker for expensive tests tied to unfinished investigations."""

from __future__ import annotations

import os
import unittest


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


RUN_INVESTIGATION_TESTS = (
    _enabled("MM9_RUN_INVESTIGATION_TESTS")
    or _enabled("MM9_RUN_SLOW_DAT_TO_ED_TESTS")
)

investigation_test = unittest.skipUnless(
    RUN_INVESTIGATION_TESTS,
    "investigation regression; set MM9_RUN_INVESTIGATION_TESTS=1 to run",
)

# Backward-compatible name for the tests and documentation that introduced the
# original, narrower DAT-to-ED opt-in tier.
slow_dat_to_ed_test = investigation_test
