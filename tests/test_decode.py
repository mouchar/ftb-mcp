"""Unit tests for decode helpers.

Every hex literal here was captured from kafkova.ftb during schema reverse
engineering, so these tests pin behaviour against real stored bytes.
"""

from __future__ import annotations

import pytest

from ftb_mcp.decode import (
    OPEN_LOWER_BOUND,
    OPEN_UPPER_BOUND,
    UNKNOWN_DATE,
    clean_text,
    norm_date,
    pb_fields,
    pb_text,
    split_ftb_date,
    year_of,
)


def blob(hex_string: str) -> str:
    """Mimic how sqlite3 hands us a TEXT column read with surrogateescape."""
    return bytes.fromhex(hex_string).decode("utf-8", "surrogateescape")


# Real rows from individual_fact_main_data.date
DATE_1735 = "0A0A37204F43542031373335222D0801100018002007280A30C70D380040004800500058BF843D60006800700078008001008801019001B69BDE52"
DATE_1791 = "0A0B3139204D41522031373931222D0801100018002013280330FF0D380040004800500058BF843D60006800700078008001008801019001D6CBB355"
DATE_BETWEEN = "0A11424554203230313220414E442032303230222C0805100018022000280030DC0F380040024800500058E40F6000680070007800800100880101900180A3F85F"
DATE_BEFORE = "0A084245462031383536222D0801100118022000280030C00E380040004800500058BF843D60006800700078008001008801019001FC8FC058"
DATE_AFTER = "0A084146542031393034222D0801100218022000280030F00E380040004800500058BF843D600068007000780080010088010190019B99EB5A"

# Real rows from individual_fact_lang_data.header (RESI facts)
ADDR_SHORT = "0A0D4272616E6EC3A120C48D2E3133"
ADDR_LONG = "125E43616E62792C2043616E656D61682C204D61706C65204C616E652C20616E64204E6577204572612050726563696E6374732043616E627920746F776E2C20436C61636B616D61732C204F7265676F6E2C20556E6974656420537461746573"


class TestProtobuf:
    def test_parses_length_delimited_and_varint_fields(self):
        parsed = pb_fields(bytes.fromhex(ADDR_SHORT))
        assert parsed[1] == [b"Brann\xc3\xa1 \xc4\x8d.13"]

    def test_extracts_czech_address_line(self):
        assert pb_text(blob(ADDR_SHORT)) == "Branná č.13"

    def test_extracts_field_two_when_field_one_absent(self):
        assert pb_text(blob(ADDR_LONG)).startswith("Canby, Canemah, Maple Lane")

    def test_plain_text_passes_through_untouched(self):
        assert pb_text("knížecí luční hajný") == "knížecí luční hajný"
        assert pb_text("sedlák") == "sedlák"

    def test_empty_input_yields_empty_string(self):
        assert pb_text("") == ""
        assert pb_text(None) == ""

    def test_malformed_protobuf_degrades_instead_of_raising(self):
        # Leading 0x0A claims a length-delimited field, then the buffer is truncated.
        assert pb_text(blob("0AFF41424344")) == "ABCD"

    def test_nested_message_is_not_mistaken_for_text(self):
        # Field 4 of a date is a nested message; asking for field 1 must ignore it.
        assert pb_text(blob(DATE_1791), fields=(1,)) == "19 MAR 1791"

    def test_parses_fixed32_and_fixed64_as_raw_bytes(self):
        # Wire type 5 (field 1) then wire type 1 (field 2): nothing in the FTB format
        # needs these interpreted, so they come back as the bytes they occupy.
        parsed = pb_fields(bytes.fromhex("0D01020304" + "11" + "0102030405060708"))
        assert parsed[1] == [bytes.fromhex("01020304")]
        assert parsed[2] == [bytes.fromhex("0102030405060708")]

    def test_truncated_fixed_width_field_is_rejected(self):
        for truncated in ("0D0102", "110102030405"):
            with pytest.raises(ValueError):
                pb_fields(bytes.fromhex(truncated))

    def test_unsupported_wire_type_is_rejected(self):
        # Wire types 3 and 4 are the deprecated groups; 6 and 7 were never defined.
        with pytest.raises(ValueError, match="wire type"):
            pb_fields(bytes.fromhex("0F00"))

    def test_field_number_zero_is_rejected(self):
        with pytest.raises(ValueError, match="field number 0"):
            pb_fields(bytes.fromhex("0000"))

    def test_overlong_and_truncated_varints_are_rejected(self):
        with pytest.raises(ValueError, match="varint"):
            pb_fields(b"\x08" + b"\xff" * 12)
        with pytest.raises(ValueError, match="truncated varint"):
            pb_fields(b"\x08\xff")


