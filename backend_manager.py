"""
Nioh 3 Mod Manager - Backend-aware core logic.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

import py7zr
import rarfile

from conflict_detection import find_conflicts
from loose_file_converter import (
    convert_selected_members_to_loose,
    is_loose_asset_member,
    sanitize_mod_dir_name,
    select_manifest_members,
)
from manifest_schema import MANIFEST_FILENAME, ModManifest, parse_manifest

_log = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    _unrar = Path(sys._MEIPASS) / "UnRAR.exe"
else:
    _unrar = Path(__file__).parent / "assets" / "UnRAR.exe"
if _unrar.exists():
    rarfile.UNRAR_TOOL = str(_unrar)

SUPPORTED_EXTENSIONS = {".zip", ".7z", ".rar"}

CORE_RDB_FILES = ("root.rdb", "root.rdx")
CORE_RDB_BACKUPS = ("root.rdb.original", "root.rdx.original")
YUMIA_EXE_NAME = "yumia_mod_insert_into_rdb.exe"
STATE_MANIFEST_FILENAME = ".nioh3_modmanager_state.json"
LEGACY_MANIFEST_FILENAME = ".nioh3_modmanager_manifest.json"

InstallBackend = Literal["yumia", "loose"]
InstallKind = Literal["legacy", "manifest"]
ArchiveKind = Literal["legacy_yumia", "manifest", "direct_loose"]


@dataclass
class ModOption:
    name: str
    archive_internal_path: str
    package_files: list[str] = field(default_factory=list)


@dataclass
class ModArchive:
    filepath: Path
    name: str
    options: list[ModOption] = field(default_factory=list)
    manifest: ModManifest | None = None
    manifest_options: dict[str, list[str]] = field(default_factory=dict)
    archive_kind: ArchiveKind = "legacy_yumia"
    archive_names: list[str] = field(default_factory=list)
    direct_loose_common_files: list[str] = field(default_factory=list)
    direct_loose_multi_select: bool = False


@dataclass
class LegacyInstalledModRecord:
    archive_filename: str
    option_name: str
    installed_files: list[str] = field(default_factory=list)


@dataclass
class InstalledModRecord:
    archive_filename: str
    backend: InstallBackend
    install_kind: InstallKind
    display_option_summary: str
    legacy_option_name: str | None = None
    feature_selections: dict[str, str | None] | None = None
    installed_paths: list[str] = field(default_factory=list)
    loose_mod_dir: str | None = None

    @property
    def option_name(self) -> str:
        return self.display_option_summary

    @property
    def installed_files(self) -> list[str]:
        if self.backend == "yumia":
            return [
                path[len("package/"):] if path.startswith("package/") else path
                for path in self.installed_paths
            ]
        return self.installed_paths


@dataclass
class EnvironmentStatus:
    package_dir_exists: bool
    game_root_exists: bool
    mods_dir_exists: bool
    yumia_available: bool
    dll_loader_available: bool
    loose_plugin_dll_available: bool
    loose_plugin_ini_available: bool
    loose_ready: bool
    active_backend: Literal["none", "yumia", "loose"]
    has_active_yumia_mods: bool
    has_active_loose_mods: bool
    can_install: bool
    can_migrate: bool


@dataclass
class MigrationPlanItem:
    record: InstalledModRecord
    archive: ModArchive
    display_option_summary: str
    legacy_option_name: str | None
    feature_selections: dict[str, str | None] | None
    loose_mod_dir: str
    installed_paths: list[str]
    files: list[tuple[str, bytes]]


class ModManager:
    def __init__(
        self,
        mods_dir: str | Path,
        game_package_dir: str | Path,
        log_callback: Optional[Callable[[str], None]] = None,
        yumia_prompt_callback: Optional[Callable[[str], bool]] = None,
    ):
        self.mods_dir = Path(mods_dir)
        self.game_package_dir = Path(game_package_dir)
        self.game_root_dir = self.game_package_dir.parent
        self.loose_mods_dir = self.game_root_dir / "mods"
        self.plugins_dir = self.game_root_dir / "plugins"
        self.yumia_exe = self.game_package_dir / YUMIA_EXE_NAME
        self.installed_mods_manifest_path = self.mods_dir / LEGACY_MANIFEST_FILENAME
        self.state_manifest_path = self.game_package_dir / STATE_MANIFEST_FILENAME
        self._log_cb = log_callback or print
        self._yumia_prompt_cb = yumia_prompt_callback

        self.archives: list[ModArchive] = []
        self.installed: dict[str, InstalledModRecord] = {}

    def log(self, msg: str):
        self._log_cb(msg)

    @staticmethod
    def _read_archive_member(filepath: Path, member: str) -> bytes:
        ext = filepath.suffix.lower()
        if ext == ".zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                return zf.read(member)
        if ext == ".7z":
            with py7zr.SevenZipFile(filepath, "r") as sz:
                results = sz.read(targets=[member])
                return results[member].read()
        if ext == ".rar":
            with rarfile.RarFile(filepath, "r") as rf:
                return rf.read(member)
        raise ValueError(f"Unsupported archive format: {ext}")

    @staticmethod
    def _list_archive_names(filepath: Path) -> list[str]:
        ext = filepath.suffix.lower()
        if ext == ".zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                names = zf.namelist()
        elif ext == ".7z":
            with py7zr.SevenZipFile(filepath, "r") as sz:
                names = sz.getnames()
        elif ext == ".rar":
            with rarfile.RarFile(filepath, "r") as rf:
                names = [info.filename for info in rf.infolist()]
        else:
            names = []
        return [name.replace("\\", "/") for name in names]

    @staticmethod
    def _extract_from_archive(filepath: Path, members: list[str], dest: Path) -> list[Path]:
        ext = filepath.suffix.lower()
        extracted: list[Path] = []
        if ext == ".zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                for member in members:
                    zf.extract(member, dest)
                    extracted.append(dest / member)
        elif ext == ".7z":
            with py7zr.SevenZipFile(filepath, "r") as sz:
                sz.extract(dest, targets=members)
                for member in members:
                    extracted.append(dest / member)
        elif ext == ".rar":
            with rarfile.RarFile(filepath, "r") as rf:
                for member in members:
                    rf.extract(member, dest)
                    extracted.append(dest / member)
        return extracted

    def _load_legacy_installed_mods_manifest(self) -> dict[str, LegacyInstalledModRecord]:
        if not self.installed_mods_manifest_path.exists():
            return {}
        try:
            data = json.loads(self.installed_mods_manifest_path.read_text(encoding="utf-8"))
            return {key: LegacyInstalledModRecord(**rec) for key, rec in data.items()}
        except Exception as exc:
            self.log(f"Warning: Could not load installed mods record: {exc}")
            return {}

    def _save_legacy_installed_mods_manifest(self):
        legacy_records: dict[str, dict[str, object]] = {}
        for key, rec in self.installed.items():
            if rec.backend != "yumia":
                continue
            installed_files = []
            for relpath in rec.installed_paths:
                if relpath.startswith("package/"):
                    installed_files.append(relpath[len("package/"):])
                else:
                    installed_files.append(relpath)
            legacy_records[key] = asdict(
                LegacyInstalledModRecord(
                    archive_filename=rec.archive_filename,
                    option_name=rec.display_option_summary,
                    installed_files=installed_files,
                )
            )

        if legacy_records:
            self.installed_mods_manifest_path.write_text(
                json.dumps(legacy_records, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        elif self.installed_mods_manifest_path.exists():
            self.installed_mods_manifest_path.unlink()

    def load_install_state(self):
        if self.state_manifest_path.exists():
            try:
                data = json.loads(self.state_manifest_path.read_text(encoding="utf-8"))
                if data.get("version") != 2:
                    raise ValueError(f"Unsupported state version: {data.get('version')!r}")
                self.installed = {
                    key: InstalledModRecord(**rec)
                    for key, rec in data.get("records", {}).items()
                }
                self.log(f"Loaded installed state: {len(self.installed)} mod(s) recorded")
                return
            except Exception as exc:
                self.log(f"Warning: Could not load install state: {exc}")
                self.installed = {}
                return

        self.backfill_legacy_state_if_needed()

    def save_install_state(self):
        self.state_manifest_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "records": {key: asdict(rec) for key, rec in self.installed.items()},
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def backfill_legacy_state_if_needed(self):
        legacy_records = self._load_legacy_installed_mods_manifest()
        self.installed = {}
        for key, legacy in legacy_records.items():
            archive = self._find_or_load_archive_by_filename(legacy.archive_filename)
            install_kind: InstallKind = "legacy"
            feature_selections = None
            legacy_option_name = legacy.option_name

            if archive and archive.manifest is not None:
                install_kind = "manifest"
                legacy_option_name = None
                feature_selections = self._resolve_feature_selections_for_summary(
                    archive,
                    legacy.option_name,
                    [f"package/{path}" for path in legacy.installed_files],
                )

            self.installed[key] = InstalledModRecord(
                archive_filename=legacy.archive_filename,
                backend="yumia",
                install_kind=install_kind,
                display_option_summary=legacy.option_name,
                legacy_option_name=legacy_option_name,
                feature_selections=feature_selections,
                installed_paths=[
                    path if path.startswith("package/") else f"package/{path}"
                    for path in legacy.installed_files
                ],
                loose_mod_dir=None,
            )

        self.save_install_state()
        self.log(f"Backfilled install state from legacy manifest: {len(self.installed)} mod(s)")

    def scan_archives(self) -> list[ModArchive]:
        self.archives = []
        if not self.mods_dir.exists():
            self.log(f"Mods directory does not exist: {self.mods_dir}")
            return self.archives

        for filepath in sorted(self.mods_dir.iterdir()):
            if not filepath.is_file() or filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                archive = self._analyze_archive(filepath)
                if archive.options or archive.manifest:
                    self.archives.append(archive)
                    if archive.manifest:
                        names = [feature.name for feature in archive.manifest.features]
                        self.log(
                            f"  {filepath.name}: manifest mod — "
                            f"{len(archive.manifest.features)} feature(s): {names}"
                        )
                    else:
                        names = [option.name for option in archive.options]
                        self.log(f"  {filepath.name}: {len(archive.options)} option(s) — {names}")
                else:
                    self.log(f"  {filepath.name}: no mod files found, skipping")
            except Exception as exc:
                self.log(f"  Error scanning {filepath.name}: {exc}")

        self.log(f"Scan complete: {len(self.archives)} valid mod archive(s)")
        return self.archives

    def _analyze_archive(self, filepath: Path) -> ModArchive:
        names = self._list_archive_names(filepath)
        archive = ModArchive(filepath=filepath, name=filepath.stem, archive_names=names)
        if not names:
            return archive

        if MANIFEST_FILENAME in names:
            try:
                manifest = parse_manifest(self._read_archive_member(filepath, MANIFEST_FILENAME))
                archive.manifest = manifest
                archive.archive_kind = "manifest"
                if manifest.mod_name:
                    archive.name = manifest.mod_name
                for feature in manifest.features:
                    prefix = feature.directory + "/"
                    options: set[str] = set()
                    for name in names:
                        if name.startswith(prefix) and not name.endswith("/"):
                            rest = name[len(prefix):]
                            parts = rest.split("/")
                            if len(parts) >= 2:
                                options.add(parts[0])
                    archive.manifest_options[feature.name] = sorted(options, key=str.lower)
                return archive
            except Exception as exc:
                self.log(
                    f"  Warning: manifest parse failed in {filepath.name}: {exc}"
                    " — falling back to package/ scan"
                )
                archive.manifest = None
                archive.manifest_options = {}

        package_prefixes: dict[str, str] = {}
        for name in names:
            parts = name.split("/")
            for i, part in enumerate(parts):
                if part.lower() == "package" and i < len(parts) - 1:
                    prefix = "/".join(parts[: i + 1]) + "/"
                    if prefix not in package_prefixes:
                        package_prefixes[prefix] = "(default)" if i == 0 else parts[i - 1]
                    break

        for prefix, option_name in package_prefixes.items():
            package_files = []
            for name in names:
                if name.startswith(prefix) and not name.endswith("/"):
                    rel = name[len(prefix):]
                    if rel:
                        package_files.append(rel)
            if package_files:
                archive.options.append(
                    ModOption(
                        name=option_name,
                        archive_internal_path=prefix,
                        package_files=package_files,
                    )
                )

        if archive.options:
            archive.archive_kind = "legacy_yumia"
            return archive

        yumia_members = [
            name for name in names
            if not name.endswith("/")
            and (name.endswith(".fdata") or name.endswith(".yumiamod.json"))
        ]
        if yumia_members:
            archive.archive_kind = "legacy_yumia"
            archive.options.append(
                ModOption(
                    name="(default)",
                    archive_internal_path="",
                    package_files=yumia_members,
                )
            )
            return archive

        loose_members = [
            name for name in names
            if not name.endswith("/") and is_loose_asset_member(name)
        ]
        if loose_members:
            archive.archive_kind = "direct_loose"
            root_loose_files: list[str] = []
            loose_groups: dict[str, list[str]] = {}
            for member in sorted(loose_members):
                parts = member.split("/")
                if len(parts) == 1:
                    root_loose_files.append(member)
                    continue
                loose_groups.setdefault(parts[0], []).append(member)

            if loose_groups and (len(loose_groups) > 1 or root_loose_files):
                archive.direct_loose_common_files = root_loose_files
                archive.direct_loose_multi_select = True
                for group_name in sorted(loose_groups, key=str.lower):
                    archive.options.append(
                        ModOption(
                            name=group_name,
                            archive_internal_path="",
                            package_files=sorted(loose_groups[group_name]),
                        )
                    )
            elif len(loose_groups) == 1 and not root_loose_files:
                    archive.options.append(
                        ModOption(
                            name="(default)",
                            archive_internal_path="",
                            package_files=sorted(next(iter(loose_groups.values()))),
                    )
                )
            else:
                    archive.options.append(
                        ModOption(
                            name="(default)",
                            archive_internal_path="",
                            package_files=sorted(root_loose_files),
                        )
                    )
        return archive

    def _find_archive_by_filename(self, archive_filename: str) -> ModArchive | None:
        for archive in self.archives:
            if archive.filepath.name == archive_filename:
                return archive
        return None

    def _find_or_load_archive_by_filename(self, archive_filename: str) -> ModArchive | None:
        archive = self._find_archive_by_filename(archive_filename)
        if archive is not None:
            return archive

        candidate = self.mods_dir / archive_filename
        if not candidate.exists() or candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None

        try:
            return self._analyze_archive(candidate)
        except Exception as exc:
            self.log(f"Warning: Could not analyze {archive_filename}: {exc}")
            return None

    def _record_abs_path(self, relpath: str) -> Path:
        return self.game_root_dir / relpath.replace("/", os.sep)

    def _record_path_exists(self, relpath: str) -> bool:
        return self._record_abs_path(relpath).exists()

    def get_environment_status(self) -> EnvironmentStatus:
        package_dir_exists = self.game_package_dir.exists()
        game_root_exists = self.game_root_dir.exists()
        mods_dir_exists = self.mods_dir.exists()
        yumia_available = self.yumia_exe.exists()
        dll_loader_available = (self.game_root_dir / "DINPUT8.dll").exists()
        loose_plugin_dll_available = (self.plugins_dir / "LooseFileLoader.dll").exists()
        loose_plugin_ini_available = (self.plugins_dir / "LooseFileLoader.ini").exists()
        loose_ready = (
            dll_loader_available
            and loose_plugin_dll_available
            and loose_plugin_ini_available
        )

        has_active_yumia_mods = any(rec.backend == "yumia" for rec in self.installed.values())
        has_active_loose_mods = any(rec.backend == "loose" for rec in self.installed.values())
        if has_active_yumia_mods:
            active_backend: Literal["none", "yumia", "loose"] = "yumia"
        elif has_active_loose_mods:
            active_backend = "loose"
        else:
            active_backend = "none"

        if has_active_yumia_mods:
            can_install = yumia_available
        elif has_active_loose_mods:
            can_install = loose_ready
        else:
            can_install = loose_ready or yumia_available

        can_migrate = has_active_yumia_mods and loose_ready and self._backups_exist()
        return EnvironmentStatus(
            package_dir_exists=package_dir_exists,
            game_root_exists=game_root_exists,
            mods_dir_exists=mods_dir_exists,
            yumia_available=yumia_available,
            dll_loader_available=dll_loader_available,
            loose_plugin_dll_available=loose_plugin_dll_available,
            loose_plugin_ini_available=loose_plugin_ini_available,
            loose_ready=loose_ready,
            active_backend=active_backend,
            has_active_yumia_mods=has_active_yumia_mods,
            has_active_loose_mods=has_active_loose_mods,
            can_install=can_install,
            can_migrate=can_migrate,
        )

    def resolve_install_backend(self) -> InstallBackend | None:
        status = self.get_environment_status()
        if status.has_active_yumia_mods:
            return "yumia" if status.yumia_available else None
        if status.has_active_loose_mods:
            return "loose" if status.loose_ready else None
        if status.loose_ready:
            return "loose"
        if status.yumia_available:
            return "yumia"
        return None

    def is_installed(self, archive_filename: str) -> bool:
        return archive_filename in self.installed

    def get_installed_option(self, archive_filename: str) -> Optional[str]:
        rec = self.installed.get(archive_filename)
        return rec.option_name if rec else None

    def get_installed_backend(self, archive_filename: str) -> Optional[InstallBackend]:
        rec = self.installed.get(archive_filename)
        return rec.backend if rec else None

    def check_installed_status(self):
        self.load_install_state()
        stale_keys = []
        for key, rec in self.installed.items():
            if not all(self._record_path_exists(path) for path in rec.installed_paths):
                self.log(
                    f"  Mod '{rec.option_name}' from {rec.archive_filename}: "
                    "files missing, marking as not installed"
                )
                stale_keys.append(key)

        for key in stale_keys:
            del self.installed[key]

        if stale_keys:
            self.save_install_state()
            self._save_legacy_installed_mods_manifest()

        self.log(f"Verified {len(self.installed)} mod(s) currently installed")

    def _selected_option_members(self, archive: ModArchive, option: ModOption) -> list[str]:
        if archive.archive_kind == "direct_loose":
            seen: set[str] = set()
            members: list[str] = []
            for member in list(archive.direct_loose_common_files) + list(option.package_files):
                if member not in seen:
                    members.append(member)
                    seen.add(member)
            return members
        return [option.archive_internal_path + pf for pf in option.package_files]

    def _selected_direct_loose_members(
        self,
        archive: ModArchive,
        options: list[ModOption],
    ) -> list[str]:
        members = list(archive.direct_loose_common_files)
        seen = set(members)
        for option in options:
            for member in option.package_files:
                if member not in seen:
                    members.append(member)
                    seen.add(member)
        return members

    @staticmethod
    def _format_loose_conversion_error(exc: Exception) -> str:
        message = str(exc)
        if message.startswith("Loose filename collision in "):
            return (
                "Cannot install this loose-file mod because multiple selected files would produce "
                "the same final filename.\n\n"
                + message
            )
        if message.startswith("No installable loose payloads found in "):
            return "The selected archive does not contain any installable loose-file payloads."
        return message

    def _build_direct_loose_summary(
        self,
        archive: ModArchive,
        options: list[ModOption],
    ) -> str:
        names = [option.name for option in options]
        if names:
            joined = "; ".join(names)
            if archive.direct_loose_common_files:
                return f"Common + {joined}"
            return joined
        if archive.direct_loose_common_files:
            return "(common files only)"
        return "(default)"

    def _manifest_dest_to_member_map(
        self,
        archive: ModArchive,
        feature_selections: dict[str, str | None],
    ) -> dict[str, str]:
        manifest = archive.manifest
        assert manifest is not None

        dest_to_member: dict[str, str] = {}
        if manifest.common_files_dir:
            prefix = manifest.common_files_dir + "/"
            for name in archive.archive_names:
                if name.startswith(prefix) and not name.endswith("/"):
                    dest_file = name[len(prefix):]
                    if dest_file:
                        dest_to_member[dest_file] = name

        for feature in manifest.features:
            chosen = feature_selections.get(feature.name)
            if chosen is None:
                continue
            prefix = f"{feature.directory}/{chosen}/"
            for name in archive.archive_names:
                if name.startswith(prefix) and not name.endswith("/"):
                    dest_file = name[len(prefix):]
                    if dest_file:
                        dest_to_member[dest_file] = name
        return dest_to_member

    def _validate_feature_selections(
        self,
        archive: ModArchive,
        feature_selections: dict[str, str | None],
    ) -> tuple[bool, str]:
        manifest = archive.manifest
        assert manifest is not None
        for feature in manifest.features:
            available = archive.manifest_options.get(feature.name, [])
            chosen = feature_selections.get(feature.name)
            if chosen is None:
                if not feature.optional:
                    return False, f"Required feature '{feature.name}' has no option selected."
                continue
            if chosen not in available:
                return (
                    False,
                    f"Invalid option '{chosen}' for feature '{feature.name}'. "
                    f"Available options: {available}",
                )
        return True, ""

    def _build_feature_summary(
        self,
        archive: ModArchive,
        feature_selections: dict[str, str | None],
    ) -> str:
        manifest = archive.manifest
        assert manifest is not None
        parts = [
            f"{feature.name}: {feature_selections[feature.name]}"
            for feature in manifest.features
            if feature_selections.get(feature.name) is not None
        ]
        return "; ".join(parts) if parts else "(common files only)"

    def _parse_feature_summary(self, summary: str) -> dict[str, str]:
        selections: dict[str, str] = {}
        for chunk in summary.split(";"):
            part = chunk.strip()
            if not part or ":" not in part:
                continue
            feature, option = part.split(":", 1)
            selections[feature.strip()] = option.strip()
        return selections

    def _feature_option_dest_files(
        self,
        archive: ModArchive,
        feature_name: str,
        option_name: str,
    ) -> set[str]:
        manifest = archive.manifest
        assert manifest is not None
        feature = next((item for item in manifest.features if item.name == feature_name), None)
        if feature is None:
            return set()
        prefix = f"{feature.directory}/{option_name}/"
        files = set()
        for name in archive.archive_names:
            if name.startswith(prefix) and not name.endswith("/"):
                dest_file = name[len(prefix):]
                if dest_file:
                    files.add(dest_file)
        return files

    def _infer_feature_selections_from_installed_files(
        self,
        archive: ModArchive,
        installed_paths: list[str],
    ) -> dict[str, str | None] | None:
        manifest = archive.manifest
        assert manifest is not None
        package_files = {
            path[len("package/"):] if path.startswith("package/") else path
            for path in installed_paths
        }
        selections: dict[str, str | None] = {}
        for feature in manifest.features:
            matches = []
            for option_name in archive.manifest_options.get(feature.name, []):
                option_files = self._feature_option_dest_files(archive, feature.name, option_name)
                if option_files and option_files.issubset(package_files):
                    matches.append(option_name)
            if len(matches) == 1:
                selections[feature.name] = matches[0]
            elif len(matches) == 0 and feature.optional:
                selections[feature.name] = None
            else:
                return None
        ok, _ = self._validate_feature_selections(archive, selections)
        return selections if ok else None

    def _resolve_feature_selections_for_summary(
        self,
        archive: ModArchive,
        summary: str,
        installed_paths: list[str],
    ) -> dict[str, str | None] | None:
        manifest = archive.manifest
        assert manifest is not None
        parsed = self._parse_feature_summary(summary)
        if parsed:
            candidate = {feature.name: parsed.get(feature.name) for feature in manifest.features}
            ok, _ = self._validate_feature_selections(archive, candidate)
            if ok:
                return candidate
        return self._infer_feature_selections_from_installed_files(archive, installed_paths)

    def _resolve_manifest_record_selections(
        self,
        record: InstalledModRecord,
        archive: ModArchive,
    ) -> dict[str, str | None] | None:
        if record.feature_selections:
            ok, _ = self._validate_feature_selections(archive, record.feature_selections)
            if ok:
                return record.feature_selections
        return self._resolve_feature_selections_for_summary(
            archive,
            record.display_option_summary,
            record.installed_paths,
        )

    def _resolve_legacy_option_for_record(
        self,
        record: InstalledModRecord,
        archive: ModArchive,
    ) -> ModOption | None:
        target_name = record.legacy_option_name or record.display_option_summary
        for option in archive.options:
            if option.name == target_name:
                return option
        if len(archive.options) == 1:
            return archive.options[0]
        return None

    def _yumia_records(self) -> dict[str, InstalledModRecord]:
        return {key: rec for key, rec in self.installed.items() if rec.backend == "yumia"}

    def _find_yumia_conflicts(
        self,
        archive: ModArchive,
        archive_members: list[str],
    ) -> list[tuple[str, set[int]]]:
        return find_conflicts(archive, archive_members, self._yumia_records(), self.game_package_dir)

    def _iter_loose_disk_files(self) -> list[str]:
        relpaths: list[str] = []
        if not self.loose_mods_dir.exists():
            return relpaths
        for path in self.loose_mods_dir.rglob("*"):
            if not path.is_file():
                continue
            rel_to_mods = path.relative_to(self.loose_mods_dir)
            if len(rel_to_mods.parts) > 2:
                continue
            relpaths.append(path.relative_to(self.game_root_dir).as_posix())
        return relpaths

    def _check_loose_conflicts(
        self,
        loose_mod_dir: str,
        filenames: list[str],
        ignore_archives: set[str] | None = None,
    ) -> tuple[bool, str]:
        ignore_archives = ignore_archives or set()
        issues: list[str] = []
        planned = {name.lower(): name for name in filenames}
        target_dir_lower = loose_mod_dir.lower()
        tracked_loose_paths = set()

        for archive_filename, rec in self.installed.items():
            if archive_filename in ignore_archives or rec.backend != "loose":
                continue
            if rec.loose_mod_dir and rec.loose_mod_dir.lower() == target_dir_lower:
                issues.append(
                    f"Target loose mod folder '{loose_mod_dir}' is already owned by '{archive_filename}'."
                )
            for relpath in rec.installed_paths:
                tracked_loose_paths.add(relpath.lower())
                basename = Path(relpath).name.lower()
                if basename in planned:
                    issues.append(
                        f"Loose file '{planned[basename]}' conflicts with installed mod '{archive_filename}'."
                    )

        for relpath in self._iter_loose_disk_files():
            if relpath.lower() in tracked_loose_paths:
                continue
            basename = Path(relpath).name.lower()
            if basename in planned:
                issues.append(
                    f"Loose file '{planned[basename]}' conflicts with existing file '{relpath}'."
                )

        target_dir = self.game_root_dir / loose_mod_dir.replace("/", os.sep)
        if target_dir.exists():
            for path in target_dir.rglob("*"):
                if not path.is_file():
                    continue
                relpath = path.relative_to(self.game_root_dir).as_posix()
                if relpath.lower() not in tracked_loose_paths:
                    issues.append(
                        f"Target loose mod folder '{loose_mod_dir}' already contains untracked files."
                    )
                    break

        if issues:
            deduped = []
            for issue in issues:
                if issue not in deduped:
                    deduped.append(issue)
            return False, "Cannot install via LooseFileLoader:\n\n" + "\n".join(deduped)
        return True, ""

    def _backups_exist(self) -> bool:
        return all((self.game_package_dir / backup).exists() for backup in CORE_RDB_BACKUPS)

    def _loose_backend_action_message(self, *, selection_specific: bool = False) -> str:
        status = self.get_environment_status()
        subject = (
            "This selected mod option uses LooseFileLoader files"
            if selection_specific
            else "This mod is packaged for LooseFileLoader"
        )
        if not status.loose_ready:
            return (
                f"{subject} and cannot be installed with yumia.\n\n"
                "Install the DLL loader and LooseFileLoader using the buttons in "
                "Setup / Backend Status, then try again."
            )
        if status.has_active_yumia_mods:
            return (
                f"{subject} and cannot be installed with yumia.\n\n"
                "LooseFileLoader is already installed, but new installs are still routed through "
                "yumia because you have active Yumia-managed mods. Use the Migrate Yumia Installs "
                "button in Setup / Backend Status, or uninstall the remaining Yumia-managed mods first."
            )
        return (
            f"{subject} and cannot be installed with yumia.\n\n"
            "LooseFileLoader is already installed. Switch to the loose-file workflow and try again."
        )

    def _restore_rdb_backups(self):
        for core, backup in zip(CORE_RDB_FILES, CORE_RDB_BACKUPS):
            src = self.game_package_dir / backup
            dst = self.game_package_dir / core
            if src.exists():
                shutil.copy2(src, dst)
                self.log(f"  Restored {backup} -> {core}")
            else:
                self.log(f"  WARNING: Backup {backup} not found!")

    def run_yumia(self) -> tuple[bool, str]:
        if os.environ.get("NIOH3MM_MOCK_YUMIA") == "1":
            self.log("Running mock yumia backend")
            for core, backup in zip(CORE_RDB_FILES, CORE_RDB_BACKUPS):
                core_path = self.game_package_dir / core
                backup_path = self.game_package_dir / backup
                if not core_path.exists():
                    core_path.write_bytes(b"")
                if not backup_path.exists():
                    shutil.copy2(core_path, backup_path)
                    self.log(f"  Created mock backup: {backup}")
            return True, "mock yumia"

        if not self.yumia_exe.exists():
            return False, f"yumia exe not found at {self.yumia_exe}"

        self.log(f"Running yumia: {self.yumia_exe}")
        try:
            proc = subprocess.Popen(
                [str(self.yumia_exe)],
                cwd=str(self.game_package_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            stdout, _ = proc.communicate(input="y\n", timeout=300)
            success = proc.returncode == 0
            self.log(f"  yumia exited with code {proc.returncode}")
            if stdout:
                for line in stdout.strip().split("\n")[-10:]:
                    self.log(f"  [yumia] {line}")
            return success, stdout or ""
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, "yumia timed out after 5 minutes"
        except Exception as exc:
            return False, f"Error running yumia: {exc}"

    def _cleanup_empty_dirs(self, start: Path, stop_at: Path):
        current = start
        while current.exists() and current != stop_at and current != current.parent:
            if any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent

    def _loose_record_from_files(
        self,
        archive: ModArchive,
        install_kind: InstallKind,
        display_summary: str,
        legacy_option_name: str | None,
        feature_selections: dict[str, str | None] | None,
        mod_name: str,
        files: list[tuple[str, bytes]],
    ) -> tuple[str, list[str], InstalledModRecord]:
        loose_mod_dir = (Path("mods") / sanitize_mod_dir_name(mod_name)).as_posix()
        installed_paths = [
            (Path(loose_mod_dir) / filename).as_posix()
            for filename, _ in files
        ]
        record = InstalledModRecord(
            archive_filename=archive.filepath.name,
            backend="loose",
            install_kind=install_kind,
            display_option_summary=display_summary,
            legacy_option_name=legacy_option_name,
            feature_selections=feature_selections,
            installed_paths=installed_paths,
            loose_mod_dir=loose_mod_dir,
        )
        return loose_mod_dir, installed_paths, record

    def _write_loose_files(
        self,
        loose_mod_dir: str,
        files: list[tuple[str, bytes]],
    ) -> tuple[bool, str]:
        target_dir = self.game_root_dir / loose_mod_dir.replace("/", os.sep)
        written_paths: list[Path] = []
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            for filename, data in files:
                dst = target_dir / filename
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
                written_paths.append(dst)
                self.log(f"  Wrote: {(Path(loose_mod_dir) / filename).as_posix()}")
            return True, f"Installed {len(files)} file(s)"
        except Exception as exc:
            for path in reversed(written_paths):
                if path.exists():
                    path.unlink()
            self._cleanup_empty_dirs(target_dir, stop_at=self.loose_mods_dir)
            return False, f"Loose install failed: {exc}"

    def _install_yumia_legacy(self, archive: ModArchive, option: ModOption) -> tuple[bool, str]:
        if archive.archive_kind == "direct_loose":
            return (
                False,
                self._loose_backend_action_message(),
            )

        members = self._selected_option_members(archive, option)
        conflicts = self._find_yumia_conflicts(archive, members)
        if conflicts:
            lines = []
            for fname, overlap in conflicts:
                samples = ", ".join(sorted(f"0x{h:08x}" for h in list(overlap)[:5]))
                suffix = f" (+{len(overlap) - 5} more)" if len(overlap) > 5 else ""
                lines.append(
                    f"  • {Path(fname).stem}  ({len(overlap)} asset(s): {samples}{suffix})"
                )
            msg = "Cannot install: conflicts with installed mod(s):\n\n"
            msg += "\n".join(lines)
            msg += "\n\nUninstall the conflicting mod(s) before proceeding."
            return False, msg

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path as _TmpPath

            tmppath = _TmpPath(tmpdir)
            self.log(f"  Extracting {len(members)} file(s)...")
            try:
                self._extract_from_archive(archive.filepath, members, tmppath)
            except Exception as exc:
                return False, f"Extraction failed: {exc}"

            extracted_base = tmppath / option.archive_internal_path.replace("/", os.sep)
            installed_paths = []
            for pf in option.package_files:
                src = extracted_base / pf.replace("/", os.sep)
                dst = self.game_package_dir / pf.replace("/", os.sep)
                if not src.exists():
                    self.log(f"  WARNING: Expected file not found after extraction: {src}")
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                relpath = (Path("package") / pf).as_posix()
                installed_paths.append(relpath)
                self.log(f"  Copied: {relpath}")

        if not installed_paths:
            return False, "No files were installed."

        self.log("  Running yumia to patch RDB files...")
        success, output = self.run_yumia()
        if not success:
            self.log("  yumia failed, rolling back...")
            for relpath in installed_paths:
                fp = self._record_abs_path(relpath)
                if fp.exists():
                    fp.unlink()
            return False, f"yumia failed: {output}"

        self.installed[archive.filepath.name] = InstalledModRecord(
            archive_filename=archive.filepath.name,
            backend="yumia",
            install_kind="legacy",
            display_option_summary=option.name,
            legacy_option_name=option.name,
            feature_selections=None,
            installed_paths=installed_paths,
            loose_mod_dir=None,
        )
        self.save_install_state()
        self._save_legacy_installed_mods_manifest()
        self.log(f"  Successfully installed '{option.name}' ({len(installed_paths)} files)")
        return True, f"Installed {len(installed_paths)} file(s)"

    def _install_loose_legacy(self, archive: ModArchive, option: ModOption) -> tuple[bool, str]:
        try:
            result = convert_selected_members_to_loose(
                archive.filepath,
                self._selected_option_members(archive, option),
                mod_name=archive.name,
            )
        except Exception as exc:
            return False, self._format_loose_conversion_error(exc)
        files = [(item.filename, item.data) for item in result.files]
        loose_mod_dir, _, record = self._loose_record_from_files(
            archive,
            install_kind="legacy",
            display_summary=option.name,
            legacy_option_name=option.name,
            feature_selections=None,
            mod_name=result.mod_name,
            files=files,
        )
        ok, msg = self._check_loose_conflicts(loose_mod_dir, [name for name, _ in files])
        if not ok:
            return False, msg

        success, message = self._write_loose_files(loose_mod_dir, files)
        if not success:
            return False, message

        self.installed[archive.filepath.name] = record
        self.save_install_state()
        self._save_legacy_installed_mods_manifest()
        self.log(f"  Successfully installed '{option.name}' ({len(files)} files)")
        return True, message

    def install_direct_loose_mod(
        self,
        archive: ModArchive,
        options: list[ModOption],
        backend: InstallBackend | None = None,
    ) -> tuple[bool, str]:
        self.log(f"Installing loose-file mod from {archive.filepath.name}...")
        if archive.filepath.name in self.installed:
            return (
                False,
                f"A mod from {archive.filepath.name} is already installed. Uninstall it first.",
            )
        if archive.archive_kind != "direct_loose":
            return False, "This archive is not a direct LooseFileLoader mod."

        backend = backend or self.resolve_install_backend()
        if backend is None:
            return False, "No supported install backend is currently available."
        if backend != "loose":
            return (
                False,
                self._loose_backend_action_message(),
            )

        selected_members = self._selected_direct_loose_members(archive, options)
        if not selected_members:
            return False, "Select at least one loose-file option to install."

        try:
            result = convert_selected_members_to_loose(
                archive.filepath,
                selected_members,
                mod_name=archive.name,
            )
        except Exception as exc:
            return False, self._format_loose_conversion_error(exc)

        files = [(item.filename, item.data) for item in result.files]
        summary = self._build_direct_loose_summary(archive, options)
        loose_mod_dir, _, record = self._loose_record_from_files(
            archive,
            install_kind="legacy",
            display_summary=summary,
            legacy_option_name=None,
            feature_selections=None,
            mod_name=result.mod_name,
            files=files,
        )
        ok, msg = self._check_loose_conflicts(loose_mod_dir, [name for name, _ in files])
        if not ok:
            return False, msg

        success, message = self._write_loose_files(loose_mod_dir, files)
        if not success:
            return False, message

        self.installed[archive.filepath.name] = record
        self.save_install_state()
        self._save_legacy_installed_mods_manifest()
        self.log(f"  Successfully installed loose-file selection ({len(files)} files)")
        return True, message

    def _install_yumia_manifest(
        self,
        archive: ModArchive,
        feature_selections: dict[str, str | None],
    ) -> tuple[bool, str]:
        valid, error = self._validate_feature_selections(archive, feature_selections)
        if not valid:
            return False, error

        dest_to_member = self._manifest_dest_to_member_map(archive, feature_selections)
        if not dest_to_member:
            return False, "No files found for the selected options."

        loose_members = [
            member
            for member in dest_to_member.values()
            if is_loose_asset_member(member)
            and not member.endswith(".fdata")
            and not member.endswith(".yumiamod.json")
        ]
        if loose_members:
            return (
                False,
                self._loose_backend_action_message(selection_specific=True),
            )

        yumia_map = {
            dest_file: member
            for dest_file, member in dest_to_member.items()
            if member.endswith(".fdata") or member.endswith(".yumiamod.json")
        }
        if not yumia_map:
            return False, "No yumia-compatible files found for the selected options."

        conflicts = self._find_yumia_conflicts(archive, list(yumia_map.values()))
        if conflicts:
            lines = []
            for fname, overlap in conflicts:
                samples = ", ".join(sorted(f"0x{h:08x}" for h in list(overlap)[:5]))
                suffix = f" (+{len(overlap) - 5} more)" if len(overlap) > 5 else ""
                lines.append(
                    f"  • {Path(fname).stem}  ({len(overlap)} asset(s): {samples}{suffix})"
                )
            msg = "Cannot install: conflicts with installed mod(s):\n\n"
            msg += "\n".join(lines)
            msg += "\n\nUninstall the conflicting mod(s) before proceeding."
            return False, msg

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path as _TmpPath

            tmppath = _TmpPath(tmpdir)
            try:
                self._extract_from_archive(archive.filepath, list(yumia_map.values()), tmppath)
            except Exception as exc:
                return False, f"Extraction failed: {exc}"

            installed_paths = []
            for dest_file, archive_member in yumia_map.items():
                src = tmppath / archive_member.replace("/", os.sep)
                dst = self.game_package_dir / dest_file.replace("/", os.sep)
                if not src.exists():
                    self.log(f"  WARNING: Expected file not found after extraction: {src}")
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                relpath = (Path("package") / dest_file).as_posix()
                installed_paths.append(relpath)
                self.log(f"  Copied: {relpath}")

        if not installed_paths:
            return False, "No files were installed."

        self.log("  Running yumia to patch RDB files...")
        success, output = self.run_yumia()
        if not success:
            self.log("  yumia failed, rolling back...")
            for relpath in installed_paths:
                fp = self._record_abs_path(relpath)
                if fp.exists():
                    fp.unlink()
            return False, f"yumia failed: {output}"

        summary = self._build_feature_summary(archive, feature_selections)
        self.installed[archive.filepath.name] = InstalledModRecord(
            archive_filename=archive.filepath.name,
            backend="yumia",
            install_kind="manifest",
            display_option_summary=summary,
            legacy_option_name=None,
            feature_selections=feature_selections,
            installed_paths=installed_paths,
            loose_mod_dir=None,
        )
        self.save_install_state()
        self._save_legacy_installed_mods_manifest()
        self.log(f"  Successfully installed manifest mod ({len(installed_paths)} files)")
        return True, f"Installed {len(installed_paths)} file(s)"

    def _install_loose_manifest(
        self,
        archive: ModArchive,
        feature_selections: dict[str, str | None],
    ) -> tuple[bool, str]:
        valid, error = self._validate_feature_selections(archive, feature_selections)
        if not valid:
            return False, error

        try:
            selected_members = select_manifest_members(
                archive.filepath.name,
                archive.archive_names,
                archive.manifest,
                feature_selections,
            )
            result = convert_selected_members_to_loose(
                archive.filepath,
                selected_members,
                mod_name=archive.name,
                manifest=archive.manifest,
                feature_selections=feature_selections,
            )
        except Exception as exc:
            return False, self._format_loose_conversion_error(exc)
        files = [(item.filename, item.data) for item in result.files]
        summary = self._build_feature_summary(archive, feature_selections)
        loose_mod_dir, _, record = self._loose_record_from_files(
            archive,
            install_kind="manifest",
            display_summary=summary,
            legacy_option_name=None,
            feature_selections=feature_selections,
            mod_name=result.mod_name,
            files=files,
        )
        ok, msg = self._check_loose_conflicts(loose_mod_dir, [name for name, _ in files])
        if not ok:
            return False, msg

        success, message = self._write_loose_files(loose_mod_dir, files)
        if not success:
            return False, message

        self.installed[archive.filepath.name] = record
        self.save_install_state()
        self._save_legacy_installed_mods_manifest()
        self.log(f"  Successfully installed manifest mod ({len(files)} files)")
        return True, message

    def install_mod(
        self,
        archive: ModArchive,
        option: ModOption,
        backend: InstallBackend | None = None,
    ) -> tuple[bool, str]:
        return self.install_legacy_mod(archive, option, backend=backend)

    def install_legacy_mod(
        self,
        archive: ModArchive,
        option: ModOption,
        backend: InstallBackend | None = None,
    ) -> tuple[bool, str]:
        self.log(f"Installing '{option.name}' from {archive.filepath.name}...")
        if archive.filepath.name in self.installed:
            return (
                False,
                f"A mod from {archive.filepath.name} is already installed. Uninstall it first.",
            )
        backend = backend or self.resolve_install_backend()
        if backend is None:
            return False, "No supported install backend is currently available."
        if backend == "yumia":
            return self._install_yumia_legacy(archive, option)
        return self._install_loose_legacy(archive, option)

    def install_manifest_mod(
        self,
        archive: ModArchive,
        feature_selections: dict[str, str | None],
        backend: InstallBackend | None = None,
    ) -> tuple[bool, str]:
        assert archive.manifest is not None, "install_manifest_mod called on non-manifest archive"
        self.log(f"Installing manifest mod {archive.filepath.name}...")
        if archive.filepath.name in self.installed:
            return (
                False,
                f"A mod from {archive.filepath.name} is already installed. Uninstall it first.",
            )
        backend = backend or self.resolve_install_backend()
        if backend is None:
            return False, "No supported install backend is currently available."
        if backend == "yumia":
            return self._install_yumia_manifest(archive, feature_selections)
        return self._install_loose_manifest(archive, feature_selections)

    def _uninstall_loose(self, archive_filename: str, rec: InstalledModRecord) -> tuple[bool, str]:
        removed = 0
        for relpath in rec.installed_paths:
            fp = self._record_abs_path(relpath)
            if fp.exists():
                fp.unlink()
                removed += 1
                self.log(f"  Removed: {relpath}")
            else:
                self.log(f"  Already missing: {relpath}")

            parent = fp.parent
            if parent.exists():
                self._cleanup_empty_dirs(parent, stop_at=self.loose_mods_dir)

        del self.installed[archive_filename]
        self.save_install_state()
        self._save_legacy_installed_mods_manifest()
        self.log(f"  Successfully uninstalled '{rec.option_name}'")
        return True, f"Removed {removed} file(s)"

    def _uninstall_yumia(self, archive_filename: str, rec: InstalledModRecord) -> tuple[bool, str]:
        removed = 0
        for relpath in rec.installed_paths:
            fp = self._record_abs_path(relpath)
            if fp.exists():
                fp.unlink()
                removed += 1
                self.log(f"  Removed: {relpath}")
            else:
                self.log(f"  Already missing: {relpath}")

            parent = fp.parent
            if parent.exists():
                self._cleanup_empty_dirs(parent, stop_at=self.game_package_dir)

        if self._backups_exist():
            self.log("  Restoring RDB backups...")
            self._restore_rdb_backups()
        else:
            self.log(
                "  WARNING: RDB backups not found! You may need to verify game file integrity via Steam."
            )

        del self.installed[archive_filename]
        self.save_install_state()
        self._save_legacy_installed_mods_manifest()

        remaining_yumia = [item for item in self.installed.values() if item.backend == "yumia"]
        if remaining_yumia:
            self.log(f"  Re-applying {len(remaining_yumia)} remaining mod(s) via yumia...")
            success, output = self.run_yumia()
            if not success:
                self.log(f"  WARNING: yumia failed during re-application: {output}")
                return (
                    True,
                    f"Mod removed ({removed} files), but yumia re-application failed. "
                    "Other legacy mods may not work correctly.",
                )
        else:
            self.log("  No other yumia mods to re-apply.")

        self.log(f"  Successfully uninstalled '{rec.option_name}'")
        return True, f"Removed {removed} file(s)"

    def uninstall_mod(self, archive_filename: str) -> tuple[bool, str]:
        rec = self.installed.get(archive_filename)
        if not rec:
            return False, f"No installed mod found for {archive_filename}"

        self.log(f"Uninstalling '{rec.option_name}' from {archive_filename}...")
        if rec.backend == "loose":
            return self._uninstall_loose(archive_filename, rec)
        return self._uninstall_yumia(archive_filename, rec)

    def _build_migration_plan(self) -> tuple[bool, str, list[MigrationPlanItem]]:
        status = self.get_environment_status()
        if not status.has_active_yumia_mods:
            return False, "No yumia-managed mods are installed.", []
        if not status.loose_ready:
            return False, "LooseFileLoader prerequisites are missing.", []
        if not self._backups_exist():
            return False, "RDB backups are required before migration.", []

        items: list[MigrationPlanItem] = []
        planned_names: dict[str, str] = {}
        planned_dirs: set[str] = set()

        for rec in self.installed.values():
            if rec.backend != "yumia":
                continue

            archive = self._find_or_load_archive_by_filename(rec.archive_filename)
            if archive is None:
                return False, f"Installed archive not found in downloads folder: {rec.archive_filename}", []

            if rec.install_kind == "manifest":
                if archive.manifest is None:
                    return False, f"Archive no longer exposes a manifest: {rec.archive_filename}", []
                selections = self._resolve_manifest_record_selections(rec, archive)
                if selections is None:
                    return False, f"Could not reconstruct manifest selections for {rec.archive_filename}.", []
                try:
                    selected_members = select_manifest_members(
                        archive.filepath.name,
                        archive.archive_names,
                        archive.manifest,
                        selections,
                    )
                    result = convert_selected_members_to_loose(
                        archive.filepath,
                        selected_members,
                        mod_name=archive.name,
                        manifest=archive.manifest,
                        feature_selections=selections,
                    )
                except Exception as exc:
                    return False, f"Could not convert {rec.archive_filename}: {exc}", []
                files = [(item.filename, item.data) for item in result.files]
                display_summary = self._build_feature_summary(archive, selections)
                legacy_option_name = None
                feature_selections = selections
            else:
                option = self._resolve_legacy_option_for_record(rec, archive)
                if option is None:
                    return False, f"Could not reconstruct the legacy option for {rec.archive_filename}.", []
                try:
                    result = convert_selected_members_to_loose(
                        archive.filepath,
                        self._selected_option_members(archive, option),
                        mod_name=archive.name,
                    )
                except Exception as exc:
                    return False, f"Could not convert {rec.archive_filename}: {exc}", []
                files = [(item.filename, item.data) for item in result.files]
                display_summary = option.name
                legacy_option_name = option.name
                feature_selections = None

            loose_mod_dir, installed_paths, _ = self._loose_record_from_files(
                archive,
                install_kind=rec.install_kind,
                display_summary=display_summary,
                legacy_option_name=legacy_option_name,
                feature_selections=feature_selections,
                mod_name=result.mod_name,
                files=files,
            )
            if loose_mod_dir.lower() in planned_dirs:
                return False, f"Migration would reuse loose mod folder '{loose_mod_dir}' more than once.", []
            planned_dirs.add(loose_mod_dir.lower())

            for filename, _data in files:
                lowered = filename.lower()
                if lowered in planned_names:
                    return False, f"Migration would create conflicting loose file '{filename}'.", []
                planned_names[lowered] = rec.archive_filename

            items.append(
                MigrationPlanItem(
                    record=rec,
                    archive=archive,
                    display_option_summary=display_summary,
                    legacy_option_name=legacy_option_name,
                    feature_selections=feature_selections,
                    loose_mod_dir=loose_mod_dir,
                    installed_paths=installed_paths,
                    files=files,
                )
            )

        for item in items:
            ok, msg = self._check_loose_conflicts(
                item.loose_mod_dir,
                [filename for filename, _ in item.files],
            )
            if not ok:
                return False, msg, []

        return True, "", items

    def migrate_all_yumia_to_loose(self) -> tuple[bool, str]:
        self.log("Starting migration from yumia installs to LooseFileLoader...")
        ok, message, plans = self._build_migration_plan()
        if not ok:
            return False, message

        staged_rdb = {
            core: (self.game_package_dir / core).read_bytes()
            for core in CORE_RDB_FILES
            if (self.game_package_dir / core).exists()
        }
        staged_package_files = {}
        for plan in plans:
            for relpath in plan.record.installed_paths:
                fp = self._record_abs_path(relpath)
                if fp.exists():
                    staged_package_files[fp] = fp.read_bytes()

        written_paths: list[Path] = []
        old_records = {key: InstalledModRecord(**asdict(rec)) for key, rec in self.installed.items()}
        try:
            for plan in plans:
                target_dir = self.game_root_dir / plan.loose_mod_dir.replace("/", os.sep)
                target_dir.mkdir(parents=True, exist_ok=True)
                for filename, data in plan.files:
                    dst = target_dir / filename
                    dst.write_bytes(data)
                    written_paths.append(dst)
                    self.log(f"  Wrote: {(Path(plan.loose_mod_dir) / filename).as_posix()}")

            for plan in plans:
                for relpath in plan.record.installed_paths:
                    fp = self._record_abs_path(relpath)
                    if fp.exists():
                        fp.unlink()
                        self.log(f"  Removed: {relpath}")

            self.log("  Restoring vanilla RDB files...")
            self._restore_rdb_backups()

            for plan in plans:
                self.installed[plan.record.archive_filename] = InstalledModRecord(
                    archive_filename=plan.record.archive_filename,
                    backend="loose",
                    install_kind=plan.record.install_kind,
                    display_option_summary=plan.display_option_summary,
                    legacy_option_name=plan.legacy_option_name,
                    feature_selections=plan.feature_selections,
                    installed_paths=plan.installed_paths,
                    loose_mod_dir=plan.loose_mod_dir,
                )

            self.save_install_state()
            self._save_legacy_installed_mods_manifest()
            self.log(f"Migration complete: {len(plans)} mod(s) moved to LooseFileLoader")
            return True, f"Migrated {len(plans)} mod(s) to LooseFileLoader"
        except Exception as exc:
            self.log(f"  Migration failed, rolling back: {exc}")
            for core, data in staged_rdb.items():
                (self.game_package_dir / core).write_bytes(data)
            for fp, data in staged_package_files.items():
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(data)
            for path in reversed(written_paths):
                if path.exists():
                    path.unlink()
            for plan in plans:
                target_dir = self.game_root_dir / plan.loose_mod_dir.replace("/", os.sep)
                if target_dir.exists():
                    self._cleanup_empty_dirs(target_dir, stop_at=self.loose_mods_dir)
            self.installed = old_records
            self.save_install_state()
            self._save_legacy_installed_mods_manifest()
            return False, f"Migration failed: {exc}"

    def validate_paths(self) -> list[str]:
        issues = []
        if not self.mods_dir.exists():
            issues.append(f"Mods directory does not exist: {self.mods_dir}")
        if not self.game_package_dir.exists():
            issues.append(f"Game package directory does not exist: {self.game_package_dir}")
        return issues
