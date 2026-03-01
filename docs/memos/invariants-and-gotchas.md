# Invariants And Gotchas

This is the memo to read before risky edits or reviews.

## Invariants Worth Preserving

- The user configures only:
  - downloads folder
  - `Nioh3/package`
- The app must not mix active backends.
- Loose installs are manager-owned subfolders under `<game>/mods/`.
- Only Yumia installs are written to the legacy downloads-folder manifest.
- Migration is global and should behave transactionally.
- Direct loose payload detection is based on hashed filenames.
- The big backend message belongs in the banner, not the tiny corner label.

## Bugs Already Rediscovered Once

### Wrong RDB backup assumptions

The repo briefly assumed `system.rdb.original`.

Correct assumptions are:

- `root.rdb.original`
- `root.rdx.original`

If migration or uninstall logic starts mentioning `system.rdb`, treat that as suspicious.

### Direct-loose self-collision

Root-level direct loose bundles once got assembled twice and then reported filename collisions against themselves.

If a direct-loose conflict looks nonsensical, inspect the member-selection path before assuming a real user conflict.

### Cryptic backend mismatch errors

A direct-loose mod blocked under Yumia used to emit a bare internal-sounding message.

The current expectation is actionable guidance:

- tell the user to install DLL loader + LooseFileLoader via the banner buttons
- or tell the user to migrate active Yumia installs first

### Layout shifts in the main window

The mod info panel once appeared only for some selections and shrank the tree, which could visually hide the selected row.

The current expectation is fixed-height, always-present mod info.

## Review Questions

When reviewing a change, ask:

- Did this accidentally allow mixed active backends?
- Did this change state in both the new and legacy manifests correctly?
- Did this alter loose filename conflict semantics?
- Did this break the actionable guidance in the UI?
- Did this create a layout shift in the main window?
- Did this change a routing rule without updating the banner behavior?

## Good Review Packet Shape

If the user wants a quick but meaningful review artifact, produce:

- change summary in a few lines
- files/functions that now own the behavior
- invariants that matter for this change
- tests run
- manual QA path, if any

See also:

- `backend-and-state.md`
- `gui-and-user-flows.md`
- `testing-and-sandbox.md`
