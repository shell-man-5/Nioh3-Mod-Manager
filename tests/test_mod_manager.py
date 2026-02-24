"""
Tests for ModManager covering all supported mod archive structures.

Legacy structures:
  - mod_with_loose_files      fdata/yumiamod.json at archive root (fallback path)
  - mod_with_package_subdir   single package/ directory at root
  - mod_with_option_subdirs   OptionA/package/ + OptionB/package/

Manifest structures:
  - manifest_mod_common_only          common files, no features
  - manifest_mod_features_only        required feature, no common
  - manifest_mod_features_and_common  required feature + common files
  - manifest_mod_optional_only        one optional feature (all-skip corner case)
"""

from mod_manager import ModManager
from tests.conftest import zip_fixture


# ── helpers ──────────────────────────────────────────────────────────────────

def make_manager(mods_dir, game_pkg_dir):
    return ModManager(mods_dir, game_pkg_dir, log_callback=lambda _: None)


# ── scanning: legacy ─────────────────────────────────────────────────────────

def test_scan_loose_files(dirs):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_loose_files", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    archive = archives[0]
    assert len(archive.options) == 1
    opt = archive.options[0]
    assert opt.name == "(default)"
    assert opt.archive_internal_path == ""
    assert "loose.fdata" in opt.package_files
    assert "loose.yumiamod.json" in opt.package_files


def test_scan_package_subdir(dirs):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_package_subdir", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    opt = archives[0].options[0]
    assert opt.name == "(default)"
    assert opt.archive_internal_path == "package/"
    assert "pkg.fdata" in opt.package_files
    assert "pkg.yumiamod.json" in opt.package_files


def test_scan_option_subdirs(dirs):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_option_subdirs", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    options = archives[0].options
    assert len(options) == 2
    names = {o.name for o in options}
    assert names == {"OptionA", "OptionB"}


# ── scanning: manifest ────────────────────────────────────────────────────────

def test_scan_manifest_common_only(dirs):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_common_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    archive = archives[0]
    assert archive.manifest is not None
    assert len(archive.manifest.features) == 0
    assert archive.manifest.common_files_dir == "common"
    assert archive.options == []


def test_scan_manifest_features_only(dirs):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    archive = archives[0]
    assert archive.manifest is not None
    assert len(archive.manifest.features) == 1
    assert archive.manifest.features[0].name == "Armor Style"
    assert archive.manifest.features[0].optional is False
    assert archive.manifest_options["Armor Style"] == ["Heavy", "Light"]
    assert archive.options == []


def test_scan_manifest_features_and_common(dirs):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_and_common", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    archive = archives[0]
    assert archive.manifest is not None
    assert archive.manifest.common_files_dir == "common"
    assert len(archive.manifest.features) == 1
    assert archive.manifest_options["Armor Style"] == ["Heavy", "Light"]


def test_scan_manifest_optional_only(dirs):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_optional_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    archive = archives[0]
    assert archive.manifest is not None
    feature = archive.manifest.features[0]
    assert feature.name == "Hair Style"
    assert feature.optional is True
    assert archive.manifest_options["Hair Style"] == ["Long", "Short"]


# ── install: legacy ───────────────────────────────────────────────────────────

