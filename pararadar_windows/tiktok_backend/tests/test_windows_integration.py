"""Approval, one-video lock, desktop PKCE and real existing Windows MP4 contract."""

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import BASE, login, send_video
from fastapi.testclient import TestClient

from app import create_app


def test_unapproved_application_never_initializes(settings, provider, video):
    app = create_app(
        replace(settings, direct_post_approved=False),
        httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
    )
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        auth = login(c)
        assert send_video(c, auth, video).status_code == 403
    assert not any(
        "video/init/" in r.url.path or r.method == "PUT" for r in provider.requests
    )


def test_single_test_lock_survives_restart(settings, provider, video):
    def make_app():
        return create_app(
            settings, httpx.AsyncClient(transport=httpx.MockTransport(provider.handler))
        )

    with TestClient(make_app(), base_url=BASE, follow_redirects=False) as c:
        auth = login(c)
        job = send_video(c, auth, video).json()["id"]
        auth["Idempotency-Key"] = "second-video-key-0001"
        assert send_video(c, auth, video).status_code == 409
        assert c.get("/api/jobs/" + job).json()["phase"] == "PUBLISH_COMPLETE"
        assert c.app.state.store.verified("test-account")
    with TestClient(make_app(), base_url=BASE, follow_redirects=False) as c:
        auth = login(c)
        auth["Idempotency-Key"] = "second-video-key-0002"
        assert send_video(c, auth, video).status_code == 409
    assert len([r for r in provider.requests if "video/init/" in r.url.path]) == 1


def test_more_posts_require_completed_test(settings, provider, video):
    app = create_app(
        replace(settings, single_test_mode=False),
        httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
    )
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        auth = login(c)
        assert send_video(c, auth, video).status_code == 403
    assert not any("video/init/" in r.url.path for r in provider.requests)


def test_scope_missing_never_calls_init(client, auth, video, provider):
    store = client.app.state.store
    tokens = store.tokens("test-account")
    tokens["scope"] = "user.info.basic"
    store.save_tokens("test-account", tokens)
    assert send_video(client, auth, video).status_code == 403
    assert not any("video/init/" in r.url.path for r in provider.requests)


def test_desktop_pkce_and_host_guard(settings, provider):
    origin = "http://127.0.0.1:8766"
    desktop = replace(
        settings,
        desktop_mode=True,
        public_base_url=origin,
        redirect_uri=origin + "/auth/tiktok/callback",
    )
    app = create_app(
        desktop, httpx.AsyncClient(transport=httpx.MockTransport(provider.handler))
    )
    with TestClient(app, base_url=origin, follow_redirects=False) as c:
        start = c.get("/auth/tiktok/start")
        params = parse_qs(urlsplit(start.headers["location"]).query)
        assert params["scope"] == ["user.info.basic,video.publish"]
        assert params["code_challenge_method"] == ["S256"]
        state = params["state"][0]
        verifier = app.state.pkce[state]
        assert params["code_challenge"] == [
            hashlib.sha256(verifier.encode()).hexdigest()
        ]
        assert "Secure" not in start.headers["set-cookie"]
        assert (
            c.get(
                "/auth/tiktok/callback", params={"state": state, "code": "desktop-code"}
            ).status_code
            == 303
        )
        token_request = next(
            r for r in provider.requests if r.url.path == "/v2/oauth/token/"
        )
        assert parse_qs(token_request.content.decode())["code_verifier"] == [verifier]
        assert c.get("/api/session").json()["authorized"] is True
        assert c.get("/health", headers={"Host": "evil.test"}).status_code == 403
        assert (
            c.get(
                "/auth/tiktok/callback", params={"state": state, "code": "desktop-code"}
            ).status_code
            == 400
        )


@pytest.mark.parametrize(
    "origin", ["http://localhost:8766", "http://0.0.0.0:8766", "https://evil.test"]
)
def test_desktop_rejects_non_loopback_configuration(settings, origin):
    with pytest.raises(ValueError):
        replace(
            settings,
            desktop_mode=True,
            public_base_url=origin,
            redirect_uri=origin + "/auth/tiktok/callback",
        ).validate()


