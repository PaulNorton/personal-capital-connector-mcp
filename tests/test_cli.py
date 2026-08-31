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
    def test_runs_the_interactive_flow(self, argv, capsys, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            auth_module, "interactive_auth", lambda email="": seen.setdefault("email", email)
        )
        argv("auth", "--email", "paul@example.com")
        cli_module.main()
        assert seen["email"] == "paul@example.com"
        assert "All done!" in capsys.readouterr().out

    def test_email_defaults_to_empty_so_the_flow_can_prompt(self, argv, capsys, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            auth_module, "interactive_auth", lambda email="": seen.setdefault("email", email)
        )
        argv("auth")
        cli_module.main()
        assert seen["email"] == ""

    def test_failure_exits_nonzero_with_the_reason(self, argv, capsys, monkeypatch):
        def boom(email=""):
            raise RuntimeError("bad 2FA code")

        monkeypatch.setattr(auth_module, "interactive_auth", boom)
        argv("auth")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "Authentication failed: bad 2FA code" in capsys.readouterr().out

    def test_ctrl_c_exits_nonzero_without_a_traceback(self, argv, capsys, monkeypatch):
        def cancel(email=""):
            raise KeyboardInterrupt

        monkeypatch.setattr(auth_module, "interactive_auth", cancel)
        argv("auth")
        with pytest.raises(SystemExit) as exc:
            cli_module.main()
        assert exc.value.code == 1
        assert "Cancelled." in capsys.readouterr().out


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
