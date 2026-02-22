#!/usr/bin/env python3
"""Nioh 3 Mod Manager — Entry Point"""

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging() -> logging.Logger:
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
    return logger


def install_crash_handler(logger: logging.Logger):
    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = handle_exception


if __name__ == "__main__":
    logger = setup_logging()
    install_crash_handler(logger)
    logger.info("Starting Nioh 3 Mod Manager")

    from gui import main
    main(logger)
