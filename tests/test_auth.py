"""Tests for session persistence, login preferences, and session validation."""

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


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    """Point auth at a throwaway preferences file under tmp_path."""
    auth_dir = tmp_path / "config"
    path = auth_dir / "prefs.json"
    monkeypatch.setattr(auth_module, "AUTH_DIR", auth_dir)
    monkeypatch.setattr(auth_module, "PREFS_FILE", path)
    return path


@pytest.fixture
def answers(monkeypatch):
    """Script the replies input() returns, in order."""

    def _set(*replies):
        queued = list(replies)

        def fake_input(prompt=""):
            assert queued, f"no scripted answer left for {prompt!r}"
            return queued.pop(0)

        monkeypatch.setattr("builtins.input", fake_input)
        return queued

    return _set


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


class TestLoadPrefs:
    def test_returns_empty_when_no_file_exists(self, prefs_file):
        assert auth_module.load_prefs() == {}

    def test_round_trips_both_fields(self, prefs_file):
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "sms"})
        assert auth_module.load_prefs() == {
            "email": "paul@example.com",
            "two_factor_mode": "sms",
        }

    def test_returns_empty_on_corrupt_json(self, prefs_file):
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text("{not json")
        assert auth_module.load_prefs() == {}

    def test_returns_empty_when_the_file_is_not_an_object(self, prefs_file):
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps(["paul@example.com"]))
        assert auth_module.load_prefs() == {}

    def test_drops_an_unknown_two_factor_mode(self, prefs_file):
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps({"two_factor_mode": "carrier-pigeon"}))
        assert auth_module.load_prefs() == {}

    def test_drops_fields_of_the_wrong_type(self, prefs_file):
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps({"email": 42, "two_factor_mode": None}))
        assert auth_module.load_prefs() == {}

    def test_drops_a_blank_email(self, prefs_file):
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps({"email": "   "}))
        assert auth_module.load_prefs() == {}

    def test_ignores_unrecognized_fields(self, prefs_file):
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps({"email": "paul@example.com", "password": "hunter2"}))
        assert auth_module.load_prefs() == {"email": "paul@example.com"}

    def test_normalizes_case_and_surrounding_space(self, prefs_file):
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(
            json.dumps({"email": " paul@example.com ", "two_factor_mode": "SMS"})
        )
        assert auth_module.load_prefs() == {
            "email": "paul@example.com",
            "two_factor_mode": "sms",
        }


class TestSavePrefs:
    def test_file_is_owner_readable_only(self, prefs_file):
        auth_module.save_prefs({"email": "paul@example.com"})
        assert stat.S_IMODE(prefs_file.stat().st_mode) == 0o600

    def test_saving_nothing_removes_the_file(self, prefs_file):
        auth_module.save_prefs({"email": "paul@example.com"})
        auth_module.save_prefs({})
        assert not prefs_file.exists()


class TestUpdatePrefs:
    def test_adds_a_field_without_touching_the_other(self, prefs_file):
        auth_module.save_prefs({"email": "paul@example.com"})
        auth_module.update_prefs(two_factor_mode="sms")
        assert auth_module.load_prefs() == {
            "email": "paul@example.com",
            "two_factor_mode": "sms",
        }

    def test_replaces_an_existing_value(self, prefs_file):
        auth_module.save_prefs({"email": "old@example.com"})
        auth_module.update_prefs(email="new@example.com")
        assert auth_module.load_prefs()["email"] == "new@example.com"

    def test_none_forgets_just_that_field(self, prefs_file):
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "sms"})
        auth_module.update_prefs(email=None)
        assert auth_module.load_prefs() == {"two_factor_mode": "sms"}

    def test_returns_the_prefs_it_saved(self, prefs_file):
        assert auth_module.update_prefs(email="paul@example.com") == {
            "email": "paul@example.com"
        }

    def test_forgetting_the_last_field_removes_the_file(self, prefs_file):
        auth_module.save_prefs({"email": "paul@example.com"})
        assert auth_module.update_prefs(email=None) == {}
        assert not prefs_file.exists()


class TestClearPrefs:
    def test_removes_the_file(self, prefs_file):
        auth_module.save_prefs({"email": "paul@example.com"})
        auth_module.clear_prefs()
        assert auth_module.load_prefs() == {}

    def test_is_a_no_op_when_there_is_nothing_to_clear(self, prefs_file):
        auth_module.clear_prefs()  # must not raise
        assert not prefs_file.exists()

    def test_leaves_the_session_alone(self, prefs_file, session_file):
        auth_module.save_session({"cookie": "abc"}, "csrf")
        auth_module.save_prefs({"email": "paul@example.com"})
        auth_module.clear_prefs()
        assert session_file.exists()


