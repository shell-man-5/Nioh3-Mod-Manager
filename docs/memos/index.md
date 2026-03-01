# Memo Index

This is the entry point for continuity in this repo.

Read this first. Then open only the topic memos that match the task. Follow cross-links if the summary here is not enough.

## Current Shape Of The App

This is a Nioh 3 mod manager with two install backends:

- Yumia for legacy package patching
- LooseFileLoader for the preferred loose-file workflow

The main complexity lives in backend routing, archive classification, migration, and keeping the GUI aligned with those rules.

## Topic Memos

### `overview.md`

What the app currently does, what users configure, the high-level file map, and where the main moving parts live.

Use when:
- starting a session cold
- explaining the product shape
- figuring out which file probably owns a behavior

See also:
- `backend-and-state.md`
- `archive-handling.md`

### `backend-and-state.md`

Backend detection, install routing, state files, uninstall behavior, and migration rules.

Use when:
- touching install backend selection
- changing Yumia vs loose behavior
- debugging migration
- changing state manifests or uninstall logic

See also:
- `invariants-and-gotchas.md`
- `gui-and-user-flows.md`

### `archive-handling.md`

Archive classification, direct-loose rules, manifest handling, and loose conversion rules.

Use when:
- touching archive scanning
- changing loose-file detection
- adding new packaging support
- debugging why a mod installs the wrong files

See also:
- `backend-and-state.md`
- `testing-and-sandbox.md`

### `gui-and-user-flows.md`

Persistent setup banner, option dialogs, mod list behavior, status label behavior, and the main user flows.

Use when:
- changing wording or buttons
- changing install/uninstall dialogs
- touching the tree layout
- debugging a UX regression

See also:
- `backend-and-state.md`
- `invariants-and-gotchas.md`

### `testing-and-sandbox.md`

Test layout, sandbox environments, launch commands, and when to use which verification path.

Use when:
- adding behavior tests
- doing manual GUI QA
- changing sandbox env generation
- validating migration or backend-routing changes

See also:
- `archive-handling.md`
- `invariants-and-gotchas.md`

### `invariants-and-gotchas.md`

The rules that are easy to break and the bugs already rediscovered once.

Use when:
- reviewing a risky change
- touching migration, direct-loose support, or UI/backend coupling
- creating a review packet

See also:
- `backend-and-state.md`
- `gui-and-user-flows.md`

## Task Lookup

- Add a new install backend rule:
  - read `backend-and-state.md`
  - then `gui-and-user-flows.md`
- Add a new archive format or option pattern:
  - read `archive-handling.md`
  - then `testing-and-sandbox.md`
- Touch migration:
  - read `backend-and-state.md`
  - then `invariants-and-gotchas.md`
- Change banner text or status behavior:
  - read `gui-and-user-flows.md`
- Manual GUI test pass:
  - read `testing-and-sandbox.md`
- Do a quick review after a big change:
  - read `overview.md`
  - plus the one or two topic memos closest to the change
