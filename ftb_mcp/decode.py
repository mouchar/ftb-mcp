"""Decoding helpers for the quirks of the FTB storage format.

Three traps live in this file, all discovered by inspecting kafkova.ftb rather than
from any published spec:

1. ``individual_fact_main_data.date`` is a protobuf message, not the free text the
   schema comment claims. Field 1 holds the display string ("19 MAR 1791").
2. ``individual_fact_lang_data.header`` is a protobuf message for RESI (residence)
   facts only. Fields 1 and 2 hold address lines. Every other fact type stores
   plain text in the same column.
3. Note and citation text is HTML, sometimes escaped twice
   (``&amp;lt;br&amp;gt;`` needs two unescape passes to become a line break).

Every function here is total: malformed input yields a best-effort string, never an
exception. Genealogy files are decades old and hand-edited; crashing on one bad row
would take down a whole query.
"""

from __future__ import annotations

import html
import re

# FTB has three ways of saying "there is no date here" in the *_search_date and
# sorted_date integer columns, and they are not interchangeable:
#
#   999999999   unknown or absent
#    99999999   open upper bound, written for AFT and FROM dates
#   -99999999   open lower bound, written for BEF and TO dates
#
# Every one exceeds the magnitude of any real YYYYMMDD -- the largest in kafkova.ftb is
# 20250127, and no value between 20250127 and 99999999 occurs -- so a single test on the
# absolute value recognises all three. Reading 99999999 as a date is how "AFT 1904" came
# to report an upper bound of the year 9999.
UNKNOWN_DATE = 999999999
OPEN_UPPER_BOUND = 99999999
OPEN_LOWER_BOUND = -99999999
_OPEN_MAGNITUDE = OPEN_UPPER_BOUND

# Every date blob also carries a nested struct (field 4) holding a date in pieces
# ({2: qualifier, 4: day, 5: month, 6: year}), and it is tempting to read a date out of
# it when the display string and the integer columns are all empty. Do not: that struct
# is the editor's working copy, not the stored fact. In kafkova.ftb it holds a date for
# 12 facts that record none, and 4 of those dates belong to a different record entirely
# -- three repeat the person's own birth date as their death, and one carries a
# daughter's birth date into her mother's. A fact whose display string and integer
# columns are all empty has no date; that is the answer, and inventing one from field 4
# produced 29 impossible parent/child chronologies before this was removed.

_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<\s*(?:br\s*/?|/\s*p|/\s*div|/\s*li)\s*>", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")


