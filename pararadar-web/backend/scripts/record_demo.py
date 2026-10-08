"""Record an operator-controlled real demo after the live Sandbox test succeeds."""

import argparse
import os
import shutil
import stat
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session-file", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Private directory OUTSIDE the repository",
    )
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in ("", "/")
        or parsed.username
        or parsed.query
        or parsed.fragment
    ):
        parser.error("Use an HTTPS backend origin")
    if stat.S_IMODE(args.session_file.stat().st_mode) & 0o077:
        parser.error("Session file must be owner-only")
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[3]):
        parser.error("Keep recordings outside the repository")
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            executable_path=os.getenv("CHROMIUM_EXECUTABLE")
            or shutil.which("chromium"),
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            record_video_dir=str(args.output),
            record_video_size={"width": 1280, "height": 900},
        )
        context.add_cookies(
            [
                {
                    "name": "__Host-pararadar_session",
                    "value": args.session_file.read_text().strip(),
                    "url": args.base_url,
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            ]
        )
        page = context.new_page()
        page.goto(args.base_url)
        print(
            "Record the preview, manual privacy choice, consent, private/inbox upload and terminal status."
        )
        print(
            "Do not open developer tools or secrets; never select public visibility without owner approval."
        )
        input("Press Enter here when the demonstration is finished: ")
        context.close()
        browser.close()
        print(
            "Recording saved in the private output directory. Review and redact it before submission."
        )


if __name__ == "__main__":
    main()
