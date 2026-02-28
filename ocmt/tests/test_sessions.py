"""Tests for session management."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from ocmt.sessions.keys import build_session_key, derive_chat_type, parse_session_key
from ocmt.sessions.store import SessionStore
from ocmt.sessions.types import SessionEntry
from ocmt.tenants.isolation import init_workspace
from ocmt.tenants.manager import TenantManager
from ocmt.tenants.types import TenantWorkspace


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.sqlite"
    mgr = TenantManager(str(tmp_path / "tenants"), str(db_path))
    yield mgr.db
    mgr.close()


@pytest.fixture
def session_store(db):
    return SessionStore(db)


@pytest.fixture
def workspace(tmp_path):
    ws = TenantWorkspace(tenant_id="test", root=tmp_path / "test-tenant")
    init_workspace(ws)
    return ws


class TestSessionKeys:
    def test_build_session_key(self):
        key = build_session_key("main", "api", "user-123")
        assert key == "agent:main:api:user-user-123"

    def test_build_session_key_defaults(self):
        key = build_session_key()
        assert key == "agent:main:api"

    def test_parse_session_key(self):
        parsed = parse_session_key("agent:main:api:user-123")
        assert parsed is not None
        assert parsed.agent_id == "main"
        assert parsed.channel == "api"

    def test_parse_invalid_key(self):
        assert parse_session_key("") is None
        assert parse_session_key("invalid") is None
        assert parse_session_key("x:y") is None

    def test_derive_chat_type(self):
        assert derive_chat_type("agent:main:api:user-123") == "direct"
        assert derive_chat_type("agent:main:telegram:group:12345") == "group"
        assert derive_chat_type("agent:main:discord:channel:xyz") == "channel"


class TestSessionStore:
    def test_get_or_create(self, session_store, db):
        # Ensure tenants table has a row
        db.execute(
            "INSERT INTO tenants (id, name, slug, api_key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t1", "Test", "test", "hash", 0),
        )
        db.commit()

        session = session_store.get_or_create("t1", "agent:main:api")
        assert session.tenant_id == "t1"
        assert session.session_key == "agent:main:api"
        assert session.total_tokens == 0

        # Get same session again
        session2 = session_store.get_or_create("t1", "agent:main:api")
        assert session2.session_key == session.session_key

    def test_update_session(self, session_store, db):
        db.execute(
            "INSERT INTO tenants (id, name, slug, api_key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t1", "Test", "test", "hash", 0),
        )
        db.commit()

        session_store.get_or_create("t1", "agent:main:api")
        session_store.update(
            "t1", "agent:main:api",
            cli_session_id="cli-123",
            total_tokens=1000,
        )

        sessions = session_store.list_sessions("t1")
        assert len(sessions) == 1
        assert sessions[0].cli_session_id == "cli-123"
        assert sessions[0].total_tokens == 1000

    def test_transcript_io(self, session_store, workspace):
        key = "agent:main:api"
        SessionStore.append_transcript(workspace, key, "user", "Hello")
        SessionStore.append_transcript(workspace, key, "assistant", "Hi there!")

        transcript = SessionStore.read_transcript(workspace, key)
        assert len(transcript) == 2
        assert transcript[0]["role"] == "user"
        assert transcript[0]["content"] == "Hello"
        assert transcript[1]["role"] == "assistant"

    def test_transcript_last_n(self, session_store, workspace):
        key = "agent:main:test"
        for i in range(10):
            SessionStore.append_transcript(workspace, key, "user", f"msg-{i}")

        last3 = SessionStore.read_transcript(workspace, key, last_n=3)
        assert len(last3) == 3
        assert last3[0]["content"] == "msg-7"
