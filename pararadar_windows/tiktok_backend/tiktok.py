"""TikTok v2 OAuth and Content Posting API adapter. No credentials in error messages."""

import asyncio
import logging
import re
import time
from contextlib import suppress
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

API = "https://open.tiktokapis.com"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = API + "/v2/oauth/token/"
CHUNK_BYTES = 10 * 1024 * 1024


def chunk_plan(size):
    chunk_size = min(size, CHUNK_BYTES)
    # TikTok appends the remainder to the final chunk, rather than a small extra chunk.
    count = max(1, size // chunk_size)
    if count > 1000:
        raise HTTPException(422, "Video requires too many upload chunks")
    return chunk_size, count


class TikTok:
    def __init__(self, settings, store, client):
        self.settings, self.store, self.client = settings, store, client
        self.refresh_lock = asyncio.Lock()

    async def request(self, method, url, allow_empty=False, retry_safe=False, **kwargs):
        for attempt in range(3 if retry_safe else 1):
            try:
                response = await self.client.request(method, url, **kwargs)
                transient = response.status_code == 429 or response.status_code >= 500
            except httpx.HTTPError:
                response, transient = None, True
            if transient and retry_safe and attempt < 2:
                delay = min(60, 2**attempt)
                if response is not None:
                    with suppress(ValueError):
                        delay = min(
                            60,
                            max(delay, int(response.headers.get("Retry-After", delay))),
                        )
                logging.warning("TikTok safe status/info retry %s", attempt + 1)
                await asyncio.sleep(delay)
                continue
            if response is None:
                logging.warning(
                    "TikTok request failed: network; initialization/refresh is not replayed"
                )
                raise HTTPException(
                    502, "TikTok network request failed; check saved job status"
                ) from None
            break
        if response.status_code >= 400:
            logging.warning("TikTok request rejected: HTTP %s", response.status_code)
        if response.status_code == 429:
            raise HTTPException(
                429,
                "TikTok rate limit reached; retry later",
                headers={"Retry-After": "60"},
            )
        if response.status_code == 401:
            raise HTTPException(401, "TikTok authorization is invalid; reconnect")
        if allow_empty and 200 <= response.status_code < 300 and not response.content:
            return {}
        try:
            body = response.json()
        except ValueError:
            raise HTTPException(502, "TikTok returned an invalid response") from None
        if not isinstance(body, dict):
            raise HTTPException(502, "TikTok returned an invalid response")
        error = body.get("error")
        if response.status_code == 401 or error == "invalid_grant":
            raise HTTPException(401, "TikTok authorization is invalid; reconnect")
        if isinstance(error, str) and error:
            raise HTTPException(502, "TikTok OAuth request was rejected")
        if isinstance(error, dict) and error.get("code") not in (None, "", "ok"):
            logging.warning("TikTok API rejected operation; provider text omitted")
            code = error.get("code")
            if code in (
                "access_token_invalid",
                "access_token_expired",
                "scope_not_authorized",
            ):
                raise HTTPException(
                    401, "TikTok authorization expired or scope missing; reconnect"
                )
            if code in (
                "rate_limit_exceeded",
                "spam_risk_too_many_posts",
                "spam_risk_too_many_pending_share",
            ):
                raise HTTPException(
                    429,
                    "TikTok posting limit reached; retry later",
                    headers={"Retry-After": "60"},
                )
            raise HTTPException(502, "TikTok API could not complete this operation")
        if response.status_code >= 400:
            raise HTTPException(502, "TikTok rejected the request")
        return body

    def normalized_tokens(self, body):
        try:
            access, refresh, account = (
                body[k] for k in ("access_token", "refresh_token", "open_id")
            )
            expires, refresh_expires = (
                int(body["expires_in"]),
                int(body["refresh_expires_in"]),
            )
            scope = body["scope"]
            if not all(
                isinstance(v, str) and v for v in (access, refresh, account, scope)
            ):
                raise ValueError()
            if (
                expires <= 0
                or refresh_expires <= 0
                or not isinstance(body.get("token_type"), str)
                or body["token_type"].lower() != "bearer"
            ):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise HTTPException(502, "TikTok returned incomplete token data") from None
        return account, {
            "access_token": access,
            "refresh_token": refresh,
            "scope": scope,
            "expires_at": time.time() + expires,
            "refresh_expires_at": time.time() + refresh_expires,
        }

    async def exchange(self, code, verifier=None):
        body = await self.request(
            "POST",
            TOKEN_URL,
            data={
                "client_key": self.settings.client_key,
                "client_secret": self.settings.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                **({"code_verifier": verifier} if verifier else {}),
                "redirect_uri": self.settings.redirect_uri,
            },
        )
        account, tokens = self.normalized_tokens(body)
        self.store.save_tokens(account, tokens)
        return account

    async def access(self, account, scope=None, force=False):
        # Deploy one process/worker per SQLite database; lock covers refresh rotation.
        async with self.refresh_lock:
            tokens = self.store.tokens(account)
            if not tokens:
                raise HTTPException(401, "Connect your TikTok account")
            if tokens["expires_at"] <= time.time() + 60 or force:
                if tokens["refresh_expires_at"] <= time.time():
                    self.store.disconnect(account)
                    raise HTTPException(
                        401, "TikTok refresh authorization expired; reconnect"
                    )
                body = await self.request(
                    "POST",
                    TOKEN_URL,
                    data={
                        "client_key": self.settings.client_key,
                        "client_secret": self.settings.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                    },
                )
                refreshed_account, tokens = self.normalized_tokens(body)
                if refreshed_account != account:
                    raise HTTPException(502, "TikTok refresh account mismatch")
                self.store.save_tokens(account, tokens)
            scopes = {s.strip() for s in tokens["scope"].split(",")}
            if scope and scope not in scopes:
                raise HTTPException(
                    403, "Required TikTok scope was not granted; reconnect"
                )
            return tokens["access_token"]

    async def api(self, account, path, payload, scope):
        token = await self.access(account, scope)
        body = await self.request(
            "POST",
            API + path,
            json=payload,
            retry_safe=path
            in (
                "/v2/post/publish/creator_info/query/",
                "/v2/post/publish/status/fetch/",
            ),
            headers={"Authorization": "Bearer " + token},
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise HTTPException(502, "TikTok returned missing API data")
        return data

    async def profile(self, account):
        token = await self.access(account, "user.info.basic")
        body = await self.request(
            "GET",
            API + "/v2/user/info/",
            params={"fields": "open_id,display_name"},
            retry_safe=True,
            headers={"Authorization": "Bearer " + token},
        )
        data = body.get("data")
        user = data.get("user") if isinstance(data, dict) else None
        if (
            not isinstance(user, dict)
            or user.get("open_id") != account
            or not isinstance(user.get("display_name"), str)
        ):
            raise HTTPException(502, "TikTok returned incomplete profile information")
        return {"display_name": user["display_name"]}

    async def creator(self, account):
        data = await self.api(
            account, "/v2/post/publish/creator_info/query/", {}, "video.publish"
        )
        required = (
            "creator_username",
            "creator_nickname",
            "privacy_level_options",
            "comment_disabled",
            "duet_disabled",
            "stitch_disabled",
            "max_video_post_duration_sec",
        )
        if (
            any(k not in data for k in required)
            or not isinstance(data.get("privacy_level_options"), list)
            or any(
                not isinstance(v, str) for v in data.get("privacy_level_options", [])
            )
            or not isinstance(data.get("creator_username"), str)
            or not isinstance(data.get("creator_nickname"), str)
            or any(
                not isinstance(data.get(k), bool)
                for k in ("comment_disabled", "duet_disabled", "stitch_disabled")
            )
            or not isinstance(data.get("max_video_post_duration_sec"), (int, float))
            or data.get("max_video_post_duration_sec", 0) <= 0
        ):
            raise HTTPException(502, "TikTok returned incomplete creator information")
        return data

    async def init(self, account, size, post_info=None):
        chunk_size, count = chunk_plan(size)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": count,
        }
        path = (
            "/v2/post/publish/video/init/"
            if post_info is not None
            else "/v2/post/publish/inbox/video/init/"
        )
        payload = {"source_info": source_info}
        if post_info is not None:
            payload["post_info"] = post_info
        data = await self.api(
            account,
            path,
            payload,
            "video.publish" if post_info is not None else "video.upload",
        )
        try:
            url = urlsplit(data.get("upload_url", ""))
            port = url.port
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(
                502, "TikTok returned an invalid upload destination"
            ) from None
        if (
            not isinstance(data.get("publish_id"), str)
            or not data["publish_id"]
            or url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or port not in (None, 443)
            or url.fragment
            or not url.hostname.endswith(".tiktokapis.com")
        ):
            raise HTTPException(502, "TikTok returned an invalid upload destination")
        return data["publish_id"], data["upload_url"]

    async def upload(self, url, file, size):
        chunk_size, count = chunk_plan(size)
        for number in range(count):
            start = number * chunk_size
            length = size - start if number == count - 1 else chunk_size
            file.seek(start)
            content = file.read(length)
            try:
                response = await self.client.put(
                    url,
                    content=content,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(length),
                        "Content-Range": f"bytes {start}-{start + length - 1}/{size}",
                    },
                )
            except httpx.HTTPError:
                raise HTTPException(
                    502, "Video upload interrupted; check the saved job status"
                ) from None
            expected = 201 if number == count - 1 else 206
            if response.status_code != expected:
                raise HTTPException(
                    502, "TikTok did not accept the video chunk; check job status"
                )

    async def status(self, account, publish_id, scope):
        data = await self.api(
            account, "/v2/post/publish/status/fetch/", {"publish_id": publish_id}, scope
        )
        known = {
            "PROCESSING_UPLOAD",
            "PROCESSING_DOWNLOAD",
            "SEND_TO_USER_INBOX",
            "PUBLISH_COMPLETE",
            "FAILED",
        }
        if data.get("status") not in known:
            raise HTTPException(502, "TikTok returned an unknown publishing status")
        # Do not propagate arbitrary provider fields into stored/displayed results.
        result = {"status": data["status"]}
        if data.get("fail_reason"):
            reason = str(data["fail_reason"])
            result["fail_reason"] = (
                reason
                if re.fullmatch(r"[a-z0-9_]{1,120}", reason)
                else "provider_failure"
            )
        if isinstance(data.get("publicaly_available_post_id"), list):
            result["post_ids"] = [str(v) for v in data["publicaly_available_post_id"]][
                :20
            ]
        return result

    async def revoke(self, account):
        token = await self.access(account)
        await self.request(
            "POST",
            API + "/v2/oauth/revoke/",
            allow_empty=True,
            data={
                "client_key": self.settings.client_key,
                "client_secret": self.settings.client_secret,
                "token": token,
            },
        )
