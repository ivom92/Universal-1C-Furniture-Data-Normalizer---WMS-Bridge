"""Rich console logging plus rotating UTF-8 file logs on Windows."""

from __future__ import annotations

import logging
import os
import sys
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path

os.environ.setdefault("STREAMLIT_SERVER_FILE_WATCHER_TYPE", "none")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
warnings.filterwarnings("ignore", category=UserWarning)
logging.getLogger("transformers").setLevel(logging.ERROR)

from rich.console import Console

def _safe_reconfigure_utf8(stream: object) -> None:
    if stream is None:
        return
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        pass


_safe_reconfigure_utf8(sys.stdout)
_safe_reconfigure_utf8(sys.stderr)

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGGER_NAME = "warehouse"
LOG_FILENAME = "warehouse_app.log"
FILE_HANDLER_NAME = "warehouse_file"
CONSOLE_HANDLER_NAME = "warehouse_console"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_FILE_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-8s | [%(name)s:%(funcName)s] %(message)s"
_CONSOLE_FORMAT = "%(levelname)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def default_log_path() -> Path:
    return PROJECT_ROOT / "logs" / LOG_FILENAME


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def _ensure_console_handler(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if handler.get_name() == CONSOLE_HANDLER_NAME:
            handler.setLevel(logging.INFO)
            return
    stream = logging.StreamHandler(sys.stdout)
    stream.set_name(CONSOLE_HANDLER_NAME)
    stream.setLevel(logging.INFO)
    stream.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    logger.addHandler(stream)


def setup_file_logging(log_dir: Path | str | None = None) -> Path:
    """Attach UTF-8 DEBUG file handler (10 MB × 5) and INFO console handler."""
    directory = Path(log_dir) if log_dir is not None else PROJECT_ROOT / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / LOG_FILENAME

    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        if handler.get_name() == FILE_HANDLER_NAME or isinstance(handler, RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.set_name(FILE_HANDLER_NAME)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(file_handler)
    _ensure_console_handler(logger)
    return log_path


setup_file_logging()
