"""Tests for the get_net_worth tool.

These cover the balance-sign bug fixed in 3c27b07: Empower reports both sides of
the balance sheet as positive numbers, so it is tempting to abs() liabilities and
to filter assets to positive balances. Both coercions corrupt the totals whenever
a balance legitimately arrives with the opposite sign.
"""

import re

import pytest

from personal_capital_connector.server import get_net_worth

from conftest import account, accounts_payload

SUMMARY_LINE = re.compile(r"^\s*(.+?):\s+\$(-?[\d,]+\.\d{2})$")


def summary(text: str) -> dict[str, float]:
    """Parse '  Label: $1,234.56' lines into {label: amount}."""
    out = {}
    for line in text.splitlines():
        match = SUMMARY_LINE.match(line)
        if match:
            out[match.group(1).strip()] = float(match.group(2).replace(",", ""))
    return out


def cash(name, balance, **kw):
    return account(name=name, balance=balance, accountTypeGroup="BANK", **kw)


def investment(name, balance, **kw):
    return account(name=name, balance=balance, accountTypeGroup="INVESTMENT", **kw)


def card(name, balance, **kw):
    return account(name=name, balance=balance, accountTypeGroup="CREDIT_CARD", **kw)


def loan(name, balance, **kw):
    return account(name=name, balance=balance, accountTypeGroup="MORTGAGE", **kw)


def other(name, balance, **kw):
    return account(name=name, balance=balance, accountTypeGroup="CRYPTO", productType=None, **kw)


class TestLiabilitySigns:
    """Regression: abs() on liabilities double-counted credit balances."""

    def test_credit_balance_reduces_total_liabilities(self, install_api):
        # A card carrying a credit (an overpayment or refund) comes back negative.
        # abs() would have booked that -300 as +300 of debt, reporting $1,500.00.
        install_api(
            accounts=accounts_payload(
                card("Visa", 1200.0),
                card("Refunded Card", -300.0),
            )
        )
        assert summary(get_net_worth())["Total Liabilities"] == 900.0

    def test_all_liabilities_in_credit_only_nets_to_a_negative_total(self, install_api):
        install_api(accounts=accounts_payload(card("Overpaid Visa", -450.0)))
        assert summary(get_net_worth())["Total Liabilities"] == -450.0

    def test_loans_and_cards_are_summed_together(self, install_api):
        install_api(
            accounts=accounts_payload(
                card("Visa", 2000.0),
                card("Amex", 500.0),
                loan("Mortgage", 310000.0),
                loan("Auto", 14500.0),
            )
        )
        assert summary(get_net_worth())["Total Liabilities"] == 327000.0

    def test_a_credit_on_one_card_nets_against_a_loan(self, install_api):
        install_api(
            accounts=accounts_payload(
                card("Overpaid Visa", -1000.0),
                loan("Auto", 5000.0),
            )
        )
        assert summary(get_net_worth())["Total Liabilities"] == 4000.0

    def test_zero_balance_liabilities_do_not_change_the_total(self, install_api):
        install_api(
            accounts=accounts_payload(card("Visa", 1200.0), card("Unused Card", 0.0))
        )
        assert summary(get_net_worth())["Total Liabilities"] == 1200.0


class TestAssetSigns:
    """Regression: a `balance > 0` filter silently dropped negative assets."""

    def test_overdrawn_account_reduces_total_assets(self, install_api):
        # The old filter dropped the -200 entirely and reported $5,000.00.
        install_api(
            accounts=accounts_payload(
                cash("Savings", 5000.0),
                cash("Overdrawn Checking", -200.0),
            )
        )
        assert summary(get_net_worth())["Total Assets"] == 4800.0

    def test_margin_account_with_negative_balance_reduces_assets(self, install_api):
        install_api(
            accounts=accounts_payload(
                investment("Brokerage", 100000.0),
                investment("Margin", -25000.0),
            )
        )
        assert summary(get_net_worth())["Total Assets"] == 75000.0

    def test_negative_other_account_reduces_assets(self, install_api):
        install_api(
            accounts=accounts_payload(other("Crypto", 8000.0), other("Adjustment", -1500.0))
        )
        assert summary(get_net_worth())["Total Assets"] == 6500.0

    def test_assets_can_net_to_zero_without_being_dropped(self, install_api):
        install_api(
            accounts=accounts_payload(cash("Savings", 500.0), cash("Overdrawn", -500.0))
        )
        assert summary(get_net_worth())["Total Assets"] == 0.0

    def test_zero_balance_assets_are_included_not_hidden(self, install_api):
        # get_net_worth always passes hide_zero_balance=False.
        install_api(
            accounts=accounts_payload(cash("Savings", 1000.0), cash("Dormant", 0.0))
        )
        assert summary(get_net_worth())["Total Assets"] == 1000.0


