"""Runtime configuration. Credentials are never defaults or committed files."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    client_key: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    encryption_key: str = ""
    database_path: str = "/var/lib/pararadar/tokens.sqlite3"
    public_base_url: str = ""
    allow_public_posts: bool = False
    session_ttl: int = 86400
    max_video_bytes: int = 1024 * 1024 * 1024

    @classmethod
    def from_env(cls):
        return cls(
            client_key=os.getenv("TIKTOK_CLIENT_KEY", ""),
            client_secret=os.getenv("TIKTOK_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("TIKTOK_REDIRECT_URI", ""),
            encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY", ""),
            database_path=os.getenv(
                "DATABASE_PATH", "/var/lib/pararadar/tokens.sqlite3"
            ),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            allow_public_posts=os.getenv("ALLOW_PUBLIC_POSTS", "false").lower()
            == "true",
            max_video_bytes=int(os.getenv("MAX_VIDEO_BYTES", str(1024 * 1024 * 1024))),
        )

    @property
    def configured(self):
        return all(
            (
                self.client_key,
                self.client_secret,
                self.redirect_uri,
                self.encryption_key,
                self.public_base_url,
            )
        )

    def validate(self):
        if not 1 <= self.max_video_bytes <= 4 * 1024**3 or self.session_ttl < 1:
            raise ValueError("Invalid upload or session limit")
        if not self.configured:
            return
        base, callback = urlsplit(self.public_base_url), urlsplit(self.redirect_uri)
        if (
            base.scheme != "https"
            or not base.netloc
            or base.username
            or base.password
            or base.path not in ("", "/")
            or base.query
            or base.fragment
            or callback.scheme != "https"
            or callback.netloc != base.netloc
            or callback.path != "/auth/tiktok/callback"
            or callback.query
            or callback.fragment
        ):
            raise ValueError(
                "PUBLIC_BASE_URL and callback must use the same HTTPS origin"
            )
        if not Path(self.database_path).is_absolute():
            raise ValueError(
                "DATABASE_PATH must be absolute and outside the public site"
            )
        backend = Path(__file__).resolve().parent
        # The source checkout nests backend beside the static site; Docker installs it at /app.
        site = backend.parent if (backend.parent / "index.html").is_file() else backend
        if Path(self.database_path).resolve().is_relative_to(site):
            raise ValueError("DATABASE_PATH must be outside the static site directory")
