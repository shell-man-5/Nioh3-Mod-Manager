"""
Shared fixtures and helpers for the Nioh 3 Mod Manager test suite.
"""

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def zip_fixture(name: str, dest_dir: Path) -> Path:
    """Zip a fixture directory into dest_dir/<name>.zip and return the path."""
    src = FIXTURES_DIR / name
    out = dest_dir / f"{name}.zip"
    with zipfile.ZipFile(out, "w") as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())
    return out


@pytest.fixture
def dirs(tmp_path):
    """Return (mods_dir, game_package_dir) as fresh tmp_path subdirectories."""
    mods = tmp_path / "mods"
    pkg = tmp_path / "game" / "package"
    mods.mkdir()
    pkg.mkdir(parents=True)
    return mods, pkg


@pytest.fixture
def mock_yumia():
    """Patch ModManager.run_yumia to succeed without running the real exe."""
    with patch("mod_manager.ModManager.run_yumia", return_value=(True, "ok")) as m:
        yield m
