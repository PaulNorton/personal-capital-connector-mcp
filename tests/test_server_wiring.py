"""Tests for server-level wiring: client caching and the auth-status tool."""

import pytest

from personal_capital_connector import server as server_module
from personal_capital_connector.server import check_auth_status

from conftest import FakeAPI


@pytest.fixture(autouse=True)
def reset_cached_api(monkeypatch):
    monkeypatch.setattr(server_module, "_api", None)


class TestGetApi:
    def test_builds_a_client_on_first_use_and_caches_it(self, monkeypatch):
        calls = []

        def fake_create():
            calls.append(1)
            return object()

        monkeypatch.setattr(server_module, "create_authenticated_client", fake_create)
        first = server_module._get_api()
        second = server_module._get_api()
        assert first is second
        assert len(calls) == 1

    def test_raises_a_actionable_error_when_unauthenticated(self, monkeypatch):
        monkeypatch.setattr(server_module, "create_authenticated_client", lambda: None)
        with pytest.raises(RuntimeError) as exc:
            server_module._get_api()
        assert "personal-capital-connector auth" in str(exc.value)

    def test_an_already_cached_client_is_reused_without_authenticating(self, monkeypatch):
        api = FakeAPI()
        monkeypatch.setattr(server_module, "_api", api)
        monkeypatch.setattr(
            server_module,
            "create_authenticated_client",
            lambda: pytest.fail("should not re-authenticate"),
        )
        assert server_module._get_api() is api


class TestCheckAuthStatus:
    def test_reports_missing_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server_module, "SESSION_FILE", tmp_path / "nope.json")
        assert "No session found" in check_auth_status()

    def test_reports_a_valid_session(self, monkeypatch, tmp_path):
        path = tmp_path / "session.json"
        path.write_text("{}")
        monkeypatch.setattr(server_module, "SESSION_FILE", path)
        monkeypatch.setattr(server_module, "create_authenticated_client", lambda: object())
        assert check_auth_status().startswith("✓ Authenticated")

    def test_reports_an_expired_session(self, monkeypatch, tmp_path):
        path = tmp_path / "session.json"
        path.write_text("{}")
        monkeypatch.setattr(server_module, "SESSION_FILE", path)
        monkeypatch.setattr(server_module, "create_authenticated_client", lambda: None)
        message = check_auth_status()
        assert "expired or invalid" in message
        assert "personal-capital-connector auth" in message

    def test_surfaces_unexpected_errors_instead_of_raising(self, monkeypatch, tmp_path):
        path = tmp_path / "session.json"
        path.write_text("{}")
        monkeypatch.setattr(server_module, "SESSION_FILE", path)

        def boom():
            raise ConnectionError("network down")

        monkeypatch.setattr(server_module, "create_authenticated_client", boom)
        assert check_auth_status() == "Auth check failed: network down"


class TestToolRegistration:
    def test_every_tool_is_registered_with_the_mcp_server(self):
        import asyncio

        names = {t.name for t in asyncio.run(server_module.mcp.list_tools())}
        assert names == {
            "check_auth_status",
            "list_accounts",
            "get_net_worth",
            "get_transactions",
            "get_asset_allocation",
        }

    def test_tools_have_descriptions_for_the_model(self):
        import asyncio

        for tool in asyncio.run(server_module.mcp.list_tools()):
            assert tool.description, tool.name
