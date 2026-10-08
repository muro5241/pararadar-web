import json
import subprocess
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import COOKIE, STATE_COOKIE, create_app
from settings import Settings

BASE = "https://backend.example.test"


def token_body(
    account="test-account", scope="user.info.basic,video.publish,video.upload"
):
    return {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "open_id": account,
        "scope": scope,
        "token_type": "Bearer",
        "expires_in": 86400,
        "refresh_expires_in": 31536000,
    }


class Provider:
    """Strict fake for API contract tests, never a substitute for live TikTok evidence."""

    def __init__(self):
        self.requests = []
        self.overrides = {}
        self.info = {
            "creator_username": "tester",
            "creator_nickname": "Test Creator",
            "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
            "comment_disabled": False,
            "duet_disabled": False,
            "stitch_disabled": False,
            "max_video_post_duration_sec": 600,
        }
        self.publish_status = "PUBLISH_COMPLETE"
        self.tokens = token_body()

    def handler(self, request):
        self.requests.append(request)
        if request.url.path in self.overrides:
            handler = self.overrides[request.url.path]
            return handler(request) if callable(handler) else handler
        if request.url.path == "/v2/oauth/token/":
            return httpx.Response(200, json=self.tokens)
        if request.url.path == "/v2/oauth/revoke/":
            return httpx.Response(200, json={"error": {"code": "ok"}})
        assert (
            request.url.host.endswith(".tiktokapis.com")
            or request.url.host == "open.tiktokapis.com"
        )
        if request.method == "PUT":
            assert "authorization" not in request.headers
            assert request.headers["content-type"] == "video/mp4"
            start_end, total = request.headers["content-range"].split(" ")[1].split("/")
            start, end = map(int, start_end.split("-"))
            assert len(request.content) == end - start + 1
            return httpx.Response(201 if end + 1 == int(total) else 206)
        assert (
            request.headers["authorization"] == "Bearer " + self.tokens["access_token"]
        )
        if request.url.path == "/v2/user/info/":
            assert request.method == "GET"
            assert request.url.params["fields"] == "open_id,display_name"
            data = {
                "user": {
                    "open_id": self.tokens["open_id"],
                    "display_name": "Test Creator",
                }
            }
        elif request.url.path == "/v2/post/publish/creator_info/query/":
            data = self.info
        elif request.url.path in (
            "/v2/post/publish/video/init/",
            "/v2/post/publish/inbox/video/init/",
        ):
            data = {
                "publish_id": "test-publish-id",
                "upload_url": "https://upload-us.tiktokapis.com/video/?token=test-upload-secret",
            }
        elif request.url.path == "/v2/post/publish/status/fetch/":
            assert json.loads(request.content) == {"publish_id": "test-publish-id"}
            data = {
                "status": self.publish_status,
                "publicaly_available_post_id": ["post-123"],
            }
            if self.publish_status == "FAILED":
                data["fail_reason"] = "video_format_check_failed"
        else:
            raise AssertionError("Unexpected upstream endpoint: " + request.url.path)
        return httpx.Response(200, json={"data": data, "error": {"code": "ok"}})


@pytest.fixture
def settings(tmp_path):
    return Settings(
        client_key="test-client",
        client_secret="test-client-secret",
        redirect_uri=BASE + "/auth/tiktok/callback",
        encryption_key=Fernet.generate_key().decode(),
        database_path=str(tmp_path / "data" / "tokens.sqlite3"),
        public_base_url=BASE,
    )


@pytest.fixture
def provider():
    return Provider()


@pytest.fixture
def client(settings, provider):
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider.handler))
    app = create_app(settings, upstream)
    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        yield client


def login(client):
    start = client.get("/auth/tiktok/start")
    assert start.status_code == 307
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    result = client.get(
        "/auth/tiktok/callback", params={"code": "test-code", "state": state}
    )
    assert result.status_code == 303, result.text
    assert COOKIE in client.cookies and STATE_COOKIE not in client.cookies
    session = client.get("/api/session").json()
    return {
        "Origin": BASE,
        "X-CSRF-Token": session["csrf_token"],
        "Idempotency-Key": "test-request-key-0001",
    }


@pytest.fixture
def auth(client):
    return login(client)


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "test.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=360x640:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def send_video(client, auth, video, **updates):
    opts = {
        "mode": "direct",
        "title": "Test only",
        "privacy_level": "SELF_ONLY",
        "consent": True,
    }
    opts.update(updates)
    return client.post(
        "/api/videos",
        headers=auth,
        files={"video": ("test.mp4", video, "video/mp4")},
        data={"options": json.dumps(opts)},
    )
