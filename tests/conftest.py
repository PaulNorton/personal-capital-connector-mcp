"""Shared fixtures: fake Empower payloads and a fake API client.

The real connector talks to Empower over HTTP. Everything here stubs that out at
two seams: `FakePC` stands in for the `personalcapital` client (so the
`PersonalCapitalAPI` wrapper can be exercised end to end), and `FakeAPI` stands
in for the wrapper itself (so the MCP tool functions can be exercised without
touching the network).
"""

from datetime import datetime

import pytest

from personal_capital_connector import client as client_module
from personal_capital_connector.client import _safe_float
from personal_capital_connector import server as server_module

# A fixed "today" so date-window math is deterministic.
FROZEN_NOW = datetime(2026, 6, 15, 12, 30, 0)


class FrozenDatetime(datetime):
    """datetime subclass whose now() is pinned to FROZEN_NOW."""

    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin client.datetime.now() to FROZEN_NOW and hand back that timestamp."""
    monkeypatch.setattr(client_module, "datetime", FrozenDatetime)
    return FROZEN_NOW


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def account(**overrides) -> dict:
    """Build a raw Empower account dict. Defaults land it in the 'cash' group."""
    base = {
        "name": "Test Account",
        "balance": 100.0,
        "accountTypeGroup": "BANK",
        "firmName": "Test Bank",
        "accountType": "Checking",
    }
    base.update(overrides)
    return base


def accounts_payload(*accts, networth=None) -> dict:
    """Wrap accounts in the spData shape that get_accounts returns.

    When networth is not given it is summed from the accounts, ignoring any
    balances that are deliberately unparseable.
    """
    if networth is None:
        networth = sum(_safe_float(a.get("balance")) or 0.0 for a in accts)
    return {"networth": networth, "accounts": list(accts)}


def txn(**overrides) -> dict:
    """Build a raw Empower transaction dict."""
    base = {
        "transactionDate": "2026-06-01",
        "description": "Coffee Shop",
        "originalDescription": "COFFEE SHOP #123",
        "merchant": "Coffee Shop",
        "amount": 4.50,
        "isCredit": False,
        "accountName": "Checking",
        "categoryName": "Restaurants",
    }
    base.update(overrides)
    return base


def holding(**overrides) -> dict:
    """Build a raw Empower holding dict."""
    base = {
        "ticker": "VTI",
        "description": "Vanguard Total Stock Market ETF",
        "quantity": 10,
        "price": 100.0,
        "value": 1000.0,
        "assetClass": "US Stocks",
        "accountName": "Brokerage",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fake transport (for PersonalCapitalAPI tests)
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakePC:
    """Stands in for the personalcapital client. Records every fetch() call."""

    def __init__(self, responses=None):
        # endpoint -> payload returned by fetch()
        self.responses = responses or {}
        self.calls = []

    def fetch(self, endpoint, data=None):
        self.calls.append((endpoint, data))
        if endpoint not in self.responses:
            raise AssertionError(f"unexpected fetch of {endpoint}")
        return FakeResponse(self.responses[endpoint])

    @property
    def last_call(self):
        return self.calls[-1]


def ok(sp_data) -> dict:
    """A successful Empower envelope."""
    return {"spHeader": {"success": True}, "spData": sp_data}


def fail(errors=None, **header) -> dict:
    """A failed Empower envelope."""
    sp_header = {"success": False, "errors": errors or [{"message": "boom"}]}
    sp_header.update(header)
    return {"spHeader": sp_header}


# ---------------------------------------------------------------------------
# Fake API (for MCP tool tests)
# ---------------------------------------------------------------------------

class FakeAPI:
    """Stands in for PersonalCapitalAPI so tool functions run without network."""

    def __init__(self, accounts=None, transactions=None, holdings=None):
        self._accounts = accounts if accounts is not None else accounts_payload()
        self._transactions = transactions or []
        self._holdings = holdings or []
        self.transaction_calls = []

    def get_accounts(self):
        return self._accounts

    def get_transactions(self, days=30, start_date=None, end_date=None):
        self.transaction_calls.append(
            {"days": days, "start_date": start_date, "end_date": end_date}
        )
        return list(self._transactions)

    def get_holdings(self):
        return list(self._holdings)


@pytest.fixture
def install_api(monkeypatch):
    """Install a FakeAPI as the server's cached client and return it."""

    def _install(**kwargs):
        api = FakeAPI(**kwargs)
        monkeypatch.setattr(server_module, "_api", api)
        return api

    return _install