def test_install_loose_files(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_loose_files", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]
    option = archive.options[0]

    ok, msg = manager.install_mod(archive, option)

    assert ok, msg
    assert (game_pkg_dir / "loose.fdata").exists()
    assert (game_pkg_dir / "loose.yumiamod.json").exists()
    assert archive.filepath.name in manager.installed
    rec = manager.installed[archive.filepath.name]
    assert "loose.fdata" in rec.installed_files
    assert "loose.yumiamod.json" in rec.installed_files


def test_install_package_subdir(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_package_subdir", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_mod(archive, archive.options[0])

    assert ok, msg
    assert (game_pkg_dir / "pkg.fdata").exists()
    assert (game_pkg_dir / "pkg.yumiamod.json").exists()


def test_install_option_a(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_option_subdirs", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]
    option_a = next(o for o in archive.options if o.name == "OptionA")

    ok, msg = manager.install_mod(archive, option_a)

    assert ok, msg
    assert (game_pkg_dir / "optA.fdata").exists()
    assert (game_pkg_dir / "optA.yumiamod.json").exists()
    assert not (game_pkg_dir / "optB.fdata").exists()


def test_install_option_b(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_option_subdirs", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]
    option_b = next(o for o in archive.options if o.name == "OptionB")

    ok, msg = manager.install_mod(archive, option_b)

    assert ok, msg
    assert (game_pkg_dir / "optB.fdata").exists()
    assert not (game_pkg_dir / "optA.fdata").exists()


# ── install: manifest ─────────────────────────────────────────────────────────

def test_install_manifest_common_only(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_common_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {})

    assert ok, msg
    assert (game_pkg_dir / "common.fdata").exists()
    assert (game_pkg_dir / "common.yumiamod.json").exists()


def test_install_manifest_feature_heavy(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Armor Style": "Heavy"})

    assert ok, msg
    assert (game_pkg_dir / "heavy.fdata").exists()
    assert (game_pkg_dir / "heavy.yumiamod.json").exists()
    assert not (game_pkg_dir / "light.fdata").exists()


def test_install_manifest_feature_light(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Armor Style": "Light"})

    assert ok, msg
    assert (game_pkg_dir / "light.fdata").exists()
    assert not (game_pkg_dir / "heavy.fdata").exists()


def test_install_manifest_features_and_common(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_and_common", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Armor Style": "Heavy"})

    assert ok, msg
    assert (game_pkg_dir / "common.fdata").exists()
    assert (game_pkg_dir / "heavy.fdata").exists()
    assert not (game_pkg_dir / "light.fdata").exists()


def test_install_manifest_optional_skipped(dirs, mock_yumia):
    """Skipping the only optional feature (no common) should be an error."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_optional_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Hair Style": None})

    assert not ok
    assert not list(game_pkg_dir.iterdir())  # nothing was copied


def test_install_manifest_optional_selected(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_optional_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Hair Style": "Short"})

    assert ok, msg
    assert (game_pkg_dir / "short.fdata").exists()
    assert not (game_pkg_dir / "long.fdata").exists()


# ── uninstall ─────────────────────────────────────────────────────────────────

def test_uninstall_legacy(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_loose_files", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, _ = manager.install_mod(archive, archive.options[0])
    assert ok
    assert (game_pkg_dir / "loose.fdata").exists()

    ok, msg = manager.uninstall_mod(archive.filepath.name)

    assert ok, msg
    assert not (game_pkg_dir / "loose.fdata").exists()
    assert not (game_pkg_dir / "loose.yumiamod.json").exists()
    assert archive.filepath.name not in manager.installed


def test_uninstall_manifest(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_and_common", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, _ = manager.install_manifest_mod(archive, {"Armor Style": "Heavy"})
    assert ok

    ok, msg = manager.uninstall_mod(archive.filepath.name)

    assert ok, msg
    assert not (game_pkg_dir / "common.fdata").exists()
    assert not (game_pkg_dir / "heavy.fdata").exists()
    assert archive.filepath.name not in manager.installed


# ── already-installed guard ───────────────────────────────────────────────────

def test_already_installed_blocked(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_loose_files", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]
    option = archive.options[0]

    ok, _ = manager.install_mod(archive, option)
    assert ok

    ok, msg = manager.install_mod(archive, option)
    assert not ok
    assert "already installed" in msg.lower()


# ── mod_name override ─────────────────────────────────────────────────────────

def test_mod_name_overrides_filename(dirs):
    """manifest mod_name is shown instead of the archive filename."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_with_name", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    assert archives[0].name == "My Cool Mod"


# ── manifest metadata fields ──────────────────────────────────────────────────

def test_manifest_full_metadata(dirs):
    """All four metadata fields parse correctly."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_full_metadata", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    m = archives[0].manifest
    assert m is not None
    assert m.mod_name == "Full Metadata Mod"
    assert m.author == "SomeAuthor"
    assert m.version == "2.3.1"
    assert m.url == "https://www.nexusmods.com/nioh3/mods/1"


def test_manifest_partial_metadata(dirs):
    """Only author and url present; mod_name and version are None."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_partial_metadata", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    m = archives[0].manifest
    assert m is not None
    assert m.mod_name is None
    assert m.author == "PartialAuthor"
    assert m.version is None
    assert m.url == "https://www.nexusmods.com/nioh3/mods/2"


def test_manifest_no_metadata(dirs):
    """Manifest with no metadata fields — all four are None."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_common_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    assert len(archives) == 1
    m = archives[0].manifest
    assert m is not None
    assert m.mod_name is None
    assert m.author is None
    assert m.version is None
    assert m.url is None


# ── conflict detection ────────────────────────────────────────────────────────

def test_conflict_blocked(dirs, mock_yumia):
    """Two mods sharing the same name_hash cannot both be installed."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_loose_files", mods_dir)
    zip_fixture("conflicting_with_loose", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    first = next(a for a in archives if "mod_with_loose_files" in a.name)
    second = next(a for a in archives if "conflicting_with_loose" in a.name)

    ok, _ = manager.install_mod(first, first.options[0])
    assert ok

    ok, msg = manager.install_mod(second, second.options[0])
    assert not ok
    assert "conflict" in msg.lower()


def test_no_conflict_different_hashes(dirs, mock_yumia):
    """Two mods with non-overlapping name_hashes can both be installed."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_loose_files", mods_dir)       # hash 1001
    zip_fixture("mod_with_package_subdir", mods_dir)    # hash 2001
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    loose = next(a for a in archives if "loose" in a.name)
    pkg = next(a for a in archives if "package_subdir" in a.name)

    ok1, msg1 = manager.install_mod(loose, loose.options[0])
    assert ok1, msg1

    ok2, msg2 = manager.install_mod(pkg, pkg.options[0])
    assert ok2, msg2


def test_conflict_legacy_then_manifest(dirs, mock_yumia):
    """Legacy mod installed first; manifest mod with same hash must be blocked."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("mod_with_loose_files", mods_dir)
    zip_fixture("conflicting_manifest", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    legacy = next(a for a in archives if "loose_files" in a.name)
    manifest = next(a for a in archives if "conflicting_manifest" in a.name)

    ok, _ = manager.install_mod(legacy, legacy.options[0])
    assert ok

    ok, msg = manager.install_manifest_mod(manifest, {})
    assert not ok
    assert "conflict" in msg.lower()


def test_conflict_manifest_then_legacy(dirs, mock_yumia):
    """Manifest mod installed first; legacy mod with same hash must be blocked."""
    mods_dir, game_pkg_dir = dirs
    zip_fixture("conflicting_manifest", mods_dir)
    zip_fixture("conflicting_with_loose", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()

    manifest = next(a for a in archives if "conflicting_manifest" in a.name)
    legacy = next(a for a in archives if "conflicting_with_loose" in a.name)

    ok, _ = manager.install_manifest_mod(manifest, {})
    assert ok

    ok, msg = manager.install_mod(legacy, legacy.options[0])
    assert not ok
    assert "conflict" in msg.lower()
