#!/usr/bin/env python3
"""Deterministic migration: Hermes SQLite session DB → Ferrite knowledge graph.

Reads user+assistant messages from the Hermes state.db, batches them into
episodes, and ingests via the Ferrite extraction pipeline. Each episode
records its source session_id and message range for traceability.

Usage:
    python scripts/migrate_from_sqlite.py [--db PATH] [--limit N] [--dry-run]

Defaults:
    --db      ~/.hermes/state.db
    --limit   0 (all sessions)
    --dry-run False

Determinism guarantees:
    1. Sessions processed in started_at order (oldest first)
    2. Messages processed in timestamp order within each session
    3. Episode IDs are deterministic: sha1(session_id + first_msg_timestamp)
    4. Re-running is idempotent — episodes with existing IDs are skipped
    5. No LLM randomness in the migration itself — extraction is the only
       non-deterministic step, and it's isolated to the pipeline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

# Bootstrap ferrite imports
FERRITE_SRC = os.path.expanduser("~/ferrite/src")
if FERRITE_SRC not in sys.path:
    sys.path.insert(0, FERRITE_SRC)

from ferrite.ingestion import IngestionPipeline  # noqa: E402
from ferrite.models import Episode  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate")

DEFAULT_DB = os.path.expanduser("~/ferrite/state.db")
HERMES_DB = os.path.expanduser("~/.hermes/state.db")
CHECKPOINT_FILE = "/tmp/ferrite-migration-checkpoint.json"


def load_checkpoint() -> dict:
    """Load checkpoint of completed session indices for fast resume."""
    try:
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"completed_sessions": [], "last_index": 0}


def save_checkpoint(checkpoint: dict) -> None:
    """Persist checkpoint to disk for crash recovery."""
    try:
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(checkpoint, f)
    except OSError as e:
        logger.warning("Failed to save checkpoint: %s", e)


def deterministic_episode_id(session_id: str, first_ts: float) -> str:
    """Generate a deterministic episode ID from session + first message timestamp."""
    raw = f"hermes:{session_id}:{first_ts}"
    h = hashlib.sha1(raw.encode()).hexdigest()
    return f"h-{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def get_sessions(conn: sqlite3.Connection, limit: int = 0) -> list[dict]:
    """Get sessions in started_at order, with message counts."""
    query = """
        SELECT id, source, model, started_at, ended_at,
               message_count, title, profile_name, chat_id, chat_type
        FROM sessions
        WHERE archived = 0 AND message_count > 0
        ORDER BY started_at ASC
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    cur = conn.execute(query)
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur]


