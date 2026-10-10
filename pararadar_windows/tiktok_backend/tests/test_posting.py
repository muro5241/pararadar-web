import io
import json
from dataclasses import replace

import httpx
import pytest
from conftest import BASE, login, send_video
from fastapi.testclient import TestClient

from app import COOKIE, create_app
from tiktok import CHUNK_BYTES, chunk_plan


def test_direct_upload_status_and_idempotency(client, auth, video, provider):
    r = send_video(client, auth, video)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["phase"] == "PROCESSING:video.publish"
    assert "publish_id" not in r.text and "upload-secret" not in r.text
    r = send_video(client, auth, video)
    assert r.status_code == 202 and r.json()["id"] == job["id"]
    inits = [
        r for r in provider.requests if r.url.path == "/v2/post/publish/video/init/"
    ]
    assert len(inits) == 1
    payload = json.loads(inits[0].content)
    assert payload["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": len(video),
        "chunk_size": len(video),
        "total_chunk_count": 1,
    }
    assert payload["post_info"]["privacy_level"] == "SELF_ONLY"
    assert payload["post_info"]["title"] == "Test only"
    status = client.get("/api/jobs/" + job["id"])
    assert status.json()["phase"] == "PUBLISH_COMPLETE"
    assert status.json()["result"]["post_ids"] == ["post-123"]
    before = len(provider.requests)
    assert client.get("/api/jobs/" + job["id"]).status_code == 200
    assert len(provider.requests) == before
    assert client.get("/api/jobs").json()[0]["id"] == job["id"]
    assert send_video(client, auth, video, title="Changed").status_code == 409


def test_inbox_is_disabled_in_windows_direct_only_service(
    client, auth, video, provider
):
    assert send_video(client, auth, video, mode="inbox").status_code == 403
    assert not any("video/init" in r.url.path for r in provider.requests)


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"consent": False}, 422),
        ({"privacy_level": None}, 422),
        ({"privacy_level": "PUBLIC_TO_EVERYONE", "public_confirmed": True}, 403),
        ({"brand_content_toggle": True}, 422),
        ({"mode": "invalid"}, 422),
        ({"title": "x" * 2201}, 422),
        ({"privacy_level": "UNKNOWN", "public_confirmed": True}, 403),
    ],
)
def test_invalid_or_unapproved_posts_never_call_init(
    client, auth, video, provider, changes, status
):
    assert send_video(client, auth, video, **changes).status_code == status
    assert not any("video/init" in r.url.path for r in provider.requests)


def test_public_post_requires_both_permissions(settings, provider, video):
    app = create_app(
        replace(settings, allow_public_posts=True, single_test_mode=False),
        httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
    )
    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        auth = login(client)
        client.app.state.store.mark_verified("test-account", "completed-test")
        assert (
            send_video(
                client, auth, video, privacy_level="PUBLIC_TO_EVERYONE"
            ).status_code
            == 403
        )
        assert (
            send_video(
                client,
                auth,
                video,
                privacy_level="PUBLIC_TO_EVERYONE",
                public_confirmed=True,
            ).status_code
            == 202
        )
        assert (
            json.loads(
                next(r for r in provider.requests if "video/init" in r.url.path).content
            )["post_info"]["privacy_level"]
            == "PUBLIC_TO_EVERYONE"
        )


def test_creator_restrictions_are_checked_again(client, auth, video, provider):
    assert client.get("/api/creator").json()["privacy_level_options"] == [
        "SELF_ONLY",
        "PUBLIC_TO_EVERYONE",
    ]
    provider.info["comment_disabled"] = True
    assert send_video(client, auth, video, disable_comment=False).status_code == 422
    provider.info["max_video_post_duration_sec"] = 0.5
    assert send_video(client, auth, video).status_code == 422
    provider.info["max_video_post_duration_sec"] = 600
    provider.info["privacy_level_options"] = []
    assert send_video(client, auth, video).status_code == 422


def test_missing_scope(client, auth, video):
    store = client.app.state.store
    t = store.tokens("test-account")
    t["scope"] = "user.info.basic"
    store.save_tokens("test-account", t)
    assert client.get("/api/creator").status_code == 403
    assert send_video(client, auth, video, mode="inbox").status_code == 403


@pytest.mark.parametrize("body", [b"", b"not a video"])
def test_empty_and_invalid_video(client, auth, body, provider):
    assert send_video(client, auth, body).status_code == 422
    assert not any("video/init" in r.url.path for r in provider.requests)


def test_wrong_mime_and_missing_idempotency_key(client, auth, video):
    assert (
        client.post(
            "/api/videos",
            headers=auth,
            files={"video": ("x.mov", video, "video/quicktime")},
            data={
                "options": '{"mode":"direct","privacy_level":"SELF_ONLY","consent":true}'
            },
        ).status_code
        == 422
    )
    auth.pop("Idempotency-Key")
    assert send_video(client, auth, video).status_code == 422


def test_upload_limit(settings, provider, video):
    app = create_app(
        replace(settings, max_video_bytes=1),
        httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
    )
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        auth = login(c)
        assert send_video(c, auth, video).status_code == 413
        assert (
            c.post(
                "/api/videos",
                headers={**auth, "Content-Length": "2000000"},
                content=b"",
            ).status_code
            == 413
        )


def test_ownership_isolation(client, auth, video):
    job = send_video(client, auth, video).json()["id"]
    store = client.app.state.store
    other = store.new_session("other-account", 86400)
    client.cookies.clear()
    client.cookies.set(COOKIE, other, domain="backend.example.test", path="/")
    assert client.get("/api/jobs/" + job).status_code == 404
    assert client.get("/api/jobs").json() == []


