"""Tests for the net worth history fetch, month-end sampling, and tool."""

import pytest

from personal_capital_connector.client import HISTORY_START, PersonalCapitalAPI, month_ends
from personal_capital_connector.server import get_net_worth_history

from conftest import FakePC, fail, ok

HISTORIES = "/account/getHistories"


def day(date, networth):
    return {"date": date, "networth": networth}


def api_for(payload) -> tuple[PersonalCapitalAPI, FakePC]:
    pc = FakePC({HISTORIES: payload})
    return PersonalCapitalAPI(pc), pc


class TestGetNetWorthHistory:
    def test_requests_the_whole_daily_series_by_default(self, frozen_now):
        api, pc = api_for(ok({"networthHistories": []}))
        api.get_net_worth_history()
        endpoint, data = pc.last_call
        assert endpoint == HISTORIES
        assert data["startDate"] == HISTORY_START
        assert data["endDate"] == "2026-06-15"
        assert data["interval"] == "DAY"
        assert data["types"] == '["networth"]'

    def test_passes_an_explicit_range(self):
        api, pc = api_for(ok({"networthHistories": []}))
        api.get_net_worth_history("2024-01-01", "2024-12-31")
        _, data = pc.last_call
        assert (data["startDate"], data["endDate"]) == ("2024-01-01", "2024-12-31")

    def test_returns_date_and_networth_oldest_first(self):
        rows = [
            {"date": "2024-01-02", "networth": "200.5", "oneDayNetworthChange": 1},
            {"date": "2024-01-01", "networth": 100.0},
        ]
        api, _ = api_for(ok({"networthHistories": rows}))
        assert api.get_net_worth_history() == [day("2024-01-01", 100.0), day("2024-01-02", 200.5)]

    def test_null_networth_becomes_zero_and_dateless_rows_drop(self):
        rows = [{"date": "2024-01-01", "networth": None}, {"networth": 5.0}]
        api, _ = api_for(ok({"networthHistories": rows}))
        assert api.get_net_worth_history() == [day("2024-01-01", 0.0)]

    def test_null_sp_data_returns_empty_list(self):
        api, _ = api_for({"spHeader": {"success": True}, "spData": None})
        assert api.get_net_worth_history() == []

    def test_unsuccessful_response_raises(self):
        api, _ = api_for(fail([{"message": "session expired"}]))
        with pytest.raises(RuntimeError) as exc:
            api.get_net_worth_history()
        assert "getHistories failed" in str(exc.value)

    def test_start_after_end_raises(self):
        api, _ = api_for(ok({}))
        with pytest.raises(ValueError):
            api.get_net_worth_history("2024-02-01", "2024-01-01")

    def test_bad_date_raises(self):
        api, _ = api_for(ok({}))
        with pytest.raises(ValueError) as exc:
            api.get_net_worth_history("01/01/2024")
        assert "start_date" in str(exc.value)


class TestMonthEnds:
    def test_keeps_the_last_day_of_each_month(self):
        history = [
            day("2024-01-30", 1.0), day("2024-01-31", 2.0),
            day("2024-02-28", 3.0), day("2024-02-29", 4.0),
            day("2024-03-05", 5.0),
        ]
        assert month_ends(history) == [
            day("2024-01-31", 2.0), day("2024-02-29", 4.0), day("2024-03-05", 5.0),
        ]

    def test_drops_zero_rows_before_the_first_linked_account(self):
        history = [day("2019-03-31", 0.0), day("2019-04-16", 0.0), day("2019-04-17", 10.0)]
        assert month_ends(history) == [day("2019-04-17", 10.0)]

    def test_keeps_a_real_zero_after_the_series_starts(self):
        history = [day("2024-01-31", 10.0), day("2024-02-29", 0.0)]
        assert month_ends(history) == [day("2024-01-31", 10.0), day("2024-02-29", 0.0)]

    def test_sorts_unordered_input(self):
        history = [day("2024-02-29", 2.0), day("2024-01-31", 1.0)]
        assert month_ends(history) == [day("2024-01-31", 1.0), day("2024-02-29", 2.0)]

    def test_empty_history(self):
        assert month_ends([]) == []


class TestTool:
    HISTORY = [
        day("2019-04-16", 0.0),
        day("2019-04-17", 1000.0), day("2019-04-30", 1500.0),
        day("2019-05-31", 1234567.89),
    ]

    def test_month_mode_lists_month_ends(self, install_api):
        install_api(history=self.HISTORY)
        out = get_net_worth_history()
        assert "2019-04-30 to 2019-05-31 (2 month rows)" in out
        assert "2019-04-30  $       1,500.00" in out
        assert "$   1,234,567.89" in out
        assert "2019-04-17" not in out

    def test_day_mode_lists_every_linked_day(self, install_api):
        install_api(history=self.HISTORY)
        out = get_net_worth_history(interval="day")
        assert "(3 day rows)" in out
        assert "2019-04-17" in out
        assert "2019-04-16" not in out

    def test_blank_dates_pass_as_none(self, install_api):
        api = install_api(history=self.HISTORY)
        get_net_worth_history()
        assert api.history_calls == [{"start_date": None, "end_date": None}]

    def test_dates_pass_through(self, install_api):
        api = install_api(history=self.HISTORY)
        get_net_worth_history("2019-01-01", "2019-12-31")
        assert api.history_calls == [{"start_date": "2019-01-01", "end_date": "2019-12-31"}]

    def test_empty_history(self, install_api):
        install_api(history=[])
        assert get_net_worth_history() == "No net worth history found for that range."