class TestFormatPrefs:
    def test_says_how_preferences_get_saved_when_there_are_none(self, prefs_file):
        assert "No saved login preferences." in auth_module.format_prefs({})

    def test_shows_both_values_and_how_to_change_them(self, prefs_file):
        out = auth_module.format_prefs(
            {"email": "paul@example.com", "two_factor_mode": "sms"}
        )
        assert "paul@example.com" in out
        assert "sms" in out
        assert "--clear" in out

    def test_marks_a_missing_field_rather_than_dropping_the_row(self, prefs_file):
        out = auth_module.format_prefs({"email": "paul@example.com"})
        assert "2FA code   —" in out


class TestPromptWithDefault:
    def test_enter_keeps_the_saved_value(self, answers):
        answers("")
        assert auth_module.prompt_with_default("Empower email", "paul@example.com") == (
            "paul@example.com",
            True,
        )

    def test_a_typed_value_replaces_the_saved_one(self, answers):
        answers("new@example.com")
        assert auth_module.prompt_with_default("Empower email", "paul@example.com") == (
            "new@example.com",
            True,
        )

    def test_surrounding_space_is_trimmed(self, answers):
        answers("  new@example.com  ")
        value, _ = auth_module.prompt_with_default("Empower email", None)
        assert value == "new@example.com"

    def test_the_saved_value_is_offered_as_the_prompt_default(self, monkeypatch):
        seen = []
        monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(prompt) or "")
        auth_module.prompt_with_default("Empower email", "paul@example.com")
        assert seen == ["Empower email [paul@example.com]: "]

    def test_no_default_is_shown_when_nothing_is_saved(self, monkeypatch):
        seen = []
        monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(prompt) or "x")
        auth_module.prompt_with_default("Empower email", None)
        assert seen == ["Empower email: "]

    def test_forget_drops_the_saved_value_and_asks_again(self, answers, capsys):
        answers(auth_module.FORGET, "new@example.com")
        assert auth_module.prompt_with_default("Empower email", "paul@example.com") == (
            "new@example.com",
            False,
        )
        assert "Forgot the saved empower email." in capsys.readouterr().out

    def test_forget_re_prompts_without_the_old_default(self, monkeypatch):
        seen = []
        replies = iter([auth_module.FORGET, "new@example.com"])

        def fake_input(prompt=""):
            seen.append(prompt)
            return next(replies)

        monkeypatch.setattr("builtins.input", fake_input)
        auth_module.prompt_with_default("Empower email", "paul@example.com")
        assert seen[1] == "Empower email: "

    def test_an_empty_answer_with_nothing_saved_asks_again(self, answers, capsys):
        answers("", "paul@example.com")
        value, _ = auth_module.prompt_with_default("Empower email", None)
        assert value == "paul@example.com"
        assert "Empower email is required." in capsys.readouterr().out


class TestPromptTwoFactorMode:
    def test_enter_takes_the_saved_mode(self, answers):
        answers("")
        assert auth_module.prompt_two_factor_mode("email") == ("email", True)

    def test_enter_defaults_to_sms_when_nothing_is_saved(self, answers):
        answers("")
        assert auth_module.prompt_two_factor_mode(None) == ("sms", True)

    def test_numbers_pick_a_mode(self, answers):
        answers("2")
        assert auth_module.prompt_two_factor_mode(None) == ("email", True)

    def test_names_pick_a_mode(self, answers):
        answers("SMS")
        assert auth_module.prompt_two_factor_mode("email") == ("sms", True)

    def test_the_saved_mode_is_marked_in_the_prompt(self, monkeypatch):
        seen = []
        monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(prompt) or "")
        auth_module.prompt_two_factor_mode("email")
        assert seen == ["Choice [2, saved]: "]

    def test_nothing_is_marked_saved_when_nothing_is_saved(self, monkeypatch):
        seen = []
        monkeypatch.setattr("builtins.input", lambda prompt="": seen.append(prompt) or "")
        auth_module.prompt_two_factor_mode(None)
        assert seen == ["Choice [1]: "]

    def test_forget_drops_the_saved_mode_and_asks_again(self, answers, capsys):
        answers(auth_module.FORGET, "2")
        assert auth_module.prompt_two_factor_mode("sms") == ("email", False)
        assert "Forgot the saved 2FA choice." in capsys.readouterr().out

    def test_forget_falls_back_to_the_plain_default(self, monkeypatch):
        seen = []
        replies = iter([auth_module.FORGET, ""])

        def fake_input(prompt=""):
            seen.append(prompt)
            return next(replies)

        monkeypatch.setattr("builtins.input", fake_input)
        assert auth_module.prompt_two_factor_mode("email") == ("sms", False)
        assert seen[1] == "Choice [1]: "

    def test_an_unknown_answer_asks_again(self, answers, capsys):
        answers("9", "1")
        assert auth_module.prompt_two_factor_mode(None) == ("sms", True)
        assert "Enter 1 or 2." in capsys.readouterr().out


