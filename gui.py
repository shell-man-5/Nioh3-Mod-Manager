"""
Nioh 3 Mod Manager - GUI (PySide6)
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mod_manager import ModManager, ModArchive, ModOption

# ── Default Paths ─────────────────────────────────────────────────────

DEFAULT_GAME_PACKAGE = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Nioh3\package"
)
DEFAULT_MODS_DIR = ""  # User must set this
DLL_LOADER_URL = "https://www.nexusmods.com/nioh3/mods/49"
LOOSE_FILE_LOADER_URL = "https://www.nexusmods.com/nioh3/mods/90"


# ── Worker Thread ─────────────────────────────────────────────────────

class WorkerThread(QThread):
    """Run a blocking operation off the main thread."""

    log_signal = Signal(str)
    finished_signal = Signal(bool, str)  # success, message
    yumia_prompt_signal = Signal(str)  # prompt text

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            if isinstance(result, tuple) and len(result) == 2:
                self.finished_signal.emit(result[0], result[1])
            else:
                self.finished_signal.emit(True, "Done")
        except Exception as e:
            self.finished_signal.emit(False, str(e))


# ── Option Selection Dialog ───────────────────────────────────────────

class OptionDialog(QDialog):
    """Dialog for choosing which mod option to install."""

    def __init__(self, archive_name: str, options: list[ModOption], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Select Option — {archive_name}")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        label = QLabel(f"<b>{archive_name}</b> has {len(options)} option(s).\nWhich do you want to install?")
        label.setWordWrap(True)
        layout.addWidget(label)

        self.combo = QComboBox()
        for opt in options:
            file_count = len(opt.package_files)
            self.combo.addItem(f"{opt.name}  ({file_count} file{'s' if file_count != 1 else ''})", userData=opt)
        layout.addWidget(self.combo)

        # Show file list for selected option
        self.file_list = QPlainTextEdit()
        self.file_list.setReadOnly(True)
        self.file_list.setMaximumHeight(150)
        self.file_list.setFont(QFont("Consolas", 9))
        layout.addWidget(self.file_list)

        self.combo.currentIndexChanged.connect(self._update_file_list)
        self._update_file_list()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_file_list(self):
        opt: ModOption = self.combo.currentData()
        if opt:
            self.file_list.setPlainText("\n".join(opt.package_files))

    def selected_option(self) -> Optional[ModOption]:
        return self.combo.currentData()


class LooseComponentDialog(QDialog):
    """Dialog for choosing optional loose-file subfolder components."""

    def __init__(self, archive: ModArchive, parent=None):
        super().__init__(parent)
        self._archive = archive
        self.setWindowTitle(f"Select Loose Options — {archive.name}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        label_text = (
            f"<b>{archive.name}</b> contains optional loose-file folders.\n"
            "Tick the folders you want to install."
        )
        if archive.direct_loose_common_files:
            label_text += (
                f"\n\n{len(archive.direct_loose_common_files)} root file(s) will always be included."
            )
        label = QLabel(label_text)
        label.setWordWrap(True)
        layout.addWidget(label)

        self._checkboxes: list[tuple[ModOption, QCheckBox]] = []
        for option in archive.options:
            checkbox = QCheckBox(
                f"{option.name}  ({len(option.package_files)} file{'s' if len(option.package_files) != 1 else ''})"
            )
            checkbox.stateChanged.connect(self._update_preview)
            layout.addWidget(checkbox)
            self._checkboxes.append((option, checkbox))

        self.file_list = QPlainTextEdit()
        self.file_list.setReadOnly(True)
        self.file_list.setMaximumHeight(180)
        self.file_list.setFont(QFont("Consolas", 9))
        layout.addWidget(self.file_list)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Install")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_preview()

    def _update_preview(self):
        selected = self.selected_options()
        lines: list[str] = []
        if self._archive.direct_loose_common_files:
            lines.extend(
                f"[common] {member}" for member in self._archive.direct_loose_common_files
            )
        for option in selected:
            lines.extend(option.package_files)
        self.file_list.setPlainText("\n".join(lines) if lines else "(nothing selected)")
        self.ok_button.setEnabled(bool(selected) or bool(self._archive.direct_loose_common_files))

    def selected_options(self) -> list[ModOption]:
        return [option for option, checkbox in self._checkboxes if checkbox.isChecked()]


# ── Manifest Selection Dialog ─────────────────────────────────────────

SKIP_LABEL = "— skip —"


class FeatureSelectionDialog(QDialog):
    """Dialog for choosing per-feature options when installing a manifest mod."""

    def __init__(self, archive: ModArchive, parent=None):
        super().__init__(parent)
        manifest = archive.manifest
        assert manifest is not None

        self.setWindowTitle(f"Configure — {archive.name}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{archive.name}</b>"))

        self._combos: dict[str, QComboBox] = {}  # feature.name -> combo

        for feature in manifest.features:
            options = archive.manifest_options.get(feature.name, [])
            required_text = "" if feature.optional else "  <small><i>(required)</i></small>"
            optional_text = "  <small><i>(optional)</i></small>" if feature.optional else ""
            header = QLabel(f"<b>{feature.name}</b>{required_text}{optional_text}")
            header.setTextFormat(Qt.RichText)
            layout.addWidget(header)

            combo = QComboBox()
            if feature.optional:
                combo.addItem(SKIP_LABEL, userData=None)
            for opt_name in options:
                file_count = len(options)
                combo.addItem(opt_name, userData=opt_name)
            self._combos[feature.name] = combo
            layout.addWidget(combo)

        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Install")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_selections(self) -> dict[str, str | None]:
        """Return {feature_name: chosen_option_name_or_None}."""
        return {name: combo.currentData() for name, combo in self._combos.items()}


# ──Yumia Prompt Dialog ──────────────────────────────────────────────

class YumiaPromptDialog(QDialog):
    """Dialog shown when yumia detects changed RDB files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Game Files Changed")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        label = QLabel(
            "The yumia tool detected that the core game files (root.rdb / root.rdx) "
            "have changed since the last mod was installed.\n\n"
            "This usually means the game received an update via Steam.\n\n"
            "Allow yumia to proceed with the updated files?"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ── Settings Dialog ───────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, mods_dir: str, game_package_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        # Mods directory
        group1 = QGroupBox("Mods Directory (where your mod archives live)")
        g1_layout = QHBoxLayout(group1)
        self.mods_dir_label = QLabel(mods_dir or "(not set)")
        self.mods_dir_label.setWordWrap(True)
        g1_layout.addWidget(self.mods_dir_label, 1)
        btn1 = QPushButton("Browse...")
        btn1.clicked.connect(self._browse_mods)
        g1_layout.addWidget(btn1)
        layout.addWidget(group1)

        # Game package directory
        group2 = QGroupBox("Game Package Directory (Nioh3/package)")
        g2_layout = QHBoxLayout(group2)
        self.game_dir_label = QLabel(game_package_dir or "(not set)")
        self.game_dir_label.setWordWrap(True)
        g2_layout.addWidget(self.game_dir_label, 1)
        btn2 = QPushButton("Browse...")
        btn2.clicked.connect(self._browse_game)
        g2_layout.addWidget(btn2)
        layout.addWidget(group2)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._mods_dir = mods_dir
        self._game_dir = game_package_dir

    def _browse_mods(self):
        d = QFileDialog.getExistingDirectory(self, "Select Mods Directory")
        if d:
            self._mods_dir = d
            self.mods_dir_label.setText(d)

    def _browse_game(self):
        d = QFileDialog.getExistingDirectory(self, "Select Nioh3/package Directory")
        if d:
            self._game_dir = d
            self.game_dir_label.setText(d)

    def get_values(self) -> tuple[str, str]:
        return self._mods_dir, self._game_dir


