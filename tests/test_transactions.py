"""Tests for the get_transactions tool: filters, paging, sorting, and signs."""

import re

import pytest

from personal_capital_connector.server import _range_label, get_transactions

from conftest import txn


# date (blank when the source row has no date), right-aligned signed amount,
# description, then "[account]".
ROW_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}|)\s+(-?[\d,]+\.\d{2})  (.*?)  \[")


def dates(text: str) -> list[str]:
    return [ROW_RE.match(ln).group(1) for ln in rows(text)]


def rows(text: str) -> list[str]:
    """Return the rendered transaction lines (they start with a date)."""
    return [ln for ln in text.splitlines() if ROW_RE.match(ln)]


def descriptions(text: str) -> list[str]:
    return [ROW_RE.match(ln).group(3) for ln in rows(text)]


def amounts(text: str) -> list[float]:
    return [float(ROW_RE.match(ln).group(2).replace(",", "")) for ln in rows(text)]


class TestRangeLabel:
    def test_defaults_to_a_lookback_window(self):
        assert _range_label(30, "", "") == "last 30 days"

    def test_start_date_only_runs_through_today(self):
        assert _range_label(30, "2026-01-01", "") == "2026-01-01 to today"

    def test_explicit_range(self):
        assert _range_label(30, "2026-01-01", "2026-02-01") == "2026-01-01 to 2026-02-01"

    def test_end_date_only_is_a_lookback_from_that_date(self):
        assert _range_label(14, "", "2026-04-20") == "14 days ending 2026-04-20"


class TestQueryForwarding:
    def test_arguments_are_forwarded_to_the_api(self, install_api):
        api = install_api(transactions=[txn()])
        get_transactions(days=7, start_date="2026-01-01", end_date="2026-02-01")
        assert api.transaction_calls == [
            {"days": 7, "start_date": "2026-01-01", "end_date": "2026-02-01"}
        ]

    def test_blank_dates_are_forwarded_as_none(self, install_api):
        api = install_api(transactions=[txn()])
        get_transactions(days=14)
        assert api.transaction_calls == [{"days": 14, "start_date": None, "end_date": None}]

    def test_header_reports_the_window_that_was_queried(self, install_api):
        install_api(transactions=[txn()])
        text = get_transactions(start_date="2026-01-01", end_date="2026-02-01")
        assert text.startswith("Transactions — 2026-01-01 to 2026-02-01")


class TestSearch:
    @pytest.fixture
    def api(self, install_api):
        return install_api(
            transactions=[
                txn(description="AVIS RENT A CAR", merchant="Avis", originalDescription="AVIS 123"),
                txn(description="Hertz Toll", merchant="Hertz", originalDescription="HERTZ TOLL"),
                txn(description="Grocery Store", merchant="Kroger", originalDescription="KROGER #7"),
            ]
        )

    def test_single_term_matches_description(self, api):
        assert descriptions(get_transactions(search="avis")) == ["AVIS RENT A CAR"]

    def test_search_is_case_insensitive(self, api):
        assert descriptions(get_transactions(search="HERTZ")) == ["Hertz Toll"]

    def test_comma_separated_terms_are_ored(self, api):
        found = descriptions(get_transactions(search="avis, hertz"))
        assert sorted(found) == ["AVIS RENT A CAR", "Hertz Toll"]

    def test_blank_terms_between_commas_are_ignored(self, api):
        assert descriptions(get_transactions(search="avis, ,")) == ["AVIS RENT A CAR"]

    def test_matches_original_description(self, install_api):
        install_api(
            transactions=[txn(description="Card Purchase", originalDescription="SQ *BLUE BOTTLE")]
        )
        assert len(rows(get_transactions(search="blue bottle"))) == 1

    def test_matches_merchant(self, install_api):
        install_api(
            transactions=[txn(description="Purchase", originalDescription="X", merchant="Peet's")]
        )
        assert len(rows(get_transactions(search="peet"))) == 1

    def test_null_text_fields_do_not_crash_the_filter(self, install_api):
        install_api(
            transactions=[
                txn(description=None, originalDescription=None, merchant=None),
                txn(description="Avis"),
            ]
        )
        assert len(rows(get_transactions(search="avis"))) == 1

    def test_search_term_is_echoed_in_the_header(self, api):
        assert 'Search: "avis"' in get_transactions(search="avis")

    def test_no_match_reports_the_window(self, api):
        text = get_transactions(search="nothing here", days=45)
        assert text == "No transactions found. (last 45 days)"


class TestAccountAndCategoryFilters:
    @pytest.fixture
    def api(self, install_api):
        return install_api(
            transactions=[
                txn(description="A", accountName="Chase Checking", categoryName="Groceries"),
                txn(description="B", accountName="Amex Platinum", categoryName="Travel"),
                txn(description="C", accountName="Chase Sapphire", categoryName="Travel"),
            ]
        )

    def test_account_filter_is_a_case_insensitive_substring(self, api):
        assert sorted(descriptions(get_transactions(account="chase"))) == ["A", "C"]

    def test_category_filter_is_a_case_insensitive_substring(self, api):
        assert sorted(descriptions(get_transactions(category="travel"))) == ["B", "C"]

    def test_filters_combine(self, api):
        assert descriptions(get_transactions(account="chase", category="travel")) == ["C"]

    def test_null_account_and_category_do_not_crash(self, install_api):
        install_api(transactions=[txn(accountName=None, categoryName=None), txn()])
        assert len(rows(get_transactions(account="checking"))) == 1
        assert len(rows(get_transactions(category="restaurants"))) == 1


class TestAmountFilters:
    @pytest.fixture
    def api(self, install_api):
        return install_api(
            transactions=[
                txn(description="Small", amount=5.0),
                txn(description="Medium", amount=50.0),
                txn(description="Large", amount=500.0),
                txn(description="Refund", amount=-75.0, isCredit=True),
            ]
        )

    def test_min_amount_filters_by_magnitude(self, api):
        assert sorted(descriptions(get_transactions(min_amount=50))) == [
            "Large",
            "Medium",
            "Refund",
        ]

    def test_max_amount_filters_by_magnitude(self, api):
        assert sorted(descriptions(get_transactions(max_amount=50))) == ["Medium", "Small"]

    def test_bounds_combine_into_a_band(self, api):
        assert sorted(descriptions(get_transactions(min_amount=40, max_amount=100))) == [
            "Medium",
            "Refund",
        ]

    def test_zero_max_amount_means_no_cap(self, api):
        assert len(rows(get_transactions(max_amount=0))) == 4

    def test_zero_min_amount_means_no_floor(self, api):
        assert len(rows(get_transactions(min_amount=0))) == 4

    def test_missing_amount_is_treated_as_zero(self, install_api):
        install_api(transactions=[{"transactionDate": "2026-06-01", "description": "No amount"}])
        assert len(rows(get_transactions(min_amount=1))) == 0

    def test_null_amount_is_treated_as_zero(self, install_api):
        # Pending rows can arrive with an explicit null amount; abs(None) raises.
        install_api(transactions=[txn(description="Pending", amount=None)])
        assert len(rows(get_transactions(min_amount=1))) == 0
        assert len(rows(get_transactions(max_amount=10))) == 1

    def test_null_amount_does_not_crash_an_unfiltered_query(self, install_api):
        install_api(transactions=[txn(description="Pending", amount=None), txn(amount=5.0)])
        assert len(rows(get_transactions())) == 2


class TestSorting:
    @pytest.fixture
    def api(self, install_api):
        return install_api(
            transactions=[
                txn(description="Middle", transactionDate="2026-06-02"),
                txn(description="Oldest", transactionDate="2026-06-01"),
                txn(description="Newest", transactionDate="2026-06-03"),
            ]
        )

    def test_newest_first_by_default(self, api):
        assert descriptions(get_transactions()) == ["Newest", "Middle", "Oldest"]

    def test_oldest_first_when_requested(self, api):
        assert descriptions(get_transactions(oldest_first=True)) == [
            "Oldest",
            "Middle",
            "Newest",
        ]

    def test_null_date_does_not_crash_the_sort(self, install_api):
        # A null date sorts as an empty string rather than raising on None < str.
        install_api(
            transactions=[
                txn(description="Undated", transactionDate=None),
                txn(description="Dated", transactionDate="2026-06-01"),
            ]
        )
        assert descriptions(get_transactions()) == ["Dated", "Undated"]

    def test_absent_date_key_does_not_crash_the_sort(self, install_api):
        install_api(
            transactions=[
                {"description": "Undated", "amount": 1.0},
                txn(description="Dated", transactionDate="2026-06-01"),
            ]
        )
        assert descriptions(get_transactions()) == ["Dated", "Undated"]

    def test_null_dated_rows_sort_first_when_oldest_first(self, install_api):
        install_api(
            transactions=[
                txn(description="Dated", transactionDate="2026-06-01"),
                txn(description="Undated", transactionDate=None),
            ]
        )
        assert descriptions(get_transactions(oldest_first=True)) == ["Undated", "Dated"]


class TestPaging:
    @pytest.fixture
    def api(self, install_api):
        return install_api(
            transactions=[
                txn(description=f"T{i:02d}", transactionDate=f"2026-06-{i:02d}")
                for i in range(1, 11)
            ]
        )

    def test_limit_truncates_the_page(self, api):
        text = get_transactions(limit=3)
        assert len(rows(text)) == 3
        assert "3 of 10 matched" in text

    def test_offset_skips_rows(self, api):
        text = get_transactions(limit=3, offset=3)
        assert descriptions(text) == ["T07", "T06", "T05"]
        assert "3 of 10 matched, starting at 3" in text

    def test_offset_is_omitted_from_the_header_when_zero(self, api):
        assert "starting at" not in get_transactions(limit=3)

    def test_remaining_count_points_at_the_next_offset(self, api):
        text = get_transactions(limit=4)
        assert "6 more. Re-run with offset=4 to continue." in text

    def test_no_continuation_hint_on_the_last_page(self, api):
        assert "more. Re-run" not in get_transactions(limit=4, offset=6)

    def test_limit_of_zero_returns_everything_from_the_offset(self, api):
        assert len(rows(get_transactions(limit=0, offset=2))) == 8

    def test_negative_offset_is_clamped_to_the_first_page(self, api):
        # A negative offset used to slice from the tail and then report a
        # nonsensical "starting at -5" with a wrong continuation hint.
        text = get_transactions(limit=3, offset=-5)
        assert descriptions(text) == ["T10", "T09", "T08"]
        assert "starting at" not in text
        assert "7 more. Re-run with offset=3 to continue." in text

    def test_offset_past_the_end_yields_an_empty_page(self, api):
        text = get_transactions(limit=5, offset=50)
        assert rows(text) == []
        assert "0 of 10 matched" in text

    def test_total_is_the_matched_count_not_the_page_size(self, api):
        assert "2 of 10 matched" in get_transactions(limit=2)

    def test_paging_covers_every_row_exactly_once(self, api):
        seen = []
        for offset in range(0, 10, 4):
            seen += descriptions(get_transactions(limit=4, offset=offset))
        assert sorted(seen) == [f"T{i:02d}" for i in range(1, 11)]


class TestTotals:
    def test_money_in_and_out_are_reported_separately(self, install_api):
        install_api(
            transactions=[
                txn(description="Paycheck", amount=3000.0, isCredit=True),
                txn(description="Rent", amount=1800.0, isCredit=False),
                txn(description="Coffee", amount=5.0, isCredit=False),
            ]
        )
        text = get_transactions()
        assert "net $1,195.00" in text
        assert "in $3,000.00" in text
        assert "out $1,805.00" in text

    def test_totals_cover_all_matches_not_just_the_page(self, install_api):
        install_api(
            transactions=[
                txn(description=f"T{i}", amount=100.0, transactionDate=f"2026-06-0{i}")
                for i in range(1, 6)
            ]
        )
        assert "out $500.00" in get_transactions(limit=1)

    def test_totals_reflect_the_active_filters(self, install_api):
        install_api(
            transactions=[
                txn(description="Avis", amount=200.0),
                txn(description="Groceries", amount=90.0),
            ]
        )
        assert "out $200.00" in get_transactions(search="avis")

    def test_totals_take_their_sign_from_is_credit_not_the_raw_amount(self, install_api):
        # A debit that arrives with a negative raw amount must still count as
        # money out, so the header total matches the sum of the rows below it.
        install_api(
            transactions=[
                txn(description="Fee", amount=-25.0, isCredit=False),
                txn(description="Refund", amount=-50.0, isCredit=True),
            ]
        )
        text = get_transactions()
        assert "out $25.00" in text
        assert "in $50.00" in text
        assert "net $25.00" in text

    def test_header_total_equals_the_sum_of_the_displayed_rows(self, install_api):
        install_api(
            transactions=[
                txn(description="Paycheck", amount=3000.0, isCredit=True),
                txn(description="Rent", amount=-1800.0, isCredit=False),
                txn(description="Coffee", amount=5.0, isCredit=False),
            ]
        )
        text = get_transactions()
        net = re.search(r"net \$(-?[\d,]+\.\d{2})", text).group(1)
        assert float(net.replace(",", "")) == sum(amounts(text))

    def test_null_amounts_do_not_crash_the_totals(self, install_api):
        install_api(transactions=[txn(amount=None), txn(amount=10.0, isCredit=True)])
        assert "in $10.00" in get_transactions()

    def test_negative_net_is_rendered(self, install_api):
        install_api(transactions=[txn(description="Rent", amount=1800.0, isCredit=False)])
        assert "net $-1,800.00" in get_transactions()


class TestRowRendering:
    def test_debits_render_negative_and_credits_positive(self, install_api):
        install_api(
            transactions=[
                txn(description="Paycheck", amount=3000.0, isCredit=True, transactionDate="2026-06-02"),
                txn(description="Rent", amount=1800.0, isCredit=False, transactionDate="2026-06-01"),
            ]
        )
        assert amounts(get_transactions()) == [3000.0, -1800.0]

    def test_sign_comes_from_is_credit_not_from_the_raw_amount(self, install_api):
        # Empower is inconsistent about the sign on the raw amount; isCredit wins.
        install_api(transactions=[txn(description="Refund", amount=-50.0, isCredit=True)])
        assert amounts(get_transactions()) == [50.0]

    def test_negative_raw_amount_on_a_debit_still_renders_negative(self, install_api):
        install_api(transactions=[txn(description="Fee", amount=-25.0, isCredit=False)])
        assert amounts(get_transactions()) == [-25.0]

    def test_null_date_renders_a_blank_date_column(self, install_api):
        install_api(transactions=[txn(transactionDate=None)])
        assert dates(get_transactions()) == [""]

    def test_date_is_truncated_to_ten_characters(self, install_api):
        install_api(transactions=[txn(transactionDate="2026-06-01T12:00:00.000Z")])
        assert rows(get_transactions())[0].startswith("2026-06-01  ")

    def test_falls_back_to_original_description(self, install_api):
        install_api(transactions=[txn(description=None, originalDescription="SQ *BLUE BOTTLE")])
        assert "SQ *BLUE BOTTLE" in get_transactions()

    def test_account_and_category_are_shown(self, install_api):
        install_api(transactions=[txn(accountName="Chase Checking", categoryName="Restaurants")])
        line = rows(get_transactions())[0]
        assert "[Chase Checking]" in line
        assert "(Restaurants)" in line

    def test_null_account_name_renders_empty_not_the_word_none(self, install_api):
        install_api(transactions=[txn(accountName=None)])
        assert "[]" in rows(get_transactions())[0]
        assert "[None]" not in rows(get_transactions())[0]

    def test_missing_category_is_omitted(self, install_api):
        install_api(transactions=[txn(categoryName=None)])
        assert "()" not in rows(get_transactions())[0]

    def test_missing_amount_renders_as_zero(self, install_api):
        install_api(transactions=[{"transactionDate": "2026-06-01", "description": "Odd"}])
        assert amounts(get_transactions()) == [-0.0]

    def test_pending_and_duplicate_flags(self, install_api):
        install_api(transactions=[txn(isPending=True, isDuplicate=True)])
        line = rows(get_transactions())[0]
        assert "(pending)" in line
        assert "(duplicate)" in line

    def test_flags_absent_by_default(self, install_api):
        install_api(transactions=[txn()])
        line = rows(get_transactions())[0]
        assert "(pending)" not in line and "(duplicate)" not in line


class TestEmptyResults:
    def test_no_transactions_at_all(self, install_api):
        install_api(transactions=[])
        assert get_transactions() == "No transactions found. (last 30 days)"

    def test_filtered_to_nothing_reports_the_explicit_range(self, install_api):
        install_api(transactions=[txn()])
        text = get_transactions(account="nonexistent", start_date="2026-01-01")
        assert text == "No transactions found. (2026-01-01 to today)"
