import os
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import BASE, login, token_body
from fastapi.testclient import TestClient

from app import COOKIE, STATE_COOKIE, create_app
from settings import Settings
from store import Store


def test_unconfigured_health_and_safe_ui():
    with TestClient(create_app(Settings()), base_url=BASE) as c:
        assert c.get("/health").json() == {"ok": True, "integration": "not_configured"}
        assert c.get("/ready").status_code == 503
        assert c.get("/auth/tiktok/start").status_code == 503
        assert c.get("/").status_code == 200
        assert c.get("/assets/studio.js").status_code == 200


@pytest.mark.parametrize(
    "field,value",
    [
        ("public_base_url", "http://backend.example.test"),
        ("redirect_uri", "https://other.example.test/auth/tiktok/callback"),
        ("redirect_uri", BASE + "/wrong"),
        ("redirect_uri", BASE + "/auth/tiktok/callback?x=1"),
        ("database_path", "tokens.sqlite3"),
    ],
)
def test_invalid_production_config(settings, field, value):
    with pytest.raises(ValueError):
        create_app(replace(settings, **{field: value}))


def test_oauth_state_and_secure_cookie(client, provider):
    r = client.get("/auth/tiktok/start")
    params = parse_qs(urlsplit(r.headers["location"]).query)
    assert set(params["scope"][0].split(",")) == {
        "user.info.basic",
        "video.publish",
        "video.upload",
    }
    assert params["redirect_uri"] == [BASE + "/auth/tiktok/callback"]
    assert all(
        v in r.headers["set-cookie"]
        for v in ("Secure", "HttpOnly", "SameSite=lax", "Path=/")
    )
    state = params["state"][0]
    assert (
        client.get(
            "/auth/tiktok/callback", params={"state": "bad", "code": "x"}
        ).status_code
        == 400
    )
    client.cookies.set(STATE_COOKIE, state, domain="backend.example.test", path="/")
    r = client.get("/auth/tiktok/callback", params={"state": state, "code": "x"})
    assert r.status_code == 303
    assert all(
        v in r.headers["set-cookie"] for v in ("Secure", "HttpOnly", "SameSite=lax")
    )
    client.cookies.set(STATE_COOKIE, state, domain="backend.example.test", path="/")
    assert (
        client.get(
            "/auth/tiktok/callback", params={"state": state, "code": "x"}
        ).status_code
        == 400
    )
    assert len([r for r in provider.requests if r.url.path == "/v2/oauth/token/"]) == 1


@pytest.mark.parametrize("params", [{"error": "access_denied"}, {}, {"code": ""}])
def test_oauth_declined_or_missing_code_consumes_state(client, params):
    r = client.get("/auth/tiktok/start")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    assert (
        client.get(
            "/auth/tiktok/callback", params={"state": state, **params}
        ).status_code
        == 400
    )
    assert not client.app.state.store.consume_state(state)


