# Nioh 3 Mod Manager

A GUI tool to manage mods for Nioh 3 (Steam version). Point it at a folder of
mod archives, click install, done.

**For users:** grab the latest release zip, extract it, run `Nioh3ModManager.exe`.
No Python needed. The rest of this README is for developers.

## How Nioh 3 Modding Works

Nioh 3 (and other Koei Tecmo games) store assets in `.rdb` database files.
Mods ship as archives containing two types of files:

- **`.fdata`** — the actual mod data (textures, models, etc.)
- **`.yumiamod.json`** — metadata telling the patcher what to replace

These files go into the game's `package/` directory. Then a patching tool
rewrites the game's `system.rdb` and `root.rdb` to reference the new files.

This manager automates that entire workflow: extracting, copying, patching,
tracking, and cleanly uninstalling.

### The yumia patcher

This tool depends on
[`yumia_mod_insert_into_rdb.exe`](https://github.com/eArmada8/yumia_fdata_tools/releases/download/v1.1.3/yumia_mod_insert_into_rdb.exe)
from the [yumia_fdata_tools](https://github.com/eArmada8/yumia_fdata_tools)
project by **eArmada8**. Place it inside your game's `package/` directory
(e.g. `C:\Program Files (x86)\Steam\steamapps\common\Nioh3\package\`).

We've tested with **v1.1.3**. Newer versions will likely work but haven't been
verified — if something breaks, try v1.1.3 first.

**Huge thanks to eArmada8** for building the yumia toolset. None of this would
be possible without their work reverse-engineering the fdata/rdb format.

## Install / Uninstall Flow

### Install

1. Scans your mods directory for `.zip` / `.7z` / `.rar` archives
2. Detects installable options by finding `package/` directories inside each
   archive (or `.fdata` / `.yumiamod.json` files at the root as a fallback)
3. If multiple options exist (e.g. different colors/variants), lets you pick one
4. Extracts the chosen files into `Nioh3/package/`
5. Runs `yumia_mod_insert_into_rdb.exe` to patch the RDB files

### Uninstall

1. Deletes the specific files that mod added to `Nioh3/package/`
2. Restores `system.rdb.original` and `root.rdb.original` backups
3. Re-runs yumia to re-apply any remaining installed mods

### Status tracking

- A manifest (`.nioh3_modmanager_manifest.json`) in your mods directory tracks
  what's installed
- On every startup/refresh, the manifest is verified against reality — if files
  are missing (e.g. you manually deleted them), the mod is marked as not installed

## Mod Archive Structure

The manager looks for `package/` directories inside archives:

```
# Single-option mod (package/ at root):
my_cool_mod.zip
└── package/
    ├── 0xffaabb00.fdata
    └── 0xffaabb00.yumiamod.json

# Multi-option mod (variants in subdirectories):
armor_colors.zip
├── Red/
│   └── package/
│       └── 0xffaabb00.fdata
├── Blue/
│   └── package/
│       └── 0xffaabb00.fdata
└── Gold/
    └── package/
        └── 0xffaabb00.fdata

# Loose mod (no package/ dir — files at archive root):
simple_mod.zip
├── 0xffffcccc.fdata
└── 0xffffcccc.yumiamod.json
```

For multi-option mods, the parent directory name (Red, Blue, Gold) becomes the
option label shown in the UI. Loose mods (no `package/` dir) are detected by
looking for `.fdata` / `.yumiamod.json` files and treated as a single default
option.

## Development Setup

```bash
# Clone and install deps (requires uv: https://docs.astral.sh/uv/)
git clone <repo-url>
cd Nioh3_mod_manager
uv sync

# Run from source
uv run python main.py

# Run tests
uv run python test_scanning.py
```

On first launch, click **Settings** to configure:

- **Mods Directory**: where your downloaded mod archives live
- **Game Package Directory**: your `Nioh3/package` path

## Building the Executable

```bash
uv run python build.py
```

This runs PyInstaller and creates a desktop shortcut. Output lands in
`dist/Nioh3ModManager/` (~120 MB). Zip that folder up for distribution.

## License

MIT — see [LICENSE](LICENSE).