class TestDates:
    def test_display_string_from_protobuf_field_one(self):
        assert norm_date(blob(DATE_1735), 17351007, 17351007, 17351007)["display"] == "7 OCT 1735"

    def test_structured_parts_from_integer_columns(self):
        result = norm_date(blob(DATE_1791), 17910319, 17910319, 17910319)
        assert (result["year"], result["month"], result["day"]) == (1791, 3, 19)
        assert result["is_range"] is False

    def test_between_range_exposes_both_years(self):
        result = norm_date(blob(DATE_BETWEEN), 20120001, 20120000, 20200000)
        assert result["display"] == "BET 2012 AND 2020"
        assert (result["year_from"], result["year_to"]) == (2012, 2020)
        assert result["is_range"] is True

    def test_before_date_has_open_lower_bound(self):
        result = norm_date(blob(DATE_BEFORE), 18559999, -99999999, 18560000)
        assert result["display"] == "BEF 1856"
        assert result["year_from"] is None
        assert result["year_to"] == 1856

    def test_after_date_has_open_upper_bound(self):
        """FTB writes 99999999 for "no upper bound"; reading it as a date gives 9999."""
        result = norm_date(blob(DATE_AFTER), 19040001, 19040000, 99999999)
        assert result["display"] == "AFT 1904"
        assert result["year_from"] == 1904
        assert result["year_to"] is None, "an open bound is unknown, not the year 9999"

    def test_unknown_date_returns_none(self):
        assert norm_date("", 999999999, 999999999, 999999999) is None

    def test_partial_date_keeps_year_drops_bogus_month_day(self):
        assert split_ftb_date(18559999) == {"year": 1855, "month": None, "day": None}

    def test_year_of_sentinel_is_none(self):
        assert year_of(999999999) is None
        assert year_of(17910319) == 1791


class TestNoDateSentinels:
    """FTB's three ways of saying "no date" must all decode to None, not to a year."""

    def test_every_sentinel_is_unknown(self):
        for sentinel in (UNKNOWN_DATE, OPEN_UPPER_BOUND, OPEN_LOWER_BOUND):
            assert split_ftb_date(sentinel) == {"year": None, "month": None, "day": None}
            assert year_of(sentinel) is None

    def test_the_sentinels_are_the_values_ftb_writes(self):
        assert (UNKNOWN_DATE, OPEN_UPPER_BOUND, OPEN_LOWER_BOUND) == (
            999999999,
            99999999,
            -99999999,
        )

    def test_no_real_date_reaches_the_sentinel_magnitude(self):
        """The test is on magnitude, so it must not swallow a plausible date."""
        assert year_of(20250127) == 2025  # the latest real date in kafkova.ftb
        assert year_of(99991231) == 9999  # absurd, but below the sentinel and so kept
        assert year_of(-18000101) == -1800  # a B.C. date stays negative

    def test_missing_value_is_unknown(self):
        assert year_of(None) is None
        assert split_ftb_date(None) == {"year": None, "month": None, "day": None}

    def test_an_open_upper_bound_does_not_become_a_range_endpoint(self):
        date = norm_date("AFT 1904", 19040001, 19040000, OPEN_UPPER_BOUND)
        assert (date["year_from"], date["year_to"]) == (1904, None)

    def test_an_open_lower_bound_does_not_become_a_range_endpoint(self):
        date = norm_date("BEF 1856", 18559999, OPEN_LOWER_BOUND, 18560000)
        assert (date["year_from"], date["year_to"]) == (None, 1856)


class TestCleanText:
    def test_strips_paragraph_tags_and_entities(self):
        assert clean_text("<p>v&iacute; se , že statku hospodařil</p>") == (
            "ví se , že statku hospodařil"
        )

    def test_double_escaped_break_becomes_newline(self):
        assert clean_text("Anna Sophia Herdova&amp;lt;br&amp;gt;Narození: 1850") == (
            "Anna Sophia Herdova\nNarození: 1850"
        )

    def test_single_escaped_break_becomes_newline(self):
        assert clean_text("František Matějů<br>Narození: 1. Duben") == (
            "František Matějů\nNarození: 1. Duben"
        )

    def test_collapses_runs_of_blank_lines(self):
        assert clean_text("<p>a</p><p></p><p></p><p>b</p>") == "a\n\nb"

    def test_resolves_entities_into_czech_diacritics(self):
        # Real note_lang_data row: FTB escapes diacritics as named HTML entities.
        assert clean_text("<p>bydli&scaron;tě: Majdalena, Plzeň</p>") == (
            "bydliště: Majdalena, Plzeň"
        )

    def test_leaves_unknown_entities_alone(self):
        assert clean_text("<p>100&nonsuch; &amp; more</p>") == "100&nonsuch; & more"

    def test_empty_input(self):
        assert clean_text(None) == ""
        assert clean_text("") == ""
