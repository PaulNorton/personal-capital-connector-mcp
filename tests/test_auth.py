"""Tests for session persistence and the session-validation path in auth.py."""

import json
import stat

import pytest

from personal_capital_connector import auth as auth_module

from conftest import FakeResponse, accounts_payload, fail, ok


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    """Point auth at a throwaway session file under tmp_path."""
    auth_dir = tmp_path / "config"
    path = auth_dir / "session.json"
    monkeypatch.setattr(auth_module, "AUTH_DIR", auth_dir)
    monkeypatch.setattr(auth_module, "SESSION_FILE", path)
    return path


class FakePersonalCapital:
    """Minimal stand-in for the PersonalCapital client used during validation."""

    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.session = None
        self.csrf = None

    def set_session(self, session):
        self.session = session

    def set_csrf(self, csrf):
        self.csrf = csrf

    def fetch(self, endpoint, data=None):
        if self.raises:
            raise self.raises
        return FakeResponse(self.response)


@pytest.fixture
def fake_pc(monkeypatch):
    """Install a FakePersonalCapital factory and expose the instance it built."""
    built = []

    def _install(**kwargs):
        pc = FakePersonalCapital(**kwargs)
        built.append(pc)
        monkeypatch.setattr(auth_module, "PersonalCapital", lambda: pc)
        return pc

    return _install


class TestSaveSession:
    def test_creates_the_directory_and_writes_both_fields(self, session_file):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        data = json.loads(session_file.read_text())
        assert data == {"session": {"cookie": "abc"}, "csrf": "csrf-token"}

    def test_file_is_owner_readable_only(self, session_file):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        mode = stat.S_IMODE(session_file.stat().st_mode)
        assert mode == 0o600

    def test_overwrites_an_existing_session(self, session_file):
        auth_module.save_session({"cookie": "old"}, "old-csrf")
        auth_module.save_session({"cookie": "new"}, "new-csrf")
        assert json.loads(session_file.read_text())["csrf"] == "new-csrf"

    def test_overwrite_does_not_leave_a_previously_wide_open_file_readable(
        self, session_file
    ):
        auth_module.save_session({"cookie": "old"}, "old-csrf")
        session_file.chmod(0o644)
        auth_module.save_session({"cookie": "new"}, "new-csrf")
        assert stat.S_IMODE(session_file.stat().st_mode) == 0o600

    def test_no_truncated_leftovers_when_the_new_payload_is_shorter(self, session_file):
        auth_module.save_session({"cookie": "x" * 200}, "old-csrf")
        auth_module.save_session({"cookie": "short"}, "new-csrf")
        assert json.loads(session_file.read_text()) == {
            "session": {"cookie": "short"},
            "csrf": "new-csrf",
        }

    def test_directory_is_not_group_or_world_accessible(self, session_file):
        auth_module.save_session({"cookie": "abc"}, "csrf")
        assert stat.S_IMODE(session_file.parent.stat().st_mode) == 0o700


class TestLoadSession:
    def test_returns_none_when_no_file_exists(self, session_file):
        assert auth_module.load_session() is None

    def test_round_trips_a_saved_session(self, session_file):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        assert auth_module.load_session() == ({"cookie": "abc"}, "csrf-token")

    def test_returns_none_on_corrupt_json(self, session_file):
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("{not json")
        assert auth_module.load_session() is None

    def test_returns_none_when_keys_are_missing(self, session_file):
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(json.dumps({"session": {}}))
        assert auth_module.load_session() is None


class TestClearSession:
    def test_removes_the_file(self, session_file):
        auth_module.save_session({"cookie": "abc"}, "csrf")
        auth_module.clear_session()
        assert not session_file.exists()

    def test_is_a_no_op_when_there_is_nothing_to_clear(self, session_file):
        auth_module.clear_session()  # must not raise
        assert not session_file.exists()


class TestCreateAuthenticatedClient:
    def test_returns_none_without_a_saved_session(self, session_file, fake_pc):
        assert auth_module.create_authenticated_client() is None

    def test_returns_the_client_when_the_session_validates(self, session_file, fake_pc):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        pc = fake_pc(response=ok(accounts_payload()))
        assert auth_module.create_authenticated_client() is pc

    def test_restores_cookies_and_csrf_onto_the_client(self, session_file, fake_pc):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        pc = fake_pc(response=ok(accounts_payload()))
        auth_module.create_authenticated_client()
        assert pc.session == {"cookie": "abc"}
        assert pc.csrf == "csrf-token"

    def test_returns_none_when_the_session_is_rejected(self, session_file, fake_pc):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        fake_pc(response=fail(authLevel="USER_IDENTIFIED"))
        assert auth_module.create_authenticated_client() is None

    def test_returns_none_when_the_request_raises(self, session_file, fake_pc):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        fake_pc(raises=ConnectionError("network down"))
        assert auth_module.create_authenticated_client() is None

    def test_returns_none_on_an_empty_response_body(self, session_file, fake_pc):
        auth_module.save_session({"cookie": "abc"}, "csrf-token")
        fake_pc(response={})
        assert auth_module.create_authenticated_client() is None