def get_session_messages(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Get user+assistant messages for a session in timestamp order."""
    cur = conn.execute(
        """
        SELECT id, role, content, timestamp, tool_name
        FROM messages
        WHERE session_id = ? AND role IN ('user', 'assistant')
            AND content IS NOT NULL AND content != ''
            AND (tool_name IS NULL OR tool_name = '')
        ORDER BY timestamp ASC
        """,
        (session_id,),
    )
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur]


def _neo4j_retry(driver, cypher, **params):
    """Run a Neo4j query with retry logic for transient errors.

    Retries on a broad set of transient errors:
    - ServiceUnavailable: Neo4j down or restarting
    - DatabaseError: internal Neo4j error during heavy writes
    - OSError: socket-level errors ("No data", "Connection reset") from
      stale pooled connections after a Neo4j restart
    - TransientError: temporary transaction conflicts

    On retry, forces the driver to drop its stale connection pool by
    calling driver.verify_connectivity() before retrying.
    """
    import neo4j.exceptions
    import time

    max_retries = 5
    transient = (
        neo4j.exceptions.ServiceUnavailable,
        neo4j.exceptions.DatabaseError,
        neo4j.exceptions.TransientError,
        OSError,  # socket-level: "No data", "Connection reset by peer"
    )

    for attempt in range(max_retries):
        try:
            with driver.session() as s:
                result = s.run(cypher, **params)
                return result.single()
        except transient as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s, 8s exponential backoff
                logger.warning(
                    "Neo4j transient error (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, e, wait,
                )
                # Force pool to discard stale connections
                try:
                    driver.verify_connectivity()
                except Exception:
                    pass  # verify itself may fail; the retry will catch it
                time.sleep(wait)
            else:
                raise


def check_episode_exists(driver, episode_id: str) -> bool:
    """Check if an episode was already ingested (idempotency)."""
    record = _neo4j_retry(
        driver,
        "MATCH (e:Episode {id: $eid}) RETURN count(e) AS c",
        eid=episode_id,
    )
    return record["c"] > 0 if record else False


def session_to_episodes(session: dict, messages: list[dict]) -> list[Episode]:
    """Convert a session's messages into batched episodes.

    Batches: 10 message-pairs (20 messages) per episode, or fewer if the
    session is short. Each episode gets a deterministic ID.
    """
    episodes = []
    batch_size = 20  # messages per episode

    for i in range(0, len(messages), batch_size):
        batch = messages[i:i + batch_size]
        first_ts = batch[0]["timestamp"]

        # Build content
        lines = []
        for msg in batch:
            role = msg["role"].capitalize()
            content = msg["content"]
            if len(content) > 2000:
                content = content[:2000] + " [...] (truncated)"
            lines.append(f"{role}: {content}")

        content = "\n".join(lines)
        ep_id = deterministic_episode_id(session["id"], first_ts)

        started_at = session.get("started_at")
        if started_at:
            recorded_at = datetime.fromtimestamp(started_at, tz=timezone.utc)
        else:
            recorded_at = datetime.now(tz=timezone.utc)

        ep = Episode(
            id=ep_id,
            content=content,
            content_type="conversation",
            source={
                "type": "hermes-sqlite",
                "name": "migration",
                "session_id": session["id"],
                "session_source": session.get("source", "unknown"),
                "session_title": session.get("title", ""),
                "profile": session.get("profile_name", ""),
                "msg_range": f"{i}-{i+len(batch)}",
            },
            namespace="shared",
            recorded_at=recorded_at,
        )
        episodes.append(ep)

    return episodes


def migrate(
    db_path: str = HERMES_DB,
    neo4j_uri: str = "bolt://localhost:7687",
    neo4j_user: str = "neo4j",
    neo4j_password: str = "ferrite123",
    redis_url: str = "redis://localhost:6379",
    litellm_base_url: str = "http://localhost:4000",
    litellm_api_key: str = "",
    llm_model: str = "glm-5.2",
    limit: int = 0,
    dry_run: bool = False,
) -> dict:
    """Run the migration. Returns summary stats."""
    logger.info("Starting SQLite → Ferrite migration")
    logger.info("  DB: %s", db_path)
    logger.info("  Neo4j: %s", neo4j_uri)
    logger.info("  Limit: %s sessions", limit if limit > 0 else "ALL")
    logger.info("  Dry run: %s", dry_run)

    # Connect to SQLite
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    logger.info("Connected to SQLite")

    # Get sessions
    sessions = get_sessions(conn, limit=limit)
    logger.info("Found %d sessions with messages", len(sessions))

    if dry_run:
        total_msgs = 0
        for s in sessions:
            msgs = get_session_messages(conn, s["id"])
            total_msgs += len(msgs)
        logger.info("[DRY RUN] Would process %d sessions, %d messages", len(sessions), total_msgs)
        conn.close()
        return {"sessions": len(sessions), "messages": total_msgs, "episodes": 0, "facts": 0}

    # Connect to Neo4j + Redis
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        neo4j_uri,
        auth=(neo4j_user, neo4j_password),
        max_connection_lifetime=300,
        max_connection_pool_size=50,
        connection_acquisition_timeout=30,
    )

    # Wait for Neo4j to be ready (up to 120s)
    import time as _time
    for attempt in range(60):
        try:
            driver.verify_connectivity()
            break
        except Exception as e:
            if attempt < 59:
                logger.warning(
                    "Neo4j not ready (attempt %d/60): %s — waiting 2s",
                    attempt + 1, e,
                )
                _time.sleep(2)
            else:
                raise RuntimeError(f"Neo4j not available after 120s: {e}")
    logger.info("Connected to Neo4j")

    # LLM client for extraction
    def llm_client(system_prompt: str, user_prompt: str) -> str:
        import urllib.request
        data = json.dumps({
            "model": llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }).encode()
        req = urllib.request.Request(
            f"{litellm_base_url}/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {litellm_api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    pipe = IngestionPipeline(
        redis_url=redis_url,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        llm_client=llm_client,
    )
    logger.info("Ingestion pipeline ready")

    # Migrate
    checkpoint = load_checkpoint()
    completed_sessions = set(checkpoint.get("completed_sessions", []))

    stats = {
        "sessions_processed": 0,
        "sessions_skipped": 0,
        "episodes_created": 0,
        "episodes_skipped": 0,
        "facts_written": 0,
        "errors": 0,
    }

    for i, session in enumerate(sessions):
        session_id = session["id"]
        title = session.get("title", "(untitled)")

        # Skip sessions already completed per checkpoint
        if session_id in completed_sessions:
            stats["sessions_skipped"] += 1
            continue

        # Get messages
        messages = get_session_messages(conn, session_id)
        if not messages:
            continue

        # Build episodes
        episodes = session_to_episodes(session, messages)

        # Check if already migrated (Neo4j idempotency)
        all_exist = True
        for ep in episodes:
            if not check_episode_exists(driver, ep.id):
                all_exist = False
                break

        if all_exist:
            stats["sessions_skipped"] += 1
            completed_sessions.add(session_id)
            save_checkpoint({"completed_sessions": list(completed_sessions), "last_index": i})
            continue

        # Ingest
        for ep in episodes:
            if check_episode_exists(driver, ep.id):
                stats["episodes_skipped"] += 1
                continue

            try:
                pipe.enqueue(ep)
                pipe.process_next()

                # Count facts (non-fatal if this fails)
                try:
                    record = _neo4j_retry(
                        driver,
                        "MATCH (ep:Episode {id: $ep_id})<-[:SOURCED_FROM]-(f:Fact) "
                        "RETURN count(f) AS c",
                        ep_id=ep.id,
                    )
                    fact_count = record["c"] if record else 0
                except Exception as count_err:
                    logger.debug("Fact count query failed for %s: %s", ep.id, count_err)
                    fact_count = 0

                stats["episodes_created"] += 1
                stats["facts_written"] += fact_count
                logger.info(
                    "  [%d/%d] %s — ep %s: %d facts",
                    i + 1, len(sessions), title[:40], ep.id[:12], fact_count,
                )
            except Exception as e:
                stats["errors"] += 1
                logger.error("  Episode %s failed: %s", ep.id, e, exc_info=True)

        stats["sessions_processed"] += 1
        completed_sessions.add(session_id)
        save_checkpoint({"completed_sessions": list(completed_sessions), "last_index": i})

        # Progress
        if (i + 1) % 50 == 0:
            logger.info(
                "Progress: %d/%d sessions (%d episodes, %d facts, %d errors)",
                i + 1, len(sessions),
                stats["episodes_created"], stats["facts_written"],
                stats["errors"],
            )

    # Cleanup
    pipe.close()
    driver.close()
    conn.close()

    logger.info("Migration complete:")
    logger.info("  Sessions processed: %d", stats["sessions_processed"])
    logger.info("  Sessions skipped (idempotent): %d", stats["sessions_skipped"])
    logger.info("  Episodes created: %d", stats["episodes_created"])
    logger.info("  Episodes skipped (idempotent): %d", stats["episodes_skipped"])
    logger.info("  Facts written: %d", stats["facts_written"])
    logger.info("  Errors: %d", stats["errors"])

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate Hermes SQLite sessions → Ferrite knowledge graph"
    )
    parser.add_argument(
        "--db", default=HERMES_DB,
        help=f"Path to Hermes state.db (default: {HERMES_DB})",
    )
    parser.add_argument(
        "--neo4j-uri", default="bolt://localhost:7687",
        help="Neo4j Bolt URI",
    )
    parser.add_argument(
        "--neo4j-user", default="neo4j",
        help="Neo4j username",
    )
    parser.add_argument(
        "--neo4j-password", default="ferrite123",
        help="Neo4j password (or set FERRITE_NEO4J_PASSWORD env var)",
    )
    parser.add_argument(
        "--redis-url", default="redis://localhost:6379",
        help="Redis URL for ingestion queue",
    )
    parser.add_argument(
        "--litellm-base-url", default="http://localhost:4000",
        help="LiteLLM proxy URL",
    )
    parser.add_argument(
        "--litellm-api-key", default="",
        help="LiteLLM API key (or set FERRITE_LITELLM_API_KEY env var)",
    )
    parser.add_argument(
        "--llm-model", default="glm-5.2",
        help="LLM model for extraction",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max sessions to migrate (0 = all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze without ingesting — shows what would be migrated",
    )

    args = parser.parse_args()

    # Pull secrets from env if not provided
    neo4j_password = args.neo4j_password
    if neo4j_password == "ferrite123":
        neo4j_password = os.environ.get("FERRITE_NEO4J_PASSWORD", neo4j_password)

    litellm_api_key = args.litellm_api_key
    if not litellm_api_key:
        litellm_api_key = os.environ.get("FERRITE_LITELLM_API_KEY", "")

    migrate(
        db_path=args.db,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=neo4j_password,
        redis_url=args.redis_url,
        litellm_base_url=args.litellm_base_url,
        litellm_api_key=litellm_api_key,
        llm_model=args.llm_model,
        limit=args.limit,
        dry_run=args.dry_run,
    )
