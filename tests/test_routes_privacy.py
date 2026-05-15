"""
Tests for /privacy/policy, /privacy/route, /privacy/scan.
"""

import pytest
from unittest.mock import patch

from app.privacy.taxonomy import PrivacyLevel, RoutingDecision


# ---------------------------------------------------------------------------
# GET /privacy/policy
# ---------------------------------------------------------------------------

def test_get_policy_returns_defaults(client):
    resp = client.get("/privacy/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_external_call"] == "deny"
    assert data["raw_personal_data_online"] is False
    assert data["audit_all_online_calls"] is True


# ---------------------------------------------------------------------------
# PATCH /privacy/policy
# ---------------------------------------------------------------------------

def test_patch_policy_updates_field(client):
    resp = client.patch("/privacy/policy", json={"audit_all_online_calls": False})
    assert resp.status_code == 200
    assert resp.json()["audit_all_online_calls"] is False
    # Restore
    client.patch("/privacy/policy", json={"audit_all_online_calls": True})


def test_patch_policy_empty_body_returns_400(client):
    resp = client.patch("/privacy/policy", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /privacy/policy/reset
# ---------------------------------------------------------------------------

def test_reset_policy_restores_defaults(client):
    client.patch("/privacy/policy", json={"raw_personal_data_online": True})
    resp = client.post("/privacy/policy/reset")
    assert resp.status_code == 200
    assert resp.json()["raw_personal_data_online"] is False


# ---------------------------------------------------------------------------
# GET /privacy/route
# ---------------------------------------------------------------------------

def test_preview_route_returns_routing(client):
    with patch("app.routes.privacy.classify", return_value=(PrivacyLevel.PUBLIC, 0.9)):
        resp = client.get("/privacy/route", params={"query": "What is Python?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["privacy_level"] == "PUBLIC"
    assert data["routing"] == "guarded-online"
    assert data["confidence"] == pytest.approx(0.9, abs=0.01)


def test_preview_route_secret_returns_blocked(client):
    with patch("app.routes.privacy.classify", return_value=(PrivacyLevel.SECRET, 0.99)):
        resp = client.get("/privacy/route", params={"query": "password: hunter2"})
    assert resp.status_code == 200
    assert resp.json()["routing"] == "blocked"


# ---------------------------------------------------------------------------
# POST /privacy/scan
# ---------------------------------------------------------------------------

def test_scan_clean_text(client):
    resp = client.post("/privacy/scan", json={"text": "Python is great for data science."})
    assert resp.status_code == 200
    assert resp.json()["has_pii"] is False


def test_scan_detects_password(client):
    resp = client.post("/privacy/scan", json={"text": "password: hunter2"})
    assert resp.status_code == 200
    assert resp.json()["has_pii"] is True


def test_scan_detects_api_key(client):
    resp = client.post("/privacy/scan", json={"text": "Use sk-" + "a" * 48})
    assert resp.status_code == 200
    assert resp.json()["has_pii"] is True
