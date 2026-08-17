"""Replay the Ferrite dead-letter queue through the ingestion pipeline.

Standalone, idempotent. The DLQ holds episode payloads whose episodes
may no longer exist in the episode hash store, so each entry is
re-registered (hset) and re-enqueued (lpush) — exactly the path a fresh
/store takes. Entries that fail extraction again will cycle through
failed_queue retries and re-dead-letter; that is expected.

Run inside the API container:
  docker exec ferrite-api python /app/scripts/replay_dlq.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import redis

from ferrite.config import get_settings
from ferrite.ingestion import DEAD_LETTER_KEY, EPISODE_KEY_PREFIX, QUEUE_KEY
from ferrite.models import Episode

settings = get_settings()
r = redis.from_url(settings.REDIS_URL)

depth = r.llen(DEAD_LETTER_KEY)
print(f"DLQ depth: {depth}")
if depth == 0:
    sys.exit(0)

requeued = 0
batch = r.lrange(DEAD_LETTER_KEY, 0, -1)
r.delete(DEAD_LETTER_KEY)
for raw in batch:
    entry = json.loads(raw)
    ep = Episode(
        id=entry["episode_id"],
        content=entry.get("content", ""),
        content_type=entry.get("content_type", "text"),
        source=entry.get("source", {}),
        namespace=entry.get("namespace", "shared"),
    )
    r.hset(f"{EPISODE_KEY_PREFIX}{ep.id}", "data", ep.model_dump_json())
    r.lpush(QUEUE_KEY, ep.id)
    requeued += 1

print(f"Re-enqueued: {requeued}")
