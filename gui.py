"""
Nioh 3 Mod Manager - GUI (PySide6)
"""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QFont, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
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


# ── Yumia Prompt Dialog ──────────────────────────────────────────────

class YumiaPromptDialog(QDialog):
    """Dialog shown when yumia detects changed RDB files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Game Files Changed")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        label = QLabel(
            "The yumia tool detected that the core game files (system.rdb / root.rdb) "
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

        # Auto-yes yumia
        self.auto_yes_check = QCheckBox(
            "Automatically answer 'Yes' to yumia's game-update prompt"
        )
        layout.addWidget(self.auto_yes_check)

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

    def get_values(self) -> tuple[str, str, bool]:
        return self._mods_dir, self._game_dir, self.auto_yes_check.isChecked()


# ── Main Window ───────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, logger: logging.Logger | None = None):
        super().__init__()
        self._logger = logger or logging.getLogger("nioh3modmanager")
        self.setWindowTitle("Nioh 3 Mod Manager")
        self.setMinimumSize(900, 600)

        # Settings persistence
        self.settings = QSettings("Nioh3ModManager", "Nioh3ModManager")
        self.mods_dir = self.settings.value("mods_dir", DEFAULT_MODS_DIR, type=str)
        self.game_package_dir = self.settings.value(
            "game_package_dir", DEFAULT_GAME_PACKAGE, type=str
        )
        self.auto_yes_yumia = self.settings.value("auto_yes_yumia", False, type=bool)

        self.manager: Optional[ModManager] = None
        self.worker: Optional[WorkerThread] = None

        self._build_ui()
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

        # ── Splitter: mod list | log ──────────────────────────────────
        splitter = QSplitter(Qt.Vertical)

        # Top: Mod tree
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        top_label = QLabel("<b>Mod Archives</b>")
        top_layout.addWidget(top_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Mod", "Status", "Installed Option", "Files"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 200)
        self.tree.setColumnWidth(3, 60)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        top_layout.addWidget(self.tree)

        # Action buttons
        action_row = QHBoxLayout()
        self.install_btn = QPushButton("📦 Install Selected")
        self.install_btn.clicked.connect(self._install_selected)
        action_row.addWidget(self.install_btn)

        self.uninstall_btn = QPushButton("🗑 Uninstall Selected")
        self.uninstall_btn.clicked.connect(self._uninstall_selected)
        action_row.addWidget(self.uninstall_btn)

        action_row.addStretch()

        self.open_mods_btn = QPushButton("📂 Open Mods Folder")
        self.open_mods_btn.clicked.connect(self._open_mods_folder)
        action_row.addWidget(self.open_mods_btn)

        top_layout.addLayout(action_row)
        splitter.addWidget(top_widget)

        # Bottom: Log
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        bottom_label = QLabel("<b>Log</b>")
        bottom_layout.addWidget(bottom_label)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setMaximumBlockCount(5000)
        bottom_layout.addWidget(self.log_text)

        splitter.addWidget(bottom_widget)
        splitter.setSizes([400, 200])

        main_layout.addWidget(splitter)

        # ── Progress bar ──────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)  # indeterminate
        main_layout.addWidget(self.progress)

    # ── Manager Init ──────────────────────────────────────────────────

    def _try_init_manager(self):
        if not self.mods_dir:
            self._append_log("⚠ Mods directory not configured. Open Settings to set it.")
            self.status_label.setText("⚠ Not configured")
            return

        self.manager = ModManager(
            mods_dir=self.mods_dir,
            game_package_dir=self.game_package_dir,
            log_callback=self._append_log,
        )

        issues = self.manager.validate_paths()
        for issue in issues:
            self._append_log(f"⚠ {issue}")

        if any("does not exist" in i for i in issues if "backup" not in i.lower()):
            self.status_label.setText("⚠ Path issues — check log")
        else:
            self.status_label.setText("Ready")
            self._refresh()

    # ── Logging ───────────────────────────────────────────────────────

    def _append_log(self, msg: str):
        self.log_text.appendPlainText(msg)
        self._logger.info(msg)

    # ── Refresh ───────────────────────────────────────────────────────

    def _refresh(self):
        if not self.manager:
            return

        self._append_log("─── Scanning archives... ───")
        self.manager.scan_archives()

        self._append_log("─── Checking installed status... ───")
        self.manager.check_installed_status()

        self._populate_tree()

    def _populate_tree(self):
        self.tree.clear()
        if not self.manager:
            return

        for archive in self.manager.archives:
            item = QTreeWidgetItem()
            item.setText(0, archive.name)
            item.setData(0, Qt.UserRole, archive)

            is_inst = self.manager.is_installed(archive.filepath.name)
            opt_name = self.manager.get_installed_option(archive.filepath.name)

            if is_inst:
                item.setText(1, "✅ Installed")
                item.setText(2, opt_name or "")
                rec = self.manager.installed.get(archive.filepath.name)
                item.setText(3, str(len(rec.installed_files)) if rec else "")
                item.setForeground(1, QColor("#2e7d32"))
            else:
                item.setText(1, "Not installed")
                n_options = len(archive.options)
                if n_options == 1:
                    item.setText(2, f"1 option: {archive.options[0].name}")
                else:
                    names = ", ".join(o.name for o in archive.options)
                    item.setText(2, f"{n_options} options: {names}")
                item.setForeground(1, QColor("#757575"))

            self.tree.addTopLevelItem(item)

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

        # Select option
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
            auto_yes_yumia=self.auto_yes_yumia,
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
        reply = QMessageBox.question(
            self,
            "Confirm Uninstall",
            f"Uninstall '{rec.option_name}' from {archive.name}?\n\n"
            f"This will remove {len(rec.installed_files)} file(s), restore RDB backups, "
            f"and re-run yumia for any remaining mods.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._run_in_worker(
            self.manager.uninstall_mod,
            archive.filepath.name,
            auto_yes_yumia=self.auto_yes_yumia,
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
        self.install_btn.setEnabled(not busy)
        self.uninstall_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)

    # ── Settings ──────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self.mods_dir, self.game_package_dir, self)
        dlg.auto_yes_check.setChecked(self.auto_yes_yumia)

        if dlg.exec() == QDialog.Accepted:
            mods_dir, game_dir, auto_yes = dlg.get_values()

            self.mods_dir = mods_dir
            self.game_package_dir = game_dir
            self.auto_yes_yumia = auto_yes

            self.settings.setValue("mods_dir", mods_dir)
            self.settings.setValue("game_package_dir", game_dir)
            self.settings.setValue("auto_yes_yumia", auto_yes)

            self._append_log("Settings updated, reinitializing...")
            self._try_init_manager()

    # ── Open Mods Folder ──────────────────────────────────────────────

    def _open_mods_folder(self):
        if self.mods_dir and Path(self.mods_dir).exists():
            import subprocess as sp

            if sys.platform == "win32":
                sp.Popen(["explorer", self.mods_dir])
            elif sys.platform == "linux":
                sp.Popen(["xdg-open", self.mods_dir])
            elif sys.platform == "darwin":
                sp.Popen(["open", self.mods_dir])
        else:
            QMessageBox.warning(
                self, "Not Found", "Mods directory is not set or doesn't exist."
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

def main(logger: logging.Logger | None = None):
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow(logger=logger)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
