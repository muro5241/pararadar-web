"""ParaRadar TikTok OAuth sandbox starter. Never put secrets in frontend code."""
import os
import secrets
from urllib.parse import urlencode
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
import httpx

app = FastAPI(title="ParaRadar TikTok Integration", version="0.1.0")
STATE_COOKIE = "pararadar_oauth_state"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

@app.get("/health")
def health():
    return {"ok": True, "integration": "not_authorized"}

@app.get("/auth/tiktok/start")
def start():
    key = os.getenv("TIKTOK_CLIENT_KEY")
    redirect_uri = os.getenv("TIKTOK_REDIRECT_URI")
    if not key or not redirect_uri:
        raise HTTPException(503, "TikTok OAuth environment is not configured")
    state = secrets.token_urlsafe(32)
    params = {
        "client_key": key, "scope": "user.info.basic,video.publish",
        "response_type": "code", "redirect_uri": redirect_uri, "state": state,
    }
    response = RedirectResponse(AUTH_URL + "?" + urlencode(params))
    response.set_cookie(STATE_COOKIE, state, httponly=True, secure=True, samesite="lax", max_age=600)
    return response

@app.get("/auth/tiktok/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    expected = request.cookies.get(STATE_COOKIE)
    if error:
        raise HTTPException(400, "TikTok authorization was declined or failed")
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "Invalid OAuth callback state")
    key, secret, redirect_uri = (os.getenv(k) for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI"))
    if not all((key, secret, redirect_uri)):
        raise HTTPException(503, "TikTok OAuth environment is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(TOKEN_URL, data={
            "client_key": key, "client_secret": secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if response.status_code != 200:
        raise HTTPException(502, "TikTok token exchange failed")
    # Deliberately do not log, display, or store access/refresh tokens.
    # Production implementation requires secure encrypted storage and user sessions.
    result = JSONResponse({"authorized": True, "note": "OAuth exchange succeeded. Publishing is not configured yet."})
    result.delete_cookie(STATE_COOKIE)
    return result
