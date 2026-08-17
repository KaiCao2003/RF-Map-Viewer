from __future__ import annotations

import os
import sqlite3
from http.cookies import SimpleCookie

import pytest

from rfmapping_web.access_gate import (
    ANSWER_ENV_NAME,
    AUTH_GENERATION_ENV_NAME,
    SESSION_MAX_AGE_SECONDS,
    AccessGate,
    cookie_value,
    csrf_cookie_header,
    expired_cookie_header,
    login_page_html,
    safe_return_url,
    same_origin_request,
    session_cookie_header,
)


def test_answer_normalization_and_session_persists(tmp_path) -> None:
    session_db = tmp_path / "private" / "sessions.sqlite3"
    first = AccessGate(answer="  Test PI  ", session_db=session_db)

    assert first.validate_answer("test pi") is True
    assert first.validate_answer("ＴＥＳＴ ＰＩ") is True
    assert first.validate_answer("wrong") is False
    assert first.validate_answer("x" * 101) is False

    token = first.issue_session_token()
    reopened = AccessGate(answer="test pi", session_db=session_db)
    assert reopened.validate_session(token) is True
    assert reopened.validate_session("not-the-token") is False
    assert os.stat(session_db).st_mode & 0o777 == 0o600
    assert os.stat(session_db.parent).st_mode & 0o777 == 0o700


def test_expired_and_revoked_sessions_are_rejected(tmp_path) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    gate = AccessGate(answer="test", session_db=session_db)
    expired_token = gate.issue_session_token()
    with sqlite3.connect(session_db) as connection:
        connection.execute("UPDATE access_gate_sessions SET expires_at = 0")
    assert gate.validate_session(expired_token) is False

    revoked_token = gate.issue_session_token()
    gate.revoke_session(revoked_token)
    assert gate.validate_session(revoked_token) is False


def test_environment_rejects_empty_placeholder_and_missing_generation(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(AUTH_GENERATION_ENV_NAME, "1")
    for answer in ("", "  replace-with-private-answer  "):
        monkeypatch.setenv(ANSWER_ENV_NAME, answer)
        with pytest.raises(RuntimeError, match=ANSWER_ENV_NAME):
            AccessGate.from_environment(session_db=tmp_path / "rejected.sqlite3")

    monkeypatch.setenv(ANSWER_ENV_NAME, "test-only-answer")
    monkeypatch.delenv(AUTH_GENERATION_ENV_NAME)
    with pytest.raises(RuntimeError, match=AUTH_GENERATION_ENV_NAME):
        AccessGate.from_environment(session_db=tmp_path / "rejected.sqlite3")


def test_auth_generation_invalidates_existing_sessions(tmp_path) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    first = AccessGate(
        answer="test-only-answer",
        generation="before-rotation",
        session_db=session_db,
    )
    token, csrf_token = first.issue_session()
    assert first.validate_session(token) is True
    assert first.validate_csrf(token, csrf_token) is True
    assert first.validate_csrf(token, "wrong") is False

    rotated = AccessGate(
        answer="new-test-only-answer",
        generation="after-rotation",
        session_db=session_db,
    )
    assert rotated.validate_session(token) is False
    assert rotated.validate_csrf(token, csrf_token) is False


def test_cookie_is_private_path_scoped_and_valid_for_thirty_days() -> None:
    header = session_cookie_header(
        "test_session",
        "opaque-token",
        path="/test-app",
        secure=False,
    )
    parsed = SimpleCookie()
    parsed.load(header)
    morsel = parsed["test_session"]

    assert morsel.value == "opaque-token"
    assert morsel["max-age"] == str(SESSION_MAX_AGE_SECONDS) == "2592000"
    assert morsel["path"] == "/test-app"
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "Strict"
    assert morsel["secure"] == ""
    assert cookie_value(["other=1", "test_session=opaque-token"], "test_session") == "opaque-token"

    secure_header = session_cookie_header(
        "test_session",
        "opaque-token",
        path="/test-app",
        secure=True,
    )
    assert "Secure" in secure_header

    csrf_header = csrf_cookie_header(
        "test_csrf",
        "csrf-token",
        path="/test-app",
        secure=False,
    )
    csrf_cookie = SimpleCookie()
    csrf_cookie.load(csrf_header)
    assert csrf_cookie["test_csrf"]["max-age"] == "2592000"
    assert csrf_cookie["test_csrf"]["path"] == "/test-app"
    assert csrf_cookie["test_csrf"]["httponly"] == ""
    assert csrf_cookie["test_csrf"]["samesite"] == "Strict"

    expired = expired_cookie_header(
        "test_session", path="/test-app", secure=False
    )
    assert "Max-Age=0" in expired
    assert "Path=/test-app" in expired
    assert "HttpOnly" in expired
    assert "SameSite=Strict" in expired


def test_return_url_cannot_escape_application_prefix() -> None:
    base_path = "/test-app"
    assert (
        safe_return_url("/test-app/jobs?state=running", base_path=base_path)
        == "/test-app/jobs?state=running"
    )
    for candidate in (
        "https://example.com/",
        "//example.com/",
        "/other-app/",
        "/test-app/login",
        "/test-app/logout",
        "/test-app/%5c%5cexample.com/",
        "/test-app/%2e%2e/other-app/",
        "/test-app/.%2e/other-app/",
        "/test-app/%2e./other-app/",
        "/test-app/#fragment",
    ):
        assert safe_return_url(candidate, base_path=base_path) == "/test-app/"


def test_login_page_contains_question_but_not_answer() -> None:
    page = login_page_html(
        app_name="Test App",
        action="/test-app/login",
        return_to="/test-app/",
        error=False,
    )
    assert "What's the PI's first name?" in page
    assert "test pi" not in page.casefold()
    assert 'action="/test-app/login"' in page


def test_same_origin_requires_browser_evidence() -> None:
    common = {
        "scheme": "https",
        "host": "lab.example:443",
        "origin": None,
        "referer": None,
        "sec_fetch_site": None,
    }
    assert same_origin_request(**(common | {"sec_fetch_site": "same-origin"}))
    assert same_origin_request(
        **(common | {"origin": "https://lab.example", "sec_fetch_site": ""})
    )
    assert not same_origin_request(
        **(common | {"origin": "https://evil.example", "sec_fetch_site": ""})
    )
    assert not same_origin_request(**(common | {"sec_fetch_site": "same-site"}))
    assert not same_origin_request(**common)
