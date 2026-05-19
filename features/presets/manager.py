"""
preset_manager.py
=================

User-defined placement presets — named templates that combine a base
WorldObject class with a set of property overrides.

A preset is the simplest way to add the same kind of custom object over
and over (e.g. a barrel with a specific model, a torch with a custom
script, a renamed civilian NPC) without touching the raw catalog each time.

On-disk format (``user_presets.json`` in the editor directory)::

    {
      "version": 1,
      "presets": [
        {
          "name":        "Metal Barrel",
          "base_class":  "Prop",
          "category":    "prop",
          "description": "A barrel using the metal barrel .abc model",
          "overrides": {
            "Filename": "models\\metalbarrel.abc",
            "Solid":    1,
            "Shadow":   1
          }
        },
        ...
      ]
    }

Usage::

    from features.presets.manager import PresetStore, UserPreset

    store = PresetStore(os.path.join(editor_dir, "user_presets.json"))
    store.load()

    preset = UserPreset(
        name="Metal Barrel",
        base_class="Prop",
        overrides={"Filename": "models\\\\metalbarrel.abc"},
        category="prop",
        description="barrel variant",
    )
    store.add(preset)
    store.save()

    for p in store.presets:
        print(p.name, p.base_class, p.overrides)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_FORMAT_VERSION = 1


@dataclass
class UserPreset:
    """A named placement preset.

    Attributes
    ----------
    name:
        Display name used in the dialog and preset list (must be unique
        within the store).
    base_class:
        WorldObject class to clone as the starting template (e.g. ``"Prop"``).
    overrides:
        Property values to apply on top of the cloned template.  Keys are
        property names (``"Filename"``, ``"Solid"``, …); values are whatever
        type the property normally holds (str, int, float, list).
    category:
        Optional category hint — used to colour-code the row in the dialog.
        Defaults to ``"other"``.
    description:
        Free-text note for the user.  Shown in the detail bar.
    """
    name:        str
    base_class:  str
    overrides:   Dict[str, Any] = field(default_factory=dict)
    category:    str = "other"
    description: str = ""

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name":        self.name,
            "base_class":  self.base_class,
            "category":    self.category,
            "description": self.description,
            "overrides":   self.overrides,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UserPreset":
        return cls(
            name=        str(d.get("name",        "Unnamed")),
            base_class=  str(d.get("base_class",  "Prop")),
            overrides=   dict(d.get("overrides",  {})),
            category=    str(d.get("category",    "other")),
            description= str(d.get("description", "")),
        )


class PresetStore:
    """Loads and persists user presets to/from *path* (a JSON file).

    The store is intentionally minimal — a flat ordered list of presets.
    Duplicate names are rejected at the ``add()`` / ``update()`` stage.

    Parameters
    ----------
    path:
        Absolute path to ``user_presets.json``.  The file does not need to
        exist yet — the first ``save()`` call creates it.
    """

    def __init__(self, path: str) -> None:
        self._path    = path
        self._presets: List[UserPreset] = []

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load presets from disk.  Silently creates an empty store if the
        file does not exist or cannot be parsed."""
        if not os.path.isfile(self._path):
            self._presets = []
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self._presets = [
                UserPreset.from_dict(d) for d in raw.get("presets", [])
            ]
        except Exception:
            # Corrupt file — start empty; will be overwritten on next save
            self._presets = []

    def save(self) -> None:
        """Persist presets to disk, creating the file (and parent dirs) if
        necessary."""
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "version": _FORMAT_VERSION,
            "presets": [p.to_dict() for p in self._presets],
        }
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @property
    def presets(self) -> List[UserPreset]:
        """Read-only view of the preset list (do not mutate directly)."""
        return list(self._presets)

    def get(self, name: str) -> Optional[UserPreset]:
        """Return the preset with the given name, or ``None``."""
        for p in self._presets:
            if p.name == name:
                return p
        return None

    def names(self) -> List[str]:
        """Return a list of all preset names, in order."""
        return [p.name for p in self._presets]

    def add(self, preset: UserPreset) -> None:
        """Append *preset* to the store.

        Raises ``ValueError`` if a preset with the same name already exists.
        """
        if any(p.name == preset.name for p in self._presets):
            raise ValueError(
                f"A preset named {preset.name!r} already exists. "
                "Use update() to replace it."
            )
        self._presets.append(preset)

    def update(self, preset: UserPreset) -> None:
        """Replace the existing preset that has the same name as *preset*.

        Raises ``KeyError`` if no preset with that name is found.
        """
        for i, p in enumerate(self._presets):
            if p.name == preset.name:
                self._presets[i] = preset
                return
        raise KeyError(f"No preset named {preset.name!r}")

    def add_or_update(self, preset: UserPreset) -> None:
        """Insert or replace, whichever is appropriate."""
        if self.get(preset.name) is None:
            self.add(preset)
        else:
            self.update(preset)

    def remove(self, name: str) -> None:
        """Delete the preset with the given name.

        Raises ``KeyError`` if not found.
        """
        for i, p in enumerate(self._presets):
            if p.name == name:
                del self._presets[i]
                return
        raise KeyError(f"No preset named {name!r}")

    def move_up(self, name: str) -> None:
        """Move the named preset one position earlier in the list."""
        for i, p in enumerate(self._presets):
            if p.name == name and i > 0:
                self._presets[i - 1], self._presets[i] = (
                    self._presets[i], self._presets[i - 1])
                return

    def move_down(self, name: str) -> None:
        """Move the named preset one position later in the list."""
        for i, p in enumerate(self._presets):
            if p.name == name and i < len(self._presets) - 1:
                self._presets[i], self._presets[i + 1] = (
                    self._presets[i + 1], self._presets[i])
                return

    def __len__(self) -> int:
        return len(self._presets)

    def __repr__(self) -> str:
        return f"PresetStore({self._path!r}, {len(self._presets)} presets)"
