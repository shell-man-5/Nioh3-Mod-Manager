# Overview

This app manages Nioh 3 mods from a downloaded-archive folder through a desktop GUI. The user only sets two paths: the archive downloads folder and `Nioh3/package`. Everything else is derived from `package`.

The current product shape is not "just an installer" anymore. It is a backend-aware manager with three hard parts:

- it has to understand several packaging patterns
- it has to keep old Yumia users working
- it has to push new usage toward LooseFileLoader without trapping users halfway through migration

The repo is therefore split pretty cleanly by responsibility:

- `backend_manager.py` owns almost all behavior
- `gui.py` owns how that behavior is presented and triggered
- `loose_file_converter.py` owns Yumia-to-loose conversion and shared loose-file helpers
- `sandbox_lab.py` owns mock environments for manual GUI testing
- `tests/test_mod_manager.py` is the main behavior safety net

The easiest way to get lost in this repo is to start reading code from the top of `backend_manager.py` without a question in mind. The better approach is:

1. find the task in `index.md`
2. read the relevant memo
3. jump straight to the owning function or file

The current user-facing feature set includes:

- legacy Yumia installs
- preferred LooseFileLoader installs
- direct loose-file mods
- manifest-based mods
- direct loose mods with multiple top-level option folders via checkboxes
- global migration from Yumia-managed installs to loose installs
- backend/setup guidance in the main window
- sandbox environments for manual testing

See also:

- `backend-and-state.md`
- `archive-handling.md`
- `gui-and-user-flows.md`
