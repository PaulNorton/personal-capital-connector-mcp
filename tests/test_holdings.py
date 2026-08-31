"""Tests for summarize_holdings and the get_asset_allocation tool.

Allocation is grouped by individual holding, not by asset class: the live
/invest/getHoldings payload carries no asset class at all.
"""

import re

import pytest

from personal_capital_connector.client import summarize_holdings
from personal_capital_connector.server import get_asset_allocation

from conftest import account, accounts_payload, holding

VTI = "Vanguard Total Stock Market ETF"


class TestSummarizeHoldings:
    def test_empty_input(self):
        result = summarize_holdings([])
        assert result == {"total_value": 0.0, "allocation": {}, "by_account": {}}

    def test_total_value_sums_every_holding(self):
        result = summarize_holdings([holding(value=1000.0), holding(value=250.5)])
        assert result["total_value"] == 1250.5

    def test_allocation_groups_by_holding_name(self):
        result = summarize_holdings(
            [
                holding(value=1000.0, description="Fidelity Total Market"),
                holding(value=500.0, description="Fidelity Total Market"),
                holding(value=500.0, description="Vanguard Total Bond"),
            ]
        )
        assert result["allocation"]["Fidelity Total Market"]["value"] == 1500.0
        assert result["allocation"]["Vanguard Total Bond"]["value"] == 500.0

    def test_same_fund_in_two_accounts_is_one_bucket(self):
        # The point of the breakdown: concentration across accounts.
        result = summarize_holdings(
            [
                holding(value=1000.0, description="Fidelity Total Market", accountName="401k"),
                holding(value=750.0, description="Fidelity Total Market", accountName="Roth IRA"),
            ]
        )
        assert list(result["allocation"]) == ["Fidelity Total Market"]
        assert result["allocation"]["Fidelity Total Market"]["value"] == 1750.0
        assert set(result["by_account"]) == {"401k", "Roth IRA"}

    def test_percentages_are_shares_of_the_total(self):
        result = summarize_holdings(
            [
                holding(value=750.0, description="Fund A"),
                holding(value=250.0, description="Fund B"),
            ]
        )
        assert result["allocation"]["Fund A"]["pct"] == 75.0
        assert result["allocation"]["Fund B"]["pct"] == 25.0

    def test_allocation_is_sorted_by_value_descending(self):
        result = summarize_holdings(
            [
                holding(value=100.0, description="Small"),
                holding(value=900.0, description="Big"),
                holding(value=400.0, description="Middle"),
            ]
        )
        assert list(result["allocation"]) == ["Big", "Middle", "Small"]

    def test_zero_total_does_not_divide_by_zero(self):
        result = summarize_holdings([holding(value=0.0)])
        assert result["total_value"] == 0.0
        assert result["allocation"][VTI]["pct"] == 0

    def test_falls_back_to_ticker_when_description_is_missing(self):
        result = summarize_holdings([holding(description=None, ticker="FSKAX", value=10.0)])
        assert list(result["allocation"]) == ["FSKAX"]

    def test_falls_back_to_unknown_without_a_description_or_ticker(self):
        result = summarize_holdings([{"value": 5.0, "accountName": "Brokerage"}])
        assert list(result["allocation"]) == ["Unknown"]

    def test_missing_or_null_account_name_becomes_unknown(self):
        result = summarize_holdings([holding(accountName=None)])
        assert list(result["by_account"]) == ["Unknown"]

    def test_null_value_is_treated_as_zero(self):
        result = summarize_holdings([holding(value=None), holding(value=100.0)])
        assert result["total_value"] == 100.0

    def test_by_account_groups_and_keeps_holding_fields(self):
        result = summarize_holdings(
            [
                holding(ticker="VTI", accountName="Brokerage", value=1000.0),
                holding(ticker="BND", accountName="Brokerage", value=500.0),
                holding(ticker="VXUS", accountName="401k", value=2000.0),
            ]
        )
        assert set(result["by_account"]) == {"Brokerage", "401k"}
        assert len(result["by_account"]["Brokerage"]) == 2
        assert result["by_account"]["401k"][0] == {
            "ticker": "VXUS",
            "description": VTI,
            "shares": 10,
            "price": 100.0,
            "value": 2000.0,
            "holding_type": "Fund",
        }

    def test_missing_optional_fields_get_defaults(self):
        result = summarize_holdings([{"value": 10.0}])
        entry = result["by_account"]["Unknown"][0]
        assert entry["ticker"] == ""
        assert entry["description"] == ""
        assert entry["shares"] == 0
        assert entry["price"] == 0
        assert entry["holding_type"] == ""

    def test_null_ticker_and_description_become_empty_strings(self):
        result = summarize_holdings([holding(ticker=None, description=None)])
        entry = result["by_account"]["Brokerage"][0]
        assert entry["ticker"] == ""
        assert entry["description"] == ""

    def test_null_holding_type_becomes_an_empty_string(self):
        result = summarize_holdings([holding(holdingType=None)])
        assert result["by_account"]["Brokerage"][0]["holding_type"] == ""

    def test_negative_values_are_preserved(self):
        # Short positions and margin balances legitimately come back negative.
        result = summarize_holdings(
            [
                holding(value=1000.0, description="Fund A"),
                holding(value=-200.0, description="Margin"),
            ]
        )
        assert result["total_value"] == 800.0
        assert result["allocation"]["Margin"]["value"] == -200.0


