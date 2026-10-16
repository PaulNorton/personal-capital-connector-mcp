"""Tests for the per-account balance history fetch and tool."""

import pytest

from personal_capital_connector.client import HISTORY_START, PersonalCapitalAPI, month_ends
from personal_capital_connector.server import get_account_balance_history

from conftest import FakePC, account, accounts_payload, fail, ok

HISTORIES = "/account/getHistories"


def api_for(payload) -> tuple[PersonalCapitalAPI, FakePC]:
    pc = FakePC({HISTORIES: payload})
    return PersonalCapitalAPI(pc), pc


def bal(date, balance, approximate=False):
    return {"date": date, "balance": balance, "approximate": approximate}


class TestGetAccountBalanceHistory:
    def test_requests_daily_balances_for_the_given_accounts(self, frozen_now):
        api, pc = api_for(ok({"histories": []}))
        api.get_account_balance_history([11, 22])
        endpoint, data = pc.last_call
        assert endpoint == HISTORIES
        assert data["startDate"] == HISTORY_START
        assert data["endDate"] == "2026-06-15"
        assert data["interval"] == "DAY"
        assert data["types"] == '["balances"]'
        assert data["userAccountIds"] == "[11,22]"

    def test_splits_days_by_account_oldest_first(self):
        days = [
            {"date": "2024-01-02", "balances": {"11": "200.5", "22": 7.0}},
            {"date": "2024-01-01", "balances": {"11": 100.0, "aggregateAnnotation": "x"}},
        ]
        api, _ = api_for(ok({"histories": days}))
        assert api.get_account_balance_history([11, 22]) == {
            "11": [bal("2024-01-01", 100.0), bal("2024-01-02", 200.5)],
            "22": [bal("2024-01-02", 7.0)],
        }

    def test_annotation_marks_the_day_approximate(self):
        days = [{"date": "2024-01-01", "balances": {"11": 5.0, "11Annotation": "unavailable"}}]
        api, _ = api_for(ok({"histories": days}))
        assert api.get_account_balance_history([11]) == {"11": [bal("2024-01-01", 5.0, True)]}

    def test_null_balance_becomes_zero(self):
        days = [{"date": "2024-01-01", "balances": {"11": None}}]
        api, _ = api_for(ok({"histories": days}))
        assert api.get_account_balance_history([11]) == {"11": [bal("2024-01-01", 0.0)]}

    def test_no_accounts_skips_the_request(self):
        api, pc = api_for(ok({}))
        assert api.get_account_balance_history([]) == {}
        assert pc.calls == []

    def test_unsuccessful_response_raises(self):
        api, _ = api_for(fail([{"message": "session expired"}]))
        with pytest.raises(RuntimeError) as exc:
            api.get_account_balance_history([11])
        assert "getHistories failed" in str(exc.value)

    def test_start_after_end_raises(self):
        api, _ = api_for(ok({}))
        with pytest.raises(ValueError):
            api.get_account_balance_history([11], "2024-02-01", "2024-01-01")


class TestMonthEndsByBalance:
    def test_drops_zero_rows_before_the_link_and_keeps_a_later_zero(self):
        series = [bal("2020-05-31", 0.0), bal("2020-06-30", 9.0), bal("2020-07-31", 0.0)]
        assert month_ends(series, "balance") == [bal("2020-06-30", 9.0), bal("2020-07-31", 0.0)]


class TestTool:
    ACCOUNTS = accounts_payload(
        account(name="Checking", originalName="Checking - Ending in 9380",
                userAccountId=1, createdDate=1591556695000),
        account(name="Mortgage", accountTypeGroup="MORTGAGE", isAsset=False,
                userAccountId=2, createdDate=1601309772000),
        account(name="Old Mortgage", accountTypeGroup="MORTGAGE", isAsset=False,
                userAccountId=3, closedDate="2020-09-28"),
    )
    BALANCES = {
        "1": [bal("2020-06-06", 0.0), bal("2020-06-30", 500.0), bal("2020-07-31", 750.0, True)],
        "2": [bal("2020-09-30", 240000.0)],
        "3": [bal("2020-08-31", 245000.0), bal("2020-09-30", 0.0)],
    }

    def test_lists_each_account_with_month_ends(self, install_api):
        install_api(accounts=self.ACCOUNTS, balances=self.BALANCES)
        out = get_account_balance_history()
        assert "3 accounts, month rows" in out
        assert "Checking (…9380) — BANK, asset, linked 2020-06-0" in out
        assert "  2020-06-30  $         500.00\n" in out
        assert "  2020-07-31  $         750.00 ~" in out
        assert "2020-06-06" not in out
        assert "Mortgage — MORTGAGE, liability" in out
        assert "Old Mortgage — MORTGAGE, liability, linked unknown, closed 2020-09-28, id 3" in out

    def test_filters_by_name_substring(self, install_api):
        api = install_api(accounts=self.ACCOUNTS, balances=self.BALANCES)
        out = get_account_balance_history(account="9380")
        assert "1 accounts" in out
        assert api.balance_calls[0]["ids"] == [1]

    def test_can_leave_out_closed_accounts(self, install_api):
        api = install_api(accounts=self.ACCOUNTS, balances=self.BALANCES)
        get_account_balance_history(include_closed=False)
        assert api.balance_calls[0]["ids"] == [1, 2]

    def test_dates_pass_through(self, install_api):
        api = install_api(accounts=self.ACCOUNTS, balances=self.BALANCES)
        get_account_balance_history(start_date="2020-01-01", end_date="2020-12-31")
        assert api.balance_calls[0]["start_date"] == "2020-01-01"
        assert api.balance_calls[0]["end_date"] == "2020-12-31"

    def test_no_match(self, install_api):
        install_api(accounts=self.ACCOUNTS, balances=self.BALANCES)
        assert get_account_balance_history(account="nothing") == "No accounts match that filter."
