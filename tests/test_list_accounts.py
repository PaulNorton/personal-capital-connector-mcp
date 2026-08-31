"""Tests for the list_accounts tool: filtering, totals, and the detail suffixes."""

import pytest

from personal_capital_connector.server import list_accounts

from conftest import account, accounts_payload


def line_for(text: str, name: str) -> str:
    """Return the rendered bullet line for a given account name."""
    for line in text.splitlines():
        if line.lstrip().startswith("•") and name in line:
            return line
    raise AssertionError(f"no line for {name!r} in:\n{text}")


PORTFOLIO = accounts_payload(
    account(name="Checking", balance=2500.0, accountTypeGroup="BANK"),
    account(name="Visa", balance=1200.0, accountTypeGroup="CREDIT_CARD"),
    account(name="401k", balance=150000.0, accountTypeGroup="RETIREMENT"),
    account(name="Mortgage", balance=310000.0, accountTypeGroup="MORTGAGE"),
    account(name="Crypto", balance=4000.0, accountTypeGroup="CRYPTO", productType=None),
    networth=-154700.0,
)


class TestFiltering:
    def test_all_shows_every_group(self, install_api):
        install_api(accounts=PORTFOLIO)
        text = list_accounts()
        for label in (
            "Cash & Bank Accounts",
            "Credit Cards",
            "Investment Accounts",
            "Loans & Mortgages",
            "Other",
        ):
            assert label in text

    @pytest.mark.parametrize(
        "type_filter,kept,dropped",
        [
            ("cash", "Checking", "Visa"),
            ("credit", "Visa", "Checking"),
            ("investment", "401k", "Visa"),
            ("loan", "Mortgage", "Checking"),
            ("other", "Crypto", "Checking"),
        ],
    )
    def test_type_filter_narrows_to_one_group(self, install_api, type_filter, kept, dropped):
        install_api(accounts=PORTFOLIO)
        text = list_accounts(type_filter=type_filter)
        assert kept in text
        assert dropped not in text

    def test_empty_groups_are_omitted(self, install_api):
        install_api(
            accounts=accounts_payload(account(name="Checking", accountTypeGroup="BANK"))
        )
        text = list_accounts()
        assert "Cash & Bank Accounts" in text
        assert "Credit Cards" not in text

    def test_filter_on_an_empty_group_still_reports_net_worth(self, install_api):
        install_api(accounts=PORTFOLIO)
        text = list_accounts(type_filter="credit")
        assert text.startswith("Net Worth: $-154,700.00")

    def test_hide_zero_balance_is_passed_through(self, install_api):
        install_api(
            accounts=accounts_payload(
                account(name="Active", balance=100.0),
                account(name="Dormant", balance=0.0),
            )
        )
        assert "Dormant" in list_accounts()
        assert "Dormant" not in list_accounts(hide_zero_balance=True)


class TestTotals:
    def test_net_worth_header_comes_from_the_api(self, install_api):
        install_api(accounts=PORTFOLIO)
        assert list_accounts().startswith("Net Worth: $-154,700.00")

    def test_group_header_shows_the_group_total(self, install_api):
        install_api(
            accounts=accounts_payload(
                account(name="Checking", balance=2500.0),
                account(name="Savings", balance=7500.0),
            )
        )
        assert "Cash & Bank Accounts ($10,000.00 total)" in list_accounts()

    def test_group_total_does_not_coerce_signs(self, install_api):
        install_api(
            accounts=accounts_payload(
                account(name="Visa", balance=1200.0, accountTypeGroup="CREDIT_CARD"),
                account(name="Overpaid", balance=-300.0, accountTypeGroup="CREDIT_CARD"),
            )
        )
        assert "Credit Cards ($900.00 total)" in list_accounts()


class TestAccountLines:
    def test_balance_is_formatted_with_separators(self, install_api):
        install_api(accounts=accounts_payload(account(name="Savings", balance=1234567.891)))
        assert "• Savings: $1,234,567.89" in list_accounts()

    def test_negative_balance_keeps_its_sign(self, install_api):
        install_api(accounts=accounts_payload(account(name="Overdrawn", balance=-42.5)))
        assert "• Overdrawn: $-42.50" in list_accounts()

    def test_last4_suffix_is_shown(self, install_api):
        install_api(
            accounts=accounts_payload(
                account(name="Checking", originalName="Chase Checking Ending in 7783")
            )
        )
        assert "• Checking (…7783):" in list_accounts()

    def test_plain_account_has_no_detail_suffixes(self, install_api):
        install_api(accounts=accounts_payload(account(name="Checking", balance=100.0)))
        assert line_for(list_accounts(), "Checking").strip() == "• Checking: $100.00"


