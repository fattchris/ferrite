#!/usr/bin/env python3
"""Deterministic migration: Hermes SQLite session DB → Ferrite knowledge graph.

STANDALONE — no Hermes dependency. Self-contained process with:
  1. File-based PID lock — ensures only one instance runs
  2. Idempotent via Neo4j episode existence check (not a checkpoint file)
  3. Auto-recovery: retries on Neo4j transient errors, waits for services
  4. Self-contained config: reads from ferrite/.env, no Hermes env required

Usage:
    python scripts/migrate_from_sqlite.py [--db PATH] [--limit N] [--dry-run]

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
import atexit
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap ferrite imports
FERRITE_ROOT = Path(__file__).resolve().parent.parent
FERRITE_SRC = str(FERRITE_ROOT / "src")
if FERRITE_SRC not in sys.path:
    sys.path.insert(0, FERRITE_SRC)

import neo4j.exceptions  # noqa: E402
from ferrite.ingestion import IngestionPipeline  # noqa: E402
from ferrite.models import Episode  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate")

# ── Paths ─────────────────────────────────────────────────
HERMES_DB = os.path.expanduser("~/.hermes/state.db")
LOCK_FILE = "/tmp/ferrite-migration.lock"
LOG_FILE = "/tmp/ferrite-migration-full.log"

# ── Config loading ─────────────────────────────────────────

def load_env() -> dict:
    """Load config from ferrite/.env — no Hermes dependency."""
    env_path = FERRITE_ROOT / ".env"
    config = {
        "neo4j_password": "ferrite123",
        "litellm_api_key": "",
        "neo4j_uri": "bolt://localhost:7687",
        "neo4j_user": "neo4j",
        "redis_url": "redis://localhost:6379",
        "litellm_base_url": "http://localhost:4000",
        "llm_model": "glm-5.2",
    }
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            kl = key.lower()
            if kl == "neo4j_password":
                config["neo4j_password"] = val
            elif kl == "litellm_api_key":
                config["litellm_api_key"] = val
            elif kl == "neo4j_uri":
                config["neo4j_uri"] = val
            elif kl == "neo4j_user":
                config["neo4j_user"] = val
            elif kl == "redis_url":
                config["redis_url"] = val
            elif kl == "litellm_base_url":
                config["litellm_base_url"] = val
            elif kl == "llm_model":
                config["llm_model"] = val
    # Allow env var overrides
    config["neo4j_password"] = os.environ.get("FERRITE_NEO4J_PASSWORD", config["neo4j_password"])
    config["litellm_api_key"] = os.environ.get("FERRITE_LITELLM_API_KEY", config["litellm_api_key"])
    return config


# ── PID Lock — ensures single instance ─────────────────────

def acquire_lock() -> None:
    """Acquire file-based PID lock. Exits if another instance is running."""
    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip())
            # Check if that PID is still alive and is actually our migration
            try:
                os.kill(old_pid, 0)  # signal 0 = check alive
                # PID is alive — check if it's actually our migration
                try:
                    cmdline = Path(f"/proc/{old_pid}/cmdline").read_text()
                    if "migrate_from_sqlite" in cmdline:
                        logger.error(
                            "Another migration is already running (PID %d). Exiting.",
                            old_pid,
                        )
                        sys.exit(1)
                except (FileNotFoundError, PermissionError):
                    # macOS doesn't have /proc — check via ps
                    import subprocess
                    result = subprocess.run(
                        ["ps", "-p", str(old_pid), "-o", "command="],
                        capture_output=True, text=True,
                    )
                    if result.returncode == 0 and "migrate_from_sqlite" in result.stdout:
                        logger.error(
                            "Another migration is already running (PID %d). Exiting.",
                            old_pid,
                        )
                        sys.exit(1)
            except (OSError, ProcessLookupError):
                # PID is dead — stale lock, claim it
                logger.info("Found stale lock (PID %d not alive) — claiming", old_pid)
        except (ValueError, OSError):
            # Corrupt lock file — overwrite
            logger.warning("Corrupt lock file — overwriting")

    # Write our PID
    lock_path.write_text(str(os.getpid()))
    # Register cleanup on exit
    atexit.register(release_lock)


def release_lock() -> None:
    """Release the PID lock on clean exit."""
    try:
        Path(LOCK_FILE).unlink(missing_ok=True)
    except Exception:
        pass


# ── Database helpers ───────────────────────────────────────

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


# ── Neo4j helpers ──────────────────────────────────────────

def _neo4j_retry(driver, cypher, **params):
    """Run a Neo4j query with retry logic for transient errors.

    Retries on:
    - ServiceUnavailable: Neo4j down or restarting
    - DatabaseError: internal Neo4j error during heavy writes
    - OSError: socket-level errors from stale pooled connections
    - TransientError: temporary transaction conflicts
    """
    max_retries = 5
    transient = (
        neo4j.exceptions.ServiceUnavailable,
        neo4j.exceptions.DatabaseError,
        neo4j.exceptions.TransientError,
        OSError,
    )

    for attempt in range(max_retries):
        try:
            with driver.session() as s:
                result = s.run(cypher, **params)
                return result.single()
        except transient as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Neo4j transient error (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, e, wait,
                )
                try:
                    driver.verify_connectivity()
                except Exception:
                    pass
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


def wait_for_neo4j(driver, timeout_sec: int = 300) -> None:
    """Wait for Neo4j to be ready, up to timeout.

    Uses exponential backoff: 2s, 4s, 8s, 15s, 15s, ...
    This avoids Neo4j's auth rate limiter which triggers on rapid retries.
    """
    backoff = 2
    elapsed = 0
    attempt = 0
    while elapsed < timeout_sec:
        try:
            driver.verify_connectivity()
            return
        except Exception as e:
            attempt += 1
            if elapsed + backoff >= timeout_sec:
                raise RuntimeError(f"Neo4j not available after {timeout_sec}s: {e}")
            logger.warning(
                "Neo4j not ready (attempt %d): %s — waiting %ds",
                attempt, str(e)[:100], backoff,
            )
            time.sleep(backoff)
            elapsed += backoff
            backoff = min(backoff * 2, 15)  # cap at 15s


# ── Episode generation ─────────────────────────────────────

def deterministic_episode_id(session_id: str, first_ts: float) -> str:
    """Generate a deterministic episode ID from session + first message timestamp."""
    raw = f"hermes:{session_id}:{first_ts}"
    h = hashlib.sha1(raw.encode()).hexdigest()
    return f"h-{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def session_to_episodes(session: dict, messages: list[dict]) -> list[Episode]:
    """Convert a session's messages into batched episodes.

    Batches: 10 message-pairs (20 messages) per episode, or fewer if the
    session is short. Each episode gets a deterministic ID.
    """
    episodes = []
    batch_size = 20

    for i in range(0, len(messages), batch_size):
        batch = messages[i:i + batch_size]
        first_ts = batch[0]["timestamp"]

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


# ── LLM client ─────────────────────────────────────────────

def make_llm_client(base_url: str, api_key: str, model: str):
    """Create a deterministic LLM client (temperature=0, no thinking)."""
    import urllib.request

    def llm_client(system_prompt: str, user_prompt: str) -> str:
        data = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            msg = json.loads(r.read())["choices"][0]["message"]
            return msg.get("content") or msg.get("reasoning_content") or ""

    return llm_client


# ── Migration ──────────────────────────────────────────────

def migrate(
    db_path: str,
    config: dict,
    limit: int = 0,
    dry_run: bool = False,
) -> dict:
    """Run the migration. Returns summary stats."""
    logger.info("Starting SQLite → Ferrite migration")
    logger.info("  DB: %s", db_path)
    logger.info("  Limit: %s sessions", limit if limit > 0 else "ALL")
    logger.info("  Dry run: %s", dry_run)
    logger.info("  PID: %d (lock: %s)", os.getpid(), LOCK_FILE)

    # Connect to SQLite
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    logger.info("Connected to SQLite")

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

    # Connect to Neo4j
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        config["neo4j_uri"],
        auth=(config["neo4j_user"], config["neo4j_password"]),
        max_connection_lifetime=300,
        max_connection_pool_size=50,
        connection_acquisition_timeout=30,
    )
    wait_for_neo4j(driver)
    logger.info("Connected to Neo4j")

    # LLM client
    llm_client = make_llm_client(
        config["litellm_base_url"],
        config["litellm_api_key"],
        config["llm_model"],
    )

    # Ingestion pipeline
    pipe = IngestionPipeline(
        redis_url=config["redis_url"],
        neo4j_uri=config["neo4j_uri"],
        neo4j_user=config["neo4j_user"],
        neo4j_password=config["neo4j_password"],
        llm_client=llm_client,
    )
    logger.info("Ingestion pipeline ready")

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
        title = session.get("title") or "(untitled)"

        # Get messages
        messages = get_session_messages(conn, session_id)
        if not messages:
            continue

        episodes = session_to_episodes(session, messages)

        # Idempotency: check if ALL episodes already exist in Neo4j
        # If they do, skip this session entirely
        try:
            all_exist = all(check_episode_exists(driver, ep.id) for ep in episodes)
        except Exception as e:
            logger.warning("Idempotency check failed for session %s: %s — processing anyway", session_id, e)
            all_exist = False

        if all_exist:
            stats["sessions_skipped"] += 1
            continue

        # Process session with auto-recovery
        session_done = False
        while not session_done:
            try:
                for ep in episodes:
                    if check_episode_exists(driver, ep.id):
                        stats["episodes_skipped"] += 1
                        continue

                    try:
                        pipe.enqueue(ep)
                        pipe.process_next()

                        # Count facts (non-fatal)
                        try:
                            record = _neo4j_retry(
                                driver,
                                "MATCH (ep:Episode {id: $ep_id})<-[:SOURCED_FROM]-(f:Fact) "
                                "RETURN count(f) AS c",
                                ep_id=ep.id,
                            )
                            fact_count = record["c"] if record else 0
                        except Exception:
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
                session_done = True

            except (neo4j.exceptions.ServiceUnavailable, neo4j.exceptions.TransientError, OSError) as e:
                logger.warning(
                    "Neo4j connection lost during session %s: %s — waiting 10s",
                    session_id, e,
                )
                time.sleep(10)
                try:
                    driver.verify_connectivity()
                    logger.info("Neo4j reconnected, resuming")
                except Exception:
                    logger.warning("Neo4j still down, waiting 30s")
                    time.sleep(30)
                    try:
                        driver.verify_connectivity()
                        logger.info("Neo4j reconnected after extended wait")
                    except Exception as e2:
                        logger.error("Neo4j still down after 40s: %s", e2)
                        raise RuntimeError(f"Neo4j unrecoverable: {e2}")

        # Progress every 50 sessions
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
        "--limit", type=int, default=0,
        help="Max sessions to migrate (0 = all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze without ingesting",
    )

    args = parser.parse_args()
    config = load_env()

    # Acquire lock — ensures we're the only instance
    acquire_lock()

    migrate(
        db_path=args.db,
        config=config,
        limit=args.limit,
        dry_run=args.dry_run,
    )
