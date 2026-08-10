"""Health monitoring, alerting, and observability for Ferrite.

Per spec §8:
- Circuit breaker (in memory provider plugin, not here)
- Health monitoring: Neo4j, Redis, queue depth, ingestion, memory, predicates
- Alerting: log file + webhook, dedup within 5 minutes
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Status constants
HEALTHY = "healthy"
WARNING = "warning"
CRITICAL = "critical"


class HealthMonitor:
    """Run health checks against all Ferrite dependencies."""

    def __init__(self, driver, redis_client=None) -> None:
        self.driver = driver
        self.redis = redis_client

    def check_neo4j(self) -> dict[str, Any]:
        """Check Neo4j connectivity. RETURN 1, 5s timeout."""
        try:
            with self.driver.session() as s:
                result = s.run("RETURN 1 AS ok")
                record = result.single()
                if record and record["ok"] == 1:
                    return {"status": HEALTHY, "detail": "Neo4j responding"}
        except Exception as e:
            return {"status": CRITICAL, "detail": f"Neo4j down: {e}"}
        return {"status": CRITICAL, "detail": "Neo4j: unexpected response"}

    def check_redis(self) -> dict[str, Any]:
        """Check Redis connectivity. PING, 2s timeout."""
        if self.redis is None:
            return {"status": WARNING, "detail": "Redis client not configured"}
        try:
            if self.redis.ping():
                return {"status": HEALTHY, "detail": "Redis responding"}
        except Exception as e:
            return {"status": CRITICAL, "detail": f"Redis down: {e}"}
        return {"status": CRITICAL, "detail": "Redis: unexpected response"}

    def check_queue_depth(self, threshold: int = 1000) -> dict[str, Any]:
        """Check Redis queue depth."""
        if self.redis is None:
            return {"status": WARNING, "detail": "Redis client not configured"}
        try:
            from .ingestion import QUEUE_KEY
            depth = self.redis.llen(QUEUE_KEY)
            if depth < threshold:
                return {"status": HEALTHY, "detail": f"Queue depth: {depth}"}
            return {"status": WARNING, "detail": f"Queue depth {depth} >= {threshold}"}
        except Exception as e:
            return {"status": CRITICAL, "detail": f"Queue check failed: {e}"}

    def check_ingestion(self, max_age_minutes: int = 5) -> dict[str, Any]:
        """Check last ingestion time."""
        try:
            threshold = datetime.now() - timedelta(minutes=max_age_minutes)
            with self.driver.session() as s:
                result = s.run(
                    "MATCH (ep:Episode) "
                    "RETURN max(ep.recorded_at) AS last_ingest, count(ep) AS total"
                )
                record = result.single()
                if not record or record["total"] == 0:
                    return {"status": WARNING, "detail": "No episodes ingested yet"}
                last_ingest = record["last_ingest"]
                # Compare last ingest against the staleness threshold
                if last_ingest and str(last_ingest) < threshold.isoformat():
                    return {
                        "status": WARNING,
                        "detail": f"Last ingest {last_ingest} older than {max_age_minutes}min",
                    }
                return {
                    "status": HEALTHY,
                    "detail": f"Last ingest: {last_ingest}, total: {record['total']}",
                }
        except Exception as e:
            return {"status": CRITICAL, "detail": f"Ingestion check failed: {e}"}

    def check_predicate_usage(self) -> dict[str, Any]:
        """Check 'other' predicate usage rate. Alert at 15%."""
        try:
            with self.driver.session() as s:
                total = s.run("MATCH (f:Fact) RETURN count(f) AS c").single()
                other = s.run(
                    "MATCH (f:Fact {predicate: 'other'}) RETURN count(f) AS c"
                ).single()
                total_count = total["c"] if total else 0
                other_count = other["c"] if other else 0
                if total_count == 0:
                    return {"status": HEALTHY, "detail": "No facts yet"}
                rate = other_count / total_count
                if rate > 0.15:
                    return {
                        "status": WARNING,
                        "detail": f"'other' predicate rate: {rate:.1%} > 15%",
                    }
                return {"status": HEALTHY, "detail": f"'other' predicate rate: {rate:.1%}"}
        except Exception as e:
            return {"status": CRITICAL, "detail": f"Predicate check failed: {e}"}

    def check_contradictions(self) -> dict[str, Any]:
        """Check for contradicted facts."""
        try:
            with self.driver.session() as s:
                result = s.run(
                    "MATCH (f:Fact {epistemic_state: 'contradicted'}) "
                    "RETURN count(f) AS c"
                )
                record = result.single()
                count = record["c"] if record else 0
                if count > 0:
                    return {
                        "status": WARNING,
                        "detail": f"{count} contradicted facts detected",
                    }
                return {"status": HEALTHY, "detail": "No contradictions"}
        except Exception as e:
            return {"status": CRITICAL, "detail": f"Contradiction check failed: {e}"}

    def run_all(self) -> dict[str, Any]:
        """Run all health checks and return a summary."""
        checks = {
            "neo4j": self.check_neo4j(),
            "redis": self.check_redis(),
            "queue_depth": self.check_queue_depth(),
            "ingestion": self.check_ingestion(),
            "predicate_usage": self.check_predicate_usage(),
            "contradictions": self.check_contradictions(),
        }
        # Determine overall status
        statuses = [c["status"] for c in checks.values()]
        if CRITICAL in statuses:
            overall = CRITICAL
        elif WARNING in statuses:
            overall = WARNING
        else:
            overall = HEALTHY
        return {
            "overall": overall,
            "timestamp": datetime.now().isoformat(),
            "checks": checks,
        }


class AlertManager:
    """Send and deduplicate alerts."""

    DEDUP_WINDOW = 300  # 5 minutes

    def __init__(
        self,
        log_file: str = "/tmp/ferrite/health.log",
        webhook_url: Optional[str] = None,
    ) -> None:
        self.log_file = log_file
        self.webhook_url = webhook_url
        self._recent: dict[str, float] = {}  # category -> last_alert_time

    def alert(self, severity: str, category: str, message: str) -> bool:
        """Send an alert. Returns True if sent, False if deduped."""
        now = time.time()
        last = self._recent.get(category)
        if last is not None and (now - last) < self.DEDUP_WINDOW:
            return False  # Deduped

        self._recent[category] = now

        # Write to log file
        entry = json.dumps({
            "timestamp": datetime.now().isoformat(),
            "severity": severity,
            "category": category,
            "message": message,
        })
        try:
            import os
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            with open(self.log_file, "a") as f:
                f.write(entry + "\n")
        except Exception as e:
            logger.error("Failed to write alert log: %s", e)

        # Send webhook if configured
        if self.webhook_url:
            try:
                data = json.dumps({
                    "severity": severity,
                    "category": category,
                    "message": message,
                    "timestamp": datetime.now().isoformat(),
                }).encode()
                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                logger.error("Webhook alert failed: %s", e)

        return True
