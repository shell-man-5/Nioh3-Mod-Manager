# Testing And Sandbox

The repo now has two different verification modes:

- automated behavior tests
- manual GUI testing through sandbox environments

Use both when the change crosses UI and backend logic.

## Automated Tests

Main test files:

- `tests/test_mod_manager.py`
  - backend routing
  - archive scanning
  - installs
  - uninstall
  - migration
  - direct loose support
  - actionable error guidance
- `tests/test_loose_file_converter.py`
  - loose conversion logic

Common command:

```powershell
uv run pytest tests/test_mod_manager.py tests/test_loose_file_converter.py -p no:cacheprovider
```

## Sandbox Harness

`sandbox_lab.py` builds mock Nioh 3 directories under `sandbox_envs/` and launches the GUI against them without touching real user settings.

Core commands:

```powershell
uv run python sandbox_lab.py list
uv run python sandbox_lab.py build --rebuild
uv run python sandbox_lab.py run empty_no_backend
uv run python sandbox_lab.py run both_with_yumia_mods
```

Current env set includes:

- no backend
- Yumia only, clean
- Yumia only, with active mods
- both backends, with active Yumia mods
- loose only, with active loose mods
- both backends, loose-only active state similar to a post-migration real setup

## When To Use What

Use tests first when:

- changing pure backend logic
- changing archive classification
- changing migration behavior

Use sandbox next when:

- changing banner behavior
- changing install-choice dialogs
- changing layout or status wording
- checking the real flow across backend states

For migration or loose installs on a real setup, also inspect the game output:

- files under `<game>/mods/<managed folder>/`
- `package/.nioh3_modmanager_state.json`
- `LooseFileLoader.log` if relevant

See also:

- `archive-handling.md`
- `gui-and-user-flows.md`
- `invariants-and-gotchas.md`
