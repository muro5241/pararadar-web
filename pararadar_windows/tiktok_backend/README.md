# Isolated Windows TikTok backend

Adapted from repository branch `feat/tiktok-oauth-content-posting` at
`2153dffc58e74de99913ba032b16ea9dc4a08110`. The original web backend is unchanged.

Start through `../tiktok_windows.py` on Windows. See `../TIKTOK_TR.md` for installation,
DPAPI configuration, OAuth Desktop PKCE, the one-video gate and status handling.

Developer tests: install `requirements-dev.txt` in an isolated environment, then
run `pytest tests -q` here. FFmpeg and Chromium are required for media/browser tests.
TikTok uses a strict fake provider in these tests. The optional live check is
read-only and skipped without an authorized HTTPS session.
