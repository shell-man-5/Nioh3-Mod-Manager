"""
Release script for Nioh 3 Mod Manager.

Usage:  uv run python release.py

Builds the exe via build.py, then zips dist/Nioh3ModManager/ into
Nioh3ModManager.zip ready for upload to Nexus Mods.
"""

import os
import shutil
import subprocess
import sys


def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(project_dir, "dist", "Nioh3ModManager")
    zip_path = os.path.join(project_dir, "Nioh3ModManager")  # shutil adds .zip

    # Step 1: Build
    subprocess.run([sys.executable, "build.py"], check=True, cwd=project_dir)

    # Step 2: Zip
    if not os.path.isdir(dist_dir):
        print(f"ERROR: dist dir not found at {dist_dir}")
        sys.exit(1)

    print(f"\nZipping {dist_dir}...")
    shutil.make_archive(zip_path, "zip", root_dir=os.path.join(project_dir, "dist"), base_dir="Nioh3ModManager")

    final = zip_path + ".zip"
    size_mb = os.path.getsize(final) / (1024 * 1024)
    print(f"Done: {final} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
