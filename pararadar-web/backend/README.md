# ParaRadar TikTok backend

This service extends the existing OAuth scaffold. It is **separate** from the public Render static site. The repository's `index.html`, `privacy.html`, `terms.html`, `style.css` and TikTok verification file are unchanged. Do not change the existing static site's publish directory or replace it with this service.

Implemented: OAuth authorization-code login with one-use state and secure server-side sessions; encrypted persistent access/refresh tokens; automatic expiry refresh and explicit refresh; revoke/disconnect and local logout; current creator restrictions; preview/consent UI; MP4 chunk upload for Direct Post or Inbox drafts; owned persistent job tracking, rate-bounded polling and duplicate-request protection. There is no background/autonomous publishing.

**Validation status:** see [VALIDATION.md](VALIDATION.md). Mocked API and real Chromium/HTTPS tests verify local behavior. Real TikTok OAuth, Sandbox posting, deployment and TikTok approval are separate checks and must not be represented as completed without live evidence.

## Install and run

Python 3.12, FFmpeg/ffprobe and persistent private storage are required. From this directory:

```sh
python -m venv /path/outside/repository/venv
/path/outside/repository/venv/bin/python -m pip install --require-hashes -r requirements.lock
/path/outside/repository/venv/bin/python -m pip install -r requirements-dev.txt
/path/outside/repository/venv/bin/python -m pytest -q
```

Browser tests use `CHROMIUM_EXECUTABLE`, system `chromium`, or Playwright's installed Chromium. If neither exists, install with `python -m playwright install --with-deps chromium`. Browser tests use a short-lived self-signed **loopback test** certificate and a strict fake TikTok server. TLS verification is retained for production and live tests.

For development, inject the environment settings below or supply an owner-only `.env` file **outside the static site and checkout**:

```sh
uvicorn app:app --env-file /secure/private/backend.env --reload --host 127.0.0.1 --port 8000 --no-access-log
```

Uvicorn loads `.env` before importing the application; the application does not silently load arbitrary files. Use an HTTPS reverse proxy or Uvicorn's TLS options for browser OAuth. `Secure`/`__Host-` cookies intentionally do not support an ordinary public HTTP origin. Open the studio at the **new backend HTTPS origin**, not the existing static site.

`GET /health` checks the running process and reports configuration presence only. `GET /ready` checks required configuration, SQLite access and ffprobe; it does **not** prove TikTok authorization. The service starts safely without credentials, with OAuth/upload unavailable (503).

## Runtime settings

| Setting | Requirement |
| --- | --- |
| `TIKTOK_CLIENT_KEY` | Actual app key from the correct TikTok Sandbox/production configuration |
| `TIKTOK_CLIENT_SECRET` | Actual secret in the backend host's secure settings; never browser code, Git, recordings or chat |
| `PUBLIC_BASE_URL` | Exact HTTPS origin of the new backend, with no path/query |
| `TIKTOK_REDIRECT_URI` | Same origin followed by `/auth/tiktok/callback`; register this exact URI with Login Kit |
| `TOKEN_ENCRYPTION_KEY` | Raw Fernet key; generate once securely and preserve across redeployments |
| `DATABASE_PATH` | Absolute private persistent path outside the static website; Docker default `/var/lib/pararadar/tokens.sqlite3` |
| `ALLOW_PUBLIC_POSTS` | Default `false`: only `SELF_ONLY` Direct Posts are allowed. Any other privacy requires server permission **and explicit per-upload confirmation** |
| `MAX_VIDEO_BYTES` | Default 1 GiB server limit; TikTok supports up to 4 GB. Also configure ingress request size/time limits and enough temporary disk |

