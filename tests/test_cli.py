"""Tests for the CLI entry point: subcommand dispatch and exit codes."""

import pytest

from personal_capital_connector import auth as auth_module
from personal_capital_connector import cli as cli_module
from personal_capital_connector import server as server_module


@pytest.fixture
def argv(monkeypatch):
    def _set(*args):
        monkeypatch.setattr("sys.argv", ["personal-capital-connector", *args])

    return _set


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    monkeypatch.setattr(auth_module, "SESSION_FILE", path)
    return path


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    auth_dir = tmp_path / "config"
    path = auth_dir / "prefs.json"
    monkeypatch.setattr(auth_module, "AUTH_DIR", auth_dir)
    monkeypatch.setattr(auth_module, "PREFS_FILE", path)
    return path


@pytest.fixture
def record_auth(monkeypatch):
    """Capture the arguments cli.main() hands to interactive_auth."""
    seen = {}

    def fake(email="", two_factor_mode="", remember=True, accept_defaults=False):
        seen.update(
            email=email,
            two_factor_mode=two_factor_mode,
            remember=remember,
            accept_defaults=accept_defaults,
        )

    monkeypatch.setattr(auth_module, "interactive_auth", fake)
    return seen


class TestStatus:
    def test_exits_nonzero_when_no_session_exists(self, argv, session_file, capsys):
        argv("status")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "Not authenticated" in capsys.readouterr().out

    def test_reports_a_valid_session_and_exits_zero(self, argv, session_file, capsys, monkeypatch):
        session_file.write_text("{}")
        monkeypatch.setattr(auth_module, "create_authenticated_client", lambda: object())
        argv("status")
        cli_module.main()
        assert "✓ Session is valid." in capsys.readouterr().out

    def test_exits_nonzero_when_the_session_is_expired(
        self, argv, session_file, capsys, monkeypatch
    ):
        session_file.write_text("{}")
        monkeypatch.setattr(auth_module, "create_authenticated_client", lambda: None)
        argv("status")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "expired" in capsys.readouterr().out


class TestAuth:
    def test_runs_the_interactive_flow(self, argv, capsys, record_auth):
        argv("auth", "--email", "paul@example.com")
        cli_module.main()
        assert record_auth["email"] == "paul@example.com"
        assert "All done!" in capsys.readouterr().out

    def test_email_defaults_to_empty_so_the_flow_can_prompt(self, argv, capsys, record_auth):
        argv("auth")
        cli_module.main()
        assert record_auth["email"] == ""

    def test_two_factor_flag_is_passed_through(self, argv, capsys, record_auth):
        argv("auth", "--2fa", "email")
        cli_module.main()
        assert record_auth["two_factor_mode"] == "email"

    def test_two_factor_defaults_to_empty_so_the_flow_can_prompt(self, argv, capsys, record_auth):
        argv("auth")
        cli_module.main()
        assert record_auth["two_factor_mode"] == ""

    def test_an_unknown_two_factor_mode_is_rejected(self, argv):
        argv("auth", "--2fa", "carrier-pigeon")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2

    def test_preferences_are_remembered_by_default(self, argv, capsys, record_auth):
        argv("auth")
        cli_module.main()
        assert record_auth["remember"] is True

    def test_no_remember_turns_preferences_off(self, argv, capsys, record_auth):
        argv("auth", "--no-remember")
        cli_module.main()
        assert record_auth["remember"] is False

    def test_defaults_are_not_auto_accepted(self, argv, capsys, record_auth):
        argv("auth")
        cli_module.main()
        assert record_auth["accept_defaults"] is False

    def test_accept_defaults_flag_is_passed_through(self, argv, capsys, record_auth):
        argv("auth", "--accept-defaults")
        cli_module.main()
        assert record_auth["accept_defaults"] is True

    def test_dash_y_is_the_same_flag(self, argv, capsys, record_auth):
        argv("auth", "-y")
        cli_module.main()
        assert record_auth["accept_defaults"] is True

    def test_failure_exits_nonzero_with_the_reason(self, argv, capsys, monkeypatch):
        def boom(email="", two_factor_mode="", remember=True, accept_defaults=False):
            raise RuntimeError("bad 2FA code")

        monkeypatch.setattr(auth_module, "interactive_auth", boom)
        argv("auth")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "Authentication failed: bad 2FA code" in capsys.readouterr().out

    def test_ctrl_c_exits_nonzero_without_a_traceback(self, argv, capsys, monkeypatch):
        def cancel(email="", two_factor_mode="", remember=True, accept_defaults=False):
            raise KeyboardInterrupt

        monkeypatch.setattr(auth_module, "interactive_auth", cancel)
        argv("auth")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "Cancelled." in capsys.readouterr().out


