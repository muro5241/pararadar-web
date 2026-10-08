"""Explicit private/inbox-only live smoke test; no public-post option exists."""

import argparse
import json
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

COOKIE = "__Host-pararadar_session"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session-file", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument(
        "--request-key",
        required=True,
        help="Reuse the SAME key if retrying this exact operation",
    )
    parser.add_argument("--mode", choices=["private", "inbox"], default="inbox")
    parser.add_argument("--confirm-upload", action="store_true", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    origin = urlsplit(base)
    if (
        origin.scheme != "https"
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
    ):
        parser.error("Use the HTTPS backend origin")
    if stat.S_IMODE(args.session_file.stat().st_mode) & 0o077:
        parser.error("Session file must have owner-only permissions")
    session = args.session_file.read_text().strip()
    with httpx.Client(
        base_url=base, cookies={COOKIE: session}, timeout=600, follow_redirects=False
    ) as c:
        c.get("/ready").raise_for_status()
        auth = c.get("/api/session")
        auth.raise_for_status()
        headers = {
            "Origin": base,
            "X-CSRF-Token": auth.json()["csrf_token"],
            "Idempotency-Key": args.request_key,
        }
        options = {
            "mode": "direct" if args.mode == "private" else "inbox",
            "privacy_level": "SELF_ONLY",
            "title": "ParaRadar Sandbox test",
            "consent": True,
        }
        with args.video.open("rb") as video:
            response = c.post(
                "/api/videos",
                headers=headers,
                files={"video": (args.video.name, video, "video/mp4")},
                data={"options": json.dumps(options)},
            )
        data = response.json()
        if response.is_error:
            # Never print a credential or an upstream response body.
            job = data.get("job")
            print(
                "Upload did not complete.",
                "Saved job: " + job["id"]
                if job
                else "No saved job. Do not blindly retry.",
            )
            raise SystemExit(1)
        job_id = data["id"]
        print("Saved job:", job_id)
        for _ in range(100):
            status = c.get("/api/jobs/" + job_id)
            status.raise_for_status()
            phase = status.json()["phase"]
            print("Status:", phase)
            if phase == "PUBLISH_COMPLETE" or phase.startswith("SEND_TO_USER_INBOX"):
                return
            if phase in ("FAILED", "INIT_UNCERTAIN"):
                raise SystemExit(1)
            time.sleep(6)
        print(
            "Still processing; check this saved job later. Do not create a duplicate upload."
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
