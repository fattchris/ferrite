"""Tests for API auth + circuit breaker integration."""

import os
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ferrite.api import create_app


class TestAuthDisabled:
    """When FERRITE_API_KEY is not set, auth is disabled."""

    def setup_method(self):
        os.environ.pop("FERRITE_API_KEY", None)

    def test_health_endpoint_no_auth(self):
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200


class TestAuthEnabled:
    """When FERRITE_API_KEY is set, all non-public endpoints require auth."""

    def setup_method(self):
        os.environ["FERRITE_API_KEY"] = "test-secret-key"

    def teardown_method(self):
        os.environ.pop("FERRITE_API_KEY", None)

    def test_public_endpoints_no_auth(self):
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200

        r = client.get("/metrics")
        assert r.status_code == 200

    def test_protected_endpoint_no_auth(self):
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.get("/search?query=test")
        assert r.status_code == 401

    def test_protected_endpoint_with_bearer(self):
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.get(
            "/search?query=test",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert r.status_code != 401

    def test_protected_endpoint_with_x_api_key(self):
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.get(
            "/search?query=test",
            headers={"X-API-Key": "test-secret-key"},
        )
        assert r.status_code != 401

    def test_protected_endpoint_wrong_key(self):
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.get(
            "/search?query=test",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401


class TestCircuitBreakerEndpoint:
    def test_get_circuit_breaker_state(self):
        os.environ.pop("FERRITE_API_KEY", None)
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.get("/circuit-breaker")
        assert r.status_code == 200
        data = r.json()
        assert data["state"] == "closed"

    def test_reset_circuit_breaker(self):
        os.environ.pop("FERRITE_API_KEY", None)
        app = create_app(pipeline=MagicMock())
        client = TestClient(app)
        r = client.post("/circuit-breaker/reset")
        assert r.status_code == 200
        assert r.json()["state"] == "closed"
