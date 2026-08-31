"""Unit tests for the small parsing helpers in client.py."""

from datetime import datetime

import pytest

from personal_capital_connector.client import _extract_last4, _parse_date, _safe_float


class TestSafeFloat:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (12, 12.0),
            (12.5, 12.5),
            ("12.5", 12.5),
            ("-300", -300.0),
            ("0", 0.0),
            (0, 0.0),
            (True, 1.0),
        ],
    )
    def test_converts_numeric_input(self, value, expected):
        assert _safe_float(value) == expected

    @pytest.mark.parametrize("value", [None, "", "abc", "1,200", [], {}, object()])
    def test_returns_none_for_unconvertible_input(self, value):
        assert _safe_float(value) is None

    def test_zero_is_not_confused_with_none(self):
        # Callers use `is None` checks, so 0.0 must survive as a real value.
        assert _safe_float(0) is not None


class TestParseDate:
    def test_parses_iso_date(self):
        assert _parse_date("2026-03-04", "start_date") == datetime(2026, 3, 4)

    @pytest.mark.parametrize(
        "value",
        ["03/04/2026", "2026-3-4x", "2026-13-01", "2026-02-30", "not a date", "", None],
    )
    def test_rejects_bad_input(self, value):
        with pytest.raises(ValueError):
            _parse_date(value, "start_date")

    def test_error_names_the_offending_field_and_value(self):
        with pytest.raises(ValueError) as exc:
            _parse_date("06/01/2026", "end_date")
        message = str(exc.value)
        assert "end_date" in message
        assert "06/01/2026" in message


class TestExtractLast4:
    @pytest.mark.parametrize(
        "original_name,expected",
        [
            ("Chase Sapphire Ending in 7783", "7783"),
            ("Savings ending in 1234", "1234"),
            ("Card Ending in 0042  ", "0042"),
            ("Ending in 9999", "9999"),
        ],
    )
    def test_extracts_trailing_four_digits(self, original_name, expected):
        assert _extract_last4(original_name) == expected

    @pytest.mark.parametrize(
        "original_name",
        [
            None,
            "",
            "Chase Sapphire",
            "Ending in 123",  # too few digits
            "Ending in 12345",  # too many digits
            "Ending in 7783 Savings",  # digits not at the end
            "7783",  # no marker phrase
        ],
    )
    def test_returns_none_when_there_is_no_match(self, original_name):
        assert _extract_last4(original_name) is None
