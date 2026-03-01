"""
Tests for ModManager covering both Yumia and LooseFileLoader workflows.
"""

import json
import struct
import zipfile

from mod_manager import (
    LEGACY_MANIFEST_FILENAME,
    STATE_MANIFEST_FILENAME,
    ModManager,
)
from loose_file_converter import sanitize_mod_dir_name
from tests.conftest import zip_fixture


# ── helpers ──────────────────────────────────────────────────────────────────

def make_manager(mods_dir, game_pkg_dir):
    return ModManager(mods_dir, game_pkg_dir, log_callback=lambda _: None)


def enable_yumia_backend(game_pkg_dir):
    (game_pkg_dir / "yumia_mod_insert_into_rdb.exe").write_text("stub", encoding="utf-8")


def enable_loose_backend(game_pkg_dir):
    game_root = game_pkg_dir.parent
    (game_root / "DINPUT8.dll").write_bytes(b"dll")
    plugins_dir = game_root / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / "LooseFileLoader.dll").write_bytes(b"plugin")
    (plugins_dir / "LooseFileLoader.ini").write_text("[LooseFileLoader]\n", encoding="utf-8")


def enable_rdb_backups(game_pkg_dir):
    for name in ("root.rdb", "root.rdx", "root.rdb.original", "root.rdx.original"):
        (game_pkg_dir / name).write_bytes(name.encode("ascii"))


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for member, data in members.items():
            zf.writestr(member, data)
    return path


def make_fdata_entry(
    payload: bytes,
    *,
    name_hash: int,
    tkid_hash: int,
    entry_type: int = 8,
    extradata: bytes = b"",
) -> bytes:
    entry_size = 0x30 + len(extradata) + len(payload)
    entry = bytearray()
    entry.extend(b"IDRK0000")
    entry.extend(struct.pack("<3Q", entry_size, len(payload), len(payload)))
    entry.extend(struct.pack("<4I", entry_type, name_hash, tkid_hash, 0))
    entry.extend(extradata)
    entry.extend(payload)
    if len(entry) % 0x10:
        entry.extend(b"\x00" * (0x10 - (len(entry) % 0x10)))
    return bytes(entry)


def make_fdata(entries):
    body = b"".join(entries)
    return b"PDRK0000" + struct.pack("<2I", 0x10, len(body) + 0x10) + body


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

    ok, msg = manager.install_mod(archive, option, backend="yumia")

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

    ok, msg = manager.install_mod(archive, archive.options[0], backend="yumia")

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

    ok, msg = manager.install_mod(archive, option_a, backend="yumia")

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

    ok, msg = manager.install_mod(archive, option_b, backend="yumia")

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

    ok, msg = manager.install_manifest_mod(archive, {}, backend="yumia")

    assert ok, msg
    assert (game_pkg_dir / "common.fdata").exists()
    assert (game_pkg_dir / "common.yumiamod.json").exists()


def test_install_manifest_feature_heavy(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Armor Style": "Heavy"}, backend="yumia")

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

    ok, msg = manager.install_manifest_mod(archive, {"Armor Style": "Light"}, backend="yumia")

    assert ok, msg
    assert (game_pkg_dir / "light.fdata").exists()
    assert not (game_pkg_dir / "heavy.fdata").exists()


def test_install_manifest_features_and_common(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_features_and_common", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Armor Style": "Heavy"}, backend="yumia")

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

    ok, msg = manager.install_manifest_mod(archive, {"Hair Style": None}, backend="yumia")

    assert not ok
    assert not list(game_pkg_dir.iterdir())  # nothing was copied


