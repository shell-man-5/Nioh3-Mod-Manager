#!/usr/bin/env python3
"""Nioh 3 Mod Manager — Entry Point"""

import argparse
import faulthandler
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging() -> tuple[logging.Logger, Path]:
    log_dir = Path(os.environ.get("APPDATA", "~")) / "Nioh3ModManager"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "nioh3modmanager.log"

    handler = RotatingFileHandler(
        log_file,
        maxBytes=1 * 1024 * 1024,  # 1 MB
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

    logger = logging.getLogger("nioh3modmanager")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return logger, log_dir


def install_crash_handler(logger: logging.Logger, log_dir: Path):
    # Python-level unhandled exceptions
    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = handle_exception

    # C-level crashes (segfault, abort) — faulthandler writes to a separate
    # file because it can't use Python logging machinery after a crash
    crash_file = log_dir / "crash.log"
    faulthandler.enable(open(crash_file, "w"), all_threads=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nioh 3 Mod Manager")
    parser.add_argument("--mods-dir")
    parser.add_argument("--game-package-dir")
    parser.add_argument("--settings-org", default="Nioh3ModManager")
    parser.add_argument("--settings-app", default="Nioh3ModManager")
    parser.add_argument("--no-persist-settings", action="store_true")
    parser.add_argument("--window-title-suffix")
    parser.add_argument("--mock-yumia", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.mock_yumia:
        os.environ["NIOH3MM_MOCK_YUMIA"] = "1"

    logger, log_dir = setup_logging()
    install_crash_handler(logger, log_dir)
    logger.info("Starting Nioh 3 Mod Manager")

    from gui import main
    main(
        logger,
        mods_dir_override=args.mods_dir,
        game_package_dir_override=args.game_package_dir,
        settings_org=args.settings_org,
        settings_app=args.settings_app,
        persist_settings=not args.no_persist_settings,
        window_title_suffix=args.window_title_suffix,
    )
