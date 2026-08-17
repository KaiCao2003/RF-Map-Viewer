"""Shared PI-question access gate primitives.

The browser receives an opaque random token. Only its SHA-256 digest is stored
server-side, so the configured answer and reusable session tokens never appear
in application source or the session database.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

ANSWER_ENV_NAME = "MOUSELINE_LOGIN_ANSWER"
AUTH_GENERATION_ENV_NAME = "MOUSELINE_AUTH_GENERATION"
ANSWER_PLACEHOLDER = "replace-with-private-answer"
CSRF_HEADER_NAME = "X-CSRF-Token"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def normalize_base_path(value: str) -> str:
    candidate = value.strip()
    if candidate in {"", "/"}:
        return ""
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    return candidate.rstrip("/")


class AccessGate:
    """Validate the shared answer and persist hashed browser sessions."""

    def __init__(
        self,
        *,
        answer: str,
        session_db: Path,
        generation: str = "1",
    ) -> None:
        normalized_answer = self._normalized_answer(answer)
        if not normalized_answer or normalized_answer == ANSWER_PLACEHOLDER:
            raise ValueError("the access-gate answer must not be empty")
        normalized_generation = generation.strip()
        if not _GENERATION_PATTERN.fullmatch(normalized_generation):
            raise ValueError("the access-gate generation is invalid")
        self._answer_digest = hashlib.sha256(
            normalized_answer.encode("utf-8")
        ).digest()
        self._generation = normalized_generation
        self.session_db = session_db.expanduser().resolve()
        self._initialize_store()

    @classmethod
    def from_environment(cls, *, session_db: Path) -> AccessGate:
        answer = os.environ.get(ANSWER_ENV_NAME, "")
        if (
            not answer.strip()
            or cls._normalized_answer(answer) == ANSWER_PLACEHOLDER
        ):
            raise RuntimeError(f"{ANSWER_ENV_NAME} must be set for the access gate")
        generation = os.environ.get(AUTH_GENERATION_ENV_NAME, "")
        if not _GENERATION_PATTERN.fullmatch(generation.strip()):
            raise RuntimeError(
                f"{AUTH_GENERATION_ENV_NAME} must be set for the access gate"
            )
        return cls(
            answer=answer,
            session_db=session_db,
            generation=generation,
        )

    @staticmethod
    def _normalized_answer(candidate: str) -> str:
        return unicodedata.normalize("NFKC", candidate).strip().casefold()

    @staticmethod
    def _token_digest(candidate: str) -> bytes:
        return hashlib.sha256(candidate.encode("utf-8")).digest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.session_db, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_store(self) -> None:
        parent_already_existed = self.session_db.parent.exists()
        self.session_db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not parent_already_existed:
            os.chmod(self.session_db.parent, 0o700)
        descriptor = os.open(
            self.session_db,
            os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        os.close(descriptor)
        os.chmod(self.session_db, 0o600)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS access_gate_sessions (
                    token_digest BLOB PRIMARY KEY,
                    csrf_digest BLOB,
                    auth_generation TEXT NOT NULL DEFAULT '',
                    expires_at INTEGER NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(access_gate_sessions)"
                )
            }
            if "csrf_digest" not in columns:
                connection.execute(
                    "ALTER TABLE access_gate_sessions ADD COLUMN csrf_digest BLOB"
                )
            if "auth_generation" not in columns:
                connection.execute(
                    """
                    ALTER TABLE access_gate_sessions
                    ADD COLUMN auth_generation TEXT NOT NULL DEFAULT ''
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS access_gate_sessions_expires_at_idx
                ON access_gate_sessions(expires_at)
                """
            )

    def validate_answer(self, candidate: str) -> bool:
        if len(candidate) > 100:
            return False
        candidate_digest = hashlib.sha256(
            self._normalized_answer(candidate).encode("utf-8")
        ).digest()
        return hmac.compare_digest(self._answer_digest, candidate_digest)

    def issue_session(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM access_gate_sessions WHERE expires_at <= ?",
                (now,),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO access_gate_sessions(
                    token_digest,
                    csrf_digest,
                    auth_generation,
                    expires_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    self._token_digest(token),
                    self._token_digest(csrf_token),
                    self._generation,
                    now + SESSION_MAX_AGE_SECONDS,
                ),
            )
        return token, csrf_token

    def issue_session_token(self) -> str:
        token, _csrf_token = self.issue_session()
        return token

    def validate_session(self, candidate: str | None) -> bool:
        if candidate is None or not candidate or len(candidate) > 256:
            return False
        now = int(time.time())
        digest = self._token_digest(candidate)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT expires_at
                FROM access_gate_sessions
                WHERE token_digest = ? AND auth_generation = ?
                """,
                (digest, self._generation),
            ).fetchone()
            if row is None:
                return False
            if int(row[0]) <= now:
                connection.execute(
                    "DELETE FROM access_gate_sessions WHERE token_digest = ?",
                    (digest,),
                )
                return False
        return True

    def validate_csrf(
        self,
        session_token: str | None,
        csrf_token: str | None,
    ) -> bool:
        if (
            session_token is None
            or csrf_token is None
            or not session_token
            or not csrf_token
            or len(session_token) > 256
            or len(csrf_token) > 256
        ):
            return False
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT csrf_digest, expires_at
                FROM access_gate_sessions
                WHERE token_digest = ? AND auth_generation = ?
                """,
                (self._token_digest(session_token), self._generation),
            ).fetchone()
        if row is None or row[0] is None or int(row[1]) <= int(time.time()):
            return False
        return hmac.compare_digest(
            bytes(row[0]),
            self._token_digest(csrf_token),
        )

    def revoke_session(self, candidate: str | None) -> None:
        if candidate is None or not candidate or len(candidate) > 256:
            return
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM access_gate_sessions WHERE token_digest = ?",
                (self._token_digest(candidate),),
            )


def cookie_value(raw_cookie_headers: list[str], cookie_name: str) -> str | None:
    cookies = SimpleCookie()
    try:
        for raw_header in raw_cookie_headers:
            cookies.load(raw_header)
    except CookieError:
        return None
    session = cookies.get(cookie_name)
    return None if session is None else session.value


def session_cookie_header(
    cookie_name: str,
    token: str,
    *,
    path: str,
    secure: bool,
) -> str:
    cookies = SimpleCookie()
    cookies[cookie_name] = token
    morsel = cookies[cookie_name]
    morsel["max-age"] = str(SESSION_MAX_AGE_SECONDS)
    morsel["path"] = path
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()


def csrf_cookie_header(
    cookie_name: str,
    token: str,
    *,
    path: str,
    secure: bool,
) -> str:
    cookies = SimpleCookie()
    cookies[cookie_name] = token
    morsel = cookies[cookie_name]
    morsel["max-age"] = str(SESSION_MAX_AGE_SECONDS)
    morsel["path"] = path
    morsel["samesite"] = "Strict"
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()


def expired_cookie_header(cookie_name: str, *, path: str, secure: bool) -> str:
    cookies = SimpleCookie()
    cookies[cookie_name] = ""
    morsel = cookies[cookie_name]
    morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    morsel["max-age"] = "0"
    morsel["path"] = path
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()


def same_origin_request(
    *,
    scheme: str,
    host: str | None,
    origin: str | None,
    referer: str | None,
    sec_fetch_site: str | None,
) -> bool:
    fetch_site = (sec_fetch_site or "").casefold()
    if fetch_site == "same-origin":
        return True
    if fetch_site in {"cross-site", "same-site"}:
        return False
    if not host or any(character in host for character in "\r\n/?#@,"):
        return False
    try:
        expected = _origin_identity(urlsplit(f"{scheme}://{host}"))
    except ValueError:
        return False
    if expected is None:
        return False
    if origin:
        try:
            parsed_origin = urlsplit(origin)
        except ValueError:
            return False
        if (
            _origin_identity(parsed_origin) != expected
            or parsed_origin.path not in {"", "/"}
        ):
            return False
    if referer:
        try:
            parsed_referer = urlsplit(referer)
        except ValueError:
            return False
        if _origin_identity(parsed_referer) != expected:
            return False
    return bool(origin or referer)


def _origin_identity(parsed: SplitResult) -> tuple[str, str, int] | None:
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


def safe_return_url(value: str, *, base_path: str) -> str:
    normalized_base = normalize_base_path(base_path)
    fallback = f"{normalized_base}/"
    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        return fallback
    if "\\" in candidate or any(ord(character) < 32 for character in candidate):
        return fallback
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return fallback
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return fallback
    decoded_path = unquote(parsed.path)
    if (
        decoded_path.startswith("//")
        or "\\" in decoded_path
        or any(ord(character) < 32 for character in decoded_path)
    ):
        return fallback
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        return fallback
    required_prefix = f"{normalized_base}/" if normalized_base else "/"
    if not decoded_path.startswith(required_prefix):
        return fallback
    route_path = decoded_path[len(normalized_base) :] if normalized_base else decoded_path
    if route_path in {"/login", "/logout"}:
        return fallback
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.path}{query}"


def login_page_html(
    *,
    app_name: str,
    action: str,
    return_to: str,
    error: bool,
) -> str:
    safe_app_name = html.escape(app_name)
    safe_action = html.escape(action, quote=True)
    safe_return_to = html.escape(return_to, quote=True)
    error_html = (
        '<div id="login-error" class="error" role="alert">'
        "That answer is not correct.</div>"
        if error
        else ""
    )
    invalid_attributes = (
        ' aria-invalid="true" aria-describedby="login-error"' if error else ""
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="referrer" content="no-referrer">
    <title>Lab access · {safe_app_name}</title>
    <style>
      :root {{ color-scheme: light; font-family: ui-sans-serif, system-ui, sans-serif; }}
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
        color: #172033; background: #f4f7fb; }}
      main {{ width: min(92vw, 420px); padding: 2rem; border: 1px solid #d8e0ec;
        border-radius: 16px; background: white; box-shadow: 0 14px 40px #15213a1a; }}
      .brand {{ margin: 0 0 .35rem; color: #526078; font-size: .82rem;
        font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
      h1 {{ margin: 0 0 1.5rem; font-size: 1.7rem; }}
      label, span {{ display: block; }}
      label span {{ margin-bottom: .55rem; font-weight: 650; }}
      input {{ width: 100%; min-height: 44px; padding: .7rem .8rem;
        border: 1px solid #aeb9ca; border-radius: 9px; font: inherit; }}
      button {{ width: 100%; margin-top: 1rem; min-height: 44px; border: 0;
        border-radius: 9px; color: white; background: #2957c8; font: inherit;
        font-weight: 700; cursor: pointer; }}
      .error {{ margin: 0 0 1rem; padding: .75rem; border-radius: 8px;
        color: #8b1e1e; background: #fff0f0; }}
    </style>
  </head>
  <body>
    <main aria-labelledby="login-title">
      <p class="brand">{safe_app_name}</p>
      <h1 id="login-title">Lab access</h1>
      {error_html}
      <form method="post" action="{safe_action}">
        <input type="hidden" name="next" value="{safe_return_to}">
        <label>
          <span>What's the PI's first name?</span>
          <input type="password" name="answer" maxlength="100" autocomplete="off"
            autocapitalize="none" spellcheck="false"{invalid_attributes} required autofocus>
        </label>
        <button type="submit">Continue</button>
      </form>
    </main>
  </body>
</html>
"""