class TestCreditCardDetails:
    def card(self, **kw):
        kw.setdefault("balance", 1000.0)
        return account(name="Visa", accountTypeGroup="CREDIT_CARD", **kw)

    def test_utilization_is_computed_from_the_limit(self, install_api):
        install_api(accounts=accounts_payload(self.card(creditLimit=5000)))
        assert "limit $5,000 (20% used)" in line_for(list_accounts(), "Visa")

    def test_utilization_uses_the_absolute_balance(self, install_api):
        # A credit balance must not render as negative utilization.
        install_api(accounts=accounts_payload(self.card(balance=-500.0, creditLimit=5000)))
        assert "(10% used)" in line_for(list_accounts(), "Visa")

    def test_no_limit_segment_when_limit_is_absent(self, install_api):
        install_api(accounts=accounts_payload(self.card()))
        assert "limit" not in line_for(list_accounts(), "Visa")

    def test_no_limit_segment_when_limit_is_zero(self, install_api):
        # Guards against a divide-by-zero on cards reporting a 0 limit.
        install_api(accounts=accounts_payload(self.card(creditLimit=0)))
        assert "limit" not in line_for(list_accounts(), "Visa")

    def test_available_credit_is_shown(self, install_api):
        install_api(accounts=accounts_payload(self.card(availableCredit=4000.0)))
        assert "available $4,000.00" in line_for(list_accounts(), "Visa")

    def test_zero_available_credit_is_still_shown(self, install_api):
        # A maxed-out card is exactly when this number matters most.
        install_api(accounts=accounts_payload(self.card(availableCredit=0)))
        assert "available $0.00" in line_for(list_accounts(), "Visa")

    def test_due_date_and_min_payment_are_shown(self, install_api):
        install_api(
            accounts=accounts_payload(
                self.card(paymentDueDate="2026-07-01", minPayment=35.0)
            )
        )
        line = line_for(list_accounts(), "Visa")
        assert "due 2026-07-01" in line
        assert "min payment $35.00" in line

    def test_zero_min_payment_is_omitted(self, install_api):
        install_api(accounts=accounts_payload(self.card(minPayment=0)))
        assert "min payment" not in line_for(list_accounts(), "Visa")

    def test_apr_is_shown_to_two_decimals(self, install_api):
        install_api(accounts=accounts_payload(self.card(interestRate=19.9)))
        assert "19.90% APR" in line_for(list_accounts(), "Visa")

    def test_zero_apr_is_omitted(self, install_api):
        install_api(accounts=accounts_payload(self.card(interestRate=0)))
        assert "APR" not in line_for(list_accounts(), "Visa")

    def test_all_details_appear_in_a_stable_order(self, install_api):
        install_api(
            accounts=accounts_payload(
                self.card(
                    creditLimit=5000,
                    availableCredit=4000.0,
                    paymentDueDate="2026-07-01",
                    minPayment=35.0,
                    interestRate=19.99,
                )
            )
        )
        line = line_for(list_accounts(), "Visa")
        positions = [line.index(part) for part in ("limit", "available", "due", "min payment", "APR")]
        assert positions == sorted(positions)


class TestLoanDetails:
    def test_apr_is_shown_for_loans(self, install_api):
        install_api(
            accounts=accounts_payload(
                account(
                    name="Mortgage",
                    accountTypeGroup="MORTGAGE",
                    balance=310000.0,
                    interestRate=4.125,
                )
            )
        )
        assert "4.12% APR" in line_for(list_accounts(), "Mortgage")


class TestEdgeCases:
    def test_no_accounts_renders_only_the_net_worth_header(self, install_api):
        install_api(accounts=accounts_payload(networth=0.0))
        assert list_accounts().strip() == "Net Worth: $0.00"

    def test_null_networth_renders_as_zero(self, install_api):
        install_api(accounts={"accounts": [], "networth": None})
        assert list_accounts().strip() == "Net Worth: $0.00"

    def test_closed_accounts_are_excluded(self, install_api):
        install_api(
            accounts=accounts_payload(
                account(name="Open", balance=100.0),
                account(name="Closed", balance=100.0, closedDate="2025-01-01"),
            )
        )
        text = list_accounts()
        assert "Open" in text
        assert "Closed" not in text
