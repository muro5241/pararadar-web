"""Mock API contracts, durable quotas and real API opt-in test."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import BASE, login
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import create_app
from nvidia import ENDPOINT, ContentRequest, GenerationLedger, Nvidia, NvidiaSettings
from store import Store

OUTPUT = {
    "script": "Kripto varlıklarda risk yönetimini öğrenelim. Dalgalanma ve kayıp riski vardır.",
    "titles": ["Risk yönetimi", "Kripto riskleri", "Finans eğitimi"],
    "description": "Eğitim amaçlıdır, yatırım tavsiyesi değildir.",
    "hashtags": ["#finans", "#kripto"],
}


def completion(content=None, finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "content": json.dumps(OUTPUT, ensure_ascii=False)
                    if content is None
                    else content
                },
            }
        ]
    }


class NvidiaProvider:
    def __init__(self):
        self.requests = []
        self.response = httpx.Response(200, json=completion())

    def handler(self, request):
        assert str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer test-nvidia-key"
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 1600 and payload["stream"] is False
        assert "Türkçe" in payload["messages"][0]["content"]
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def nvidia_provider():
    return NvidiaProvider()


@pytest.fixture
def nvidia_client(settings, provider, nvidia_provider):
    app = create_app(
        settings,
        httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
        NvidiaSettings("test-nvidia-key"),
        httpx.AsyncClient(transport=httpx.MockTransport(nvidia_provider.handler)),
    )
    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        yield client


def generate(client, headers, **updates):
    return client.post(
        "/api/content/generate",
        headers=headers,
        json={"topic": "Kripto riskleri", **updates},
    )


def test_encrypted_restart_and_idempotency(nvidia_client, nvidia_provider, settings):
    auth = login(nvidia_client)
    first = generate(nvidia_client, auth)
    assert first.status_code == 200, first.text
    assert first.json()["titles"] == OUTPUT["titles"]
    assert "yatırım tavsiyesi" in first.json()["disclaimer"]
    assert generate(nvidia_client, auth).json() == first.json()
    ledger = GenerationLedger(Store(settings.database_path, settings.encryption_key))
    assert (
        ledger.reserve(
            "test-account",
            auth["Idempotency-Key"],
            ContentRequest(topic="Kripto riskleri"),
        )
        == first.json()
    )
    assert len(nvidia_provider.requests) == 1
    assert OUTPUT["script"].encode() not in Path(settings.database_path).read_bytes()
    assert generate(nvidia_client, auth, topic="Farklı konu").status_code == 409
    assert nvidia_client.get("/api/jobs").json() == []


def test_auth_csrf_body(nvidia_client, nvidia_provider):
    assert generate(nvidia_client, {}).status_code == 401
    auth = login(nvidia_client)
    for changed in ({"Origin": "https://other.example"}, {"X-CSRF-Token": "bad"}):
        assert generate(nvidia_client, {**auth, **changed}).status_code == 403
    assert (
        nvidia_client.post(
            "/api/content/generate", headers=auth, content=b"x" * 8193
        ).status_code
        == 413
    )
    assert not nvidia_provider.requests


@pytest.mark.parametrize(
    "updates",
    [
        {"topic": " "},
        {"topic": "x" * 301},
        {"context": "x" * 1001},
        {"category": "stocks"},
        {"duration_seconds": 120},
        {"max_tokens": 99999},
    ],
)
def test_input_limits(nvidia_client, updates):
    assert generate(nvidia_client, login(nvidia_client), **updates).status_code == 422


def test_missing_key_preserves_tiktok(client, auth):
    assert generate(client, auth).status_code == 503
    assert client.get("/api/creator").status_code == 200
    assert client.get("/api/session").json()["nvidia_available"] is False


@pytest.mark.parametrize("key", ["", "short", "x" * 129, "x" * 16 + " "])
def test_key_limits(nvidia_client, key):
    auth = login(nvidia_client)
    assert generate(nvidia_client, {**auth, "Idempotency-Key": key}).status_code == 400


@pytest.mark.parametrize(
    "status,expected",
    [(401, 503), (403, 503), (429, 429), (500, 502), (302, 502), (404, 502)],
)
def test_safe_errors_no_retries(nvidia_client, nvidia_provider, status, expected):
    nvidia_provider.response = httpx.Response(
        status,
        text="test-nvidia-key secret provider body",
        headers={"Retry-After": "123", "Location": "https://attacker.example"},
    )
    auth = login(nvidia_client)
    response = generate(nvidia_client, auth)
    assert response.status_code == expected
    assert "secret" not in response.text and "test-nvidia-key" not in response.text
    if status == 429:
        assert response.headers["retry-after"] == "123"
    assert generate(nvidia_client, auth).status_code == 409
    assert len(nvidia_provider.requests) == 1


@pytest.mark.parametrize(
    "retry,expected",
    [("invalid", "60"), ("9999999999999999999", "60"), ("0", "1"), ("9999", "3600")],
)
def test_retry_after(nvidia_client, nvidia_provider, retry, expected):
    nvidia_provider.response = httpx.Response(429, headers={"Retry-After": retry})
    assert (
        generate(nvidia_client, login(nvidia_client)).headers["retry-after"] == expected
    )


@pytest.mark.parametrize(
    "error,expected",
    [(httpx.ReadTimeout("secret"), 504), (httpx.ConnectError("secret"), 503)],
)
def test_network_errors(nvidia_client, nvidia_provider, error, expected):
    nvidia_provider.response = error
    response = generate(nvidia_client, login(nvidia_client))
    assert response.status_code == expected and "secret" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [None]},
        completion("not json"),
        completion(finish="length"),
        completion(json.dumps({**OUTPUT, "titles": ["one"]})),
        completion(json.dumps({**OUTPUT, "hashtags": ["<script>"]})),
        completion(json.dumps({**OUTPUT, "titles": ["x" * 121] * 3})),
        completion(json.dumps({**OUTPUT, "extra": "bad"})),
        completion("x" * 20001),
    ],
)
def test_invalid_output(nvidia_client, nvidia_provider, body):
    nvidia_provider.response = httpx.Response(200, json=body)
    assert generate(nvidia_client, login(nvidia_client)).status_code == 502


def test_json_fence(nvidia_client, nvidia_provider):
    nvidia_provider.response = httpx.Response(
        200, json=completion("```json\n" + json.dumps(OUTPUT) + "\n```")
    )
    assert generate(nvidia_client, login(nvidia_client)).status_code == 200


def test_persistent_limits_and_expiry(settings, monkeypatch):
    ledger = GenerationLedger(Store(settings.database_path, settings.encryption_key))
    now = [100000.0]
    monkeypatch.setattr("nvidia.time.time", lambda: now[0])
    request = ContentRequest(topic="Finans")
    for i in range(5):
        ledger.reserve("a", f"request-key-{i:08d}", request)
    with pytest.raises(HTTPException) as exc:
        ledger.reserve("a", "minute-limit-key", request)
    assert exc.value.status_code == 429
    for i in range(5, 20):
        now[0] += 61
        ledger.reserve("a", f"request-key-{i:08d}", request)
    now[0] += 61
    with pytest.raises(HTTPException):
        ledger.reserve("a", "daily-limit-key-0", request)
    for i in range(80):
        ledger.reserve(f"account-{i}", f"request-key-{i:08d}", request)
    with pytest.raises(HTTPException):
        ledger.reserve("another", "global-limit-key", request)
    now[0] += 86401
    assert ledger.reserve("a", "new-day-request-key", request) is None


def test_disconnect_erases_output_keeps_budget(nvidia_client, settings):
    auth = login(nvidia_client)
    assert generate(nvidia_client, auth).status_code == 200
    assert nvidia_client.post("/api/disconnect", headers=auth).status_code == 200
    with Store(settings.database_path, settings.encryption_key).db() as db:
        row = db.execute("SELECT * FROM generations").fetchone()
        assert row["result"] is None and row["account"] != "test-account"


def test_settings(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-secret")
    settings = NvidiaSettings.from_env()
    assert settings.api_key == "test-secret" and "test-secret" not in repr(settings)
    with pytest.raises(ValueError):
        replace(settings, model="https://attacker.example").validate()
    with pytest.raises(ValueError):
        replace(settings, api_key="bad key").validate()


@pytest.mark.skipif(
    not os.getenv("NVIDIA_API_KEY") or os.getenv("RUN_LIVE_NVIDIA") != "1",
    reason="Real NVIDIA API needs NVIDIA_API_KEY and RUN_LIVE_NVIDIA=1",
)
def test_live_nvidia():
    async def run():
        async with httpx.AsyncClient() as client:
            return await Nvidia(NvidiaSettings.from_env(), client).generate(
                ContentRequest(topic="Bileşik faiz ve risk", duration_seconds=30)
            )

    result = asyncio.run(run())
    assert len(result["titles"]) == 3 and result["script"]


def test_atomic_reservation_under_concurrency(settings):
    from concurrent.futures import ThreadPoolExecutor

    ledger = GenerationLedger(Store(settings.database_path, settings.encryption_key))

    def attempt(i):
        try:
            ledger.reserve(
                "account", f"concurrent-key-{i:06d}", ContentRequest(topic="Finans")
            )
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))
    assert results.count(200) == 5 and results.count(429) == 5


def test_cli_without_key_is_safe():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/generate_content.py", "Finans"],
        env={**os.environ, "NVIDIA_API_KEY": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and "not configured" in result.stderr
    assert not result.stdout