Generate the encryption key on your trusted machine and place it directly into secure host settings (not this repository). For example, `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Do not regenerate on every startup. A cloud proxy placeholder cannot replace a real local encryption key. Back up the encrypted database and its key separately; losing the key requires reconnecting accounts. Key migration must decrypt/re-encrypt existing records, not overwrite the key blindly.

`SESSION_SECRET` from the old example is no longer needed: sessions use random 256-bit browser credentials, SHA-256 hashes in SQLite, expiry and per-session CSRF tokens. Access/refresh tokens are Fernet encrypted, never returned to the browser. Logout removes only the current session; disconnect revokes upstream access and deletes local tokens, sessions and jobs. Failed revocation preserves credentials for retry. Expired sessions and OAuth states are cleaned when new ones are created.

Deploy **one Uvicorn worker, one service instance per SQLite database**. The refresh lock prevents concurrent refresh-token rotation in that process. Multi-worker/horizontal scaling requires a shared database and distributed refresh coordination before enabling it. SQLite/job writes survive restart; processes and temporary video files do not.

## Deploy a new HTTPS backend on Render

The Dockerfile installs FFmpeg and checksum-locked Python dependencies; the entrypoint prepares the private disk directory and runs the application as UID 10001. `render.yaml.example` is a reference for a **new** Docker Web Service, not an automatically applied Blueprint. The existing static service is not edited. Persistent disks require a paid plan: review pricing before provisioning.

1. Review/merge the PR or deploy its branch as a new Web Service. Root directory: `pararadar-web/backend`; Dockerfile: `Dockerfile`.
2. Attach a persistent disk at `/var/lib/pararadar`, inject the settings above, keep `ALLOW_PUBLIC_POSTS=false`, and set the health check to `/ready`.
3. Register the new service's exact HTTPS callback in TikTok Login Kit. Enable Content Posting API and approved/requested `user.info.basic`, `video.publish`, `video.upload` scopes. Use the matching Sandbox app keys and permitted test user(s); unaudited Direct Post test accounts must meet TikTok's current private-account requirements.
4. Test `/ready`, then connect the test account in the studio. A static Render service cannot run Python or handle OAuth callbacks. Never set the callback to `https://pararadar-web.onrender.com/auth/tiktok/callback` unless that origin is deliberately moved to a backend in a separate future deployment task.

Required egress: `open.tiktokapis.com`, and the exact TikTok `*.tiktokapis.com` upload host returned by initialization (documented examples include `open-upload.tiktokapis.com` and `upload.us.tiktokapis.com`). Browser OAuth uses `www.tiktok.com`. No external `PULL_FROM_URL` fetch is implemented, so verified media-URL ownership and arbitrary remote downloads are not needed. HTTP redirect upload destinations are rejected and bearer tokens are never sent to the pre-signed upload URL.

Keep proxy access logs from recording OAuth `code` query parameters. The provided startup command disables Uvicorn access logging; hosting-platform logs need separate review. Do not enable HTTPX debug logging with credential-bearing upload URLs. Use durable backups and HTTPS throughout; no CORS is needed because the studio is served from the backend origin.

## Upload/status semantics

MP4 H.264/HEVC, 360–4096 pixels, 23–60 FPS, size and account-specific duration are validated by ffprobe before initialization. The browser sends no video to TikTok until the user consents. Privacy has no default, disabled creator interactions cannot be enabled, title is editable, and commercial/AI disclosures are available.

`POST /api/videos` takes multipart `video` plus JSON string `options`. It requires a session, exact `Origin`, `X-CSRF-Token` from `/api/session`, and `Idempotency-Key` (16–128 ASCII characters). Reusing a key with the same bytes/options returns the existing job; different content returns 409. Jobs are committed before initialization, and publish IDs before upload. Upload init/PUT requests are never blindly retried.

`INITIALIZING`/`INIT_UNCERTAIN` means the provider might have accepted an init whose result was lost. Do not repost automatically: inspect the original account/job. `CHECK_STATUS:*` after an interrupted upload keeps its publish ID for status checks. Temporary uploads are deleted after the request; byte-offset upload resume is not offered. An in-flight process crash can leave these states; a user can inspect/reconcile before explicitly starting a new upload.

`PROCESSING_*` does not mean published. `SEND_TO_USER_INBOX` means TikTok notified the user to finish editing/publishing; it can later advance to `PUBLISH_COMPLETE`. `FAILED` includes a sanitized failure reason. Private posts can complete without a public post ID; that field is returned only after public moderation. The UI polls every six seconds for up to 30 minutes; later manual checks remain available. Server-side polling is bounded to below 30 requests/minute/user.

## Live checks and review recording

See [REVIEW_DEMO.md](REVIEW_DEMO.md) for safe real Sandbox validation and recording. `scripts/sandbox_smoke.py` supports only Inbox or private Direct Post, with an explicit upload-confirmation argument. `scripts/record_demo.py` records an operator-controlled real browser session after a live test passes. It does not invent a completed demo or automate TikTok login/approval.

TikTok's [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) explicitly exclude internal/private account-management utilities from Direct Post audit eligibility. Technical completion and a recording cannot guarantee approval. ParaRadar needs an accurately described, eligible creator-facing product and truthful published privacy/terms before requesting production review. Existing informational-site policies describe a static site; they are intentionally preserved here and require owner review before exposing the integration publicly.

Official protocol references checked during implementation: [Web Login](https://developers.tiktok.com/doc/login-kit-web), [token management](https://developers.tiktok.com/doc/oauth-user-access-token-management), [creator query](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info), [Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [Inbox upload](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video), [media transfer](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide), [status](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status).

## Optional NVIDIA content generation

See [NVIDIA.md](NVIDIA.md) for official sources, secure environment configuration,
authenticated Turkish draft generation, usage limits, CLI and live-test instructions.
The feature does not upload or publish videos and does not require a key for mock tests.
