"""In-memory metrics collection for Ferrite.

Per spec §8: no external dependency (no Prometheus). Simple in-memory
counters, histograms, and gauges with optional log output.

Tracked metrics: ingestion_count, extraction_errors, query_count,
query_latency_ms, queue_depth, facts_total, entities_total,
circuit_breaker_state.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Thread-safe in-memory metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._gauges: dict[str, float] = {}

    def increment(self, name: str, value: float = 1, tags: dict | None = None) -> None:
        """Increment a counter."""
        key = self._tag_key(name, tags)
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, value: float, tags: dict | None = None) -> None:
        """Record a histogram observation."""
        key = self._tag_key(name, tags)
        with self._lock:
            self._histograms[key].append(value)
            # Keep last 1000 observations to bound memory
            if len(self._histograms[key]) > 1000:
                self._histograms[key] = self._histograms[key][-500:]

    def gauge(self, name: str, value: float, tags: dict | None = None) -> None:
        """Set a gauge value."""
        key = self._tag_key(name, tags)
        with self._lock:
            self._gauges[key] = value

    def snapshot(self) -> dict[str, Any]:
        """Return all metrics as a dict."""
        with self._lock:
            counters = dict(self._counters)
            histograms = {
                k: {
                    "count": len(v),
                    "min": min(v) if v else 0,
                    "max": max(v) if v else 0,
                    "avg": sum(v) / len(v) if v else 0,
                    "last": v[-1] if v else 0,
                }
                for k, v in self._histograms.items()
            }
            gauges = dict(self._gauges)
        return {
            "counters": counters,
            "histograms": histograms,
            "gauges": gauges,
        }

    def reset(self) -> None:
        """Reset all metrics. Mainly for testing."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._gauges.clear()

    @staticmethod
    def _tag_key(name: str, tags: dict | None) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


# Global singleton
_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """Get the global MetricsCollector singleton."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