def test_install_manifest_optional_selected(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    zip_fixture("manifest_mod_optional_only", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    archive = archives[0]

    ok, msg = manager.install_manifest_mod(archive, {"Hair Style": "Short"}, backend="yumia")

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

    ok, _ = manager.install_mod(archive, archive.options[0], backend="yumia")
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

    ok, _ = manager.install_manifest_mod(archive, {"Armor Style": "Heavy"}, backend="yumia")
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

    ok, _ = manager.install_mod(archive, option, backend="yumia")
    assert ok

    ok, msg = manager.install_mod(archive, option, backend="yumia")
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

    ok, _ = manager.install_mod(first, first.options[0], backend="yumia")
    assert ok

    ok, msg = manager.install_mod(second, second.options[0], backend="yumia")
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

    ok1, msg1 = manager.install_mod(loose, loose.options[0], backend="yumia")
    assert ok1, msg1

    ok2, msg2 = manager.install_mod(pkg, pkg.options[0], backend="yumia")
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

    ok, _ = manager.install_mod(legacy, legacy.options[0], backend="yumia")
    assert ok

    ok, msg = manager.install_manifest_mod(manifest, {}, backend="yumia")
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

    ok, _ = manager.install_manifest_mod(manifest, {}, backend="yumia")
    assert ok

    ok, msg = manager.install_mod(legacy, legacy.options[0], backend="yumia")
    assert not ok
    assert "conflict" in msg.lower()


def test_environment_status_prefers_loose_when_ready(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    manager = make_manager(mods_dir, game_pkg_dir)

    status = manager.get_environment_status()

    assert status.loose_ready
    assert not status.yumia_available
    assert manager.resolve_install_backend() == "loose"


def test_mock_yumia_creates_required_backups(monkeypatch, dirs):
    mods_dir, game_pkg_dir = dirs
    (game_pkg_dir / "root.rdb").write_bytes(b"root rdb")
    (game_pkg_dir / "root.rdx").write_bytes(b"root rdx")
    manager = make_manager(mods_dir, game_pkg_dir)

    monkeypatch.setenv("NIOH3MM_MOCK_YUMIA", "1")
    ok, msg = manager.run_yumia()

    assert ok, msg
    assert (game_pkg_dir / "root.rdb.original").exists()
    assert (game_pkg_dir / "root.rdx.original").exists()


def test_environment_status_blocks_new_installs_when_yumia_is_active_but_missing(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    enable_rdb_backups(game_pkg_dir)
    zip_fixture("mod_with_loose_files", mods_dir)
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    ok, msg = manager.install_mod(archive, archive.options[0], backend="yumia")
    assert ok, msg

    status = manager.get_environment_status()

    assert status.has_active_yumia_mods
    assert not status.yumia_available
    assert status.can_migrate
    assert not status.can_install
    assert manager.resolve_install_backend() is None


def test_direct_loose_archive_with_top_level_folders_uses_multi_select_options(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    archive_path = make_zip(
        mods_dir / "direct_loose_bundle.zip",
        {
            "PackA/0x11111111.g1t": b"texture-a",
            "PackB/0x22222222.g1m": b"model-b",
            "README.txt": b"ignore me",
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)

    archives = manager.scan_archives()
    assert len(archives) == 1
    archive = archives[0]
    assert archive.archive_kind == "direct_loose"
    assert archive.direct_loose_multi_select is True
    assert archive.direct_loose_common_files == []
    assert [option.name for option in archive.options] == ["PackA", "PackB"]

    ok, msg = manager.install_direct_loose_mod(archive, archive.options)
    assert ok, msg

    target_dir = game_pkg_dir.parent / "mods" / sanitize_mod_dir_name(archive.name)
    assert (target_dir / "0x11111111.g1t").read_bytes() == b"texture-a"
    assert (target_dir / "0x22222222.g1m").read_bytes() == b"model-b"
    assert not (target_dir / "README.txt").exists()

    rec = manager.installed[archive_path.name]
    assert rec.backend == "loose"
    assert rec.loose_mod_dir == f"mods/{sanitize_mod_dir_name(archive.name)}"
    assert not (mods_dir / LEGACY_MANIFEST_FILENAME).exists()
    assert (game_pkg_dir / STATE_MANIFEST_FILENAME).exists()


def test_direct_loose_archive_with_common_root_files_and_optional_folder_selection(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    make_zip(
        mods_dir / "direct_loose_common_plus_options.zip",
        {
            "0xAAAA0001.g1t": b"common",
            "Blue/0xBBBB0002.g1m": b"blue",
            "Red/0xCCCC0003.g1m": b"red",
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    assert archive.direct_loose_multi_select is True
    assert archive.direct_loose_common_files == ["0xAAAA0001.g1t"]
    assert [option.name for option in archive.options] == ["Blue", "Red"]

    selected = [option for option in archive.options if option.name == "Red"]
    ok, msg = manager.install_direct_loose_mod(archive, selected, backend="loose")
    assert ok, msg

    target_dir = game_pkg_dir.parent / "mods" / sanitize_mod_dir_name(archive.name)
    assert (target_dir / "0xAAAA0001.g1t").read_bytes() == b"common"
    assert (target_dir / "0xCCCC0003.g1m").read_bytes() == b"red"
    assert not (target_dir / "0xBBBB0002.g1m").exists()


def test_direct_loose_root_files_install_without_self_collision(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    make_zip(
        mods_dir / "direct_loose_root_files.zip",
        {
            "0x14000001.g1t": b"texture",
            "0x14000002.g1m": b"model",
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    assert archive.direct_loose_multi_select is False
    assert archive.direct_loose_common_files == []
    assert archive.options[0].package_files == ["0x14000001.g1t", "0x14000002.g1m"]

    ok, msg = manager.install_mod(archive, archive.options[0], backend="loose")

    assert ok, msg
    target_dir = game_pkg_dir.parent / "mods" / sanitize_mod_dir_name(archive.name)
    assert (target_dir / "0x14000001.g1t").read_bytes() == b"texture"
    assert (target_dir / "0x14000002.g1m").read_bytes() == b"model"


def test_direct_loose_single_wrapper_folder_still_installs_as_default_option(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    make_zip(
        mods_dir / "direct_loose_wrapper.zip",
        {
            "Wrapper/0x11111111.g1t": b"texture-a",
            "Wrapper/0x22222222.g1m": b"model-b",
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    assert archive.direct_loose_multi_select is False
    assert archive.options[0].name == "(default)"
    assert archive.options[0].package_files == [
        "Wrapper/0x11111111.g1t",
        "Wrapper/0x22222222.g1m",
    ]

    ok, msg = manager.install_mod(archive, archive.options[0], backend="loose")
    assert ok, msg


def test_direct_loose_duplicate_filename_blocked(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    make_zip(
        mods_dir / "direct_loose_duplicate.zip",
        {
            "A/0x11111111.g1t": b"one",
            "B/0x11111111.g1t": b"two",
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    ok, msg = manager.install_direct_loose_mod(archive, archive.options, backend="loose")

    assert not ok
    assert "collision" in msg.lower()


def test_loose_install_conflicts_with_untracked_disk_file(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    make_zip(
        mods_dir / "direct_loose_conflict.zip",
        {
            "0x11111111.g1t": b"modded",
        },
    )
    existing = game_pkg_dir.parent / "mods" / "OtherMod"
    existing.mkdir(parents=True)
    (existing / "0x11111111.g1t").write_bytes(b"existing")

    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    ok, msg = manager.install_mod(archive, archive.options[0], backend="loose")

    assert not ok
    assert "conflict" in msg.lower()


def test_manifest_loose_assets_install_via_loose_backend(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    manifest = {
        "mod_manager_version": "1.0",
        "mod_name": "Loose Manifest Mod",
        "common_files_dir": "common",
        "features": [
            {
                "name": "Variant",
                "directory": "variant",
                "optional": False,
            }
        ],
    }
    make_zip(
        mods_dir / "manifest_loose_assets.zip",
        {
            "nioh3modmanifest.json": json.dumps(manifest),
            "common/0x11111111.g1t": b"common",
            "variant/Blue/0x22222222.g1m": b"blue",
            "variant/Red/0x33333333.g1m": b"red",
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    ok, msg = manager.install_manifest_mod(archive, {"Variant": "Blue"})

    assert ok, msg
    target_dir = game_pkg_dir.parent / "mods" / "Loose Manifest Mod"
    assert (target_dir / "0x11111111.g1t").read_bytes() == b"common"
    assert (target_dir / "0x22222222.g1m").read_bytes() == b"blue"
    assert not (target_dir / "0x33333333.g1m").exists()


def test_direct_loose_mod_yumia_error_includes_actionable_guidance(dirs):
    mods_dir, game_pkg_dir = dirs
    enable_yumia_backend(game_pkg_dir)
    make_zip(
        mods_dir / "direct_loose_only.zip",
        {
            "0x11111111.g1t": b"texture",
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    ok, msg = manager.install_mod(archive, archive.options[0], backend="yumia")

    assert not ok
    assert "LooseFileLoader" in msg
    assert "Setup / Backend Status" in msg
    assert "DLL loader" in msg


def test_manifest_loose_assets_yumia_error_includes_migration_guidance(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    enable_yumia_backend(game_pkg_dir)
    enable_loose_backend(game_pkg_dir)
    enable_rdb_backups(game_pkg_dir)

    legacy_members = {
        "0x20000001.fdata": make_fdata(
            [
                make_fdata_entry(
                    b"legacy",
                    name_hash=0x20100001,
                    tkid_hash=0xAFBEC60C,
                )
            ]
        ),
        "0x20000001.yumiamod.json": json.dumps(
            {
                "files": [
                    {
                        "filename": "0x20100001.g1t",
                        "name_hash": 0x20100001,
                        "tkid_hash": 0xAFBEC60C,
                        "entry_type": 8,
                        "f_extradata": "",
                        "r_extradata": "",
                    }
                ]
            }
        ),
    }
    make_zip(
        mods_dir / "legacy_yumia_installed.zip",
        legacy_members,
    )

    manifest = {
        "mod_manager_version": "1.0",
        "mod_name": "Loose Selection Manifest",
        "features": [
            {
                "name": "Variant",
                "directory": "variant",
                "optional": False,
            }
        ],
    }
    make_zip(
        mods_dir / "manifest_loose_selection.zip",
        {
            "nioh3modmanifest.json": json.dumps(manifest),
            "variant/Blue/0x33333333.g1m": b"blue",
        },
    )

    manager = make_manager(mods_dir, game_pkg_dir)
    archives = manager.scan_archives()
    legacy_archive = next(a for a in archives if a.filepath.name == "legacy_yumia_installed.zip")
    loose_manifest = next(a for a in archives if a.filepath.name == "manifest_loose_selection.zip")

    ok, msg = manager.install_mod(legacy_archive, legacy_archive.options[0], backend="yumia")
    assert ok, msg

    ok, msg = manager.install_manifest_mod(
        loose_manifest,
        {"Variant": "Blue"},
        backend="yumia",
    )

    assert not ok
    assert "Migrate Yumia Installs" in msg
    assert "Setup / Backend Status" in msg


def test_backfill_legacy_manifest_creates_v2_state_and_recovers_manifest_selection(dirs):
    mods_dir, game_pkg_dir = dirs
    archive_path = zip_fixture("manifest_mod_features_and_common", mods_dir)
    installed_files = [
        "common.fdata",
        "common.yumiamod.json",
        "heavy.fdata",
        "heavy.yumiamod.json",
    ]
    for filename in installed_files:
        (game_pkg_dir / filename).write_bytes(b"x")

    legacy_state = {
        archive_path.name: {
            "archive_filename": archive_path.name,
            "option_name": "Armor Style: Heavy",
            "installed_files": installed_files,
        }
    }
    (mods_dir / LEGACY_MANIFEST_FILENAME).write_text(
        json.dumps(legacy_state),
        encoding="utf-8",
    )

    manager = make_manager(mods_dir, game_pkg_dir)
    manager.check_installed_status()

    rec = manager.installed[archive_path.name]
    assert rec.backend == "yumia"
    assert rec.install_kind == "manifest"
    assert rec.feature_selections == {"Armor Style": "Heavy"}

    state_data = json.loads((game_pkg_dir / STATE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert state_data["version"] == 2
    assert archive_path.name in state_data["records"]


def test_migrate_all_yumia_to_loose_preserves_selection_and_cleans_legacy_state(dirs, mock_yumia):
    mods_dir, game_pkg_dir = dirs
    enable_loose_backend(game_pkg_dir)
    enable_rdb_backups(game_pkg_dir)
    common_payload = b"common-texture"
    heavy_payload = b"heavy-model"
    common_hash = 0x11111111
    heavy_hash = 0x22222222
    common_tkid = 0xAFBEC60C
    heavy_tkid = 0x563BDEF1
    archive_path = mods_dir / "migration_manifest.zip"
    manifest = {
        "mod_manager_version": "1.0",
        "mod_name": "Migration Manifest",
        "common_files_dir": "common",
        "features": [
            {
                "name": "Armor Style",
                "directory": "armor_style",
                "optional": False,
            }
        ],
    }
    common_json = {
        "files": [
            {
                "filename": f"0x{common_hash:08X}.g1t",
                "name_hash": common_hash,
                "tkid_hash": common_tkid,
                "entry_type": 8,
                "f_extradata": "",
                "r_extradata": "",
            }
        ]
    }
    heavy_json = {
        "files": [
            {
                "filename": f"0x{heavy_hash:08X}.g1m",
                "name_hash": heavy_hash,
                "tkid_hash": heavy_tkid,
                "entry_type": 8,
                "f_extradata": "",
                "r_extradata": "",
            }
        ]
    }
    make_zip(
        archive_path,
        {
            "nioh3modmanifest.json": json.dumps(manifest),
            "common/0xAAAABBBB.yumiamod.json": json.dumps(common_json),
            "common/0xAAAABBBB.fdata": make_fdata(
                [make_fdata_entry(common_payload, name_hash=common_hash, tkid_hash=common_tkid)]
            ),
            "armor_style/Heavy/0xCCCCDDDD.yumiamod.json": json.dumps(heavy_json),
            "armor_style/Heavy/0xCCCCDDDD.fdata": make_fdata(
                [make_fdata_entry(heavy_payload, name_hash=heavy_hash, tkid_hash=heavy_tkid)]
            ),
        },
    )
    manager = make_manager(mods_dir, game_pkg_dir)
    archive = manager.scan_archives()[0]

    ok, msg = manager.install_manifest_mod(
        archive,
        {"Armor Style": "Heavy"},
        backend="yumia",
    )
    assert ok, msg
    assert (mods_dir / LEGACY_MANIFEST_FILENAME).exists()
    assert (game_pkg_dir / "0xAAAABBBB.fdata").exists()
    assert (game_pkg_dir / "0xCCCCDDDD.fdata").exists()

    ok, msg = manager.migrate_all_yumia_to_loose()
    assert ok, msg

    rec = manager.installed[archive.filepath.name]
    assert rec.backend == "loose"
    assert rec.install_kind == "manifest"
    assert rec.feature_selections == {"Armor Style": "Heavy"}
    assert rec.installed_paths
    for relpath in rec.installed_paths:
        assert (game_pkg_dir.parent / relpath).exists()

    target_dir = game_pkg_dir.parent / "mods" / "Migration Manifest"
    assert (target_dir / f"0x{common_hash:08X}.g1t").read_bytes() == common_payload
    assert (target_dir / f"0x{heavy_hash:08X}.g1m").read_bytes() == heavy_payload

    assert not (game_pkg_dir / "0xAAAABBBB.fdata").exists()
    assert not (game_pkg_dir / "0xCCCCDDDD.fdata").exists()
    assert not (mods_dir / LEGACY_MANIFEST_FILENAME).exists()

    state_data = json.loads((game_pkg_dir / STATE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert state_data["records"][archive.filepath.name]["backend"] == "loose"
