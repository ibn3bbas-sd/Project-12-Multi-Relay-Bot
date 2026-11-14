"""Entry point. Run with: python -m src.main"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config
from .scheduler import register_jobs
from .telegram_bot import build_app


def setup_logging() -> None:
    # Directories first — a FileHandler against a missing folder kills startup,
    # which under a Windows service means a silent restart loop.
    config.ensure_dirs()

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ]
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # These two are chatty at INFO and drown out the relay's own lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("YouTube Relay Bot starting (base dir: %s)", config.BASE_DIR)
    logger.info("Source channel: %s → target: %s", config.SOURCE_CHANNEL_ID, config.TARGET_CHANNEL_ID)

    app = build_app()
    register_jobs(app)

    app.run_polling(drop_pending_updates=True, stop_signals=None)
    logger.info("YouTube Relay Bot stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