class TestAccountNameResolution:
    """Holdings without an accountName are matched on userAccountId."""

    ACCOUNTS = accounts_payload(
        account(name="Paul Norton 529", accountTypeGroup="EDUCATIONAL", userAccountId=111),
        account(name="Amazon HSA", accountTypeGroup="HEALTH", userAccountId=222),
    )

    def test_nameless_holding_is_grouped_under_its_real_account(self, install_api):
        install_api(
            accounts=self.ACCOUNTS,
            holdings=[
                holding(description="Cash", accountName=None, userAccountId=111, value=0.0),
                holding(description="Fund A", accountName="Amazon HSA", value=500.0),
            ],
        )
        text = get_asset_allocation()
        assert "Paul Norton 529 ($0.00)" in text
        assert "Unknown" not in text

    def test_resolution_happens_before_the_account_filter(self, install_api):
        # Otherwise a nameless holding could never match account_filter.
        install_api(
            accounts=self.ACCOUNTS,
            holdings=[
                holding(description="Cash", accountName=None, userAccountId=111, value=25.0),
                holding(description="Fund A", accountName="Amazon HSA", value=500.0),
            ],
        )
        text = get_asset_allocation(account_filter="529")
        assert "Paul Norton 529 ($25.00)" in text
        assert "Fund A" not in text

    def test_resolved_name_merges_with_the_holdings_feed_name(self, install_api):
        # The accounts feed decorates names with a "(…1234)" suffix that the
        # holdings feed omits. Resolving must not split one account into two.
        install_api(
            accounts=accounts_payload(
                account(name="Amazon HSA", accountTypeGroup="HEALTH", userAccountId=222,
                        originalName="Fidelity HSA Ending in 6005"),
            ),
            holdings=[
                holding(description="Fund A", accountName="Amazon HSA", value=500.0),
                holding(description="Cash", accountName=None, userAccountId=222, value=0.0),
            ],
        )
        text = get_asset_allocation()
        assert "Amazon HSA ($500.00)" in text
        assert "(…6005)" not in text
        assert text.count("Amazon HSA (") == 1

    def test_falls_back_to_firm_and_type_when_the_account_is_unnamed(self, install_api):
        install_api(
            accounts=accounts_payload(
                account(name="", firmName="Fidelity", accountType="HSA", userAccountId=333),
            ),
            holdings=[holding(description="Cash", accountName=None, userAccountId=333, value=2.0)],
        )
        assert "Fidelity HSA ($2.00)" in get_asset_allocation()

    def test_string_and_integer_account_ids_both_match(self, install_api):
        install_api(
            accounts=self.ACCOUNTS,
            holdings=[holding(description="Cash", accountName=None, userAccountId="111", value=1.0)],
        )
        assert "Paul Norton 529" in get_asset_allocation()

    def test_unmatched_id_still_falls_back_to_unknown(self, install_api):
        install_api(
            accounts=self.ACCOUNTS,
            holdings=[holding(description="Cash", accountName=None, userAccountId=999, value=1.0)],
        )
        assert "Unknown ($1.00)" in get_asset_allocation()

    def test_accounts_are_not_fetched_when_every_holding_is_named(self, install_api):
        api = install_api(
            accounts=self.ACCOUNTS,
            holdings=[holding(accountName="Amazon HSA", value=500.0)],
        )
        get_asset_allocation()
        assert api.account_calls == 0

    def test_accounts_are_fetched_once_when_a_name_is_missing(self, install_api):
        api = install_api(
            accounts=self.ACCOUNTS,
            holdings=[holding(accountName=None, userAccountId=111, value=1.0)],
        )
        get_asset_allocation()
        assert api.account_calls == 1

    def test_a_failure_resolving_names_does_not_sink_the_tool(self, install_api):
        api = install_api(
            accounts=self.ACCOUNTS,
            holdings=[holding(description="Cash", accountName=None, userAccountId=111, value=7.0)],
        )

        def boom():
            raise RuntimeError("accounts endpoint down")

        api.get_accounts = boom
        text = get_asset_allocation()
        assert "Unknown ($7.00)" in text
        assert "$7.00 total" in text

    def test_existing_account_names_are_never_overwritten(self, install_api):
        install_api(
            accounts=self.ACCOUNTS,
            holdings=[
                holding(accountName="Amazon HSA", userAccountId=111, value=500.0),
                holding(accountName=None, userAccountId=111, value=1.0),
            ],
        )
        text = get_asset_allocation()
        assert "Amazon HSA ($500.00)" in text
        assert "Paul Norton 529 ($1.00)" in text