@pytest.mark.parametrize(
    "destination",
    [
        "http://upload-us.tiktokapis.com/a",
        "https://evil.test/video",
        "https://tiktokapis.com.evil.test/video",
        "https://user:password@upload-us.tiktokapis.com/a",
        "https://upload-us.tiktokapis.com:444/a",
    ],
)
def test_invalid_upload_destination_rejected(
    client, auth, video, provider, destination
):
    provider.overrides["/v2/post/publish/video/init/"] = httpx.Response(
        200,
        json={
            "data": {"publish_id": "id", "upload_url": destination},
            "error": {"code": "ok"},
        },
    )
    r = send_video(client, auth, video)
    assert r.status_code == 502
    assert r.json()["job"]["phase"] == "INIT_UNCERTAIN"
    assert not any(r.method == "PUT" for r in provider.requests)


def test_interrupted_upload_persists_job_and_does_not_reinitialize(
    client, auth, video, provider
):
    provider.overrides["/video/"] = lambda request: (_ for _ in ()).throw(
        httpx.ReadTimeout("failure")
    )
    r = send_video(client, auth, video)
    assert r.status_code == 502
    job = r.json()["job"]
    assert job["phase"] == "CHECK_STATUS:video.publish"
    assert send_video(client, auth, video).json()["id"] == job["id"]
    assert len([r for r in provider.requests if "video/init/" in r.url.path]) == 1
    assert client.get("/api/jobs/" + job["id"]).json()["phase"] == "PUBLISH_COMPLETE"


@pytest.mark.parametrize("upstream_status", [400, 500, 429])
def test_init_error_records_uncertain_job(
    client, auth, video, provider, upstream_status
):
    provider.overrides["/v2/post/publish/video/init/"] = httpx.Response(
        upstream_status,
        json={"error": {"code": "bad", "message": "secret-provider-message"}},
    )
    r = send_video(client, auth, video)
    assert r.status_code in (502, 429)
    assert "secret-provider-message" not in r.text
    job = r.json()["job"]
    assert job["phase"] == "INIT_UNCERTAIN"
    assert client.get("/api/jobs/" + job["id"]).json()["phase"] == "INIT_UNCERTAIN"
    assert send_video(client, auth, video).json()["id"] == job["id"]


@pytest.mark.parametrize(
    "status", ["PROCESSING_UPLOAD", "FAILED", "SEND_TO_USER_INBOX"]
)
def test_publish_processing_and_failure_are_not_reported_as_success(
    client, auth, video, provider, status
):
    job = send_video(client, auth, video).json()["id"]
    provider.publish_status = status
    data = client.get("/api/jobs/" + job).json()
    assert data["result"]["status"] == status
    if status == "FAILED":
        assert data["result"]["fail_reason"] == "video_format_check_failed"


def test_chunks_use_exact_ranges_and_no_bearer_leak(client, provider):
    import asyncio

    size = 2 * CHUNK_BYTES + 7
    tk = client.app.state.tiktok
    asyncio.run(
        tk.upload(
            "https://upload-us.tiktokapis.com/video/", io.BytesIO(b"x" * size), size
        )
    )
    puts = [r for r in provider.requests if r.method == "PUT"]
    assert len(puts) == 2
    assert puts[0].headers["content-range"] == f"bytes 0-{CHUNK_BYTES - 1}/{size}"
    assert puts[1].headers["content-range"] == f"bytes {CHUNK_BYTES}-{size - 1}/{size}"
    assert chunk_plan(1) == (1, 1)
    assert chunk_plan(CHUNK_BYTES) == (CHUNK_BYTES, 1)


def test_security_headers_and_no_cors(client):
    r = client.get("/")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "access-control-allow-origin" not in r.headers


def test_utf16_caption_limit(client, auth, video):
    assert send_video(client, auth, video, title="😀" * 1101).status_code == 422


def test_anonymous_upload_rejected_before_parsing(client):
    assert (
        client.post("/api/videos", content=b"invalid multipart body").status_code == 401
    )


def test_status_poll_rate_is_bounded(client, auth, video, provider):
    provider.publish_status = "PROCESSING_UPLOAD"
    job = send_video(client, auth, video).json()["id"]
    for _ in range(5):
        assert client.get("/api/jobs/" + job).status_code == 200
    assert (
        len(
            [
                r
                for r in provider.requests
                if r.url.path == "/v2/post/publish/status/fetch/"
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("comment_disabled", "false"),
        ("max_video_post_duration_sec", "600"),
        ("privacy_level_options", [None]),
    ],
)
def test_malformed_creator_response_is_safe_error(client, auth, provider, field, value):
    provider.info[field] = value
    assert client.get("/api/creator").status_code == 502


def test_streamed_body_limit_before_unbounded_spooling(settings, provider):
    app = create_app(
        replace(settings, max_video_bytes=1),
        httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
    )
    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        auth = login(client)

        def body():
            yield b'--test\r\nContent-Disposition: form-data; name="video"; filename="large.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
            yield b"x" * (1024 * 1024)
            yield b"\r\n--test--\r\n"

        response = client.post(
            "/api/videos",
            headers={**auth, "Content-Type": "multipart/form-data; boundary=test"},
            content=body(),
        )
        assert response.status_code == 413
        assert not any("video/init" in r.url.path for r in provider.requests)
