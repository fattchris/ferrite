"""Tests for mental models and disposition traits (§3.6, §3.7)."""

from unittest.mock import MagicMock

from ferrite.mental_models import (
    DEFAULT_DISPOSITIONS,
    create_mental_model,
    draft_mental_model,
    flag_stale_mental_models,
    get_disposition,
    get_recall_priority,
    search_mental_models,
    update_mental_model,
)


class TestDisposition:
    def test_default_shared(self):
        d = get_disposition("shared")
        assert d == {"skepticism": 3, "literalism": 3, "empathy": 3}

    def test_default_personal(self):
        d = get_disposition("personal")
        assert d == {"skepticism": 2, "literalism": 4, "empathy": 4}

    def test_unknown_namespace_falls_back(self):
        d = get_disposition("unknown")
        assert d == DEFAULT_DISPOSITIONS["shared"]

    def test_overrides_merge(self):
        overrides = {"shared": {"skepticism": 5}}
        d = get_disposition("shared", overrides)
        assert d["skepticism"] == 5
        assert d["literalism"] == 3  # unchanged


class TestCreateMentalModel:
    def test_create_returns_id(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        model_id = create_mental_model(
            mock_driver,
            title="Spark fleet patterns",
            summary="All spark nodes run GLM-5.2",
            curated_for=["spark-01", "spark-02"],
            namespace="shared",
        )
        assert isinstance(model_id, str)
        assert len(model_id) > 0

    def test_create_with_tags(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        model_id = create_mental_model(
            mock_driver,
            title="Test",
            summary="Test summary",
            curated_for=["spark-01"],
            tags=["infra", "deployment"],
        )
        assert isinstance(model_id, str)


class TestSearchMentalModels:
    def test_search_returns_models(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        # First call (fulltext) succeeds
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            {"id": "m1", "title": "Spark overview", "summary": "test",
             "tags": [], "stale": False, "score": 1.0}
        ]))
        mock_session.run.return_value = mock_result

        results = search_mental_models(mock_driver, "spark")
        assert len(results) == 1
        assert results[0]["id"] == "m1"


class TestUpdateMentalModel:
    def test_update_summary(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        update_mental_model(mock_driver, "m1", summary="Updated text")
        mock_session.run.assert_called()

    def test_update_nothing_returns_early(self):
        mock_driver = MagicMock()
        update_mental_model(mock_driver, "m1")  # no fields
        # Should not call session.run
        mock_driver.session.assert_not_called()


class TestFlagStale:
    def test_flag_stale(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_result = MagicMock()
        mock_result.single.return_value = MagicMock()
        mock_result.single.return_value.__getitem__ = MagicMock(return_value=2)
        mock_session.run.return_value = mock_result

        count = flag_stale_mental_models(mock_driver)
        assert count == 2


class TestDraftMentalModel:
    def test_no_facts_returns_none(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_session.run.return_value = mock_result

        result = draft_mental_model(mock_driver, "nonexistent", MagicMock())
        assert result is None


class TestGetRecallPriority:
    def test_returns_mixed_types(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=None)

        # Mock: mental models fulltext (first call), observations (second),
        # tempr search (multiple calls)
        call_count = [0]
        def mock_run(*args, **kwargs):
            call_count[0] += 1
            mock_r = MagicMock()
            if call_count[0] == 1:
                # Mental models search
                mock_r.__iter__ = MagicMock(return_value=iter([
                    {"id": "m1", "title": "Spark", "summary": "test",
                     "tags": [], "stale": False, "score": 1.0}
                ]))
            elif call_count[0] == 2:
                # Observations search
                mock_r.__iter__ = MagicMock(return_value=iter([
                    {"id": "o1", "summary": "obs", "predicate": "runs_on",
                     "proof_count": 3, "score": 0.8}
                ]))
            else:
                mock_r.__iter__ = MagicMock(return_value=iter([
                    {"id": "f1", "statement": "fact", "score": 0.5,
                     "epistemic_state": "active"}
                ]))
            return mock_r

        mock_session.run = mock_run

        results = get_recall_priority(mock_driver, "spark")
        assert len(results) >= 1