def test_expired_state(client):
    r = client.get("/auth/tiktok/start")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    with client.app.state.store.db() as db:
        db.execute("UPDATE oauth_states SET expires=0")
    assert (
        client.get(
            "/auth/tiktok/callback", params={"state": state, "code": "x"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(200, json={"access_token": "bad"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["not an object"]),
        httpx.Response(429, json={}),
    ],
)
def test_exchange_failure_does_not_create_session(client, provider, response):
    provider.overrides["/v2/oauth/token/"] = response
    r = client.get("/auth/tiktok/start")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = client.get("/auth/tiktok/callback", params={"state": state, "code": "x"})
    assert r.status_code in (401, 502, 429)
    assert COOKIE not in client.cookies and STATE_COOKIE not in client.cookies
    assert "access_token" not in r.text


def test_encrypted_at_rest_and_survives_reopen(client, auth, settings):
    store = client.app.state.store
    session = store.session(client.cookies.get(COOKIE))
    tokens = store.tokens(session["account"])
    with open(settings.database_path, "rb") as database:
        raw = database.read()
    assert b"test-access-token" not in raw and b"test-refresh-token" not in raw
    assert client.cookies.get(COOKIE).encode() not in raw
    assert os.stat(settings.database_path).st_mode & 0o777 == 0o600
    reopened = Store(settings.database_path, settings.encryption_key)
    assert reopened.tokens(session["account"]) == tokens
    assert reopened.session(client.cookies.get(COOKIE)) == session


def test_access_expiry_rotates_and_persists_tokens(client, auth, provider):
    store = client.app.state.store
    account = store.session(client.cookies.get(COOKIE))["account"]
    tokens = store.tokens(account)
    tokens["expires_at"] = 0
    store.save_tokens(account, tokens)
    provider.tokens = token_body()
    provider.tokens["access_token"] = "rotated-access"
    provider.tokens["refresh_token"] = "rotated-refresh"
    assert client.get("/api/creator").status_code == 200
    assert store.tokens(account)["refresh_token"] == "rotated-refresh"
    refresh = [r for r in provider.requests if r.url.path == "/v2/oauth/token/"][-1]
    assert parse_qs(refresh.content.decode())["grant_type"] == ["refresh_token"]
    assert client.get("/api/creator").status_code == 200
    assert len([r for r in provider.requests if r.url.path == "/v2/oauth/token/"]) == 2


def test_expired_refresh_requires_reconnection(client, auth):
    store = client.app.state.store
    account = store.session(client.cookies.get(COOKIE))["account"]
    tokens = store.tokens(account)
    tokens.update(expires_at=0, refresh_expires_at=0)
    store.save_tokens(account, tokens)
    assert client.get("/api/creator").status_code == 401
    assert store.tokens(account) is None
    assert client.get("/api/session").status_code == 401


def test_refresh_account_mismatch_not_saved(client, auth, provider):
    provider.tokens = token_body(account="attacker-account")
    assert client.post("/api/token/refresh", headers=auth).status_code == 502
    assert (
        client.app.state.store.tokens("test-account")["access_token"]
        == "test-access-token"
    )


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://evil.test"}, {"Origin": BASE, "X-CSRF-Token": "wrong"}],
)
def test_csrf_on_mutation(client, auth, headers):
    assert client.post("/api/token/refresh", headers=headers).status_code == 403
    assert client.post("/api/logout", headers=headers).status_code == 403
    assert client.post("/api/disconnect", headers=headers).status_code == 403


def test_refresh_logout_and_disconnect(client, auth, provider):
    assert client.post("/api/token/refresh", headers=auth).json() == {"refreshed": True}
    assert client.post("/api/logout", headers=auth).status_code == 200
    assert client.get("/api/session").status_code == 401
    auth = login(client)
    assert client.post("/api/disconnect", headers=auth).json() == {"disconnected": True}
    assert client.app.state.store.tokens("test-account") is None
    assert client.get("/api/session").status_code == 401
    assert any(r.url.path == "/v2/oauth/revoke/" for r in provider.requests)


def test_revocation_failure_preserves_retry_credentials(client, auth, provider):
    provider.overrides["/v2/oauth/revoke/"] = httpx.Response(500, text="failure")
    assert client.post("/api/disconnect", headers=auth).status_code == 502
    assert client.app.state.store.tokens("test-account") is not None


def test_session_expiry(client, auth):
    with client.app.state.store.db() as db:
        db.execute("UPDATE sessions SET expires=0")
    assert client.get("/api/session").status_code == 401


def test_empty_successful_revocation_body(client, auth, provider):
    provider.overrides["/v2/oauth/revoke/"] = httpx.Response(200, content=b"")
    assert client.post("/api/disconnect", headers=auth).status_code == 200
    assert client.app.state.store.tokens("test-account") is None


def test_network_failure_removes_state_cookie(client, provider):
    def fail(request):
        raise httpx.ConnectTimeout("test transport timeout")

    provider.overrides["/v2/oauth/token/"] = fail
    start = client.get("/auth/tiktok/start")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = client.get("/auth/tiktok/callback", params={"state": state, "code": "x"})
    assert response.status_code == 502
    assert STATE_COOKIE not in client.cookies


def test_profile_for_inbox_only_authorization(client, auth):
    store = client.app.state.store
    tokens = store.tokens("test-account")
    tokens["scope"] = "user.info.basic,video.upload"
    store.save_tokens("test-account", tokens)
    assert client.get("/api/profile").json() == {"display_name": "Test Creator"}
    assert client.get("/api/creator").status_code == 403


def test_profile_account_mismatch_rejected(client, auth, provider):
    provider.overrides["/v2/user/info/"] = httpx.Response(
        200,
        json={"data": {"user": {"open_id": "other-account", "display_name": "Wrong"}}},
    )
    assert client.get("/api/profile").status_code == 502


def test_private_database_allowed_in_relocated_docker_layout(
    settings, tmp_path, monkeypatch
):
    import settings as configuration

    monkeypatch.setattr(
        configuration, "__file__", str(tmp_path / "app" / "settings.py")
    )
    settings.validate()
    with pytest.raises(ValueError):
        replace(
            settings, database_path=str(tmp_path / "app" / "tokens.sqlite3")
        ).validate()


def test_database_cannot_be_in_source_static_directory(settings):
    from pathlib import Path

    site = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError):
        replace(settings, database_path=str(site / "private.sqlite3")).validate()
