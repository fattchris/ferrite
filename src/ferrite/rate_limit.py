"""Per-key rate limiting via Redis sliding window (§15, F-4 fix).

Token bucket approximation using Redis INCR with TTL.
Separate limits for reads vs writes.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Rate limits (requests per window)
READ_LIMIT = 100      # 100 reads per 10s window
WRITE_LIMIT = 20      # 20 writes per 10s window
WINDOW_SECONDS = 10


def check_rate_limit(
    redis_client,
    key_id: str,
    is_write: bool = False,
    read_limit: int = READ_LIMIT,
    write_limit: int = WRITE_LIMIT,
    window: int = WINDOW_SECONDS,
) -> tuple[bool, Optional[int]]:
    """Check if a request is within rate limits.

    Uses Redis INCR with TTL for a sliding window approximation.
    Returns (allowed, retry_after_seconds).
    """
    if redis_client is None:
        return True, None  # No Redis = no rate limiting (dev mode)

    limit = write_limit if is_write else read_limit
    bucket = f"rl:{key_id}:{'w' if is_write else 'r'}:{int(time.time() // window)}"

    try:
        count = redis_client.incr(bucket)
        if count == 1:
            redis_client.expire(bucket, window)

        if count > limit:
            retry_after = window - int(time.time() % window)
            logger.warning(
                "Rate limit exceeded for key %s (%s): %d/%d",
                key_id, "write" if is_write else "read", count, limit,
            )
            return False, retry_after

        return True, None
    except Exception as e:
        logger.warning("Rate limit check failed (allowing): %s", e)
        return True, None
