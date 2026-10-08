"""Isolated HTTPS TikTok backend; existing public static site is never mounted here."""

import asyncio
import hashlib
import json
import math
import secrets
import shutil
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from fractions import Fraction
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from settings import Settings
from store import Store
from tiktok import AUTH_URL, TikTok

COOKIE = "__Host-pararadar_session"
STATE_COOKIE = "__Host-pararadar_oauth_state"
ROOT = Path(__file__).resolve().parent


def inspect_video(path):
    if not shutil.which("ffprobe"):
        raise HTTPException(503, "ffprobe is required on the backend server")
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_entries",
                "format=duration,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        videos = [s for s in data["streams"] if s.get("codec_type") == "video"]
        if (
            not math.isfinite(duration)
            or duration <= 0
            or not videos
            or "mp4" not in data["format"]["format_name"]
            or any(v.get("codec_name") not in ("h264", "hevc") for v in videos)
            or any(
                not 23 <= float(Fraction(v.get("avg_frame_rate", "0"))) <= 60
                for v in videos
            )
            or any(
                not 360 <= v.get("width", 0) <= 4096
                or not 360 <= v.get("height", 0) <= 4096
                for v in videos
            )
        ):
            raise ValueError()
    except (
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
        ZeroDivisionError,
    ):
        raise HTTPException(
            422, "Use a valid MP4 (H.264/HEVC), 360–4096 pixel video"
        ) from None
    return duration


class PostOptions(BaseModel):
    mode: str = Field(pattern="^(direct|inbox)$")
    title: str = Field(default="", max_length=2200)
    privacy_level: str | None = None
    disable_comment: bool = True
    disable_duet: bool = True
    disable_stitch: bool = True
    brand_content_toggle: bool = False
    brand_organic_toggle: bool = False
    is_aigc: bool = False
    consent: bool = False
    public_confirmed: bool = False

    model_config = {"extra": "forbid"}


