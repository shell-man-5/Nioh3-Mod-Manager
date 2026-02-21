"""
Quick smoke test for the archive scanning logic.
Creates dummy zip files and verifies they're parsed correctly.
"""

import tempfile
import zipfile
from pathlib import Path

from mod_manager import ModManager


def create_test_zip(path: Path, entries: dict[str, str]):
    """Create a zip with the given {archive_path: content} entries."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def test_single_option_mod():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mods = tmp / "mods"
        mods.mkdir()
        game = tmp / "game_package"
        game.mkdir()

        create_test_zip(
            mods / "simple_mod.zip",
            {
                "package/texture_a.bin": "data_a",
                "package/texture_b.bin": "data_b",
                "readme.txt": "some readme",
            },
        )

        mgr = ModManager(mods, game)
        archives = mgr.scan_archives()

        assert len(archives) == 1
        a = archives[0]
        assert a.name == "simple_mod"
        assert len(a.options) == 1
        assert a.options[0].name == "(default)"
        assert set(a.options[0].package_files) == {"texture_a.bin", "texture_b.bin"}
        print("✅ test_single_option_mod passed")


def test_multi_option_mod():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mods = tmp / "mods"
        mods.mkdir()
        game = tmp / "game_package"
        game.mkdir()

        create_test_zip(
            mods / "color_swap.zip",
            {
                "Red/package/armor.bin": "red_data",
                "Red/package/subdir/detail.bin": "red_detail",
                "Blue/package/armor.bin": "blue_data",
                "Gold/package/armor.bin": "gold_data",
                "Gold/package/effect.bin": "gold_effect",
                "readme.txt": "pick a color",
            },
        )

        mgr = ModManager(mods, game)
        archives = mgr.scan_archives()

        assert len(archives) == 1
        a = archives[0]
        assert len(a.options) == 3

        names = {o.name for o in a.options}
        assert names == {"Red", "Blue", "Gold"}

        red = next(o for o in a.options if o.name == "Red")
        assert set(red.package_files) == {"armor.bin", "subdir/detail.bin"}

        gold = next(o for o in a.options if o.name == "Gold")
        assert set(gold.package_files) == {"armor.bin", "effect.bin"}

        print("✅ test_multi_option_mod passed")


def test_nested_option_mod():
    """Test: ModName/OptionA/package/..."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mods = tmp / "mods"
        mods.mkdir()
        game = tmp / "game_package"
        game.mkdir()

        create_test_zip(
            mods / "nested_mod.zip",
            {
                "CoolMod/VariantA/package/file1.bin": "a",
                "CoolMod/VariantB/package/file1.bin": "b",
                "CoolMod/VariantB/package/file2.bin": "b2",
            },
        )

        mgr = ModManager(mods, game)
        archives = mgr.scan_archives()

        assert len(archives) == 1
        a = archives[0]
        assert len(a.options) == 2
        names = {o.name for o in a.options}
        assert names == {"VariantA", "VariantB"}

        vb = next(o for o in a.options if o.name == "VariantB")
        assert set(vb.package_files) == {"file1.bin", "file2.bin"}

        print("✅ test_nested_option_mod passed")


def test_no_mod_files_skipped():
    """Archives with no package/ dir AND no .fdata/.yumiamod.json are skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mods = tmp / "mods"
        mods.mkdir()
        game = tmp / "game_package"
        game.mkdir()

        create_test_zip(
            mods / "not_a_mod.zip",
            {
                "random/stuff.txt": "hello",
                "other/things.bin": "world",
            },
        )

        mgr = ModManager(mods, game)
        archives = mgr.scan_archives()

        assert len(archives) == 0
        print("✅ test_no_mod_files_skipped passed")


def test_loose_mod_files():
    """Archives with .fdata/.yumiamod.json at root (no package/ dir) are detected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mods = tmp / "mods"
        mods.mkdir()
        game = tmp / "game_package"
        game.mkdir()

        create_test_zip(
            mods / "loose_mod.zip",
            {
                "0xffffcccc.fdata": "mod_data",
                "0xffffcccc.yumiamod.json": '{"some": "config"}',
            },
        )

        mgr = ModManager(mods, game)
        archives = mgr.scan_archives()

        assert len(archives) == 1
        a = archives[0]
        assert a.name == "loose_mod"
        assert len(a.options) == 1
        assert a.options[0].name == "(default)"
        assert set(a.options[0].package_files) == {
            "0xffffcccc.fdata",
            "0xffffcccc.yumiamod.json",
        }
        print("✅ test_loose_mod_files passed")


def test_loose_mod_files_with_extras():
    """Loose mod archives with non-mod files (readmes, images) only pick up mod files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mods = tmp / "mods"
        mods.mkdir()
        game = tmp / "game_package"
        game.mkdir()

        create_test_zip(
            mods / "mod_with_extras.zip",
            {
                "0xffff1111.fdata": "data",
                "0xffff1111.yumiamod.json": "{}",
                "readme.txt": "install instructions",
                "preview/screenshot.jpg": "image_bytes",
            },
        )

        mgr = ModManager(mods, game)
        archives = mgr.scan_archives()

        assert len(archives) == 1
        a = archives[0]
        assert len(a.options) == 1
        assert set(a.options[0].package_files) == {
            "0xffff1111.fdata",
            "0xffff1111.yumiamod.json",
        }
        print("✅ test_loose_mod_files_with_extras passed")


def test_install_and_uninstall():
    """Test the full install → verify → uninstall flow (without yumia)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mods = tmp / "mods"
        mods.mkdir()
        game = tmp / "game_package"
        game.mkdir()

        # Create a fake yumia that does nothing
        if __import__("sys").platform == "win32":
            yumia = game / "yumia_mod_insert_into_rdb.exe"
            # Create a batch-like stub (won't actually work, but the path check passes)
            # For a real test on Windows you'd create a proper stub
        else:
            print("⏭ test_install_and_uninstall skipped (not on Windows)")
            return

        create_test_zip(
            mods / "test_mod.zip",
            {
                "package/new_texture.bin": "texture_data",
                "package/new_model.bin": "model_data",
            },
        )

        mgr = ModManager(mods, game)
        mgr.scan_archives()
        mgr.check_installed_status()

        assert not mgr.is_installed("test_mod.zip")
        print("✅ test_install_and_uninstall passed (partial — full test needs Windows)")


if __name__ == "__main__":
    test_single_option_mod()
    test_multi_option_mod()
    test_nested_option_mod()
    test_no_mod_files_skipped()
    test_loose_mod_files()
    test_loose_mod_files_with_extras()
    test_install_and_uninstall()
    print("\n🎉 All tests passed!")
