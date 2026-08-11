"""Application logging configuration.

Never log passwords, API keys, database credentials, authorization tokens,
or other sensitive user information.
"""

from __future__ import annotations

import logging
import sys
from typing import Final

_CONFIGURED: Final[str] = "_mindsurve_logging_configured"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure a consistent root logger for the application.

    Safe to call multiple times; subsequent calls are no-ops once configured.
    """
    root = logging.getLogger()
    if getattr(root, _CONFIGURED, False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Azure SDK request dumps are extremely noisy and slow the upload path in the console.
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
        logging.WARNING
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    setattr(root, _CONFIGURED, True)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger under the MindSurve logging hierarchy."""
    return logging.getLogger(name)