def test_safe_query_retries_but_init_is_never_repeated(
    client, auth, video, provider, monkeypatch
):
    async def no_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls = []

    def transient(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503, json={})
        return httpx.Response(
            200, json={"data": provider.info, "error": {"code": "ok"}}
        )

    provider.overrides["/v2/post/publish/creator_info/query/"] = transient
    assert client.get("/api/creator").status_code == 200
    assert len(calls) == 3
    provider.overrides["/v2/post/publish/video/init/"] = lambda request: (
        _ for _ in ()
    ).throw(httpx.ReadTimeout("sensitive request"))
    result = send_video(client, auth, video)
    assert result.status_code == 502
    assert result.json()["job"]["phase"] == "INIT_UNCERTAIN"
    assert "sensitive request" not in result.text
    assert len([r for r in provider.requests if "video/init/" in r.url.path]) == 1
    assert send_video(client, auth, video).json()["id"] == result.json()["job"]["id"]
    assert len([r for r in provider.requests if "video/init/" in r.url.path]) == 1


def test_existing_windows_mp4_uploads_byte_for_byte(client, auth, provider):
    path = Path(__file__).resolve().parents[2] / "ilk_test_videosu" / "video.mp4"
    assert path.is_file()
    original = path.read_bytes()
    fingerprint = hashlib.sha256(original).hexdigest()
    response = send_video(client, auth, original, is_aigc=True)
    assert response.status_code == 202, response.text
    puts = [r for r in provider.requests if r.method == "PUT"]
    assert b"".join(r.content for r in puts) == original
    assert (
        client.get("/api/jobs/" + response.json()["id"]).json()["phase"]
        == "PUBLISH_COMPLETE"
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == fingerprint
    # Provider is a strict fake; this test does not prove a live TikTok publish.


def test_concurrent_single_test_reservation(client):
    from concurrent.futures import ThreadPoolExecutor

    store = client.app.state.store

    def reserve(n):
        try:
            return store.new_job(
                "concurrent-account", f"key-{n}", str(n), single_test=True
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(reserve, range(5)))
    assert sum(result is not None for result in results) == 1


def test_dpapi_refuses_plaintext_fallback():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "tiktok_windows.py"
    spec = importlib.util.spec_from_file_location("tiktok_windows", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.os.name == "nt":
        assert module.dpapi(module.dpapi(b"test-value"), decrypt=True) == b"test-value"
    else:
        with pytest.raises(RuntimeError):
            module.dpapi(b"test-value")


def test_local_videos_only_expose_validated_outputs(settings, provider, tmp_path):
    import json

    origin = "http://127.0.0.1:8766"
    root = tmp_path / "videos"
    good = root / "job-1" / "01"
    good.mkdir(parents=True)
    (good / "video.mp4").write_bytes(b"local video")
    (good / "test_results.json").write_text(json.dumps({"passed": True}))
    bad = root / "job-1" / "02"
    bad.mkdir()
    (bad / "video.mp4").write_bytes(b"not validated")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "video.mp4").write_bytes(b"outside file")
    (outside / "test_results.json").write_text(json.dumps({"passed": True}))
    (root / "job-1" / "03").symlink_to(outside, target_is_directory=True)
    config = replace(
        settings,
        desktop_mode=True,
        public_base_url=origin,
        redirect_uri=origin + "/auth/tiktok/callback",
        video_directory=str(root),
    )
    app = create_app(
        config, httpx.AsyncClient(transport=httpx.MockTransport(provider.handler))
    )
    with TestClient(app, base_url=origin, follow_redirects=False) as c:
        assert c.get("/api/local-videos").status_code == 401
        start = c.get("/auth/tiktok/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        assert (
            c.get(
                "/auth/tiktok/callback", params={"state": state, "code": "test-code"}
            ).status_code
            == 303
        )
        listed = c.get("/api/local-videos").json()
        assert len(listed) == 1
        assert "job-1" in listed[0]["name"] and "01" in listed[0]["name"]
        assert c.get("/api/local-videos/" + listed[0]["id"]).content == b"local video"
        assert c.get("/api/local-videos/arbitrary-path").status_code == 404
        assert not any("video/init/" in r.url.path for r in provider.requests)
