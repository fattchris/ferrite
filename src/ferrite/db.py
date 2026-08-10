"""Thread-safe Neo4j driver singleton.

Lazily creates a single Neo4j driver instance shared across the process,
using settings from config.py. All modules should use get_driver() instead
of constructing their own GraphDatabase.driver(...) — this avoids
connection leaks and ensures consistent auth configuration.
"""

import logging
import threading

from neo4j import GraphDatabase

from .config import get_settings

logger = logging.getLogger(__name__)

_driver = None
_lock = threading.Lock()


def get_driver():
    """Return the process-wide Neo4j driver singleton.

    Creates the driver on first call (lazy init), guarded by a lock
    so concurrent threads don't create duplicates.
    """
    global _driver
    if _driver is None:
        with _lock:
            # Double-checked locking
            if _driver is None:
                settings = get_settings()
                _driver = GraphDatabase.driver(
                    settings.NEO4J_URI,
                    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                )
                logger.info(
                    "Neo4j driver created for %s (user=%s)",
                    settings.NEO4J_URI,
                    settings.NEO4J_USER,
                )
    return _driver


def close_driver() -> None:
    """Close the singleton driver (for clean shutdown / tests)."""
    global _driver
    with _lock:
        if _driver is not None:
            _driver.close()
            _driver = None
            logger.info("Neo4j driver closed")
