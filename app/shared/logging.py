"""Logging configuration — idempotent setup to prevent duplicate handlers."""

import logging
import sys


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the root 'bluestone' logger. Safe to call multiple times."""
    logger = logging.getLogger("bluestone")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'bluestone' namespace."""
    return logging.getLogger(f"bluestone.{name}")
