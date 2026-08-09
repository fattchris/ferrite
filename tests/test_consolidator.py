"""Tests for observation consolidation (§3.5, A6/A7)."""

from unittest.mock import MagicMock

from ferrite.consolidator import (
    GROUP_CAP,
    _get_group_facts,
    _group_key,
    consolidate_group,
    consolidate_pending,
    dequeue_consolidation,
    enqueue_consolidation,
    get_pending_groups,
)


class TestGroupKey:
    def test_format(self):
        key = _group_key("spark-01", "runs_on", "shared")
        assert key == "spark-01|runs_on|shared"

    def test_different_entities_different_keys(self):
        k1 = _group_key("spark-01", "runs_on", "shared")
        k2 = _group_key("spark-02", "runs_on", "shared")
        assert k1 != k2

    def test_different_predicates_different_keys(self):
        k1 = _group_key("spark-01", "runs_on", "shared")
        k2 = _group_key("spark-01", "version_is", "shared")
        assert k1 != k2

    def test_different_namespaces_different_keys(self):
        k1 = _group_key("spark-01", "runs_on", "shared")
        k2 = _group_key("spark-01", "runs_on", "personal")
        assert k1 != k2


class TestRedisOperations:
    def test_enqueue(self):
        mock_redis = MagicMock()
        enqueue_consolidation(mock_redis, "test|key|shared")
        mock_redis.sadd.assert_called_once_with("pending_consolidation", "test|key|shared")

    def test_dequeue(self):
        mock_redis = MagicMock()
        dequeue_consolidation(mock_redis, "test|key|shared")
        mock_redis.srem.assert_called_once_with("pending_consolidation", "test|key|shared")

    def test_get_pending(self):
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {b"a|b|c", b"d|e|f"}
        result = get_pending_groups(mock_redis)
        assert result == {"a|b|c", "d|e|f"}

    def test_get_pending_none_redis(self):
        assert get_pending_groups(None) == set()

    def test_get_pending_redis_error(self):
        mock_redis = MagicMock()
        mock_redis.smembers.side_effect = Exception("Connection lost")
        assert get_pending_groups(mock_redis) == set()


class TestGetGroupFacts:
    def test_returns_facts(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            {"id": "f1", "statement": "spark-01 runs_on glm-5.2", "predicate": "runs_on"},
        ]))
        mock_session.run.return_value = mock_result

        facts = _get_group_facts(mock_driver, "spark-01", "runs_on", "shared")
        assert len(facts) == 1

    def test_group_cap_enforced(self):
        """GROUP_CAP is 20 per spec."""
        assert GROUP_CAP == 20


class TestConsolidateGroup:
    def test_no_facts_returns_none(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_session.run.return_value = mock_result

        result = consolidate_group(
            mock_driver, "spark-01", "runs_on", "shared",
            MagicMock(), None
        )
        assert result is None


class TestConsolidatePending:
    def test_empty_queue(self):
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = set()
        assert consolidate_pending(MagicMock(), MagicMock(), mock_redis) == 0

    def test_no_redis(self):
        """Without Redis, consolidate_pending is a no-op."""
        assert consolidate_pending(MagicMock(), MagicMock(), None) == 0
