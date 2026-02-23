"""
Nioh 3 Mod Manager - Core Logic

Handles archive scanning, mod installation/uninstallation, and status tracking.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

from conflict_detection import find_conflicts
from manifest_schema import MANIFEST_FILENAME, ModManifest, parse_manifest

import py7zr
import rarfile

# Point rarfile at UnRAR.exe — frozen exe uses _MEIPASS, dev uses assets/
if getattr(sys, "frozen", False):
    _unrar = Path(sys._MEIPASS) / "UnRAR.exe"
else:
    _unrar = Path(__file__).parent / "assets" / "UnRAR.exe"
if _unrar.exists():
    rarfile.UNRAR_TOOL = str(_unrar)

SUPPORTED_EXTENSIONS = {".zip", ".7z", ".rar"}

CORE_RDB_FILES = ("system.rdb", "root.rdb")
CORE_RDB_BACKUPS = ("system.rdb.original", "root.rdb.original")
YUMIA_EXE_NAME = "yumia_mod_insert_into_rdb.exe"


@dataclass
class ModOption:
    """A single installable option within a mod archive."""

    name: str  # Display name (parent dir name)
    archive_internal_path: str  # Path prefix inside archive (e.g. "OptionA/package/")
    package_files: list[str] = field(default_factory=list)  # Filenames inside package/


@dataclass
class ModArchive:
    """A mod archive file containing one or more installable options."""

    filepath: Path
    name: str  # Archive filename without extension
    options: list[ModOption] = field(default_factory=list)
    manifest: ModManifest | None = None
    manifest_options: dict[str, list[str]] = field(default_factory=dict)
    # manifest_options: feature.name -> sorted list of discovered option names


@dataclass
class InstalledModRecord:
    """Persisted record of an installed mod for tracking."""

    archive_filename: str  # e.g. "cool_armor.zip"
    option_name: str  # Which option was chosen
    installed_files: list[str] = field(
        default_factory=list
    )  # Filenames in game package dir


class ModManager:
    """
    Main mod manager controller.

    Workflow:
        1. scan_archives() to discover mods in the mods directory
        2. check_installed_status() to verify which mods are actually installed
        3. install_mod() / uninstall_mod() to manage mods
    """

    def __init__(
        self,
        mods_dir: str | Path,
        game_package_dir: str | Path,
        log_callback: Optional[Callable[[str], None]] = None,
        yumia_prompt_callback: Optional[Callable[[str], bool]] = None,
    ):
        self.mods_dir = Path(mods_dir)
        self.game_package_dir = Path(game_package_dir)
        self.yumia_exe = self.game_package_dir / YUMIA_EXE_NAME
        self.installed_mods_manifest_path = self.mods_dir / ".nioh3_modmanager_manifest.json"
        self._log_cb = log_callback or print
        self._yumia_prompt_cb = yumia_prompt_callback

        # Runtime state
        self.archives: list[ModArchive] = []
        self.installed: dict[str, InstalledModRecord] = {}  # key = archive_filename

    # ── Logging ───────────────────────────────────────────────────────

    def log(self, msg: str):
        self._log_cb(msg)

    # ── Installed Mods Tracking ───────────────────────────────────────

    def _load_installed_mods_manifest(self):
        if self.installed_mods_manifest_path.exists():
            try:
                data = json.loads(self.installed_mods_manifest_path.read_text(encoding="utf-8"))
                self.installed = {}
                for key, rec in data.items():
                    self.installed[key] = InstalledModRecord(**rec)
                self.log(f"Loaded installed mods: {len(self.installed)} mod(s) recorded")
            except Exception as e:
                self.log(f"Warning: Could not load installed mods record: {e}")
                self.installed = {}
        else:
            self.installed = {}

    def _save_installed_mods_manifest(self):
        data = {key: asdict(rec) for key, rec in self.installed.items()}
        self.installed_mods_manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── Archive Content Listing ───────────────────────────────────────

    @staticmethod
    def _read_archive_member(filepath: Path, member: str) -> bytes:
        """Read a single member from an archive into bytes without extracting."""
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

    @staticmethod
    def _list_archive_names(filepath: Path) -> list[str]:
        ext = filepath.suffix.lower()
        names = []

        if ext == ".zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                names = zf.namelist()
        elif ext == ".7z":
            with py7zr.SevenZipFile(filepath, "r") as sz:
                names = sz.getnames()
        elif ext == ".rar":
            with rarfile.RarFile(filepath, "r") as rf:
                names = [info.filename for info in rf.infolist()]

        return [n.replace("\\", "/") for n in names]

    @staticmethod
    def _extract_from_archive(
        filepath: Path, members: list[str], dest: Path
    ) -> list[Path]:
        ext = filepath.suffix.lower()
        extracted = []

        if ext == ".zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                for m in members:
                    zf.extract(m, dest)
                    extracted.append(dest / m)
        elif ext == ".7z":
            with py7zr.SevenZipFile(filepath, "r") as sz:
                sz.extract(dest, targets=members)
                for m in members:
                    extracted.append(dest / m)
        elif ext == ".rar":
            with rarfile.RarFile(filepath, "r") as rf:
                for m in members:
                    rf.extract(m, dest)
                    extracted.append(dest / m)

        return extracted

    # ── Archive Scanning ──────────────────────────────────────────────

    def scan_archives(self) -> list[ModArchive]:
        self.archives = []

        if not self.mods_dir.exists():
            self.log(f"Mods directory does not exist: {self.mods_dir}")
            return self.archives

        for f in sorted(self.mods_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                archive = self._analyze_archive(f)
                if archive.options or archive.manifest:
                    self.archives.append(archive)
                    if archive.manifest:
                        feat_names = [ft.name for ft in archive.manifest.features]
                        self.log(
                            f"  {f.name}: manifest mod — "
                            f"{len(archive.manifest.features)} feature(s): {feat_names}"
                        )
                    else:
                        option_names = [o.name for o in archive.options]
                        self.log(
                            f"  {f.name}: {len(archive.options)} option(s) — {option_names}"
                        )
                else:
                    self.log(f"  {f.name}: no mod files found, skipping")
            except Exception as e:
                self.log(f"  Error scanning {f.name}: {e}")

        self.log(f"Scan complete: {len(self.archives)} valid mod archive(s)")
        return self.archives

    def _analyze_archive(self, filepath: Path) -> ModArchive:
        archive = ModArchive(filepath=filepath, name=filepath.stem)
        names = self._list_archive_names(filepath)
        if not names:
            return archive

        # ── Manifest detection ────────────────────────────────────────────
        # If the archive contains nioh3modmanifest.json, parse it and return
        # early — manifest mods don't use package/ directories.
        if MANIFEST_FILENAME in names:
            try:
                data = self._read_archive_member(filepath, MANIFEST_FILENAME)
                manifest = parse_manifest(data)
                archive.manifest = manifest
                for feature in manifest.features:
                    prefix = feature.directory + "/"
                    opts: set[str] = set()
                    for name in names:
                        if name.startswith(prefix) and not name.endswith("/"):
                            rest = name[len(prefix):]
                            parts = rest.split("/")
                            if len(parts) >= 2:  # option_name/file — not a bare file
                                opts.add(parts[0])
                    archive.manifest_options[feature.name] = sorted(opts)
                return archive
            except Exception as exc:
                self.log(
                    f"  Warning: manifest parse failed in {filepath.name}: {exc}"
                    " — falling back to package/ scan"
                )
                archive.manifest = None
                archive.manifest_options = {}
                # fall through to package/ scanning

        # Find all paths that contain a "package" directory with files inside.
        # Group by the prefix up to and including "package/".
        #
        # Examples:
        #   package/somefile.ext                -> option "(default)", prefix "package/"
        #   OptionA/package/somefile.ext        -> option "OptionA", prefix "OptionA/package/"
        #   Mod/OptionA/package/somefile.ext    -> option "OptionA", prefix "Mod/OptionA/package/"

        package_prefixes: dict[str, str] = {}  # prefix -> option display name

        for name in names:
            parts = name.split("/")
            for i, part in enumerate(parts):
                if part.lower() == "package" and i < len(parts) - 1:
                    prefix = "/".join(parts[: i + 1]) + "/"
                    if prefix not in package_prefixes:
                        if i == 0:
                            package_prefixes[prefix] = "(default)"
                        else:
                            package_prefixes[prefix] = parts[i - 1]
                    break

        for prefix, option_name in package_prefixes.items():
            pkg_files = []
            for name in names:
                if name.startswith(prefix) and not name.endswith("/"):
                    rel = name[len(prefix) :]
                    if rel:
                        pkg_files.append(rel)

            if pkg_files:
                archive.options.append(
                    ModOption(
                        name=option_name,
                        archive_internal_path=prefix,
                        package_files=pkg_files,
                    )
                )

        # Fallback: if no package/ directories found, look for yumia mod files
        # (.fdata / .yumiamod.json) anywhere in the archive and treat them as
        # belonging directly in the game's package/ directory.
        if not archive.options:
            mod_files = [
                n for n in names
                if not n.endswith("/")
                and (n.endswith(".fdata") or n.endswith(".yumiamod.json"))
            ]
            if mod_files:
                archive.options.append(
                    ModOption(
                        name="(default)",
                        archive_internal_path="",
                        package_files=mod_files,
                    )
                )

        return archive

    # ── Installation Status ───────────────────────────────────────────

    def check_installed_status(self):
        self._load_installed_mods_manifest()

        stale_keys = []
        for key, rec in self.installed.items():
            all_present = all(
                (self.game_package_dir / f).exists() for f in rec.installed_files
            )
            if not all_present:
                self.log(
                    f"  Mod '{rec.option_name}' from {rec.archive_filename}: "
                    f"files missing, marking as not installed"
                )
                stale_keys.append(key)

        for key in stale_keys:
            del self.installed[key]

        if stale_keys:
            self._save_installed_mods_manifest()

        self.log(f"Verified {len(self.installed)} mod(s) currently installed")

    def is_installed(self, archive_filename: str) -> bool:
        return archive_filename in self.installed

    def get_installed_option(self, archive_filename: str) -> Optional[str]:
        rec = self.installed.get(archive_filename)
        return rec.option_name if rec else None

    # ── Backup / Restore Core RDB Files ───────────────────────────────

    def _backups_exist(self) -> bool:
        return all(
            (self.game_package_dir / b).exists() for b in CORE_RDB_BACKUPS
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

    # ── Yumia Execution ───────────────────────────────────────────────

    def run_yumia(self, auto_yes: bool = False) -> tuple[bool, str]:
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

            if auto_yes:
                stdout, _ = proc.communicate(input="Y\n", timeout=300)
            else:
                stdout, _ = proc.communicate(timeout=300)

            success = proc.returncode == 0
            self.log(f"  yumia exited with code {proc.returncode}")
            if stdout:
                for line in stdout.strip().split("\n")[-10:]:
                    self.log(f"  [yumia] {line}")

            return success, stdout or ""

        except subprocess.TimeoutExpired:
            proc.kill()
            return False, "yumia timed out after 5 minutes"
        except Exception as e:
            return False, f"Error running yumia: {e}"

    # ── Install ───────────────────────────────────────────────────────

    def install_mod(
        self, archive: ModArchive, option: ModOption, auto_yes_yumia: bool = False
    ) -> tuple[bool, str]:
        self.log(f"Installing '{option.name}' from {archive.filepath.name}...")

        if archive.filepath.name in self.installed:
            return (
                False,
                f"A mod from {archive.filepath.name} is already installed. "
                f"Uninstall it first.",
            )

        # Conflict check: block install if any installed mod patches the same game assets
        members = [option.archive_internal_path + pf for pf in option.package_files]
        conflicts = find_conflicts(archive, members, self.installed, self.game_package_dir)
        if conflicts:
            lines = []
            for fname, overlap in conflicts:
                samples = ", ".join(sorted(f"0x{h:08x}" for h in list(overlap)[:5]))
                suffix = f" (+{len(overlap) - 5} more)" if len(overlap) > 5 else ""
                lines.append(f"  \u2022 {Path(fname).stem}  ({len(overlap)} asset(s): {samples}{suffix})")
            msg = "Cannot install: conflicts with installed mod(s):\n\n"
            msg += "\n".join(lines)
            msg += "\n\nUninstall the conflicting mod(s) before proceeding."
            return False, msg

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            members = [option.archive_internal_path + pf for pf in option.package_files]

            self.log(f"  Extracting {len(members)} file(s)...")
            try:
                self._extract_from_archive(archive.filepath, members, tmppath)
            except Exception as e:
                return False, f"Extraction failed: {e}"

            extracted_base = tmppath / option.archive_internal_path.replace("/", os.sep)
            installed_files = []

            for pf in option.package_files:
                src = extracted_base / pf.replace("/", os.sep)
                dst = self.game_package_dir / pf.replace("/", os.sep)

                if not src.exists():
                    self.log(
                        f"  WARNING: Expected file not found after extraction: {src}"
                    )
                    continue

                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                installed_files.append(pf)
                self.log(f"  Copied: {pf}")

        if not installed_files:
            return False, "No files were installed"

        self.log("  Running yumia to patch RDB files...")
        success, output = self.run_yumia(auto_yes=auto_yes_yumia)

        if not success:
            self.log("  yumia failed, rolling back...")
            for pf in installed_files:
                fp = self.game_package_dir / pf
                if fp.exists():
                    fp.unlink()
            return False, f"yumia failed: {output}"

        self.installed[archive.filepath.name] = InstalledModRecord(
            archive_filename=archive.filepath.name,
            option_name=option.name,
            installed_files=installed_files,
        )
        self._save_installed_mods_manifest()

        self.log(
            f"  Successfully installed '{option.name}' ({len(installed_files)} files)"
        )
        return True, f"Installed {len(installed_files)} file(s)"

    # ── Install (manifest) ────────────────────────────────────────────

    def install_manifest_mod(
        self,
        archive: ModArchive,
        feature_selections: dict[str, str | None],
        auto_yes_yumia: bool = False,
    ) -> tuple[bool, str]:
        """Install a manifest-based mod with per-feature option selections.

        ``feature_selections`` maps each feature name to the chosen option name,
        or ``None`` to skip an optional feature.
        """
        manifest = archive.manifest
        assert manifest is not None, "install_manifest_mod called on non-manifest archive"

        self.log(f"Installing manifest mod {archive.filepath.name}...")

        if archive.filepath.name in self.installed:
            return (
                False,
                f"A mod from {archive.filepath.name} is already installed. "
                "Uninstall it first.",
            )

        # Validate required features all have a selection
        for feature in manifest.features:
            if not feature.optional and feature_selections.get(feature.name) is None:
                return False, f"Required feature '{feature.name}' has no option selected."

        # Build dest_to_member: dest_filename -> archive_member_path
        # Common files first, then each selected feature (feature wins on collision).
        names_set = set(self._list_archive_names(archive.filepath))
        dest_to_member: dict[str, str] = {}

        if manifest.common_files_dir:
            prefix = manifest.common_files_dir + "/"
            for name in names_set:
                if name.startswith(prefix) and not name.endswith("/"):
                    dest_file = name[len(prefix):]
                    if dest_file:
                        dest_to_member[dest_file] = name

        for feature in manifest.features:
            chosen = feature_selections.get(feature.name)
            if chosen is None:
                continue
            prefix = f"{feature.directory}/{chosen}/"
            for name in names_set:
                if name.startswith(prefix) and not name.endswith("/"):
                    dest_file = name[len(prefix):]
                    if dest_file:
                        dest_to_member[dest_file] = name  # overwrites common on collision

        if not dest_to_member:
            return False, "No files found for the selected options."

        # Conflict check
        archive_members = list(dest_to_member.values())
        conflicts = find_conflicts(archive, archive_members, self.installed, self.game_package_dir)
        if conflicts:
            lines = []
            for fname, overlap in conflicts:
                samples = ", ".join(sorted(f"0x{h:08x}" for h in list(overlap)[:5]))
                suffix = f" (+{len(overlap) - 5} more)" if len(overlap) > 5 else ""
                lines.append(
                    f"  \u2022 {Path(fname).stem}  ({len(overlap)} asset(s): {samples}{suffix})"
                )
            msg = "Cannot install: conflicts with installed mod(s):\n\n"
            msg += "\n".join(lines)
            msg += "\n\nUninstall the conflicting mod(s) before proceeding."
            return False, msg

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            try:
                self._extract_from_archive(archive.filepath, archive_members, tmppath)
            except Exception as e:
                return False, f"Extraction failed: {e}"

            installed_files = []
            for dest_file, archive_member in dest_to_member.items():
                src = tmppath / archive_member.replace("/", os.sep)
                dst = self.game_package_dir / dest_file.replace("/", os.sep)

                if not src.exists():
                    self.log(f"  WARNING: Expected file not found after extraction: {src}")
                    continue

                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                installed_files.append(dest_file)
                self.log(f"  Copied: {dest_file}")

        if not installed_files:
            return False, "No files were installed."

        self.log("  Running yumia to patch RDB files...")
        success, output = self.run_yumia(auto_yes=auto_yes_yumia)

        if not success:
            self.log("  yumia failed, rolling back...")
            for pf in installed_files:
                fp = self.game_package_dir / pf
                if fp.exists():
                    fp.unlink()
            return False, f"yumia failed: {output}"

        # Build a human-readable summary of what was installed
        parts = [
            f"{ft.name}: {feature_selections[ft.name]}"
            for ft in manifest.features
            if feature_selections.get(ft.name) is not None
        ]
        option_summary = "; ".join(parts) if parts else "(common files only)"

        self.installed[archive.filepath.name] = InstalledModRecord(
            archive_filename=archive.filepath.name,
            option_name=option_summary,
            installed_files=installed_files,
        )
        self._save_installed_mods_manifest()

        self.log(f"  Successfully installed manifest mod ({len(installed_files)} files)")
        return True, f"Installed {len(installed_files)} file(s)"

    # ── Uninstall ─────────────────────────────────────────────────────

    def uninstall_mod(
        self, archive_filename: str, auto_yes_yumia: bool = False
    ) -> tuple[bool, str]:
        rec = self.installed.get(archive_filename)
        if not rec:
            return False, f"No installed mod found for {archive_filename}"

        self.log(f"Uninstalling '{rec.option_name}' from {archive_filename}...")

        # Step 1: Delete this mod's files
        removed = 0
        for pf in rec.installed_files:
            fp = self.game_package_dir / pf
            if fp.exists():
                fp.unlink()
                removed += 1
                self.log(f"  Removed: {pf}")
            else:
                self.log(f"  Already missing: {pf}")

            # Clean up empty parent dirs (not package dir itself)
            parent = fp.parent
            if (
                parent != self.game_package_dir
                and parent.exists()
                and not any(parent.iterdir())
            ):
                parent.rmdir()
                self.log(f"  Removed empty dir: {parent.name}")

        # Step 2: Restore RDB backups
        if self._backups_exist():
            self.log("  Restoring RDB backups...")
            self._restore_rdb_backups()
        else:
            self.log(
                "  WARNING: RDB backups not found! "
                "You may need to verify game file integrity via Steam."
            )

        # Step 3: Remove from manifest BEFORE re-running yumia
        del self.installed[archive_filename]
        self._save_installed_mods_manifest()

        # Step 4: If there are other mods still installed, re-run yumia
        if self.installed:
            self.log(
                f"  Re-applying {len(self.installed)} remaining mod(s) via yumia..."
            )
            success, output = self.run_yumia(auto_yes=auto_yes_yumia)
            if not success:
                self.log(f"  WARNING: yumia failed during re-application: {output}")
                return (
                    True,
                    f"Mod removed ({removed} files), but yumia re-application "
                    f"failed. Other mods may not work correctly.",
                )
        else:
            self.log("  No other mods to re-apply.")

        self.log(f"  Successfully uninstalled '{rec.option_name}'")
        return True, f"Removed {removed} file(s)"

    # ── Validation ────────────────────────────────────────────────────

    def validate_paths(self) -> list[str]:
        issues = []

        if not self.mods_dir.exists():
            issues.append(f"Mods directory does not exist: {self.mods_dir}")

        if not self.game_package_dir.exists():
            issues.append(
                f"Game package directory does not exist: {self.game_package_dir}"
            )

        if not self.yumia_exe.exists():
            issues.append(f"yumia exe not found: {self.yumia_exe}")

        for backup in CORE_RDB_BACKUPS:
            if not (self.game_package_dir / backup).exists():
                issues.append(
                    f"RDB backup not found: {backup} "
                    f"(will be created on first yumia run)"
                )

        return issues
