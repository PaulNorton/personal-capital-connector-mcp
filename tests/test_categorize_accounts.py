"""Tests for categorize_accounts: grouping, filtering, and field mapping."""

import pytest

from personal_capital_connector.client import categorize_accounts

from conftest import account, accounts_payload

GROUPS = ("cash", "credit", "investment", "loan", "other")


def group_of(acct: dict) -> str:
    """Categorize a single account and return the group it landed in."""
    result = categorize_accounts(accounts_payload(acct))
    landed = [g for g in GROUPS if result["accounts"][g]]
    assert len(landed) == 1, f"expected exactly one group, got {landed}"
    return landed[0]


class TestShape:
    def test_empty_payload_returns_all_groups_empty(self):
        result = categorize_accounts({})
        assert result["networth"] == 0
        assert result["total_accounts"] == 0
        assert set(result["accounts"]) == set(GROUPS)
        assert all(result["accounts"][g] == [] for g in GROUPS)

    def test_null_accounts_list_is_treated_as_empty(self):
        result = categorize_accounts({"accounts": None, "networth": 100.0})
        assert result["total_accounts"] == 0
        assert all(result["accounts"][g] == [] for g in GROUPS)

    def test_null_networth_becomes_zero(self):
        # Callers format this with :,.2f, which raises on None.
        assert categorize_accounts({"accounts": [], "networth": None})["networth"] == 0

    def test_networth_is_passed_through_untouched(self):
        # networth comes straight from Empower; it is never recomputed locally.
        payload = accounts_payload(account(balance=1.0), networth=987654.32)
        assert categorize_accounts(payload)["networth"] == 987654.32

    def test_total_accounts_counts_only_included_accounts(self):
        payload = accounts_payload(
            account(name="Open"),
            account(name="Closed", closedDate="2025-01-01"),
            account(name="Zero", balance=0.0),
        )
        assert categorize_accounts(payload, hide_zero_balance=True)["total_accounts"] == 1
        assert categorize_accounts(payload, hide_zero_balance=False)["total_accounts"] == 2


class TestGroupingByAccountTypeGroup:
    @pytest.mark.parametrize(
        "type_group,expected",
        [
            ("BANK", "cash"),
            ("CREDIT_CARD", "credit"),
            ("RETIREMENT", "investment"),
            ("INVESTMENT", "investment"),
            ("EDUCATIONAL", "investment"),
            ("HEALTH", "investment"),
            ("LOAN", "loan"),
            ("MORTGAGE", "loan"),
            ("ESOP", "other"),
        ],
    )
    def test_group_maps_to_bucket(self, type_group, expected):
        assert group_of(account(accountTypeGroup=type_group, productType=None)) == expected

    def test_matching_is_case_insensitive(self):
        assert group_of(account(accountTypeGroup="bank", productType=None)) == "cash"
        assert group_of(account(accountTypeGroup="Credit_Card", productType=None)) == "credit"


class TestGroupingByProductType:
    @pytest.mark.parametrize(
        "product,expected",
        [
            ("BANK", "cash"),
            ("CHECKING", "cash"),
            ("SAVINGS", "cash"),
            ("CD", "cash"),
            ("MONEY_MARKET", "cash"),
            ("CREDIT_CARD", "credit"),
            ("INVESTMENT", "investment"),
            ("401K", "investment"),
            ("IRA", "investment"),
            ("ROTH_IRA", "investment"),
            ("BROKERAGE", "investment"),
            ("529", "investment"),
            ("SEP_IRA", "investment"),
            ("SIMPLE_IRA", "investment"),
            ("403B", "investment"),
            ("PENSION", "investment"),
            ("ANNUITY", "investment"),
            ("STOCK_PLAN", "investment"),
            ("HSA", "investment"),
            ("LOAN", "loan"),
            ("MORTGAGE", "loan"),
            ("AUTO_LOAN", "loan"),
            ("STUDENT_LOAN", "loan"),
            ("HOME_EQUITY", "loan"),
            ("PERSONAL_LOAN", "loan"),
        ],
    )
    def test_product_type_classifies_when_group_is_absent(self, product, expected):
        assert group_of(account(accountTypeGroup=None, productType=product)) == expected

    def test_product_type_matching_is_case_insensitive(self):
        assert group_of(account(accountTypeGroup=None, productType="auto_loan")) == "loan"


class TestGroupingFallbacks:
    def test_unrecognized_classification_lands_in_other(self):
        assert group_of(account(accountTypeGroup="CRYPTO", productType="CRYPTO")) == "other"

    def test_missing_classification_lands_in_other(self):
        assert group_of({"name": "Mystery", "balance": 5.0}) == "other"

    def test_none_classification_lands_in_other(self):
        assert group_of(account(accountTypeGroup=None, productType=None)) == "other"

    def test_account_type_group_wins_over_product_type(self):
        # Branches are checked in order, so the group field takes precedence.
        acct = account(accountTypeGroup="BANK", productType="CREDIT_CARD")
        assert group_of(acct) == "cash"


