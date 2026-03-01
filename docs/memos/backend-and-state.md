# Backend And State

The backend model is the center of gravity now.

The app supports two install backends:

- `yumia`: copy payloads into `package/`, patch RDB files
- `loose`: write loose assets into `<game>/mods/<managed folder>/`

The code deliberately refuses to mix active backends. That rule matters because it keeps the user model and uninstall logic sane.

## Detection

The game root is derived from the configured package dir.

Detection inputs:

- Yumia exe: `<package>/yumia_mod_insert_into_rdb.exe`
- DLL loader: `<game_root>/DINPUT8.dll`
- Loose plugin DLL: `<game_root>/plugins/LooseFileLoader.dll`
- Loose plugin INI: `<game_root>/plugins/LooseFileLoader.ini`

The environment snapshot is represented by `EnvironmentStatus` in `backend_manager.py`.

## Install Routing

Routing order is intentional:

1. if tracked Yumia-managed mods are active, keep using Yumia
2. else if loose prerequisites are ready, use loose
3. else if Yumia is available, use Yumia
4. else installs are blocked

That means the presence of LooseFileLoader alone does not force a backend switch while legacy Yumia installs are still active.

## State Files

There are two manifests for compatibility reasons:

- authoritative state:
  - `<package>/.nioh3_modmanager_state.json`
- legacy compatibility state:
  - `<downloads>/.nioh3_modmanager_manifest.json`

Only Yumia installs are written to the legacy downloads-folder manifest. Loose installs live only in the package-folder state file.

If an old user upgrades, `backfill_legacy_state_if_needed` reconstructs v2 package-folder state from the legacy manifest.

## Uninstall

Uninstall behavior depends on the recorded backend.

Loose uninstall:

- remove tracked loose files
- remove the managed loose mod folder if it became empty

Yumia uninstall:

- remove tracked files from `package/`
- restore `root.rdb` / `root.rdx` from their `.original` backups
- re-run Yumia if other tracked Yumia mods remain

The backup filenames are:

- `root.rdb.original`
- `root.rdx.original`

Anything referring to `system.rdb.original` is stale or wrong for this repo.

## Migration

Migration is global. It is not a per-mod operation.

The flow is:

1. resolve all active Yumia records back to source archives and selections
2. build the loose outputs
3. stage rollback copies
4. write loose files
5. remove Yumia payloads from `package/`
6. restore vanilla `root.rdb` and `root.rdx`
7. rewrite state from `backend=yumia` to `backend=loose`
8. clean up legacy manifest entries

This is one of the riskiest codepaths in the repo. Treat it as transactional.

## Where To Start In Code

- environment snapshot:
  - `ModManager.get_environment_status`
- backend selection:
  - `ModManager.resolve_install_backend`
- state load/save:
  - `ModManager.load_install_state`
  - `ModManager.save_install_state`
  - `ModManager.backfill_legacy_state_if_needed`
- uninstall:
  - `ModManager.uninstall_mod`
- migration:
  - `ModManager.migrate_all_yumia_to_loose`

See also:

- `archive-handling.md`
- `gui-and-user-flows.md`
- `invariants-and-gotchas.md`