class TestPrefs:
    def test_reports_when_nothing_is_saved(self, argv, prefs_file, capsys):
        argv("prefs")
        cli_module.main()
        assert "No saved login preferences." in capsys.readouterr().out

    def test_shows_what_is_saved(self, argv, prefs_file, capsys):
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "sms"})
        argv("prefs")
        cli_module.main()
        out = capsys.readouterr().out
        assert "paul@example.com" in out
        assert "sms" in out

    def test_showing_does_not_change_anything(self, argv, prefs_file, capsys):
        auth_module.save_prefs({"email": "paul@example.com"})
        argv("prefs")
        cli_module.main()
        assert auth_module.load_prefs() == {"email": "paul@example.com"}

    def test_sets_the_email(self, argv, prefs_file, capsys):
        argv("prefs", "--email", "paul@example.com")
        cli_module.main()
        assert auth_module.load_prefs()["email"] == "paul@example.com"

    def test_sets_the_two_factor_mode(self, argv, prefs_file, capsys):
        argv("prefs", "--2fa", "email")
        cli_module.main()
        assert auth_module.load_prefs()["two_factor_mode"] == "email"

    def test_sets_both_at_once(self, argv, prefs_file, capsys):
        argv("prefs", "--email", "paul@example.com", "--2fa", "sms")
        cli_module.main()
        assert auth_module.load_prefs() == {
            "email": "paul@example.com",
            "two_factor_mode": "sms",
        }

    def test_setting_one_leaves_the_other_alone(self, argv, prefs_file, capsys):
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "sms"})
        argv("prefs", "--2fa", "email")
        cli_module.main()
        assert auth_module.load_prefs()["email"] == "paul@example.com"

    def test_clear_with_no_value_forgets_everything(self, argv, prefs_file, capsys):
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "sms"})
        argv("prefs", "--clear")
        cli_module.main()
        assert auth_module.load_prefs() == {}
        assert "No saved login preferences." in capsys.readouterr().out

    def test_clear_email_keeps_the_two_factor_mode(self, argv, prefs_file, capsys):
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "sms"})
        argv("prefs", "--clear", "email")
        cli_module.main()
        assert auth_module.load_prefs() == {"two_factor_mode": "sms"}

    def test_clear_2fa_keeps_the_email(self, argv, prefs_file, capsys):
        auth_module.save_prefs({"email": "paul@example.com", "two_factor_mode": "sms"})
        argv("prefs", "--clear", "2fa")
        cli_module.main()
        assert auth_module.load_prefs() == {"email": "paul@example.com"}

    def test_clearing_leaves_the_session_alone(self, argv, prefs_file, session_file, capsys):
        session_file.write_text("{}")
        auth_module.save_prefs({"email": "paul@example.com"})
        argv("prefs", "--clear")
        cli_module.main()
        assert session_file.exists()

    def test_an_unknown_clear_target_is_rejected(self, argv, prefs_file):
        argv("prefs", "--clear", "password")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2

    def test_an_unknown_two_factor_mode_is_rejected(self, argv, prefs_file):
        argv("prefs", "--2fa", "carrier-pigeon")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2


class TestServe:
    def test_no_subcommand_starts_the_server(self, argv, monkeypatch):
        started = []
        monkeypatch.setattr(server_module, "main", lambda: started.append(True))
        argv()
        cli_module.main()
        assert started == [True]

    def test_serve_subcommand_starts_the_server(self, argv, monkeypatch):
        started = []
        monkeypatch.setattr(server_module, "main", lambda: started.append(True))
        argv("serve")
        cli_module.main()
        assert started == [True]

    def test_unknown_subcommand_is_rejected(self, argv):
        argv("bogus")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 2