class FakeLoginClient:
    """Stand-in for PersonalCapital across a whole interactive_auth run."""

    def __init__(self, needs_two_factor=False):
        self.needs_two_factor = needs_two_factor
        self.login_args = None
        self.challenged = None

    def login(self, email, password):
        self.login_args = (email, password)
        if self.needs_two_factor:
            raise auth_module.RequireTwoFactorException("2fa")

    def two_factor_challenge(self, mode):
        self.challenged = mode

    def two_factor_authenticate(self, mode, code):
        pass

    def authenticate_password(self, password):
        return FakeResponse(ok({}))

    def get_session(self):
        return {"cookie": "abc"}

    def get_csrf(self):
        return "csrf-token"


@pytest.fixture
def login_flow(monkeypatch, prefs_file, session_file):
    """Run interactive_auth against a fake Empower that always succeeds."""

    def _install(needs_two_factor=False):
        pc = FakeLoginClient(needs_two_factor=needs_two_factor)
        monkeypatch.setattr(auth_module, "PersonalCapital", lambda: pc)
        monkeypatch.setattr(auth_module.getpass, "getpass", lambda prompt="": "hunter2")
        monkeypatch.setattr(auth_module, "create_authenticated_client", lambda: pc)
        return pc

    return _install


class TestInteractiveAuthPreferences:
    def test_saves_the_email_that_was_typed(self, login_flow, answers):
        login_flow()
        answers("paul@example.com")
        auth_module.interactive_auth()
        assert auth_module.load_prefs() == {"email": "paul@example.com"}

    def test_saves_the_email_passed_as_an_argument(self, login_flow, answers):
        login_flow()
        auth_module.interactive_auth(email="paul@example.com")
        assert auth_module.load_prefs() == {"email": "paul@example.com"}

    def test_the_saved_email_is_reused_without_retyping(self, login_flow, answers):
        pc = login_flow()
        auth_module.save_prefs({"email": "paul@example.com"})
        answers("")
        auth_module.interactive_auth()
        assert pc.login_args == ("paul@example.com", "hunter2")

    def test_a_new_email_replaces_the_saved_one(self, login_flow, answers):
        login_flow()
        auth_module.save_prefs({"email": "old@example.com"})
        answers("new@example.com")
        auth_module.interactive_auth()
        assert auth_module.load_prefs()["email"] == "new@example.com"

    def test_forgetting_the_email_at_the_prompt_drops_it(self, login_flow, answers):
        login_flow()
        auth_module.save_prefs({"email": "paul@example.com"})
        answers(auth_module.FORGET, "once@example.com")
        auth_module.interactive_auth()
        assert auth_module.load_prefs() == {}

    def test_saves_the_two_factor_mode_that_was_chosen(self, login_flow, answers):
        login_flow(needs_two_factor=True)
        answers("paul@example.com", "2", "123456")
        auth_module.interactive_auth()
        assert auth_module.load_prefs()["two_factor_mode"] == "email"

    def test_the_saved_two_factor_mode_is_reused_without_retyping(self, login_flow, answers):
        pc = login_flow(needs_two_factor=True)
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "email"})
        answers("", "", "123456")
        auth_module.interactive_auth()
        assert pc.challenged == auth_module.TwoFactorVerificationModeEnum.EMAIL

    def test_the_two_factor_argument_skips_the_prompt(self, login_flow, answers):
        pc = login_flow(needs_two_factor=True)
        answers("paul@example.com", "123456")
        auth_module.interactive_auth(two_factor_mode="email")
        assert pc.challenged == auth_module.TwoFactorVerificationModeEnum.EMAIL
        assert auth_module.load_prefs()["two_factor_mode"] == "email"

    def test_forgetting_the_two_factor_mode_drops_it(self, login_flow, answers):
        login_flow(needs_two_factor=True)
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "email"})
        answers("", auth_module.FORGET, "1", "123456")
        auth_module.interactive_auth()
        assert auth_module.load_prefs() == {"email": "paul@example.com"}

    def test_a_login_without_2fa_leaves_the_saved_mode_alone(self, login_flow, answers):
        login_flow()
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "email"})
        answers("")
        auth_module.interactive_auth()
        assert auth_module.load_prefs()["two_factor_mode"] == "email"

    def test_no_remember_ignores_the_saved_email(self, login_flow, answers, monkeypatch):
        pc = login_flow()
        auth_module.save_prefs({"email": "paul@example.com"})
        answers("other@example.com")
        auth_module.interactive_auth(remember=False)
        assert pc.login_args == ("other@example.com", "hunter2")

    def test_no_remember_leaves_the_saved_preferences_untouched(self, login_flow, answers):
        login_flow()
        auth_module.save_prefs({"email": "paul@example.com"})
        answers("other@example.com")
        auth_module.interactive_auth(remember=False)
        assert auth_module.load_prefs() == {"email": "paul@example.com"}

    def test_accept_defaults_uses_the_saved_email_without_asking(self, login_flow, answers):
        pc = login_flow()
        auth_module.save_prefs({"email": "paul@example.com"})
        answers()  # any prompt would fail: nothing is scripted
        auth_module.interactive_auth(accept_defaults=True)
        assert pc.login_args == ("paul@example.com", "hunter2")

    def test_accept_defaults_echoes_the_email_it_used(self, login_flow, answers, capsys):
        login_flow()
        auth_module.save_prefs({"email": "paul@example.com"})
        answers()
        auth_module.interactive_auth(accept_defaults=True)
        assert "Empower email: paul@example.com (saved)" in capsys.readouterr().out

    def test_accept_defaults_uses_the_saved_two_factor_mode(self, login_flow, answers):
        pc = login_flow(needs_two_factor=True)
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "email"})
        answers("123456")  # only the 2FA code is asked for
        auth_module.interactive_auth(accept_defaults=True)
        assert pc.challenged == auth_module.TwoFactorVerificationModeEnum.EMAIL

    def test_accept_defaults_says_which_two_factor_mode_it_used(
        self, login_flow, answers, capsys
    ):
        login_flow(needs_two_factor=True)
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "email"})
        answers("123456")
        auth_module.interactive_auth(accept_defaults=True)
        assert "Using your saved choice: email." in capsys.readouterr().out

    def test_accept_defaults_keeps_what_it_accepted(self, login_flow, answers):
        login_flow(needs_two_factor=True)
        saved = {"email": "paul@example.com", "two_factor_mode": "email"}
        auth_module.save_prefs(saved)
        answers("123456")
        auth_module.interactive_auth(accept_defaults=True)
        assert auth_module.load_prefs() == saved

    def test_accept_defaults_still_asks_for_an_unsaved_email(self, login_flow, answers, capsys):
        pc = login_flow()
        answers("paul@example.com")
        auth_module.interactive_auth(accept_defaults=True)
        assert pc.login_args == ("paul@example.com", "hunter2")
        assert "Nothing saved to accept yet" in capsys.readouterr().out

    def test_accept_defaults_still_asks_for_an_unsaved_two_factor_mode(
        self, login_flow, answers
    ):
        pc = login_flow(needs_two_factor=True)
        auth_module.save_prefs({"email": "paul@example.com"})
        answers("2", "123456")
        auth_module.interactive_auth(accept_defaults=True)
        assert pc.challenged == auth_module.TwoFactorVerificationModeEnum.EMAIL

    def test_an_argument_still_wins_over_the_saved_email(self, login_flow, answers):
        pc = login_flow()
        auth_module.save_prefs({"email": "saved@example.com"})
        answers()
        auth_module.interactive_auth(email="flag@example.com", accept_defaults=True)
        assert pc.login_args == ("flag@example.com", "hunter2")

    def test_no_remember_leaves_accept_defaults_nothing_to_accept(
        self, login_flow, answers
    ):
        pc = login_flow()
        auth_module.save_prefs({"email": "saved@example.com"})
        answers("typed@example.com")
        auth_module.interactive_auth(accept_defaults=True, remember=False)
        assert pc.login_args == ("typed@example.com", "hunter2")

    def test_a_failed_login_saves_nothing(self, login_flow, answers, monkeypatch):
        login_flow()
        monkeypatch.setattr(auth_module, "create_authenticated_client", lambda: None)
        answers("paul@example.com")
        with pytest.raises(RuntimeError):
            auth_module.interactive_auth()
        assert auth_module.load_prefs() == {}


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
