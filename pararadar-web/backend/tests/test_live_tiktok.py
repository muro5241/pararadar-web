"""Opt-in, read-only live check. Does not upload, publish, or refresh explicitly."""

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from app import COOKIE


@pytest.mark.skipif(
    not (os.getenv("TIKTOK_LIVE_BASE_URL") and os.getenv("TIKTOK_LIVE_SESSION_FILE")),
    reason="Live HTTPS backend and authorized TikTok session are not supplied",
)
def test_live_authorized_creator():
    base = os.environ["TIKTOK_LIVE_BASE_URL"].rstrip("/")
    parsed = urlsplit(base)
    assert (
        parsed.scheme == "https"
        and parsed.netloc
        and not parsed.username
        and not parsed.query
    )
    path = Path(os.environ["TIKTOK_LIVE_SESSION_FILE"])
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, (
        "Session file must be owner-only"
    )
    session = path.read_text().strip()
    assert session
    with httpx.Client(
        base_url=base, timeout=30, follow_redirects=False, cookies={COOKIE: session}
    ) as client:
        assert client.get("/ready").status_code == 200
        assert client.get("/api/session").status_code == 200
        response = client.get("/api/creator")
        assert response.status_code == 200
        assert response.json()["creator_username"]
        assert "SELF_ONLY" in response.json()["privacy_level_options"]
