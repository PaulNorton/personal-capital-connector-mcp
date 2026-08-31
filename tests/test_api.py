"""Tests for PersonalCapitalAPI: envelope handling and date-window math."""

import pytest

from personal_capital_connector.client import PersonalCapitalAPI

from conftest import FakePC, account, accounts_payload, fail, holding, ok, txn

ACCOUNTS = "/newaccount/getAccounts"
TRANSACTIONS = "/transaction/getUserTransactions"
HOLDINGS = "/invest/getHoldings"


def api_for(endpoint, payload) -> tuple[PersonalCapitalAPI, FakePC]:
    pc = FakePC({endpoint: payload})
    return PersonalCapitalAPI(pc), pc


class TestGetAccounts:
    def test_returns_sp_data_on_success(self):
        payload = accounts_payload(account(), networth=42.0)
        api, pc = api_for(ACCOUNTS, ok(payload))
        assert api.get_accounts() == payload
        assert pc.last_call == (ACCOUNTS, None)

    def test_missing_sp_data_returns_empty_dict(self):
        api, _ = api_for(ACCOUNTS, {"spHeader": {"success": True}})
        assert api.get_accounts() == {}

    def test_null_sp_data_returns_empty_dict(self):
        # Callers treat the result as a dict; a null must not leak through.
        api, _ = api_for(ACCOUNTS, {"spHeader": {"success": True}, "spData": None})
        assert api.get_accounts() == {}

    def test_unsuccessful_response_raises(self):
        api, _ = api_for(ACCOUNTS, fail([{"message": "session expired"}]))
        with pytest.raises(RuntimeError) as exc:
            api.get_accounts()
        assert "getAccounts failed" in str(exc.value)
        assert "session expired" in str(exc.value)

    def test_missing_header_is_treated_as_failure(self):
        api, _ = api_for(ACCOUNTS, {})
        with pytest.raises(RuntimeError):
            api.get_accounts()


class TestGetHoldings:
    def test_returns_holdings_list(self):
        api, pc = api_for(HOLDINGS, ok({"holdings": [holding()]}))
        assert api.get_holdings() == [holding()]
        assert pc.last_call == (HOLDINGS, None)

    def test_missing_holdings_key_returns_empty_list(self):
        api, _ = api_for(HOLDINGS, ok({}))
        assert api.get_holdings() == []

    def test_null_holdings_returns_empty_list(self):
        api, _ = api_for(HOLDINGS, ok({"holdings": None}))
        assert api.get_holdings() == []

    def test_null_sp_data_returns_empty_list(self):
        api, _ = api_for(HOLDINGS, {"spHeader": {"success": True}, "spData": None})
        assert api.get_holdings() == []

    def test_unsuccessful_response_raises(self):
        api, _ = api_for(HOLDINGS, fail())
        with pytest.raises(RuntimeError, match="getHoldings failed"):
            api.get_holdings()


class TestGetTransactionsWindow:
    """The date window is the interesting part: three input styles, one output."""

    def window(self, pc_api, **kwargs) -> tuple[str, str]:
        api, pc = pc_api
        api.get_transactions(**kwargs)
        _, body = pc.last_call
        return body["startDate"], body["endDate"]

    @pytest.fixture
    def pc_api(self):
        return api_for(TRANSACTIONS, ok({"transactions": [txn()]}))

    def test_default_looks_back_thirty_days_from_today(self, pc_api, frozen_now):
        assert self.window(pc_api) == ("2026-05-16", "2026-06-15")

    def test_days_controls_the_lookback(self, pc_api, frozen_now):
        assert self.window(pc_api, days=7) == ("2026-06-08", "2026-06-15")

    def test_zero_days_queries_a_single_day(self, pc_api, frozen_now):
        assert self.window(pc_api, days=0) == ("2026-06-15", "2026-06-15")

    def test_start_date_only_runs_through_today(self, pc_api, frozen_now):
        assert self.window(pc_api, start_date="2026-01-01") == ("2026-01-01", "2026-06-15")

    def test_start_date_overrides_days(self, pc_api, frozen_now):
        # days is documented as ignored once start_date is set.
        assert self.window(pc_api, days=3, start_date="2026-01-01") == (
            "2026-01-01",
            "2026-06-15",
        )

    def test_explicit_range_is_used_verbatim(self, pc_api, frozen_now):
        assert self.window(pc_api, start_date="2026-02-01", end_date="2026-03-01") == (
            "2026-02-01",
            "2026-03-01",
        )

    def test_end_date_only_looks_back_days_from_that_date(self, pc_api, frozen_now):
        assert self.window(pc_api, days=10, end_date="2026-04-20") == (
            "2026-04-10",
            "2026-04-20",
        )

    def test_single_day_range_is_allowed(self, pc_api, frozen_now):
        assert self.window(pc_api, start_date="2026-03-05", end_date="2026-03-05") == (
            "2026-03-05",
            "2026-03-05",
        )

    def test_hits_the_transactions_endpoint(self, pc_api, frozen_now):
        api, pc = pc_api
        api.get_transactions()
        endpoint, body = pc.last_call
        assert endpoint == TRANSACTIONS
        assert set(body) == {"startDate", "endDate"}


class TestGetTransactionsValidation:
    @pytest.fixture
    def api(self):
        return api_for(TRANSACTIONS, ok({"transactions": []}))[0]

    def test_inverted_range_raises_before_fetching(self, api, frozen_now):
        with pytest.raises(ValueError) as exc:
            api.get_transactions(start_date="2026-05-01", end_date="2026-04-01")
        assert "2026-05-01" in str(exc.value)
        assert "2026-04-01" in str(exc.value)

    def test_negative_days_producing_an_inverted_range_raises(self, api, frozen_now):
        with pytest.raises(ValueError, match="is after"):
            api.get_transactions(days=-5, end_date="2026-04-20")

    def test_bad_start_date_raises_with_field_name(self, api, frozen_now):
        with pytest.raises(ValueError, match="start_date"):
            api.get_transactions(start_date="05/01/2026")

    def test_bad_end_date_raises_with_field_name(self, api, frozen_now):
        with pytest.raises(ValueError, match="end_date"):
            api.get_transactions(start_date="2026-01-01", end_date="not-a-date")

    def test_bad_end_date_alone_raises(self, api, frozen_now):
        with pytest.raises(ValueError, match="end_date"):
            api.get_transactions(end_date="2026/04/20")


class TestGetTransactionsResponse:
    def test_returns_the_transaction_list(self, frozen_now):
        rows = [txn(description="A"), txn(description="B")]
        api, _ = api_for(TRANSACTIONS, ok({"transactions": rows}))
        assert api.get_transactions() == rows

    def test_missing_transactions_key_returns_empty_list(self, frozen_now):
        api, _ = api_for(TRANSACTIONS, ok({}))
        assert api.get_transactions() == []

    def test_null_transactions_returns_empty_list(self, frozen_now):
        api, _ = api_for(TRANSACTIONS, ok({"transactions": None}))
        assert api.get_transactions() == []

    def test_null_sp_data_returns_empty_list(self, frozen_now):
        api, _ = api_for(TRANSACTIONS, {"spHeader": {"success": True}, "spData": None})
        assert api.get_transactions() == []

    def test_unsuccessful_response_raises(self, frozen_now):
        api, _ = api_for(TRANSACTIONS, fail([{"message": "rate limited"}]))
        with pytest.raises(RuntimeError) as exc:
            api.get_transactions()
        assert "getTransactions failed" in str(exc.value)
        assert "rate limited" in str(exc.value)
