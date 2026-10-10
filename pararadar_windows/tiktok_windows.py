"""Separate loopback TikTok service; never changes or starts the production queue."""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tiktok_backend"))

load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")


def dpapi(data: bytes, decrypt=False) -> bytes:
    """Current Windows user, no machine-wide protection, no plaintext fallback."""
    if os.name != "nt":
        raise RuntimeError("DPAPI storage requires Windows; credentials were not saved")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    fn.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN prevents prompts or machine-wide fallback.
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError("Windows credential protection failed")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def protected_dir() -> Path:
    if os.name != "nt":
        raise RuntimeError("Run the TikTok Windows service on your Windows computer")
    base = Path(os.environ["LOCALAPPDATA"]) / "ParaRadar" / "TikTok"
    base.mkdir(parents=True, exist_ok=True)
    who = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    import csv

    sid = next(csv.reader([who.stdout.strip()]))[1]
    # Separate private credential directory; does not alter the production data ACL.
    subprocess.run(
        [
            "icacls",
            str(base),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    )
    return base


def read_config(base):
    try:
        return json.loads(dpapi((base / "settings.dpapi").read_bytes(), decrypt=True))
    except (OSError, ValueError):
        raise RuntimeError(
            "Configure TikTok first; protected settings are missing or invalid"
        ) from None


def write_config(base, config):
    path = base / "settings.dpapi"
    temp = path.with_suffix(".tmp")
    temp.write_bytes(dpapi(json.dumps(config).encode()))
    temp.replace(path)


def settings_for(base, config):
    from settings import Settings

    origin = "http://127.0.0.1:8766"
    return Settings(
        client_key=config["client_key"],
        client_secret=config["client_secret"],
        redirect_uri=origin + "/auth/tiktok/callback",
        public_base_url=origin,
        encryption_key=config["encryption_key"],
        database_path=str(base / "tokens.sqlite3"),
        desktop_mode=True,
        direct_post_approved=config.get("direct_post_approved") is True,
        single_test_mode=config.get("single_test_mode", True),
        video_directory=str(
            Path(os.getenv("PARARADAR_DATA", str(ROOT / "data"))) / "videos"
        ),
        allow_public_posts=config.get("allow_public_posts") is True,
    )


def configure(base):
    from cryptography.fernet import Fernet

    old = read_config(base) if (base / "settings.dpapi").exists() else {}
    print(
        "TikTok Developer Portal: Login Kit Desktop + Content Posting API / Direct Post."
    )
    print("Register callback: http://127.0.0.1:8766/auth/tiktok/callback")
    key = input("Client key: ").strip()
    secret = getpass.getpass(
        "Client secret (hidden, saved with Windows DPAPI): "
    ).strip()
    if not key or not secret:
        raise RuntimeError("Client key and secret are required")
    if old and old["client_key"] != key:
        raise RuntimeError(
            "App key changed; use a separate Windows profile to avoid mixing accounts"
        )
    approved = (
        input(
            "Portal confirms this app is approved for Direct Post + video.publish? Type APPROVED, otherwise Enter: "
        ).strip()
        == "APPROVED"
    )
    config = dict(
        client_key=key,
        client_secret=secret,
        encryption_key=old.get("encryption_key") or Fernet.generate_key().decode(),
        direct_post_approved=approved,
        single_test_mode=True,
        allow_public_posts=False,
    )
    write_config(base, config)
    print(
        "Saved securely. Publishing "
        + (
            "limited to one Only Me test."
            if approved
            else "DISABLED until app approval is confirmed."
        )
    )


def enable_after_test(base, config):
    from store import Store

    if not config.get("direct_post_approved"):
        raise RuntimeError("Direct Post approval is not confirmed")
    store = Store(str(base / "tokens.sqlite3"), config["encryption_key"])
    with store.db() as db:
        passed = db.execute("SELECT COUNT(*) FROM verified_tests").fetchone()[0]
    if not passed:
        raise RuntimeError(
            "TikTok has not reported PUBLISH_COMPLETE for the single test; more posts remain locked"
        )
    print(
        "Every subsequent video still requires preview, visibility selection and posting consent."
    )
    config["single_test_mode"] = False
    config["allow_public_posts"] = (
        input(
            "Approved/audited app allows public posts? Type PUBLIC to enable, otherwise Enter: "
        ).strip()
        == "PUBLIC"
    )
    write_config(base, config)
    print(
        "Single-video lock removed. Restart TikTok service. Posting consent remains required."
    )


def serve(base, config):
    # Same-user locking prevents two service processes refreshing a rotated token.
    import msvcrt

    lock = open(base / "service.lock", "a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        raise RuntimeError("TikTok service is already running") from None
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(base / "tiktok.log", encoding="utf-8")],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    import uvicorn
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from app import create_app

    app = create_app(settings_for(base, config))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])

    def open_when_ready():
        # Loopback is local-only and intentionally not sent through outbound proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            try:
                with opener.open("http://127.0.0.1:8766/health", timeout=1):
                    webbrowser.open("http://127.0.0.1:8766/")
                    return
            except OSError:
                time.sleep(0.2)

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        # Query strings contain OAuth codes. Disable access logging entirely.
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8766,
            workers=1,
            access_log=False,
            log_level="warning",
        )
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(
        description="ParaRadar official TikTok Direct Post"
    )
    parser.add_argument(
        "command", choices=["configure", "serve", "enable-after-test", "status"]
    )
    args = parser.parse_args()
    try:
        base = protected_dir()
        if args.command == "configure":
            configure(base)
            return
        config = read_config(base)
        if args.command == "serve":
            serve(base, config)
        elif args.command == "enable-after-test":
            enable_after_test(base, config)
        else:
            print(
                json.dumps(
                    {
                        "app_approval_confirmed": config.get(
                            "direct_post_approved", False
                        ),
                        "single_test_mode": config.get("single_test_mode", True),
                        "public_posts_allowed": config.get("allow_public_posts", False),
                        "panel": "http://127.0.0.1:8766/",
                        "log": str(base / "tiktok.log"),
                    }
                )
            )
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
