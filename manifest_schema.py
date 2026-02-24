"""
Manifest schema for Nioh 3 Mod Manager.

Mod authors can include a ``nioh3modmanifest.json`` at the root of their archive
to describe multi-feature mods. The manager reads it during archive scanning and
presents each feature as an independent selection step.

If no manifest is present the manager falls back to standard package/ directory
scanning -- existing mods require no changes.

Schema version 1.0
------------------
Archive layout example:

    my_mod.zip
    ├── nioh3modmanifest.json
    ├── common/           <- common_files_dir; always installed
    ├── armor_style/      <- feature directory (matches "directory" in the manifest)
    │   ├── Light/
    │   ├── Medium/
    │   └── Heavy/
    └── skin/             <- optional feature
        ├── Normal/
        └── Wet/

Manifest:

{
    "mod_manager_version": "1.0",
    "common_files_dir": "common",
    "features": [
        {
            "name": "Armor Style",
            "directory": "armor_style",
            "optional": false
        },
        {
            "name": "Skin",
            "directory": "skin",
            "optional": true
        }
    ]
}
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field, field_validator, model_validator

MANIFEST_FILENAME = "nioh3modmanifest.json"
CURRENT_VERSION = (1, 1)  # (major, minor) supported by this build

_log = logging.getLogger(__name__)


class ManifestFeature(BaseModel):
    """One independently selectable feature within a manifest-driven mod.

    ``name`` is the human-readable label shown in the UI.  ``directory``
    is the archive subdirectory containing one subdirectory per option;
    files inside the chosen option subdirectory are installed directly into
    the game's package/ directory.
    """

    name: str
    directory: str
    optional: bool = False

    @field_validator("directory")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return v.replace("\\", "/").strip("/")


class ModManifest(BaseModel):
    """Parsed contents of a nioh3modmanifest.json file."""

    mod_manager_version: str
    mod_name: str | None = None
    author: str | None = None
    version: str | None = None
    url: str | None = None
    common_files_dir: str | None = None
    features: list[ManifestFeature] = Field(default_factory=list)

    @field_validator("mod_manager_version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        try:
            major, minor = (int(x) for x in v.split("."))
        except ValueError:
            raise ValueError(
                f"Invalid mod_manager_version {v!r} — expected 'major.minor' (e.g. '1.0')"
            )
        cur_major, cur_minor = CURRENT_VERSION
        if major > cur_major:
            raise ValueError(
                f"mod_manager_version {v!r} requires a newer mod manager "
                f"(this build supports up to version {cur_major}.x)"
            )
        if major == cur_major and minor > cur_minor:
            _log.warning(
                "Manifest version %s is newer than this build supports (%d.%d) — "
                "some features may be ignored.",
                v, cur_major, cur_minor,
            )
        return v

    @field_validator("common_files_dir")
    @classmethod
    def _normalize_common(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.replace("\\", "/").strip("/")

    @model_validator(mode="after")
    def _no_duplicate_features(self) -> ModManifest:
        names = [f.name for f in self.features]
        dirs = [f.directory for f in self.features]
        for label, values in (("name", names), ("directory", dirs)):
            seen = set()
            for v in values:
                if v in seen:
                    raise ValueError(f"Duplicate feature {label}: {v!r}")
                seen.add(v)
        return self


def parse_manifest(data: bytes) -> ModManifest:
    """Parse raw JSON bytes into a ModManifest.

    Raises ``pydantic.ValidationError`` if the data is invalid.
    Raises ``json.JSONDecodeError`` if the bytes are not valid JSON.
    """
    return ModManifest.model_validate(json.loads(data))
