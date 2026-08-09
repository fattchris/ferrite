"""Tests for observability: health monitoring, alerting, metrics."""

import json
from unittest.mock import MagicMock

from ferrite.metrics import MetricsCollector, get_metrics
from ferrite.observability import AlertManager, HealthMonitor


class TestHealthMonitor:
    def test_check_neo4j_ok(self):
        """Neo4j health check passes when RETURN 1 works."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value=1)
        mock_session.run.return_value.single.return_value = mock_record

        monitor = HealthMonitor(mock_driver)
        result = monitor.check_neo4j()
        assert result["status"] == "healthy"

    def test_check_neo4j_down(self):
        """Neo4j health check fails when driver throws."""
        mock_driver = MagicMock()
        mock_driver.session.side_effect = Exception("Connection refused")

        monitor = HealthMonitor(mock_driver)
        result = monitor.check_neo4j()
        assert result["status"] == "critical"

    def test_check_redis_ok(self):
        """Redis health check passes when PING returns True."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        monitor = HealthMonitor(MagicMock(), mock_redis)
        result = monitor.check_redis()
        assert result["status"] == "healthy"

    def test_check_redis_down(self):
        """Redis health check fails when ping throws."""
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = Exception("Connection refused")

        monitor = HealthMonitor(MagicMock(), mock_redis)
        result = monitor.check_redis()
        assert result["status"] == "critical"

    def test_check_queue_depth(self):
        """Queue depth check returns healthy when below threshold."""
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 5

        monitor = HealthMonitor(MagicMock(), mock_redis)
        result = monitor.check_queue_depth()
        assert result["status"] == "healthy"
        assert "5" in result["detail"]

    def test_check_queue_depth_warning(self):
        """Queue depth check returns warning when above threshold."""
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 1500

        monitor = HealthMonitor(MagicMock(), mock_redis)
        result = monitor.check_queue_depth()
        assert result["status"] == "warning"

    def test_check_contradictions(self):
        """Contradiction check with no contradictions is healthy."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value=0)
        mock_session.run.return_value.single.return_value = mock_record

        monitor = HealthMonitor(mock_driver)
        result = monitor.check_contradictions()
        assert result["status"] == "healthy"

    def test_run_all(self):
        """run_all returns dict with overall status and all checks."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        # Mock all queries to return healthy values
        def mock_run(query, **kwargs):
            mock_result = MagicMock()
            mock_record = MagicMock()
            mock_record.__getitem__ = MagicMock(return_value=1)
            mock_result.single.return_value = mock_record
            return mock_result

        mock_session.run = mock_run

        monitor = HealthMonitor(mock_driver, MagicMock())
        result = monitor.run_all()
        assert "overall" in result
        assert "checks" in result
        assert "timestamp" in result
        assert "neo4j" in result["checks"]


class TestAlertManager:
    def test_alert_sent(self, tmp_path):
        """First alert is sent successfully."""
        log_file = str(tmp_path / "health.log")
        am = AlertManager(log_file=log_file)
        sent = am.alert("WARNING", "neo4j", "Neo4j slow")
        assert sent is True

        # Verify log file written
        with open(log_file) as f:
            entry = json.loads(f.readline())
            assert entry["severity"] == "WARNING"
            assert entry["category"] == "neo4j"

    def test_alert_dedup(self, tmp_path):
        """Same alert within 5 minutes is deduped."""
        log_file = str(tmp_path / "health.log")
        am = AlertManager(log_file=log_file)

        # First alert goes through
        assert am.alert("CRITICAL", "redis", "Redis down") is True
        # Second same alert is deduped
        assert am.alert("CRITICAL", "redis", "Redis down") is False

    def test_different_categories_not_deduped(self, tmp_path):
        """Different alert categories are not deduped."""
        log_file = str(tmp_path / "health.log")
        am = AlertManager(log_file=log_file)

        assert am.alert("WARNING", "neo4j", "Neo4j slow") is True
        assert am.alert("CRITICAL", "redis", "Redis down") is True


class TestMetricsCollector:
    def test_increment(self):
        """Counter increments correctly."""
        m = MetricsCollector()
        m.increment("queries")
        m.increment("queries")
        m.increment("errors", 5)

        snap = m.snapshot()
        assert snap["counters"]["queries"] == 2
        assert snap["counters"]["errors"] == 5

    def test_gauge(self):
        """Gauge sets the value."""
        m = MetricsCollector()
        m.gauge("queue_depth", 42)
        m.gauge("queue_depth", 10)  # overwrites

        snap = m.snapshot()
        assert snap["gauges"]["queue_depth"] == 10

    def test_observe(self):
        """Histogram tracks observations."""
        m = MetricsCollector()
        m.observe("latency_ms", 100)
        m.observe("latency_ms", 200)
        m.observe("latency_ms", 300)

        snap = m.snapshot()
        hist = snap["histograms"]["latency_ms"]
        assert hist["count"] == 3
        assert hist["min"] == 100
        assert hist["max"] == 300
        assert abs(hist["avg"] - 200) < 1

    def test_reset(self):
        """Reset clears all metrics."""
        m = MetricsCollector()
        m.increment("test")
        m.observe("test", 1)
        m.gauge("test", 1)

        m.reset()
        snap = m.snapshot()
        assert len(snap["counters"]) == 0
        assert len(snap["histograms"]) == 0
        assert len(snap["gauges"]) == 0

    def test_get_metrics_singleton(self):
        """get_metrics returns the same instance."""
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2
