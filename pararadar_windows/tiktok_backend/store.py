"""Encrypted, persistent tokens; hashed opaque browser credentials; owned upload jobs."""

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path, key):
        self.cipher = Fernet(key.encode())
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create with restrictive permissions before SQLite writes credential data.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS oauth_states (
                    hash TEXT PRIMARY KEY, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, tokens BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verified_tests (
                    account TEXT PRIMARY KEY, job_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, account TEXT NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    publish_id TEXT, phase TEXT NOT NULL,
                    result TEXT, created REAL NOT NULL,
                    UNIQUE(account, request_key)
                );
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def new_state(self):
        value = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("DELETE FROM oauth_states WHERE expires < ?", (time.time(),))
            db.execute(
                "INSERT INTO oauth_states VALUES (?, ?)",
                (digest(value), time.time() + 600),
            )
        return value

    def consume_state(self, value):
        with self.db() as db:
            row = db.execute(
                "DELETE FROM oauth_states WHERE hash = ? AND expires > ? RETURNING hash",
                (digest(value), time.time()),
            ).fetchone()
            return row is not None

    def save_tokens(self, account, tokens):
        encrypted = self.cipher.encrypt(json.dumps(tokens).encode())
        with self.db() as db:
            db.execute(
                "INSERT INTO accounts VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET tokens=excluded.tokens",
                (account, encrypted),
            )

    def tokens(self, account):
        with self.db() as db:
            row = db.execute(
                "SELECT tokens FROM accounts WHERE id=?", (account,)
            ).fetchone()
        return json.loads(self.cipher.decrypt(row["tokens"])) if row else None

    def new_session(self, account, ttl):
        value, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (digest(value), account, csrf, time.time() + ttl),
            )
        return value

    def session(self, value):
        if not value:
            return None
        with self.db() as db:
            row = db.execute(
                "SELECT account, csrf FROM sessions WHERE hash=? AND expires>?",
                (digest(value), time.time()),
            ).fetchone()
        return dict(row) if row else None

    def forget_session(self, value):
        if value:
            with self.db() as db:
                db.execute("DELETE FROM sessions WHERE hash=?", (digest(value),))

    def disconnect(self, account):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE account=?", (account,))
            db.execute("DELETE FROM accounts WHERE id=?", (account,))
            db.execute("DELETE FROM jobs WHERE account=?", (account,))
            db.execute("DELETE FROM verified_tests WHERE account=?", (account,))

    def new_job(self, account, request_key, fingerprint, single_test=False):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if single_test:
                existing = db.execute(
                    "SELECT request_key FROM jobs WHERE account=?", (account,)
                ).fetchall()
                if existing and all(
                    row["request_key"] != request_key for row in existing
                ):
                    raise ValueError(
                        "Single video test is locked; inspect its status before enabling more posts"
                    )
            job_id = secrets.token_urlsafe(18)
            db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, ?, NULL, ?, NULL, ?)",
                (
                    job_id,
                    account,
                    request_key,
                    fingerprint,
                    "INITIALIZING",
                    time.time(),
                ),
            )
            row = db.execute(
                "SELECT * FROM jobs WHERE account=? AND request_key=?",
                (account, request_key),
            ).fetchone()
        return dict(row), row["id"] == job_id

    def mark_verified(self, account, job_id):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO verified_tests VALUES (?, ?)", (account, job_id)
            )

    def verified(self, account):
        with self.db() as db:
            return (
                db.execute(
                    "SELECT 1 FROM verified_tests WHERE account=?", (account,)
                ).fetchone()
                is not None
            )

    def update_job(self, job_id, phase, publish_id=None, result=None):
        with self.db() as db:
            db.execute(
                "UPDATE jobs SET phase=?, publish_id=COALESCE(?, publish_id), result=? WHERE id=?",
                (
                    phase,
                    publish_id,
                    json.dumps(result) if result is not None else None,
                    job_id,
                ),
            )

    def job(self, account, job_id):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE account=? AND id=?", (account, job_id)
            ).fetchone()
        return dict(row) if row else None

    def jobs(self, account):
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE account=? ORDER BY created DESC LIMIT 50",
                (account,),
            ).fetchall()
        return [dict(row) for row in rows]
