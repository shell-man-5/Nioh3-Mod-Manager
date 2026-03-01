# Archive Handling

Most user-facing weirdness in this app starts with archive classification.

The app supports more than one mod packaging culture now, so the scanner has to recognize intent rather than only one canonical structure.

## Classification Order

The scan order is deliberate:

1. manifest present => manifest mod
2. `package/` layout => legacy Yumia
3. root-level `.fdata` or `.yumiamod.json` => legacy Yumia
4. hashed loose filenames like `0x12345678.g1t` => direct loose
5. otherwise unsupported

That order lives in `ModManager.scan_archives`.

## Direct Loose Rules

Only hashed filenames count as loose payloads. Readmes, screenshots, and random extras should not become install targets.

Current handling rules:

- root loose files are treated as common files
- one top-level wrapper folder is treated as a normal single install option
- multiple top-level loose folders become optional components shown as checkboxes
- selected loose files are flattened into one manager-owned first-level folder under `<game>/mods/`

That flattening matters because it keeps conflict detection and uninstall predictable.

## Manifest Handling

Manifest mods may now contain either:

- Yumia payloads
- direct loose payloads
- or both across common and feature-selected directories

That means loose installs for manifest mods cannot assume everything flows through `.fdata`.

## Loose Conversion

`loose_file_converter.py` is shared logic, not just a one-off prototype anymore.

The important functions are:

- `select_manifest_members`
- `convert_selected_members_to_loose`
- `convert_archive_to_loose`
- `sanitize_mod_dir_name`

The converter also tolerates some older/minimal `.yumiamod.json` formats because real-world archives are not fully uniform.

## Conflict Shape

Legacy Yumia conflicts are hash-based through `conflict_detection.py`.

Loose conflicts are simpler and stricter:

- collision key is the final loose filename
- compare against tracked loose installs
- compare against untracked loose files already on disk

If a direct-loose install ever reports a collision with itself, suspect selection assembly first. That happened once with root-level direct loose bundles and was fixed.

## Where To Start In Code

- archive scanning:
  - `ModManager.scan_archives`
- loose legacy installs:
  - `ModManager._install_loose_legacy`
- loose manifest installs:
  - `ModManager._install_loose_manifest`
- direct loose installs:
  - `ModManager.install_direct_loose_mod`
- conversion helpers:
  - `loose_file_converter.py`

See also:

- `backend-and-state.md`
- `testing-and-sandbox.md`
- `invariants-and-gotchas.md`