class TestIsAssetGuard:
    def test_non_asset_account_is_excluded_from_total_assets(self, install_api):
        install_api(
            accounts=accounts_payload(
                cash("Savings", 5000.0),
                cash("Escrow Liability", 900.0, isAsset=False),
            )
        )
        assert summary(get_net_worth())["Total Assets"] == 5000.0

    def test_accounts_without_is_asset_are_treated_as_assets(self, install_api):
        payload = accounts_payload(
            {"name": "Legacy", "balance": 750.0, "accountTypeGroup": "BANK"}
        )
        install_api(accounts=payload)
        assert summary(get_net_worth())["Total Assets"] == 750.0

    def test_null_is_asset_counts_as_an_asset_not_a_liability(self, install_api):
        # A null isAsset must not push a real balance onto the liability side.
        install_api(accounts=accounts_payload(cash("Savings", 5000.0, isAsset=None)))
        parsed = summary(get_net_worth())
        assert parsed["Total Assets"] == 5000.0
        assert parsed["Total Liabilities"] == 0.0

    def test_is_asset_is_not_applied_to_the_liability_side(self, install_api):
        # Cards and loans are liabilities by construction; Empower still flags them
        # isAsset=False, and that must not remove them from the liability total.
        install_api(accounts=accounts_payload(card("Visa", 1200.0, isAsset=False)))
        assert summary(get_net_worth())["Total Liabilities"] == 1200.0

    def test_non_asset_account_outside_credit_and_loan_counts_as_a_liability(
        self, install_api
    ):
        # A manual liability that Empower does not classify as a card or loan
        # lands in 'other'. It must move to the liability side rather than
        # vanishing from both totals.
        install_api(
            accounts=accounts_payload(
                cash("Savings", 5000.0),
                other("Manual Liability", 2000.0, isAsset=False),
            )
        )
        parsed = summary(get_net_worth())
        assert parsed["Total Assets"] == 5000.0
        assert parsed["Total Liabilities"] == 2000.0

    @pytest.mark.parametrize("builder", [cash, investment, other])
    def test_non_asset_accounts_move_sides_from_every_asset_group(
        self, install_api, builder
    ):
        install_api(
            accounts=accounts_payload(
                cash("Savings", 5000.0),
                builder("Owed", 750.0, isAsset=False),
            )
        )
        parsed = summary(get_net_worth())
        assert parsed["Total Assets"] == 5000.0
        assert parsed["Total Liabilities"] == 750.0

    def test_moved_liabilities_keep_their_sign(self, install_api):
        install_api(
            accounts=accounts_payload(
                other("Escrow", 900.0, isAsset=False),
                other("Escrow Credit", -100.0, isAsset=False),
            )
        )
        assert summary(get_net_worth())["Total Liabilities"] == 800.0

    def test_every_account_lands_on_exactly_one_side(self, install_api):
        install_api(
            accounts=accounts_payload(
                cash("Checking", 1000.0),
                investment("401k", 5000.0),
                other("Manual Liability", 2000.0, isAsset=False),
                card("Visa", 300.0),
                loan("Mortgage", 90000.0),
                networth=-86300.0,
            )
        )
        parsed = summary(get_net_worth())
        assert parsed["Total Assets"] == 6000.0
        assert parsed["Total Liabilities"] == 92300.0
        assert parsed["Total Assets"] - parsed["Total Liabilities"] == parsed["Net Worth"]


class TestNetWorthValue:
    def test_net_worth_comes_from_the_api_not_from_local_arithmetic(self, install_api):
        # Deliberately inconsistent: the tool must echo Empower's number.
        install_api(
            accounts=accounts_payload(cash("Savings", 100.0), networth=999999.99)
        )
        assert summary(get_net_worth())["Net Worth"] == 999999.99

    def test_missing_networth_renders_as_zero(self, install_api):
        install_api(accounts={"accounts": [cash("Savings", 100.0)]})
        assert summary(get_net_worth())["Net Worth"] == 0.0

    def test_null_networth_renders_as_zero(self, install_api):
        install_api(accounts={"accounts": [cash("Savings", 100.0)], "networth": None})
        assert summary(get_net_worth())["Net Worth"] == 0.0

    def test_null_accounts_list_renders_an_empty_summary(self, install_api):
        install_api(accounts={"accounts": None, "networth": 0.0})
        parsed = summary(get_net_worth())
        assert parsed == {"Net Worth": 0.0, "Total Assets": 0.0, "Total Liabilities": 0.0}

    def test_negative_net_worth_is_rendered(self, install_api):
        install_api(accounts=accounts_payload(loan("Mortgage", 400000.0), networth=-380000.0))
        assert summary(get_net_worth())["Net Worth"] == -380000.0


