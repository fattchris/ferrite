"""Tests for supersession and contradiction temporal logic."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from ferrite.temporal import (
    apply_contradiction,
    apply_supersession,
    detect_contradiction,
    detect_supersession,
)


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    return driver, session


@pytest.fixture
def mock_result():
    """Create a mock query result with configurable records."""
    result = MagicMock()
    return result


class TestDetectSupersession:
    def test_no_existing_fact_returns_none(self, mock_driver):
        driver, session = mock_driver
        result_mock = MagicMock()
        result_mock.__iter__ = MagicMock(return_value=iter([]))
        session.run.return_value = result_mock

        result = detect_supersession(
            driver, "entity-1", "works_at", "acme", "entity", "shared"
        )
        assert result is None

    def test_existing_fact_different_object_triggers_supersession(self, mock_driver):
        driver, session = mock_driver

        # First query: find existing active fact
        fact_result = MagicMock()
        fact_result.__iter__ = MagicMock(
            return_value=iter([{
                "id": "old-fact-id",
                "statement": "alice works_at oldcorp",
                "valid_at": datetime(2024, 1, 1),
                "namespace": "shared",
            }])
        )

        # Second query: get the object of that fact
        obj_result = MagicMock()
        obj_result.single.return_value = {"obj_value": "oldcorp"}

        session.run.side_effect = [fact_result, obj_result]

        result = detect_supersession(
            driver, "entity-1", "works_at", "newcorp", "entity", "shared"
        )

        assert result is not None
        assert result["id"] == "old-fact-id"

    def test_same_object_no_supersession(self, mock_driver):
        driver, session = mock_driver

        fact_result = MagicMock()
        fact_result.__iter__ = MagicMock(
            return_value=iter([{
                "id": "old-fact-id",
                "statement": "alice works_at acme",
                "valid_at": datetime(2024, 1, 1),
                "namespace": "shared",
            }])
        )

        obj_result = MagicMock()
        obj_result.single.return_value = {"obj_value": "acme"}

        session.run.side_effect = [fact_result, obj_result]

        result = detect_supersession(
            driver, "entity-1", "works_at", "acme", "entity", "shared"
        )

        assert result is None


class TestApplySupersession:
    def test_supersession_sets_invalid_at_and_state(self, mock_driver):
        driver, session = mock_driver
        new_valid_at = datetime(2024, 6, 1)

        apply_supersession(driver, "old-fact-id", "new-fact-id", new_valid_at)

        # Verify the Cypher was executed with correct params
        session.run.assert_called_once()
        call_args = session.run.call_args
        assert "old.invalid_at = $new_valid_at" in call_args.args[0]
        assert "old.epistemic_state = 'superseded'" in call_args.args[0]
        assert "SUPERSEDES" in call_args.args[0]
        assert call_args.kwargs["old_fact_id"] == "old-fact-id"
        assert call_args.kwargs["new_fact_id"] == "new-fact-id"

    def test_supersession_does_not_delete_fact(self, mock_driver):
        driver, session = mock_driver

        apply_supersession(driver, "old-fact-id", "new-fact-id", datetime(2024, 6, 1))

        call_args = session.run.call_args
        # Verify no DETACH DELETE or DELETE in the query
        query = call_args.args[0]
        assert "DELETE" not in query.upper()
        assert "DETACH" not in query.upper()


class TestDetectContradiction:
    def test_no_existing_fact_returns_none(self, mock_driver):
        driver, session = mock_driver
        result_mock = MagicMock()
        result_mock.__iter__ = MagicMock(return_value=iter([]))
        session.run.return_value = result_mock

        result = detect_contradiction(
            driver, "entity-1", "works_at", "acme", True, "shared"
        )
        assert result is None

    def test_same_object_with_negation_triggers_contradiction(self, mock_driver):
        driver, session = mock_driver

        fact_result = MagicMock()
        fact_result.__iter__ = MagicMock(
            return_value=iter([{"id": "existing-fact-id"}])
        )

        obj_result = MagicMock()
        obj_result.single.return_value = {"obj_value": "acme"}

        session.run.side_effect = [fact_result, obj_result]

        result = detect_contradiction(
            driver, "entity-1", "works_at", "acme", True, "shared"
        )

        assert result == "existing-fact-id"

    def test_different_object_no_contradiction(self, mock_driver):
        driver, session = mock_driver

        fact_result = MagicMock()
        fact_result.__iter__ = MagicMock(
            return_value=iter([{"id": "existing-fact-id"}])
        )

        obj_result = MagicMock()
        obj_result.single.return_value = {"obj_value": "oldcorp"}

        session.run.side_effect = [fact_result, obj_result]

        result = detect_contradiction(
            driver, "entity-1", "works_at", "acme", True, "shared"
        )

        assert result is None


class TestApplyContradiction:
    def test_contradiction_sets_both_facts_to_contradicted(self, mock_driver):
        driver, session = mock_driver

        apply_contradiction(driver, "existing-fact-id", "new-fact-id")

        session.run.assert_called_once()
        call_args = session.run.call_args
        query = call_args.args[0]
        assert "contradicted" in query
        assert "CONTRADICTS" in query
        assert call_args.kwargs["existing_id"] == "existing-fact-id"
        assert call_args.kwargs["new_id"] == "new-fact-id"

    def test_contradiction_does_not_delete_facts(self, mock_driver):
        driver, session = mock_driver

        apply_contradiction(driver, "existing-fact-id", "new-fact-id")

        query = session.run.call_args.args[0]
        assert "DELETE" not in query.upper()


class TestTemporalRules:
    """Verify the spec's temporal rules are correctly enforced."""

    def test_facts_are_never_deleted_in_supersession(self, mock_driver):
        """Spec: Facts are NEVER deleted. Old Fact gets invalid_at and SUPERSEDES edge."""
        driver, session = mock_driver

        apply_supersession(driver, "old", "new", datetime(2024, 6, 1))

        query = session.run.call_args.args[0]
        assert "DELETE" not in query.upper()
        assert "invalid_at" in query
        assert "superseded" in query
        assert "SUPERSEDES" in query

    def test_facts_are_never_deleted_in_contradiction(self, mock_driver):
        """Spec: both flagged as contradicted, neither deleted."""
        driver, session = mock_driver

        apply_contradiction(driver, "old", "new")

        query = session.run.call_args.args[0]
        assert "DELETE" not in query.upper()
        assert "contradicted" in query
        assert "CONTRADICTS" in query

    def test_supersession_sets_invalid_at_to_new_valid_at(self, mock_driver):
        """Spec: old gets invalid_at = new.valid_at"""
        driver, session = mock_driver
        new_valid_at = datetime(2024, 7, 15)

        apply_supersession(driver, "old", "new", new_valid_at)

        call_args = session.run.call_args
        assert "old.invalid_at = $new_valid_at" in call_args.args[0]
        assert call_args.kwargs["new_valid_at"] is not None
