"""Make the bundled mm9_patcher package importable."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATCHER = os.path.join(_HERE, "mm9_patcher")
if _PATCHER not in sys.path:
    sys.path.insert(0, _PATCHER)