class TestCategoryBreakdown:
    def test_subtotals_are_reported_per_category(self, install_api):
        install_api(
            accounts=accounts_payload(
                cash("Checking", 2500.0),
                cash("Savings", 7500.0),
                investment("401k", 150000.0),
                card("Visa", 1200.0),
                loan("Mortgage", 310000.0),
                other("Crypto", 4000.0),
            )
        )
        parsed = summary(get_net_worth())
        assert parsed["Cash & Bank"] == 10000.0
        assert parsed["Investments"] == 150000.0
        assert parsed["Credit Cards"] == 1200.0
        assert parsed["Loans"] == 310000.0
        assert parsed["Other"] == 4000.0

    def test_empty_categories_are_omitted(self, install_api):
        install_api(accounts=accounts_payload(cash("Checking", 100.0)))
        parsed = summary(get_net_worth())
        assert "Cash & Bank" in parsed
        for label in ("Investments", "Credit Cards", "Loans", "Other"):
            assert label not in parsed

    def test_subtotals_never_coerce_signs_either(self, install_api):
        install_api(
            accounts=accounts_payload(card("Visa", 1200.0), card("Overpaid", -300.0))
        )
        assert summary(get_net_worth())["Credit Cards"] == 900.0

    def test_subtotals_include_non_asset_accounts_in_their_group(self, install_api):
        # The category lines sum every balance in the group, unlike Total Assets.
        install_api(
            accounts=accounts_payload(
                cash("Savings", 5000.0), cash("Escrow", 900.0, isAsset=False)
            )
        )
        parsed = summary(get_net_worth())
        assert parsed["Cash & Bank"] == 5900.0
        assert parsed["Total Assets"] == 5000.0


class TestOutputShape:
    def test_header_and_all_summary_lines_are_present(self, install_api):
        install_api(accounts=accounts_payload(cash("Checking", 1.0)))
        text = get_net_worth()
        assert text.startswith("Net Worth Summary\n=================\n")
        assert "By category:" in text
        for label in ("Net Worth:", "Total Assets:", "Total Liabilities:"):
            assert label in text

    def test_empty_portfolio_renders_zeroes(self, install_api):
        install_api(accounts=accounts_payload())
        parsed = summary(get_net_worth())
        assert parsed == {"Net Worth": 0.0, "Total Assets": 0.0, "Total Liabilities": 0.0}

    def test_amounts_are_thousands_separated_to_two_decimals(self, install_api):
        install_api(accounts=accounts_payload(cash("Savings", 1234567.891)))
        assert "$1,234,567.89" in get_net_worth()

    def test_negative_totals_render_with_a_leading_minus(self, install_api):
        install_api(accounts=accounts_payload(card("Overpaid", -1250.5)))
        assert "Total Liabilities: $-1,250.50" in get_net_worth()

    def test_closed_accounts_are_excluded_from_every_total(self, install_api):
        install_api(
            accounts=accounts_payload(
                cash("Open", 1000.0),
                cash("Closed", 50000.0, closedDate="2025-03-01"),
                card("Closed Card", 9000.0, closedDate="2025-03-01"),
            )
        )
        parsed = summary(get_net_worth())
        assert parsed["Total Assets"] == 1000.0
        assert parsed["Total Liabilities"] == 0.0


class TestRealisticPortfolio:
    @pytest.fixture
    def parsed(self, install_api):
        install_api(
            accounts=accounts_payload(
                cash("Checking", 4200.0),
                cash("Savings", 25000.0),
                cash("Overdrawn Joint", -150.0),
                investment("401k", 310000.0),
                investment("Roth IRA", 88000.0),
                investment("Brokerage", 45000.0),
                other("Home", 520000.0),
                card("Visa", 2300.0),
                card("Amex", -120.0),
                loan("Mortgage", 285000.0),
                loan("Auto Loan", 18400.0),
                networth=686470.0,
            )
        )
        return summary(get_net_worth())

    def test_total_assets(self, parsed):
        assert parsed["Total Assets"] == 992050.0

    def test_total_liabilities(self, parsed):
        assert parsed["Total Liabilities"] == 305580.0

    def test_totals_reconcile_with_reported_net_worth(self, parsed):
        assert parsed["Total Assets"] - parsed["Total Liabilities"] == parsed["Net Worth"]
