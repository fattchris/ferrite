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

    Connection pool is tuned for resilience:
    - max_connection_lifetime=300s: reaper culls stale connections before
      they can be handed out, preventing "defunct connection" errors after
      Neo4j restarts.
    - connection_acquisition_timeout=30s: bounded wait instead of infinite
      hang when the pool is exhausted under write bursts.
    - max_connection_pool_size=50: enough for concurrent reads during
      migration without starving the API consumer.
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
                    max_connection_lifetime=300,        # 5 min — cull stale conns
                    max_connection_pool_size=50,         # enough for migration + API
                    connection_acquisition_timeout=30,   # bounded wait, not infinite
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
