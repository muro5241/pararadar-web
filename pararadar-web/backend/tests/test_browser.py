"""Real Chromium against real HTTPS backend, with explicitly mocked TikTok only."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.fixture(scope="module")
def browser_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("https-browser")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"https://127.0.0.1:{port}"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(root / "key.pem"),
            "-out",
            str(root / "cert.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    environment = {
        **os.environ,
        "PARARADAR_TEST_ORIGIN": base,
        "PARARADAR_TEST_DATABASE": str(root / "test.sqlite3"),
    }
    backend = Path(__file__).resolve().parent.parent
    with (root / "server.log").open("w") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "browser_server:app",
                "--app-dir",
                str(backend / "tests"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--ssl-keyfile",
                str(root / "key.pem"),
                "--ssl-certfile",
                str(root / "cert.pem"),
                "--no-access-log",
            ],
            cwd=backend,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            # Self-signed certificate is limited to this ephemeral loopback test server.
            with httpx.Client(verify=False, trust_env=False) as c:
                for _ in range(100):
                    if proc.poll() is not None:
                        pytest.fail((root / "server.log").read_text())
                    try:
                        if c.get(base + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("HTTPS test server did not start")
            yield base
        finally:
            proc.terminate()
            proc.wait(timeout=10)


@pytest.fixture
def page(browser_server):
    with sync_playwright() as playwright:
        executable = os.getenv("CHROMIUM_EXECUTABLE") or shutil.which("chromium")
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def authorize(route):
            # Intercept the first redirect hop: Playwright does not route subsequent
            # redirect URLs. Preserve the real server's state cookie, mock only TikTok consent.
            response = route.fetch(max_redirects=0)
            state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
            route.fulfill(
                response=response,
                status=302,
                headers={
                    **response.headers,
                    "Location": browser_server
                    + "/auth/tiktok/callback?"
                    + urlencode({"state": state, "code": "test-code"}),
                },
            )

        page.route(browser_server + "/auth/tiktok/start", authorize)
        page.goto(browser_server)
        page.get_by_role("link", name="TikTok hesabını bağla").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.startswith(browser_server), page.url
        assert page.locator("#studio").count(), page.locator("body").inner_text()
        expect(page.locator("#studio")).to_be_visible()
        expect(page.locator("#creator")).to_contain_text("Test Creator")
        yield page
        assert not errors, errors
        context.close()
        browser.close()


def test_browser_preview_consent_creator_settings_and_private_post(page, video):
    expect(page.locator("#privacy")).to_have_value("")
    expect(page.locator("#comment")).to_be_disabled()
    expect(page.locator("#duet")).not_to_be_checked()
    assert page.locator('#privacy option[value="PUBLIC_TO_EVERYONE"]').evaluate(
        "option => option.disabled"
    )
    page.locator("#video").set_input_files(
        {"name": "test.mp4", "mimeType": "video/mp4", "buffer": video}
    )
    expect(page.locator("#preview")).to_be_visible()
    page.locator("#privacy").select_option("SELF_ONLY")
    page.locator("#title").fill("Browser test video")
    expect(page.locator("#consent")).not_to_be_checked()
    page.locator("#consent").check()
    page.locator("#submit").click()
    expect(page.locator("#jobs")).to_contain_text("PROCESSING:video.publish")
    page.locator("#check-jobs").click()
    expect(page.locator("#jobs")).to_contain_text("PUBLISH_COMPLETE")
    # Same button, same unchanged file/options -> same idempotency key -> one owned job.
    page.locator("#submit").click()
    expect(page.locator("#jobs li")).to_have_count(1)


def test_browser_disclosures_and_inbox(page, video):
    page.locator("#commercial").check()
    expect(page.locator("#submit")).to_be_disabled()
    page.locator("#organic").check()
    expect(page.locator("#submit")).to_be_enabled()
    page.locator("#privacy").select_option("SELF_ONLY")
    expect(page.locator("#branded")).to_be_disabled()
    page.locator("#mode").select_option("inbox")
    expect(page.locator("#inbox-note")).to_be_visible()
    expect(page.locator("#direct-options")).not_to_be_visible()
    page.locator("#video").set_input_files(
        {"name": "test.mp4", "mimeType": "video/mp4", "buffer": video}
    )
    page.locator("#consent").check()
    page.locator("#submit").click()
    expect(page.locator("#jobs")).to_contain_text("PROCESSING:video.upload")


def test_browser_refresh_and_logout(page):
    page.locator("#refresh").click()
    expect(page.locator("#notice")).to_contain_text("Yetki yenilendi")
    page.locator("#logout").click()
    expect(page.locator("#connect")).to_be_visible()
    expect(page.locator("#studio")).not_to_be_visible()
