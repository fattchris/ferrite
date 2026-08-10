"""Entry point: schema init, background worker, and uvicorn launch."""

import logging
import os
import threading
import time

import uvicorn

from .api import create_app
from .config import get_settings
from .db import get_driver
from .ingestion import IngestionPipeline
from .schema import init_schema
from .structured_logging import setup_logging

# Configure structured JSON logging before any app code runs.
# LOG_LEVEL env var controls verbosity (DEBUG, INFO, WARNING, ERROR).
# Logs go to stderr → captured by Docker → `docker logs ferrite-api`.
setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def run_worker(pipeline: IngestionPipeline, stop_event: threading.Event):
    """Background worker that processes the ingestion queue."""
    logger.info("Ingestion worker started")
    while not stop_event.is_set():
        try:
            episode_id = pipeline.process_next()
            if episode_id is None:
                time.sleep(1)  # No work; wait before polling again
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            time.sleep(5)
    logger.info("Ingestion worker stopped")


def main():
    """Initialize schema, start worker, and launch the API server."""
    settings = get_settings()

    # Initialize Neo4j schema
    logger.info("Initializing Neo4j schema...")
    schema_driver = get_driver()
    init_schema(schema_driver)

    # Create ingestion pipeline
    pipeline = IngestionPipeline(
        redis_url=settings.REDIS_URL,
        neo4j_uri=settings.NEO4J_URI,
        neo4j_user=settings.NEO4J_USER,
        neo4j_password=settings.NEO4J_PASSWORD,
    )

    # Start background worker
    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=run_worker, args=(pipeline, stop_event), daemon=True
    )
    worker_thread.start()

    # Create and run FastAPI app
    app = create_app(pipeline=pipeline)

    try:
        uvicorn.run(app, host="0.0.0.0", port=settings.SERVER_PORT)
    finally:
        logger.info("Shutting down...")
        stop_event.set()
        worker_thread.join(timeout=10)
        pipeline.close()


if __name__ == "__main__":
    main()