# ── Main Window ───────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    # Signal used to safely append log messages from background threads.
    # Qt automatically queues cross-thread signal emissions to the main thread.
    _log_message = Signal(str)

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        mods_dir_override: str | None = None,
        game_package_dir_override: str | None = None,
        settings_org: str = "Nioh3ModManager",
        settings_app: str = "Nioh3ModManager",
        persist_settings: bool = True,
        window_title_suffix: str | None = None,
    ):
        super().__init__()
        self._logger = logger or logging.getLogger("nioh3modmanager")
        title = "Nioh 3 Mod Manager"
        if window_title_suffix:
            title += f" {window_title_suffix}"
        self.setWindowTitle(title)
        self.setMinimumSize(900, 600)

        # Settings persistence
        self._persist_settings = persist_settings
        self.settings = QSettings(settings_org, settings_app)
        stored_mods_dir = self.settings.value("mods_dir", DEFAULT_MODS_DIR, type=str)
        stored_game_package_dir = self.settings.value(
            "game_package_dir", DEFAULT_GAME_PACKAGE, type=str
        )
        self.mods_dir = mods_dir_override if mods_dir_override is not None else stored_mods_dir
        self.game_package_dir = (
            game_package_dir_override
            if game_package_dir_override is not None
            else stored_game_package_dir
        )
        self.manager: Optional[ModManager] = None
        self.worker: Optional[WorkerThread] = None

        self._build_ui()
        self._log_message.connect(self.log_text.appendPlainText)
        self._try_init_manager()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # ── Toolbar row ───────────────────────────────────────────────
        toolbar = QHBoxLayout()

        self.settings_btn = QPushButton("⚙ Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        toolbar.addWidget(self.settings_btn)

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(self.refresh_btn)

        toolbar.addStretch()

        self.status_label = QLabel()
        toolbar.addWidget(self.status_label)

        main_layout.addLayout(toolbar)

        self.backend_banner = QGroupBox("Setup / Backend Status")
        self.backend_banner.setVisible(False)
        banner_layout = QVBoxLayout(self.backend_banner)
        banner_layout.setContentsMargins(12, 24, 12, 10)
        banner_layout.setSpacing(8)

        self.backend_banner_label = QLabel()
        self.backend_banner_label.setWordWrap(True)
        self.backend_banner_label.setTextFormat(Qt.PlainText)
        banner_layout.addWidget(self.backend_banner_label)

        banner_actions = QHBoxLayout()

        self.download_dll_btn = QPushButton("Download DLL Loader")
        self.download_dll_btn.clicked.connect(lambda: self._open_url(DLL_LOADER_URL))
        banner_actions.addWidget(self.download_dll_btn)

        self.download_loose_btn = QPushButton("Download LooseFileLoader")
        self.download_loose_btn.clicked.connect(
            lambda: self._open_url(LOOSE_FILE_LOADER_URL)
        )
        banner_actions.addWidget(self.download_loose_btn)

        self.migrate_btn = QPushButton("Migrate Yumia Installs")
        self.migrate_btn.clicked.connect(self._migrate_yumia_installs)
        banner_actions.addWidget(self.migrate_btn)

        self.open_game_mods_btn = QPushButton("Open Game Mods Folder")
        self.open_game_mods_btn.clicked.connect(self._open_game_mods_folder)
        banner_actions.addWidget(self.open_game_mods_btn)

        banner_actions.addStretch()
        banner_layout.addLayout(banner_actions)
        main_layout.addWidget(self.backend_banner)

        # ── Splitter: mod list | log ──────────────────────────────────
        splitter = QSplitter(Qt.Vertical)

        # Top: Mod tree
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        top_label = QLabel("<b>Mod Archives</b>")
        top_layout.addWidget(top_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Mod", "Status", "Options", "Files"])
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 420)
        self.tree.setColumnWidth(3, 60)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        top_layout.addWidget(self.tree, 1)

        # Action buttons
        action_row = QHBoxLayout()
        self.install_btn = QPushButton("📦 Install Selected")
        self.install_btn.clicked.connect(self._install_selected)
        action_row.addWidget(self.install_btn)

        self.uninstall_btn = QPushButton("🗑 Uninstall Selected")
        self.uninstall_btn.clicked.connect(self._uninstall_selected)
        action_row.addWidget(self.uninstall_btn)

        action_row.addStretch()

        self.open_downloads_btn = QPushButton("📂 Open Downloads Folder")
        self.open_downloads_btn.clicked.connect(self._open_mods_folder)
        action_row.addWidget(self.open_downloads_btn)

        top_layout.addLayout(action_row)

        # Mod Info panel — hidden until a manifest mod with metadata is selected
        self.info_box = QGroupBox("Mod Info")
        self._info_form = QFormLayout(self.info_box)
        self._info_form.setContentsMargins(6, 4, 6, 4)
        self._info_form.setSpacing(3)
        self.info_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.info_box.setMinimumHeight(120)
        self.info_box.setMaximumHeight(120)
        info_form = self._info_form

        self._info_empty_row = QLabel("Select a manifest mod with metadata to view details.")
        self._info_empty_row.setWordWrap(True)
        info_form.addRow(self._info_empty_row)

        self._info_archive_row = QLabel()
        self._info_author_row  = QLabel()
        self._info_version_row = QLabel()
        self._info_url_row     = QLabel()
        self._info_url_row.setOpenExternalLinks(True)

        info_form.addRow("Archive:",  self._info_archive_row)
        info_form.addRow("Author:",   self._info_author_row)
        info_form.addRow("Version:",  self._info_version_row)
        info_form.addRow("Mod page:", self._info_url_row)

        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        splitter.addWidget(top_widget)

        # Bottom: Log
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        bottom_layout.addWidget(self.info_box)

        bottom_label = QLabel("<b>Log</b>")
        bottom_layout.addWidget(bottom_label)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setMaximumBlockCount(5000)
        bottom_layout.addWidget(self.log_text, 1)

        splitter.addWidget(bottom_widget)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([520, 140])

        main_layout.addWidget(splitter)

        # ── Progress bar ──────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)  # indeterminate
        main_layout.addWidget(self.progress)

    # ── Manager Init ──────────────────────────────────────────────────

    def _try_init_manager(self):
        self.manager = None
        self.tree.clear()

        if not self.mods_dir:
            self._append_log("Mods directory not configured. Open Settings to set it.")
            self.status_label.setText("Not configured")
            self._update_backend_banner()
            return

        if not self.game_package_dir:
            self._append_log("Game package directory not configured. Open Settings to set it.")
            self.status_label.setText("Not configured")
            self._update_backend_banner()
            return

        self.manager = ModManager(
            mods_dir=self.mods_dir,
            game_package_dir=self.game_package_dir,
            log_callback=self._append_log,
        )
        if Path(self.game_package_dir).exists():
            self.manager.load_install_state()

        issues = self.manager.validate_paths()
        for issue in issues:
            self._append_log(f"Warning: {issue}")

        self._update_backend_banner()
        if any("does not exist" in i for i in issues if "backup" not in i.lower()):
            self.status_label.setText("Path issues")
            return

        self.status_label.setText("Ready")
        self._refresh()

    # ── Logging ───────────────────────────────────────────────────────

    def _append_log(self, msg: str):
        self._logger.info(msg)
        self._log_message.emit(msg)  # thread-safe: Qt queues this to the main thread

    def _set_banner_message(self, message: str, severity: str = "info"):
        colors = {
            "info": ("#4f8cff", "#1c2739", "#dce8ff"),
            "warning": ("#d39b2a", "#2f2618", "#f3dfb3"),
            "error": ("#d16060", "#3b1e1e", "#ffe2e2"),
            "success": ("#48a868", "#193124", "#daf4e2"),
        }
        border, background, text = colors.get(severity, colors["info"])
        self.backend_banner.setStyleSheet(
            "QGroupBox {"
            f"border: 1px solid {border};"
            "border-radius: 6px;"
            "margin-top: 10px;"
            "padding-top: 8px;"
            f"background-color: {background};"
            "}"
            "QGroupBox::title {"
            "subcontrol-origin: margin;"
            "left: 10px;"
            "padding: 0 6px 0 6px;"
            f"color: {text};"
            f"background-color: {background};"
            "}"
        )
        self.backend_banner_label.setStyleSheet(
            f"color: {text}; background: transparent;"
        )
        self.backend_banner_label.setText(message)
        self.backend_banner.setVisible(True)

    def _missing_rdb_backups(self) -> list[str]:
        if not self.manager:
            return []
        required = ["root.rdb.original", "root.rdx.original"]
        return [
            name
            for name in required
            if not (self.manager.game_package_dir / name).exists()
        ]

    def _update_backend_buttons(
        self,
        *,
        show_downloads: bool,
        show_migrate: bool,
        show_open_game_mods: bool,
        migrate_enabled: bool = True,
        migrate_tooltip: str = "",
    ):
        has_dll_url = bool(DLL_LOADER_URL)
        has_loose_url = bool(LOOSE_FILE_LOADER_URL)
        self.download_dll_btn.setVisible(show_downloads and has_dll_url)
        self.download_loose_btn.setVisible(show_downloads and has_loose_url)
        self.migrate_btn.setVisible(show_migrate)
        self.open_game_mods_btn.setVisible(show_open_game_mods)

        self.download_dll_btn.setEnabled(has_dll_url)
        self.download_loose_btn.setEnabled(has_loose_url)
        self.migrate_btn.setEnabled(show_migrate and migrate_enabled and not self.progress.isVisible())
        self.migrate_btn.setToolTip(migrate_tooltip)
        self.open_game_mods_btn.setEnabled(show_open_game_mods and not self.progress.isVisible())

    def _update_backend_banner(self):
        if not self.mods_dir:
            self._set_banner_message(
                "Set your Mods Directory first. This stays as the folder for downloaded mod archives.",
                "warning",
            )
            self._update_backend_buttons(
                show_downloads=False,
                show_migrate=False,
                show_open_game_mods=False,
            )
            return

        if not self.game_package_dir:
            self._set_banner_message(
                "Set your Game Package Directory to the Nioh3/package folder to enable installs.",
                "warning",
            )
            self._update_backend_buttons(
                show_downloads=False,
                show_migrate=False,
                show_open_game_mods=False,
            )
            return

        if not self.manager:
            self._set_banner_message(
                "The manager is not initialized yet. Fix the configured paths and refresh.",
                "warning",
            )
            self._update_backend_buttons(
                show_downloads=False,
                show_migrate=False,
                show_open_game_mods=False,
            )
            return

        status = self.manager.get_environment_status()
        missing_backups = self._missing_rdb_backups()
        missing_backup_text = ", ".join(missing_backups)
        self.install_btn.setEnabled(status.can_install and not self.progress.isVisible())

        if not status.package_dir_exists:
            self._set_banner_message(
                f"Game package directory not found: {self.game_package_dir}",
                "error",
            )
            self._update_backend_buttons(
                show_downloads=False,
                show_migrate=False,
                show_open_game_mods=False,
            )
            return

        if not status.mods_dir_exists:
            self._set_banner_message(
                f"Mods download directory not found: {self.mods_dir}",
                "error",
            )
            self._update_backend_buttons(
                show_downloads=False,
                show_migrate=False,
                show_open_game_mods=False,
            )
            return

        if status.has_active_yumia_mods and not status.yumia_available:
            if status.loose_ready:
                if status.can_migrate:
                    message = (
                        "Legacy Yumia-managed mods are still installed, but yumia is missing. "
                        "Use Migrate Yumia Installs to move them to LooseFileLoader."
                    )
                else:
                    message = (
                        "Legacy Yumia-managed mods are still installed and LooseFileLoader is ready, "
                        f"but migration is blocked because these backup files are missing: {missing_backup_text}."
                    )
            else:
                message = (
                    "Legacy Yumia-managed mods are still installed, but yumia is missing and "
                    "LooseFileLoader is not ready. Restore yumia or install the DLL loader and LooseFileLoader."
                )
            self._set_banner_message(message, "warning")
            self._update_backend_buttons(
                show_downloads=not status.loose_ready,
                show_migrate=status.loose_ready,
                show_open_game_mods=status.game_root_exists,
                migrate_enabled=status.can_migrate,
                migrate_tooltip=(
                    "" if status.can_migrate else f"Missing backup files: {missing_backup_text}"
                ),
            )
            self.status_label.setText("Ready")
            return

        if not status.loose_ready and not status.yumia_available:
            self._set_banner_message(
                "No install backend is available. Install the DLL loader and LooseFileLoader to enable the preferred workflow.",
                "error",
            )
            self._update_backend_buttons(
                show_downloads=True,
                show_migrate=False,
                show_open_game_mods=status.game_root_exists,
            )
            self.status_label.setText("No backend available")
            return

        if status.yumia_available and not status.loose_ready:
            self._set_banner_message(
                "Yumia installs still work, but LooseFileLoader is the preferred workflow. Install the DLL loader and LooseFileLoader, then come back and migrate.",
                "warning",
            )
            self._update_backend_buttons(
                show_downloads=True,
                show_migrate=False,
                show_open_game_mods=status.game_root_exists,
            )
            self.status_label.setText("Ready")
            return

        if status.loose_ready and not status.yumia_available:
            self._set_banner_message(
                "LooseFileLoader is ready. New installs will use the loose-file workflow.",
                "success",
            )
            self._update_backend_buttons(
                show_downloads=False,
                show_migrate=False,
                show_open_game_mods=True,
            )
            self.status_label.setText("Ready")
            return

        if status.has_active_yumia_mods:
            if status.can_migrate:
                message = (
                    "Yumia-managed mods are still active. New installs will keep using Yumia until you migrate them to LooseFileLoader."
                )
            else:
                message = (
                    "Yumia-managed mods are still active. LooseFileLoader is ready, but migration is blocked "
                    f"because these backup files are missing: {missing_backup_text}. New installs will keep using Yumia."
                )
            self._set_banner_message(message, "warning")
            self._update_backend_buttons(
                show_downloads=not status.loose_ready,
                show_migrate=status.loose_ready,
                show_open_game_mods=True,
                migrate_enabled=status.can_migrate,
                migrate_tooltip=(
                    "" if status.can_migrate else f"Missing backup files: {missing_backup_text}"
                ),
            )
            self.status_label.setText("Ready")
            return

        self._set_banner_message(
            "LooseFileLoader is ready and will be used for new installs.",
            "success",
        )
        self._update_backend_buttons(
            show_downloads=False,
            show_migrate=False,
            show_open_game_mods=True,
        )
        self.status_label.setText("Ready")

    def _open_url(self, url: str):
        if not url:
            QMessageBox.information(
                self,
                "Link Not Set",
                "This download link has not been configured yet.",
            )
            return
        QDesktopServices.openUrl(QUrl(url))

    def _available_option_summary(self, archive: ModArchive) -> str:
        if archive.manifest is not None:
            chunks = []
            for feature in archive.manifest.features:
                options = archive.manifest_options.get(feature.name, [])
                if options:
                    chunks.append(f"{feature.name}: {', '.join(options)}")
            return "; ".join(chunks) if chunks else "(common files only)"

        if not archive.options:
            return ""

        return ", ".join(option.name for option in archive.options)

    # ── Refresh ───────────────────────────────────────────────────────

    def _refresh(self):
        if not self.manager:
            self._update_backend_banner()
            return

        self._append_log("─── Scanning archives... ───")
        self.manager.scan_archives()

        self._append_log("─── Checking installed status... ───")
        self.manager.check_installed_status()

        self._populate_tree()
        self._update_backend_banner()

    def _populate_tree(self):
        self.tree.clear()
        if not self.manager:
            return

        for archive in self.manager.archives:
            item = QTreeWidgetItem()
            m = archive.manifest
            if m and m.mod_name and m.version:
                label = f"{m.mod_name} \u2014 {m.version}"
            elif m and m.mod_name:
                label = m.mod_name
            else:
                label = archive.name
            item.setText(0, label)
            item.setData(0, Qt.UserRole, archive)

            is_inst = self.manager.is_installed(archive.filepath.name)
            opt_name = self.manager.get_installed_option(archive.filepath.name)
            backend = self.manager.get_installed_backend(archive.filepath.name)

            if is_inst:
                backend_label = "Loose" if backend == "loose" else "Yumia"
                item.setText(1, f"Installed ({backend_label})")
                item.setText(2, opt_name or "")
                rec = self.manager.installed.get(archive.filepath.name)
                item.setText(3, str(len(rec.installed_files)) if rec else "")
                item.setForeground(1, QColor("#2e7d32"))
            else:
                item.setText(1, "Not installed")
                item.setText(2, self._available_option_summary(archive))
                item.setForeground(1, QColor("#757575"))

            self.tree.addTopLevelItem(item)

    # ── Mod Info panel ────────────────────────────────────────────────

    def _on_selection_changed(self):
        item = self.tree.currentItem()
        archive: ModArchive | None = None
        if item:
            self.tree.scrollToItem(item)
            archive = item.data(0, Qt.UserRole)
        m = archive.manifest if archive else None

        archive_stem = archive.filepath.stem if (archive and m and m.mod_name) else None
        author  = m.author  if m else None
        version = m.version if m else None
        url     = m.url     if m else None

        self._info_empty_row.setVisible(False)

        if not any((archive_stem, author, version, url)):
            self._info_empty_row.setVisible(True)
            self._info_archive_row.clear()
            self._info_author_row.clear()
            self._info_version_row.clear()
            self._info_url_row.clear()
            for widget in (
                self._info_archive_row,
                self._info_author_row,
                self._info_version_row,
                self._info_url_row,
            ):
                lbl = self._info_form.labelForField(widget)
                if lbl:
                    lbl.setVisible(False)
                widget.setVisible(False)
            return

        def _set_row(widget: QLabel, value: str | None) -> None:
            lbl = self._info_form.labelForField(widget)
            visible = bool(value)
            widget.setVisible(visible)
            if lbl:
                lbl.setVisible(visible)
            if value:
                widget.setText(value)

        _set_row(self._info_archive_row, archive_stem)
        _set_row(self._info_author_row,  author)
        _set_row(self._info_version_row, version)
        _set_row(self._info_url_row, f'<a href="{url}">{url}</a>' if url else None)

    # ── Install ───────────────────────────────────────────────────────

    def _install_selected(self):
        item = self.tree.currentItem()
        if not item or not self.manager:
            return

        archive: ModArchive = item.data(0, Qt.UserRole)
        if not archive:
            return

        if self.manager.is_installed(archive.filepath.name):
            QMessageBox.warning(
                self,
                "Already Installed",
                f"A mod from '{archive.name}' is already installed.\n"
                "Uninstall it first.",
            )
            return

        backend = self.manager.resolve_install_backend()
        if backend is None:
            QMessageBox.warning(
                self,
                "No Install Backend",
                "No supported install backend is currently available.\n"
                "Install the DLL loader and LooseFileLoader, or restore yumia if you still have active Yumia-managed mods.",
            )
            self._update_backend_banner()
            return

        if archive.manifest is not None:
            self._install_with_manifest(archive, backend)
        elif archive.archive_kind == "direct_loose" and archive.direct_loose_multi_select:
            self._install_direct_loose_components(archive, backend)
        else:
            self._install_legacy(archive, backend)

    def _install_with_manifest(self, archive, backend: str):
        dlg = FeatureSelectionDialog(archive, self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._run_in_worker(
            self.manager.install_manifest_mod,
            archive,
            dlg.get_selections(),
            backend=backend,
        )

    def _install_legacy(self, archive, backend: str):
        if len(archive.options) == 1:
            option = archive.options[0]
        else:
            dlg = OptionDialog(archive.name, archive.options, self)
            if dlg.exec() != QDialog.Accepted:
                return
            option = dlg.selected_option()
            if not option:
                return
        self._run_in_worker(
            self.manager.install_mod,
            archive,
            option,
            backend=backend,
        )

    def _install_direct_loose_components(self, archive, backend: str):
        dlg = LooseComponentDialog(archive, self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._run_in_worker(
            self.manager.install_direct_loose_mod,
            archive,
            dlg.selected_options(),
            backend=backend,
        )

    # ── Uninstall ─────────────────────────────────────────────────────

    def _uninstall_selected(self):
        item = self.tree.currentItem()
        if not item or not self.manager:
            return

        archive: ModArchive = item.data(0, Qt.UserRole)
        if not archive:
            return

        if not self.manager.is_installed(archive.filepath.name):
            QMessageBox.information(
                self, "Not Installed", f"'{archive.name}' is not currently installed."
            )
            return

        rec = self.manager.installed[archive.filepath.name]
        if rec.backend == "loose":
            body = (
                f"Uninstall '{rec.option_name}' from {archive.name}?\n\n"
                f"This will remove {len(rec.installed_files)} loose file(s) from the game mods folder."
            )
        else:
            body = (
                f"Uninstall '{rec.option_name}' from {archive.name}?\n\n"
                f"This will remove {len(rec.installed_files)} file(s), restore RDB backups, "
                "and re-run yumia for any remaining Yumia-managed mods."
            )
        reply = QMessageBox.question(
            self,
            "Confirm Uninstall",
            body,
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._run_in_worker(
            self.manager.uninstall_mod,
            archive.filepath.name,
        )

    # ── Worker Thread Management ──────────────────────────────────────

    def _run_in_worker(self, func, *args, **kwargs):
        self._set_busy(True)

        self.worker = WorkerThread(func, *args, **kwargs)
        self.worker.finished_signal.connect(self._on_worker_finished)
        self.worker.start()

    def _on_worker_finished(self, success: bool, message: str):
        self._set_busy(False)

        if success:
            self._append_log(f"✅ {message}")
        else:
            self._append_log(f"❌ {message}")
            QMessageBox.warning(self, "Operation Failed", message)

        self._refresh()

    def _set_busy(self, busy: bool):
        self.progress.setVisible(busy)
        self.status_label.setText("Working..." if busy else "Ready")
        if self.manager and not busy:
            self.install_btn.setEnabled(self.manager.get_environment_status().can_install)
        else:
            self.install_btn.setEnabled(not busy)
        self.uninstall_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)
        self.open_downloads_btn.setEnabled(not busy)
        self.settings_btn.setEnabled(not busy)
        self.migrate_btn.setEnabled(self.migrate_btn.isVisible() and not busy)
        self.open_game_mods_btn.setEnabled(self.open_game_mods_btn.isVisible() and not busy)

    # ── Settings ──────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self.mods_dir, self.game_package_dir, self)

        if dlg.exec() == QDialog.Accepted:
            mods_dir, game_dir = dlg.get_values()

            self.mods_dir = mods_dir
            self.game_package_dir = game_dir

            if self._persist_settings:
                self.settings.setValue("mods_dir", mods_dir)
                self.settings.setValue("game_package_dir", game_dir)

            self._append_log("Settings updated, reinitializing...")
            self._try_init_manager()

    def _migrate_yumia_installs(self):
        if not self.manager:
            return

        status = self.manager.get_environment_status()
        if not status.can_migrate:
            missing = self._missing_rdb_backups()
            if missing:
                detail = (
                    "Migration is blocked because these backup files are missing:\n\n"
                    + "\n".join(missing)
                    + "\n\nRun one Yumia install flow that creates them, or restore them manually, then try again."
                )
            else:
                detail = (
                    "Migration is only available when Yumia-managed mods are installed and the LooseFileLoader prerequisites are ready."
                )
            QMessageBox.information(
                self,
                "Migration Unavailable",
                detail,
            )
            return

        reply = QMessageBox.question(
            self,
            "Migrate Yumia Installs",
            "Migrate all currently installed Yumia-managed mods to LooseFileLoader now?\n\n"
            "This copies the same selected mod payloads into the game mods folder, removes the old Yumia package files, and restores the vanilla RDB files.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._run_in_worker(self.manager.migrate_all_yumia_to_loose)

    def _open_game_mods_folder(self):
        if self.manager:
            mods_path = self.manager.loose_mods_dir
        elif self.game_package_dir:
            mods_path = Path(self.game_package_dir).parent / "mods"
        else:
            mods_path = None

        if mods_path and mods_path.exists():
            import subprocess as sp

            if sys.platform == "win32":
                os.startfile(str(mods_path))
            elif sys.platform == "linux":
                sp.Popen(["xdg-open", str(mods_path)])
            elif sys.platform == "darwin":
                sp.Popen(["open", str(mods_path)])
        else:
            QMessageBox.warning(
                self,
                "Not Found",
                "The game mods folder does not exist yet.",
            )

    # ── Open Mods Folder ──────────────────────────────────────────────

    def _open_mods_folder(self):
        if self.mods_dir and Path(self.mods_dir).exists():
            import subprocess as sp

            if sys.platform == "win32":
                os.startfile(str(self.mods_dir))
            elif sys.platform == "linux":
                sp.Popen(["xdg-open", self.mods_dir])
            elif sys.platform == "darwin":
                sp.Popen(["open", self.mods_dir])
        else:
            QMessageBox.warning(
                self, "Not Found", "Downloads folder is not set or does not exist."
            )

    # ── Close ─────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Operation in Progress",
                "An operation is still running. Quit anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


# ── Entry Point ───────────────────────────────────────────────────────

def main(
    logger: logging.Logger | None = None,
    *,
    mods_dir_override: str | None = None,
    game_package_dir_override: str | None = None,
    settings_org: str = "Nioh3ModManager",
    settings_app: str = "Nioh3ModManager",
    persist_settings: bool = True,
    window_title_suffix: str | None = None,
):
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow(
        logger=logger,
        mods_dir_override=mods_dir_override,
        game_package_dir_override=game_package_dir_override,
        settings_org=settings_org,
        settings_app=settings_app,
        persist_settings=persist_settings,
        window_title_suffix=window_title_suffix,
    )
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
