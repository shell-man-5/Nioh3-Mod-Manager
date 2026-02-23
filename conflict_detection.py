"""
Conflict detection for Nioh 3 Mod Manager.

A "conflict" means two mods both replace the same underlying game asset,
identified by the name_hash field inside .yumiamod.json metadata files.
Installing conflicting mods produces undefined visual results (the RDB ends
up with two competing entries for the same asset; whichever yumia wrote last
"wins", making the outcome depend on install order).

Public API
----------
find_conflicts(archive, archive_members, installed, game_package_dir)
    -> list of (archive_filename, set_of_overlapping_name_hashes)
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import py7zr
import rarfile

if TYPE_CHECKING:
    from mod_manager import InstalledModRecord, ModArchive

_log = logging.getLogger(__name__)


# ── Low-level archive reading ─────────────────────────────────────────


def _read_archive_member(filepath: Path, member: str) -> bytes:
    """Read a single member from an archive into bytes without extracting to disk."""
    ext = filepath.suffix.lower()
    if ext == ".zip":
        with zipfile.ZipFile(filepath, "r") as zf:
            return zf.read(member)
    elif ext == ".7z":
        with py7zr.SevenZipFile(filepath, "r") as sz:
            results = sz.read(targets=[member])
            return results[member].read()
    elif ext == ".rar":
        with rarfile.RarFile(filepath, "r") as rf:
            return rf.read(member)
    raise ValueError(f"Unsupported archive format: {ext}")


# ── name_hash collection ──────────────────────────────────────────────


def _name_hashes_from_archive(
    filepath: Path, archive_members: list[str]
) -> set[int]:
    """Return the set of name_hash values from all .yumiamod.json files in
    the given list of archive member paths (read directly from the archive)."""
    hashes: set[int] = set()
    for member in archive_members:
        if not member.endswith(".yumiamod.json"):
            continue
        try:
            data = _read_archive_member(filepath, member)
            for entry in json.loads(data).get("files", []):
                hashes.add(entry["name_hash"])
        except Exception as exc:
            _log.warning("Could not read %s for conflict check: %s", member, exc)
    return hashes


def _name_hashes_from_disk(
    game_package_dir: Path, installed_files: list[str]
) -> set[int]:
    """Return the set of name_hash values from .yumiamod.json files that are
    already on disk in game_package_dir for an installed mod."""
    hashes: set[int] = set()
    for pf in installed_files:
        if not pf.endswith(".yumiamod.json"):
            continue
        fp = game_package_dir / pf
        if not fp.exists():
            continue
        try:
            for entry in json.loads(fp.read_text(encoding="utf-8")).get("files", []):
                hashes.add(entry["name_hash"])
        except Exception as exc:
            _log.warning("Could not read %s for conflict check: %s", fp.name, exc)
    return hashes


# ── Public API ────────────────────────────────────────────────────────


def find_conflicts(
    archive: ModArchive,
    archive_members: list[str],
    installed: dict[str, InstalledModRecord],
    game_package_dir: Path,
) -> list[tuple[str, set[int]]]:
    """Check whether installing the given archive members would conflict with
    any currently-installed mod.

    ``archive_members`` is the flat list of archive-internal paths that would
    be read (e.g. ``["package/foo.fdata", "package/foo.yumiamod.json"]``).

    Returns a list of ``(archive_filename, overlapping_name_hashes)`` tuples,
    one per conflicting installed mod.  An empty list means no conflicts.
    """
    incoming = _name_hashes_from_archive(archive.filepath, archive_members)
    if not incoming:
        return []

    conflicts: list[tuple[str, set[int]]] = []
    for fname, rec in installed.items():
        overlap = incoming & _name_hashes_from_disk(game_package_dir, rec.installed_files)
        if overlap:
            conflicts.append((fname, overlap))

    return conflicts