class TestAssetAllocationTool:
    def test_no_holdings_message(self, install_api):
        install_api(holdings=[])
        assert get_asset_allocation() == "No holdings found."

    def test_no_holdings_message_mentions_the_filter(self, install_api):
        install_api(holdings=[holding(accountName="Brokerage")])
        assert get_asset_allocation(account_filter="401k") == 'No holdings found. (filter: "401k")'

    def test_header_shows_the_total(self, install_api):
        install_api(holdings=[holding(value=1234.5)])
        assert get_asset_allocation().startswith("Investment Holdings — $1,234.50 total")

    def test_section_is_labelled_by_holding_not_asset_class(self, install_api):
        # Empower supplies no asset class, so the tool must not claim to show one.
        install_api(holdings=[holding()])
        text = get_asset_allocation()
        assert "Allocation by holding:" in text
        assert "Asset Allocation" not in text

    def test_allocation_rows_show_value_and_percentage(self, install_api):
        install_api(
            holdings=[
                holding(value=750.0, description="Fund A"),
                holding(value=250.0, description="Fund B"),
            ]
        )
        text = get_asset_allocation()
        assert re.search(r"Fund A\s+\$\s*750\.00\s+75\.0%", text)
        assert re.search(r"Fund B\s+\$\s*250\.00\s+25\.0%", text)

    def test_long_fund_names_are_truncated_to_keep_columns_aligned(self, install_api):
        long_name = "Vanguard Morningstar Total Stock Market Index Fund Admiral Shares"
        install_api(
            holdings=[
                holding(value=750.0, description=long_name),
                holding(value=250.0, description="Fund B"),
            ]
        )
        alloc = [
            ln for ln in get_asset_allocation().splitlines()
            if "75.0%" in ln or "25.0%" in ln
        ]
        assert "…" in alloc[0]
        # Both rows put the dollar sign in the same column.
        assert alloc[0].index("$") == alloc[1].index("$")

    def test_short_names_are_not_truncated(self, install_api):
        install_api(holdings=[holding(description="Fund A", value=10.0)])
        assert "…" not in get_asset_allocation()

    def test_bar_length_is_half_the_percentage(self, install_api):
        install_api(holdings=[holding(value=1000.0)])
        line = [ln for ln in get_asset_allocation().splitlines() if "100.0%" in ln][0]
        assert line.count("█") == 50

    def test_account_filter_is_a_case_insensitive_substring(self, install_api):
        install_api(
            holdings=[
                holding(ticker="VTI", accountName="Fidelity Brokerage", value=1000.0),
                holding(ticker="VXUS", accountName="Vanguard 401k", value=2000.0),
            ]
        )
        text = get_asset_allocation(account_filter="fidelity")
        assert "VTI" in text
        assert "VXUS" not in text
        assert "$1,000.00 total" in text

    def test_null_account_names_do_not_crash_the_filter(self, install_api):
        install_api(holdings=[holding(accountName=None), holding(accountName="Brokerage")])
        assert "Brokerage" in get_asset_allocation(account_filter="broker")

    def test_per_account_totals_are_shown(self, install_api):
        install_api(
            holdings=[
                holding(ticker="VTI", accountName="Brokerage", value=1000.0),
                holding(ticker="BND", accountName="Brokerage", value=500.0),
            ]
        )
        assert "Brokerage ($1,500.00)" in get_asset_allocation()

    def test_holdings_are_listed_largest_first_within_an_account(self, install_api):
        install_api(
            holdings=[
                holding(ticker="SMALL", accountName="Brokerage", value=100.0),
                holding(ticker="BIG", accountName="Brokerage", value=900.0),
            ]
        )
        text = get_asset_allocation()
        assert text.index("[BIG]") < text.index("[SMALL]")

    def test_holding_type_is_shown_as_the_suffix(self, install_api):
        install_api(holdings=[holding(description="Cash", ticker="", holdingType="Cash", value=5.0)])
        assert "• Cash: $5.00 (Cash)" in get_asset_allocation()

    def test_missing_holding_type_suffix_is_omitted(self, install_api):
        install_api(holdings=[holding(description="Fund A", ticker="", holdingType=None, value=5.0)])
        assert "• Fund A: $5.00" in get_asset_allocation()
        assert "()" not in get_asset_allocation()

    def test_ticker_prefix_is_omitted_when_absent(self, install_api):
        install_api(holdings=[holding(ticker="", description="Cash Sweep", value=10.0)])
        assert "• Cash Sweep: $10.00" in get_asset_allocation()

    def test_null_description_does_not_render_as_the_word_none(self, install_api):
        install_api(holdings=[holding(ticker=None, description=None, value=10.0)])
        text = get_asset_allocation()
        assert "None" not in text

    def test_only_the_top_25_holdings_per_account_are_listed(self, install_api):
        install_api(
            holdings=[
                holding(ticker=f"T{i:02d}", description=f"Fund {i:02d}",
                        accountName="Brokerage", value=float(100 - i))
                for i in range(30)
            ]
        )
        text = get_asset_allocation()
        assert text.count("    • ") == 25
        assert "[T00]" in text  # largest
        assert "[T29]" not in text  # smallest, trimmed

    def test_all_accounts_are_shown(self, install_api):
        install_api(
            holdings=[
                holding(accountName="Brokerage", value=100.0),
                holding(accountName="401k", value=200.0),
                holding(accountName="Roth IRA", value=300.0),
            ]
        )
        text = get_asset_allocation()
        for name in ("Brokerage", "401k", "Roth IRA"):
            assert f"{name} ($" in text
