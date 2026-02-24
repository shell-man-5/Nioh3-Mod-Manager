# Nioh 3 Mod Manager

A GUI tool to manage mods for Nioh 3 (Steam version). Point it at a folder of
mod archives, click install, done.

**For users:** grab the latest release zip, extract it, run `Nioh3ModManager.exe`.
No Python needed. The rest of this README is for developers and mod authors.

## Table of Contents

- [How Nioh 3 Modding Works](#how-nioh-3-modding-works)
- [Install / Uninstall Flow](#install--uninstall-flow)
- [For Mod Authors](#for-mod-authors)
- [Development Setup](#development-setup)
- [Building the Executable](#building-the-executable)
- [License](#license)

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
2. Detects the archive's packaging format: `package/` subdirectory layout,
   loose files at the root, or a `nioh3modmanifest.json` for multi-feature mods
3. For option-based mods, lets you pick a variant; for multi-feature mods,
   lets you configure each feature independently
4. Extracts the chosen files into `Nioh3/package/`
5. Runs `yumia_mod_insert_into_rdb.exe` to patch the RDB files

### Uninstall

1. Deletes the specific files that mod added to `Nioh3/package/`
2. Restores `system.rdb.original` and `root.rdb.original` backups
3. Re-runs yumia to re-apply any remaining installed mods

### Status tracking

- An installed-mods record (`.nioh3_modmanager_manifest.json`) in your mods
  directory tracks what's installed
- On every startup/refresh, the record is verified against reality — if files
  are missing (e.g. you manually deleted them), the mod is marked as not installed

## For Mod Authors

Package your mod as a `.zip`, `.7z`, or `.rar` archive. The manager supports
three layouts — pick whichever fits your mod:

<details>
<summary><strong>Simple mod</strong> — one set of files, no choices</summary>

Place your files inside a `package/` subdirectory:

```
my_mod.zip
└── package/
    ├── 0xffaabb00.fdata
    └── 0xffaabb00.yumiamod.json
```

Files at the archive root are also accepted (no `package/` subdir needed):

```
my_mod.zip
├── 0xffaabb00.fdata
└── 0xffaabb00.yumiamod.json
```

</details>

<details>
<summary><strong>Mod with exclusive options</strong> — user picks exactly one variant</summary>

Put each variant in its own named subdirectory, each containing a `package/` folder:

```
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
```

The subdirectory name (Red, Blue, Gold) becomes the label shown in the UI.
The user installs exactly one variant.

</details>

<details>
<summary><strong>Multi-feature mod</strong> — independent per-feature selections, with optional shared files</summary>

Add a `nioh3modmanifest.json` at the archive root:

```json
{
  "mod_manager_version": "1.0",
  "mod_name": "Awesome Armor Pack",
  "author": "YourName",
  "version": "1.0",
  "url": "https://www.nexusmods.com/nioh3/mods/1",
  "common_files_dir": "common",
  "features": [
    { "name": "Armor Style", "directory": "armor_style", "optional": false },
    { "name": "Skin",        "directory": "skin",        "optional": true  }
  ]
}
```

Matching archive layout:

```
my_mod.zip
├── nioh3modmanifest.json
├── common/              ← always installed (omit if not needed)
│   └── ...
├── armor_style/         ← matches "directory" in the manifest
│   ├── Heavy/           ← becomes an option in the UI
│   │   └── ...
│   └── Light/
│       └── ...
└── skin/                ← optional: user may skip
    ├── Normal/
    │   └── ...
    └── Wet/
        └── ...
```

- `mod_name` is optional — if set, shown in the mod list (with `version` appended if present).
- `author`, `version`, `url` are optional — shown in a detail panel when the mod is selected. `url` is a clickable link.
- `name` is the human-readable label shown in the UI.
- Each feature's subdirectory names become the available options.
- Common files are installed first; selected feature files are layered on top
  (feature wins on filename collision).
- Features marked `"optional": true` can be skipped by the user.

</details>

## Development Setup

```bash
# Clone and install deps (requires uv: https://docs.astral.sh/uv/)
git clone <repo-url>
cd Nioh3_mod_manager
uv sync

# Run from source
uv run python main.py

# Run tests
uv run pytest tests/
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
