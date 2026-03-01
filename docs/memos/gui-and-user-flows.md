# GUI And User Flows

The GUI is now doing more than listing archives. It communicates backend readiness, steers users toward LooseFileLoader, and exposes different install flows without making the user think in implementation terms.

## Main Window Shape

The main user-facing pieces are:

- toolbar with settings, refresh, and a small corner status label
- persistent backend/setup banner
- main mod tree
- install/uninstall buttons
- fixed-height mod info panel
- log panel

The corner status label is intentionally minimal now:

- `Ready` when the app is usable and idle
- `Working...` during operations
- specific text only for true blockers like path/config issues

The big backend state communication belongs in the banner, not the corner label.

## Banner

The banner is the place for:

- backend readiness
- download guidance for DLL loader and LooseFileLoader
- migration prompts
- open-game-mods-folder action

The banner is persistent and non-popup by design.

If the backend logic changes, the banner text and button visibility usually need to change too.

## Selection Flows

There are three main install-choice dialogs:

- `OptionDialog`
  - exactly one option for classic option-based mods
- `LooseComponentDialog`
  - checkbox selection for direct loose archives with multiple top-level loose folders
- `FeatureSelectionDialog`
  - per-feature selection for manifest mods

There is also `YumiaPromptDialog` for backend guidance.

## Tree Behavior

The tree columns are:

- `Mod`
- `Status`
- `Options`
- `Files`

The `Options` column deliberately shows either the available options or the installed selection summary, depending on state.

The mod info area is kept always present and fixed-height so selecting a manifest mod does not shrink the tree and visually hide the selected row.

## Where To Start In Code

- window construction:
  - `MainWindow._build_ui`
- backend banner:
  - `MainWindow._update_backend_banner`
- tree repopulation:
  - `MainWindow._refresh`
- install flow:
  - `MainWindow._install_selected`
- uninstall flow:
  - `MainWindow._uninstall_selected`
- migration button flow:
  - `MainWindow._migrate_yumia_installs`

See also:

- `backend-and-state.md`
- `invariants-and-gotchas.md`