def create_app(settings=None, http_client=None):
    settings = settings or Settings.from_env()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = (
            Store(settings.database_path, settings.encryption_key)
            if settings.configured
            else None
        )
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=15), follow_redirects=False
        )
        app.state.tiktok = TikTok(settings, app.state.store, client)
        app.state.upload_lock = asyncio.Semaphore(2)
        app.state.status_checks = {}
        yield
        if http_client is None:
            await client.aclose()

    app = FastAPI(
        title="ParaRadar TikTok Integration", version="1.0.0", lifespan=lifespan
    )
    app.state.settings = settings
    templates = Jinja2Templates(directory=ROOT / "templates")
    app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")

    @app.middleware("http")
    async def security(request, call_next):
        # Reject unauthorized upload bodies before FastAPI's multipart parser writes them to disk.
        if request.url.path == "/api/videos" and request.method == "POST":
            try:
                session(request, mutate=True)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        length = request.headers.get("content-length")
        if length:
            try:
                if (
                    int(length) < 0
                    or int(length) > settings.max_video_bytes + 1024 * 1024
                ):
                    return JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
            except ValueError:
                return JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                )
        # Enforce limits for chunked requests too, before multipart parsing spools to disk.
        received = 0
        original_receive = request._receive

        async def limited_receive():
            nonlocal received
            message = await original_receive()
            received += len(message.get("body", b""))
            if received > settings.max_video_bytes + 1024 * 1024:
                raise HTTPException(413, "Request body too large")
            return message

        request._receive = limited_receive
        response = await call_next(request)
        if received > settings.max_video_bytes + 1024 * 1024:
            # Multipart parsers may normalize receive errors to 400; preserve our size-limit contract.
            response = JSONResponse(
                {"detail": "Request body too large"}, status_code=413
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                "Strict-Transport-Security": "max-age=31536000",
            }
        )
        return response

    def store():
        value = app.state.store
        if value is None:
            raise HTTPException(503, "TikTok OAuth environment is not configured")
        return value

    def session(request, mutate=False):
        s = store().session(request.cookies.get(COOKIE))
        if not s:
            raise HTTPException(401, "Connect your TikTok account")
        if mutate:
            if request.headers.get("origin") != settings.public_base_url:
                raise HTTPException(403, "Invalid request origin")
            csrf = request.headers.get("x-csrf-token", "")
            if not csrf or not secrets.compare_digest(csrf, s["csrf"]):
                raise HTTPException(403, "Invalid CSRF token")
        return s

    def public_job(job):
        result = json.loads(job["result"]) if job["result"] else None
        return {
            "id": job["id"],
            "phase": job["phase"],
            "result": result,
            "created": job["created"],
        }

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "integration": "configured" if settings.configured else "not_configured",
        }

    @app.get("/ready")
    def ready():
        store()
        if not shutil.which("ffprobe"):
            raise HTTPException(503, "ffprobe is missing")
        with store().db() as db:
            db.execute("SELECT 1")
        return {"ready": True}

    @app.get("/")
    def dashboard(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "configured": settings.configured,
                "allow_public": settings.allow_public_posts,
            },
        )

    @app.get("/auth/tiktok/start")
    def start(request: Request):
        state = store().new_state()
        response = RedirectResponse(
            AUTH_URL
            + "?"
            + urlencode(
                {
                    "client_key": settings.client_key,
                    "scope": "user.info.basic,video.publish,video.upload",
                    "response_type": "code",
                    "redirect_uri": settings.redirect_uri,
                    "state": state,
                }
            )
        )
        response.set_cookie(
            STATE_COOKIE,
            state,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=600,
            path="/",
        )
        return response

    @app.get("/auth/tiktok/callback")
    async def callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ):
        expected = request.cookies.get(STATE_COOKIE)
        if (
            not state
            or not expected
            or len(state) > 200
            or not secrets.compare_digest(state, expected)
            or not store().consume_state(state)
        ):
            response = JSONResponse(
                {"detail": "Invalid or expired OAuth callback state"}, status_code=400
            )
        elif error:
            response = JSONResponse(
                {"detail": "TikTok authorization was declined or failed"},
                status_code=400,
            )
        elif not code or len(code) > 2048:
            response = JSONResponse(
                {"detail": "Missing or invalid authorization code"}, status_code=400
            )
        else:
            try:
                account = await app.state.tiktok.exchange(code)
                cookie = store().new_session(account, settings.session_ttl)
                store().forget_session(request.cookies.get(COOKIE))
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(
                    COOKIE,
                    cookie,
                    httponly=True,
                    secure=True,
                    samesite="lax",
                    max_age=settings.session_ttl,
                    path="/",
                )
            except HTTPException as exc:
                response = JSONResponse(
                    {"detail": exc.detail},
                    status_code=exc.status_code,
                    headers=exc.headers,
                )
        response.delete_cookie(
            STATE_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
        )
        return response

    @app.get("/api/session")
    def current_session(request: Request):
        s = session(request)
        return {
            "authorized": True,
            "csrf_token": s["csrf"],
            "allow_public_posts": settings.allow_public_posts,
        }

    @app.get("/api/profile")
    async def profile(request: Request):
        s = session(request)
        return await app.state.tiktok.profile(s["account"])

    @app.get("/api/creator")
    async def creator(request: Request):
        s = session(request)
        info = await app.state.tiktok.creator(s["account"])
        return info

    @app.post("/api/token/refresh")
    async def refresh(request: Request):
        s = session(request, mutate=True)
        await app.state.tiktok.access(s["account"], force=True)
        return {"refreshed": True}

    @app.post("/api/logout")
    def logout(request: Request):
        session(request, mutate=True)
        store().forget_session(request.cookies.get(COOKIE))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(
            COOKIE, secure=True, httponly=True, samesite="lax", path="/"
        )
        return response

    @app.post("/api/disconnect")
    async def disconnect(request: Request):
        s = session(request, mutate=True)
        # Keep local credentials if revocation fails so the operation can be retried.
        await app.state.tiktok.revoke(s["account"])
        store().disconnect(s["account"])
        response = JSONResponse({"disconnected": True})
        response.delete_cookie(
            COOKIE, secure=True, httponly=True, samesite="lax", path="/"
        )
        return response

    @app.post("/api/videos", status_code=202)
    async def videos(
        request: Request,
        video: Annotated[UploadFile, File()],
        options: Annotated[str, Form()],
    ):
        s = session(request, mutate=True)
        try:
            post = PostOptions.model_validate_json(options)
        except ValueError:
            raise HTTPException(422, "Invalid posting options") from None
        if len(post.title.encode("utf-16-le")) // 2 > 2200:
            raise HTTPException(422, "Caption exceeds 2200 UTF-16 units")
        if not post.consent:
            raise HTTPException(
                422, "Explicit posting and music usage consent is required"
            )
        if post.mode == "direct":
            if not post.privacy_level:
                raise HTTPException(422, "Choose a privacy level; there is no default")
            if post.privacy_level != "SELF_ONLY" and (
                not settings.allow_public_posts or not post.public_confirmed
            ):
                raise HTTPException(
                    403,
                    "Posting beyond Only Me requires server permission and explicit confirmation",
                )
            if post.brand_content_toggle and post.privacy_level == "SELF_ONLY":
                raise HTTPException(
                    422, "Branded content cannot use Only Me visibility"
                )
        request_key = request.headers.get("idempotency-key", "")
        if not 16 <= len(request_key) <= 128 or not request_key.isascii():
            raise HTTPException(422, "A 16–128 character Idempotency-Key is required")
        if video.content_type != "video/mp4":
            raise HTTPException(422, "Only MP4 uploads are accepted")
        async with app.state.upload_lock:
            with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
                size, sha = 0, hashlib.sha256()
                while chunk := await video.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_video_bytes:
                        raise HTTPException(413, "Video exceeds server upload limit")
                    sha.update(chunk)
                    tmp.write(chunk)
                await video.close()
                if size == 0:
                    raise HTTPException(422, "Video is empty")
                tmp.flush()
                duration = await asyncio.to_thread(inspect_video, tmp.name)
                if post.mode == "direct":
                    # Always fetch current account restrictions immediately before initialization.
                    info = await app.state.tiktok.creator(s["account"])
                    if post.privacy_level not in info["privacy_level_options"]:
                        raise HTTPException(
                            422, "Privacy level is not available for this creator"
                        )
                    if duration > info["max_video_post_duration_sec"]:
                        raise HTTPException(422, "Video exceeds creator duration limit")
                    for name in ("comment", "duet", "stitch"):
                        if info[name + "_disabled"] and not getattr(
                            post, "disable_" + name
                        ):
                            raise HTTPException(422, "Creator settings disable " + name)
                elif duration > 600:
                    raise HTTPException(422, "Inbox videos must be at most 10 minutes")
                fingerprint = hashlib.sha256(
                    (sha.hexdigest() + post.model_dump_json()).encode()
                ).hexdigest()
                job, fresh = store().new_job(s["account"], request_key, fingerprint)
                if job["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Idempotency key already belongs to another request"
                    )
                if not fresh:
                    return public_job(job)
                scope = "video.publish" if post.mode == "direct" else "video.upload"
                post_info = (
                    post.model_dump(exclude={"mode", "consent", "public_confirmed"})
                    if post.mode == "direct"
                    else None
                )
                try:
                    publish_id, upload_url = await app.state.tiktok.init(
                        s["account"], size, post_info
                    )
                    store().update_job(
                        job["id"], "UPLOADING:" + scope, publish_id=publish_id
                    )
                    await app.state.tiktok.upload(upload_url, tmp, size)
                    store().update_job(
                        job["id"], "PROCESSING:" + scope, publish_id=publish_id
                    )
                except HTTPException as exc:
                    # Initialization may have succeeded upstream; never automatically repost.
                    store().update_job(
                        job["id"],
                        "CHECK_STATUS:" + scope
                        if store().job(s["account"], job["id"])["publish_id"]
                        else "INIT_UNCERTAIN",
                    )
                    return JSONResponse(
                        {
                            "detail": exc.detail,
                            "job": public_job(store().job(s["account"], job["id"])),
                        },
                        status_code=exc.status_code,
                        headers=exc.headers,
                    )
                return public_job(store().job(s["account"], job["id"]))

    @app.get("/api/jobs")
    def jobs(request: Request):
        s = session(request)
        return [public_job(j) for j in store().jobs(s["account"])]

    @app.get("/api/jobs/{job_id}")
    async def status(request: Request, job_id: str):
        s = session(request)
        job = store().job(s["account"], job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if not job["publish_id"] or job["phase"] in (
            "PUBLISH_COMPLETE",
            "FAILED",
        ):
            return public_job(job)
        # Respect TikTok's 30 status requests/minute/user across all job IDs.
        now = time.monotonic()
        if now - app.state.status_checks.get(s["account"], 0) < 2.1:
            return public_job(job)
        app.state.status_checks[s["account"]] = now
        scope = job["phase"].split(":")[-1]
        result = await app.state.tiktok.status(s["account"], job["publish_id"], scope)
        terminal = result["status"] in (
            "PUBLISH_COMPLETE",
            "FAILED",
        )
        store().update_job(
            job_id,
            result["status"] if terminal else result["status"] + ":" + scope,
            result=result,
        )
        return public_job(store().job(s["account"], job_id))

    return app


app = create_app()
