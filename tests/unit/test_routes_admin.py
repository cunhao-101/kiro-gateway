# -*- coding: utf-8 -*-

"""
Unit tests for read-only admin routes.
"""

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiro import routes_admin
from kiro.routes_admin import router


def make_client():
    app = FastAPI()
    app.include_router(router)

    account = SimpleNamespace(
        id="/root/.aws/sso/cache/kiro-acc1.json",
        auth_manager=object(),
        failures=2,
        last_failure_time=time.time(),
        models_cached_at=123.0,
        stats=SimpleNamespace(
            total_requests=5,
            successful_requests=3,
            failed_requests=2,
            input_tokens=1200,
            output_tokens=300,
            total_tokens=1500,
            credits_used=1.25,
            upstream_cache_read_input_tokens=0,
            upstream_cache_creation_input_tokens=0,
            simulated_cache_read_input_tokens=800,
            simulated_cache_creation_input_tokens=400,
        ),
    )
    manager = SimpleNamespace(
        _accounts={account.id: account},
        _model_to_accounts={
            "claude-sonnet-4.5": SimpleNamespace(accounts=[account.id]),
        },
    )
    app.state.account_manager = manager
    app.state.account_system = True
    return TestClient(app)


def test_admin_page_is_public_shell_only():
    client = make_client()

    response = client.get("/admin")

    assert response.status_code == 200
    assert "Kiro Gateway Admin" in response.text
    assert "PROXY_API_KEY" in response.text


def test_admin_status_requires_api_key(monkeypatch):
    monkeypatch.setattr(routes_admin, "PROXY_API_KEY", "secret")
    client = make_client()

    response = client.get("/admin/api/status")

    assert response.status_code == 401


def test_admin_status_returns_usage_summary(monkeypatch):
    monkeypatch.setattr(routes_admin, "PROXY_API_KEY", "secret")
    client = make_client()

    response = client.get("/admin/api/status", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    data = response.json()
    assert data["account_system"] is True
    assert data["accounts_total"] == 1
    assert data["total_requests"] == 5
    assert data["successful_requests"] == 3
    assert data["failed_requests"] == 2
    assert data["success_rate"] == 60.0
    assert data["accounts_cooling"] == 1
    assert data["accounts_initialized"] == 1
    assert data["models_mapped"] == 1
    assert data["total_tokens"] == 1500
    assert data["credits_used"] == 1.25
    assert data["cost_estimate"]["included_value_usd"] == 0.025
    assert data["cost_estimate"]["overage_cost_usd"] == 0.0
    assert data["simulated_cache_hit_rate"] == 66.7


def test_admin_accounts_are_sanitized(monkeypatch):
    monkeypatch.setattr(routes_admin, "PROXY_API_KEY", "secret")
    client = make_client()

    response = client.get("/admin/api/accounts", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    account = response.json()[0]
    assert account["label"] == "kiro-acc1.json"
    assert account["initialized"] is True
    assert account["status"] == "cooling"
    assert account["models_count"] == 1
    assert account["stats"]["success_rate"] == 60.0
    assert account["stats"]["total_tokens"] == 1500
    assert account["stats"]["credits_used"] == 1.25
    assert account["stats"]["cost_estimate"]["included_value_usd"] == 0.025
    assert account["stats"]["cost_estimate"]["overage_cost_usd"] == 0.0
    assert account["stats"]["simulated_cache_hit_rate"] == 66.7
    assert "access_token" not in account
    assert "refresh_token" not in account


def test_admin_models_returns_sanitized_model_mapping(monkeypatch):
    monkeypatch.setattr(routes_admin, "PROXY_API_KEY", "secret")
    client = make_client()

    response = client.get("/admin/api/models", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    data = response.json()
    assert data == [
        {
            "model": "claude-sonnet-4.5",
            "accounts_count": 1,
            "accounts": ["kiro-acc1.json"],
        }
    ]