class TestExclusions:
    def test_closed_accounts_are_always_excluded(self):
        payload = accounts_payload(account(balance=500.0, closedDate="2024-08-01"))
        for hide_zero in (True, False):
            result = categorize_accounts(payload, hide_zero_balance=hide_zero)
            assert result["accounts"]["cash"] == []
            assert result["total_accounts"] == 0

    def test_zero_balance_kept_by_default(self):
        payload = accounts_payload(account(balance=0.0))
        assert len(categorize_accounts(payload)["accounts"]["cash"]) == 1

    def test_zero_balance_dropped_when_requested(self):
        payload = accounts_payload(account(balance=0.0))
        result = categorize_accounts(payload, hide_zero_balance=True)
        assert result["accounts"]["cash"] == []

    def test_unparseable_balance_counts_as_zero_for_hiding(self):
        payload = accounts_payload(account(balance="n/a"), account(balance=None))
        result = categorize_accounts(payload, hide_zero_balance=True)
        assert result["accounts"]["cash"] == []

    def test_negative_balances_are_never_hidden(self):
        payload = accounts_payload(account(balance=-25.0))
        result = categorize_accounts(payload, hide_zero_balance=True)
        assert len(result["accounts"]["cash"]) == 1


class TestBalanceCoercion:
    @pytest.mark.parametrize(
        "raw,expected",
        [(1234.56, 1234.56), ("1234.56", 1234.56), (-300, -300.0), (None, 0.0), ("junk", 0.0)],
    )
    def test_balance_is_coerced_to_float(self, raw, expected):
        result = categorize_accounts(accounts_payload(account(balance=raw)))
        assert result["accounts"]["cash"][0]["balance"] == expected

    def test_missing_balance_defaults_to_zero(self):
        payload = {"accounts": [{"accountTypeGroup": "BANK", "name": "No Balance"}]}
        result = categorize_accounts(payload)
        assert result["accounts"]["cash"][0]["balance"] == 0.0


class TestDisplayName:
    def test_prefers_the_user_assigned_name(self):
        acct = account(name="Vacation Fund", firmName="Ally", accountType="Savings")
        result = categorize_accounts(accounts_payload(acct))
        assert result["accounts"]["cash"][0]["name"] == "Vacation Fund"

    def test_falls_back_to_firm_and_type(self):
        acct = account(name="", firmName="Ally", accountType="Savings")
        result = categorize_accounts(accounts_payload(acct))
        assert result["accounts"]["cash"][0]["name"] == "Ally Savings"

    def test_fallback_strips_when_one_part_is_missing(self):
        acct = account(name=None, firmName="Ally", accountType="")
        result = categorize_accounts(accounts_payload(acct))
        assert result["accounts"]["cash"][0]["name"] == "Ally"

    def test_appends_last4_from_original_name(self):
        acct = account(name="Checking", originalName="Chase Checking Ending in 7783")
        result = categorize_accounts(accounts_payload(acct))
        assert result["accounts"]["cash"][0]["name"] == "Checking (…7783)"

    def test_no_suffix_when_original_name_has_no_last4(self):
        acct = account(name="Checking", originalName="Chase Checking")
        result = categorize_accounts(accounts_payload(acct))
        assert result["accounts"]["cash"][0]["name"] == "Checking"


class TestFieldMapping:
    def test_is_asset_defaults_to_true_when_absent(self):
        result = categorize_accounts(accounts_payload(account()))
        assert result["accounts"]["cash"][0]["is_asset"] is True

    def test_is_asset_false_is_preserved(self):
        result = categorize_accounts(accounts_payload(account(isAsset=False)))
        assert result["accounts"]["cash"][0]["is_asset"] is False

    def test_null_is_asset_is_treated_as_an_asset(self):
        # Absent and null both mean "asset"; only an explicit false flips it.
        result = categorize_accounts(accounts_payload(account(isAsset=None)))
        assert result["accounts"]["cash"][0]["is_asset"] is True

    def test_defaults_for_optional_metadata(self):
        entry = categorize_accounts(accounts_payload(account()))["accounts"]["cash"][0]
        assert entry["is_manual"] is False
        assert entry["currency"] == "USD"
        assert entry["last_refreshed"] == ""
        assert entry["subtype"] == ""

    def test_numeric_extras_are_none_when_absent(self):
        entry = categorize_accounts(accounts_payload(account()))["accounts"]["cash"][0]
        for field in (
            "credit_limit",
            "available_credit",
            "min_payment",
            "interest_rate",
            "original_loan_amount",
        ):
            assert entry[field] is None, field
        assert entry["payment_due_date"] is None

    def test_credit_card_fields_are_coerced_to_floats(self):
        acct = account(
            accountTypeGroup="CREDIT_CARD",
            creditLimit="10000",
            availableCredit="8800.50",
            minPayment="35",
            paymentDueDate="2026-07-01",
            interestRate="19.99",
        )
        entry = categorize_accounts(accounts_payload(acct))["accounts"]["credit"][0]
        assert entry["credit_limit"] == 10000.0
        assert entry["available_credit"] == 8800.50
        assert entry["min_payment"] == 35.0
        assert entry["interest_rate"] == 19.99
        assert entry["payment_due_date"] == "2026-07-01"

    def test_loan_fields_are_coerced_to_floats(self):
        acct = account(
            accountTypeGroup="MORTGAGE",
            interestRate="4.125",
            originalLoanAmount="400000",
        )
        entry = categorize_accounts(accounts_payload(acct))["accounts"]["loan"][0]
        assert entry["interest_rate"] == 4.125
        assert entry["original_loan_amount"] == 400000.0

    def test_unparseable_extras_become_none_rather_than_raising(self):
        acct = account(accountTypeGroup="CREDIT_CARD", creditLimit="unknown")
        entry = categorize_accounts(accounts_payload(acct))["accounts"]["credit"][0]
        assert entry["credit_limit"] is None


class TestOrdering:
    def test_accounts_keep_their_input_order_within_a_group(self):
        payload = accounts_payload(
            account(name="First"), account(name="Second"), account(name="Third")
        )
        names = [a["name"] for a in categorize_accounts(payload)["accounts"]["cash"]]
        assert names == ["First", "Second", "Third"]
