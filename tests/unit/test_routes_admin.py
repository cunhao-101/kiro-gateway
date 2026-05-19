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


def test_admin_accounts_are_sanitized(monkeypatch):
    monkeypatch.setattr(routes_admin, "PROXY_API_KEY", "secret")
    client = make_client()

    response = client.get("/admin/api/accounts", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    account = response.json()[0]
    assert account["label"] == "kiro-acc1.json"
    assert account["initialized"] is True
    assert account["models_count"] == 1
    assert "access_token" not in account
    assert "refresh_token" not in account
