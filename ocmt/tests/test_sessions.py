"""Tests for session management."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from ocmt.sessions.keys import build_session_key, parse_session_key
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
    def test_build_session_key_with_user(self):
        key = build_session_key("main", "user-123")
        assert key == "agent:main:user-user-123"

    def test_build_session_key_defaults(self):
        key = build_session_key()
        assert key == "agent:main"

    def test_channel_not_in_key(self):
        """Channel must not affect the session key — isolation is tenant-level."""
        key_api = build_session_key("main", "user-123")
        key_cli = build_session_key("main", "user-123")
        assert key_api == key_cli
        assert "api" not in key_api
        assert "cli" not in key_api

    def test_parse_session_key(self):
        parsed = parse_session_key("agent:main:user-alice")
        assert parsed is not None
        assert parsed.agent_id == "main"
        assert parsed.user_id == "alice"

    def test_parse_session_key_no_user(self):
        parsed = parse_session_key("agent:main")
        assert parsed is not None
        assert parsed.agent_id == "main"
        assert parsed.user_id == ""

    def test_parse_invalid_key(self):
        assert parse_session_key("") is None
        assert parse_session_key("invalid") is None


class TestSessionStore:
    def test_get_or_create(self, session_store, db):
        # Ensure tenants table has a row
        db.execute(
            "INSERT INTO tenants (id, name, slug, api_key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t1", "Test", "test", "hash", 0),
        )
        db.commit()

        session = session_store.get_or_create("t1", "agent:main")
        assert session.tenant_id == "t1"
        assert session.session_key == "agent:main"
        assert session.total_tokens == 0

        # Get same session again
        session2 = session_store.get_or_create("t1", "agent:main")
        assert session2.session_key == session.session_key

    def test_update_session(self, session_store, db):
        db.execute(
            "INSERT INTO tenants (id, name, slug, api_key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t1", "Test", "test", "hash", 0),
        )
        db.commit()

        session_store.get_or_create("t1", "agent:main")
        session_store.update(
            "t1", "agent:main",
            cli_session_id="cli-123",
            total_tokens=1000,
        )

        sessions = session_store.list_sessions("t1")
        assert len(sessions) == 1
        assert sessions[0].cli_session_id == "cli-123"
        assert sessions[0].total_tokens == 1000

    def test_transcript_io(self, session_store, workspace):
        key = "agent:main"
        SessionStore.append_transcript(workspace, key, "user", "Hello", channel="api")
        SessionStore.append_transcript(workspace, key, "assistant", "Hi!", channel="api")

        transcript = SessionStore.read_transcript(workspace, key)
        assert len(transcript) == 2
        assert transcript[0]["role"] == "user"
        assert transcript[0]["content"] == "Hello"
        assert transcript[0]["channel"] == "api"
        assert transcript[1]["role"] == "assistant"

    def test_transcript_channel_metadata(self, session_store, workspace):
        """Messages from different channels land in the same transcript."""
        key = "agent:main:user-alice"
        SessionStore.append_transcript(workspace, key, "user", "from CLI", channel="cli")
        SessionStore.append_transcript(workspace, key, "assistant", "reply 1", channel="cli")
        SessionStore.append_transcript(workspace, key, "user", "from API", channel="api")
        SessionStore.append_transcript(workspace, key, "assistant", "reply 2", channel="api")

        transcript = SessionStore.read_transcript(workspace, key)
        assert len(transcript) == 4
        assert transcript[0]["channel"] == "cli"
        assert transcript[2]["channel"] == "api"

    def test_transcript_last_n(self, session_store, workspace):
        key = "agent:main:user-test"
        for i in range(10):
            SessionStore.append_transcript(workspace, key, "user", f"msg-{i}")

        last3 = SessionStore.read_transcript(workspace, key, last_n=3)
        assert len(last3) == 3
        assert last3[0]["content"] == "msg-7"

    def test_same_user_same_session_across_channels(self, session_store, db):
        """A user should get the same session regardless of channel."""
        db.execute(
            "INSERT INTO tenants (id, name, slug, api_key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t1", "Test", "test", "hash", 0),
        )
        db.commit()

        key = build_session_key("main", "alice")
        s1 = session_store.get_or_create("t1", key, channel="cli")
        s2 = session_store.get_or_create("t1", key, channel="api")
        s3 = session_store.get_or_create("t1", key, channel="ws")

        # All resolve to the same session
        assert s1.session_key == s2.session_key == s3.session_key
        # Only one session exists
        assert len(session_store.list_sessions("t1")) == 1
