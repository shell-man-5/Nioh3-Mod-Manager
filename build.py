"""
Build script for Nioh 3 Mod Manager.

Usage:  uv run python build.py

Creates:
  - dist/Nioh3ModManager/  (the standalone app folder)
  - Desktop shortcut        (Nioh 3 Mod Manager.lnk)

Both steps are idempotent — safe to re-run for upgrades.
"""

import os
import shutil
import subprocess
import sys
import time


def build_exe():
    """Run PyInstaller with the spec file."""
    project_dir = os.path.dirname(os.path.abspath(__file__))

    # Pre-clean dist/ ourselves with retries (Windows sometimes holds .pyd files
    # briefly for Defender scanning or indexing)
    dist_dir = os.path.join(project_dir, "dist", "Nioh3ModManager")
    if os.path.isdir(dist_dir):
        for attempt in range(3):
            try:
                shutil.rmtree(dist_dir)
                break
            except PermissionError:
                print(f"dist/ locked, retrying in 2s... ({attempt + 1}/3)")
                time.sleep(2)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "Nioh3ModManager.spec",
        "--clean",
        "--noconfirm",
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print("\nBuild complete! Output is in dist/Nioh3ModManager/")


def create_desktop_shortcut():
    """Create (or overwrite) a desktop shortcut pointing to the built exe."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    exe_path = os.path.join(project_dir, "dist", "Nioh3ModManager", "Nioh3ModManager.exe")
    working_dir = os.path.join(project_dir, "dist", "Nioh3ModManager")

    if not os.path.exists(exe_path):
        print(f"ERROR: exe not found at {exe_path}")
        return

    # Use PowerShell to get the real Desktop path (handles OneDrive redirection)
    # then create the shortcut via WScript.Shell COM
    ps_script = (
        f'$desktop = [Environment]::GetFolderPath("Desktop"); '
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("$desktop\\Nioh 3 Mod Manager.lnk"); '
        f'$s.TargetPath = "{exe_path}"; '
        f'$s.WorkingDirectory = "{working_dir}"; '
        f'$s.Save(); '
        f'Write-Host "Shortcut created at: $desktop\\Nioh 3 Mod Manager.lnk"'
    )
    subprocess.run(["powershell", "-Command", ps_script], check=True)
    print("Desktop shortcut created.")


if __name__ == "__main__":
    build_exe()
    create_desktop_shortcut()
