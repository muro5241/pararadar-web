"""Loopback-only fake upstream for browser tests. Never deploy this module."""

import os
import sys
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parent))
from conftest import Provider
from test_nvidia import NvidiaProvider

from app import create_app
from nvidia import NvidiaSettings
from settings import Settings

provider = Provider()
provider.info["comment_disabled"] = True
base = os.environ["PARARADAR_TEST_ORIGIN"]
settings = Settings(
    client_key="test-client",
    client_secret="test-secret",
    redirect_uri=base + "/auth/tiktok/callback",
    public_base_url=base,
    encryption_key=Fernet.generate_key().decode(),
    database_path=os.environ["PARARADAR_TEST_DATABASE"],
)
app = create_app(
    settings,
    httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
    NvidiaSettings("test-nvidia-key"),
    httpx.AsyncClient(transport=httpx.MockTransport(NvidiaProvider().handler)),
)
