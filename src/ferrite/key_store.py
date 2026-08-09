"""SQLite-backed API key management (§6.1, §6.2).

Keys live in keys.db on the API container's volume.
Single writer (API process), trivial scale.
"""

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = os.environ.get(
    "FERRITE_KEYS_DB",
    str(Path.home() / "ferrite" / "data" / "keys.db"),
)


def _get_db_path() -> str:
    """Get DB path, creating parent dirs."""
    path = os.environ.get("FERRITE_KEYS_DB", _DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


def init_db(db_path: Optional[str] = None) -> None:
    """Create the api_keys table if it doesn't exist."""
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id       TEXT PRIMARY KEY,
                agent_name   TEXT NOT NULL,
                key_hash     TEXT NOT NULL,
                scopes       TEXT NOT NULL DEFAULT '["read","write"]',
                namespaces   TEXT NOT NULL DEFAULT '["shared"]',
                active       INTEGER DEFAULT 1,
                created_at   TEXT DEFAULT (datetime('now')),
                revoked_at   TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_key_hash ON api_keys(key_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_active ON api_keys(active)"
        )
        conn.commit()
    finally:
        conn.close()


def _hash_token(token: str) -> str:
    """SHA-256 hash of bearer token."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_key(
    agent_name: str,
    scopes: list[str] | None = None,
    namespaces: list[str] | None = None,
    db_path: Optional[str] = None,
) -> dict:
    """Create a new API key. Returns {key_id, token, agent_name, scopes, namespaces}.

    The token is returned ONCE — only the hash is stored.
    """
    init_db(db_path)
    path = db_path or _get_db_path()
    key_id = str(uuid.uuid4())
    token = f"ferrite_{key_id.replace('-', '')}_{uuid.uuid4().hex[:16]}"
    key_hash = _hash_token(token)

    scopes = scopes or ["read", "write"]
    namespaces = namespaces or ["shared"]

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO api_keys (key_id, agent_name, key_hash, scopes, namespaces)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                key_id,
                agent_name,
                key_hash,
                json.dumps(scopes),
                json.dumps(namespaces),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Created API key for agent '{agent_name}' (id={key_id})")
    return {
        "key_id": key_id,
        "token": token,
        "agent_name": agent_name,
        "scopes": scopes,
        "namespaces": namespaces,
    }


def revoke_key(key_id: str, db_path: Optional[str] = None) -> bool:
    """Revoke a key by setting active=0 and revoked_at=now()."""
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute(
            """
            UPDATE api_keys
            SET active = 0, revoked_at = datetime('now')
            WHERE key_id = ? AND active = 1
            """,
            (key_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_keys(active_only: bool = True, db_path: Optional[str] = None) -> list[dict]:
    """List all keys with status."""
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        if active_only:
            cur = conn.execute(
                "SELECT * FROM api_keys WHERE active = 1 ORDER BY created_at DESC"
            )
        else:
            cur = conn.execute(
                "SELECT * FROM api_keys ORDER BY created_at DESC"
            )
        rows = cur.fetchall()
        return [
            {
                "key_id": r["key_id"],
                "agent_name": r["agent_name"],
                "scopes": json.loads(r["scopes"]),
                "namespaces": json.loads(r["namespaces"]),
                "active": bool(r["active"]),
                "created_at": r["created_at"],
                "revoked_at": r["revoked_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def validate_token(token: str, db_path: Optional[str] = None) -> dict | None:
    """Validate a bearer token. Returns key info if valid, None if invalid.

    Returns: {key_id, agent_name, scopes, namespaces} or None.
    """
    if not token:
        return None

    # Check if it's the env-based admin key (backward compat)
    env_key = os.environ.get("FERRITE_API_KEY", "")
    if env_key and token == env_key:
        return {
            "key_id": "env-admin",
            "agent_name": "admin",
            "scopes": ["read", "write", "ingest", "admin"],
            "namespaces": ["shared", "personal", "e2e-test"],
        }

    path = db_path or _get_db_path()
    if not os.path.exists(path):
        return None

    key_hash = _hash_token(token)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT key_id, agent_name, scopes, namespaces
            FROM api_keys
            WHERE key_hash = ? AND active = 1
            """,
            (key_hash,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "key_id": row["key_id"],
            "agent_name": row["agent_name"],
            "scopes": json.loads(row["scopes"]),
            "namespaces": json.loads(row["namespaces"]),
        }
    finally:
        conn.close()


def has_scope(key_info: dict | None, scope: str) -> bool:
    """Check if a validated key has a given scope."""
    if key_info is None:
        return False
    return scope in key_info.get("scopes", [])


def has_namespace_access(key_info: dict | None, namespace: str) -> bool:
    """Check if a validated key can access a namespace."""
    if key_info is None:
        return False
    return namespace in key_info.get("namespaces", [])