# --------------------------------------------------------------------------- protobuf


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Read a base-128 varint. Returns (value, new_position)."""
    result = 0
    shift = 0
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def pb_fields(buf: bytes) -> dict[int, list[bytes | int]]:
    """Parse a protobuf message into {field_number: [values]}.

    Length-delimited fields yield bytes, varints yield ints. Fixed32/64 yield raw
    bytes since nothing in the FTB format needs them interpreted.
    """
    out: dict[int, list[bytes | int]] = {}
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field, wire = key >> 3, key & 0x07
        if field == 0:
            raise ValueError("invalid field number 0")
        value: bytes | int
        if wire == 0:
            value, pos = _read_varint(buf, pos)
        elif wire == 2:
            length, pos = _read_varint(buf, pos)
            if length < 0 or pos + length > len(buf):
                raise ValueError("length-delimited field overruns buffer")
            value = buf[pos : pos + length]
            pos += length
        elif wire == 5:
            if pos + 4 > len(buf):
                raise ValueError("truncated fixed32")
            value, pos = buf[pos : pos + 4], pos + 4
        elif wire == 1:
            if pos + 8 > len(buf):
                raise ValueError("truncated fixed64")
            value, pos = buf[pos : pos + 8], pos + 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(field, []).append(value)
    return out


def to_bytes(value: str | bytes | None) -> bytes:
    """Recover the original bytes of a column read with surrogateescape decoding."""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", "surrogateescape")


def _printable(value: str) -> str:
    """Last-resort cleanup: drop control and replacement chars, keep the remainder."""
    return "".join(ch for ch in value if (ch.isprintable() and ch != "�") or ch in "\n\t").strip()


def looks_like_protobuf(value: str | bytes | None) -> bool:
    """True when a nominally-text column actually holds a protobuf message.

    FTB never starts a real text value with a control character, but every protobuf
    message here starts with a field key byte such as 0x0A (field 1) or 0x12 (field 2).
    """
    raw = to_bytes(value)
    return bool(raw) and raw[0] < 0x20


def pb_text(value: str | bytes | None, fields: tuple[int, ...] = (1, 2)) -> str:
    """Extract human-readable text from a possibly-protobuf column.

    Plain text passes through untouched. Protobuf input yields the requested string
    fields, deduplicated and joined. Undecodable input degrades to its printable
    characters rather than raising.
    """
    if not value:
        return ""
    if not looks_like_protobuf(value):
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else value

    raw = to_bytes(value)
    try:
        parsed = pb_fields(raw)
    except ValueError:
        return _printable(raw.decode("utf-8", "replace"))

    parts: list[str] = []
    for field in fields:
        for entry in parsed.get(field, []):
            if not isinstance(entry, bytes):
                continue
            text = entry.decode("utf-8", "replace").strip()
            if text and text not in parts:
                parts.append(text)

    # A message that parsed cleanly is authoritative: if the requested fields hold no
    # text, the answer is "no text". Dumping the raw buffer here used to surface the
    # neighbouring binary fields as gibberish for rows whose field 1 is an empty
    # string -- a date would come back as "- (08@HPX=`hpxT" instead of empty.
    # The malformed case still degrades to printable characters, above.
    return ", ".join(parts)


# ------------------------------------------------------------------------------ HTML


def clean_text(raw: str | bytes | None) -> str:
    """Turn stored HTML into plain text.

    Unescapes twice because some citation descriptions were escaped on the way in and
    again on export, leaving ``&amp;lt;br&amp;gt;`` where a line break belongs.
    """
    if not raw:
        return ""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw

    for _ in range(2):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_RE.sub("\n\n", text).strip()


# ------------------------------------------------------------------------------ dates


def split_ftb_date(value: int | None) -> dict[str, int | None]:
    """Split a YYYYMMDD integer into parts, mapping FTB's unknown markers to None.

    An open bound is unknown, not a date: "AFT 1904" has no upper year at all, and
    saying 9999 would be inventing one.
    """
    if value is None or abs(value) >= _OPEN_MAGNITUDE:
        return {"year": None, "month": None, "day": None}
    negative = value < 0
    value = abs(value)
    year, month, day = value // 10000, (value // 100) % 100, value % 100
    return {
        "year": -year if negative and year else (year or None),
        "month": month if 1 <= month <= 12 else None,
        "day": day if 1 <= day <= 31 else None,
    }


def year_of(value: int | None) -> int | None:
    """Extract just the year from a YYYYMMDD integer, or None if unknown."""
    return split_ftb_date(value)["year"]


def norm_date(
    raw: str | bytes | None,
    sorted_date: int | None = None,
    lower_bound: int | None = None,
    upper_bound: int | None = None,
) -> dict[str, object] | None:
    """Build a date object combining the display string with the parsed bounds.

    The display string comes from protobuf field 1 of the ``date`` column; the
    structured values come from the integer columns FTB maintains alongside it, which
    already encode range semantics ("BET 2012 AND 2020" -> lower 2012, upper 2020).
    Returns None when the row carries no date at all.
    """
    display = pb_text(raw, fields=(1,)).strip()
    if display in {"0000-00-00", "0"}:
        display = ""

    parts = split_ftb_date(sorted_date)
    lower, upper = split_ftb_date(lower_bound), split_ftb_date(upper_bound)
    is_range = lower["year"] is not None and lower["year"] != upper["year"]
    sort_key = sorted_date if sorted_date not in (None, UNKNOWN_DATE) else None

    if not display and parts["year"] is None and lower["year"] is None and upper["year"] is None:
        return None

    return {
        "display": display,
        "year": parts["year"],
        "month": parts["month"],
        "day": parts["day"],
        "year_from": lower["year"],
        "year_to": upper["year"],
        "is_range": is_range,
        "sort_key": sort_key,
    }
