"""The committed fixtures must still be what tests/make_fixtures.py produces.

Without this, the generator and the files the tests actually read can drift apart, and
the generator quietly stops documenting the data.

The two files are checked differently on purpose. The GEDCOM fixture is written by pure
Python and is compared byte for byte, which also catches a checkout mangling its CRLF
line endings. The SQLite fixture cannot be compared that way: byte 96 of the header
stamps the version of the SQLite library that last wrote the file, so the same generator
produces different bytes under a different Python build. Its *content* is compared
instead -- schema and every row -- which is what the tests depend on anyway.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.conftest import GEDCOM_SAMPLE, SAMPLE
from tests.make_fixtures import build_ftb, build_gedcom


def _content(path: Path) -> dict[str, list]:
    """Schema and rows of a database, in a form two files can be compared by."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    # Bytes throughout: several columns hold protobuf that is not valid UTF-8, and
    # comparing the decoded form would hide a difference in the original bytes.
    conn.text_factory = bytes
    try:
        schema = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        content: dict[str, list] = {"schema": schema}
        for kind, name, _sql in schema:
            if kind != b"table":
                continue
            table = name.decode()
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            content[table] = sorted(rows, key=repr)
        return content
    finally:
        conn.close()


def test_gedcom_fixture_matches_its_generator(tmp_path):
    rebuilt = build_gedcom(tmp_path / "sample.ged")
    assert rebuilt.read_bytes() == GEDCOM_SAMPLE.read_bytes(), (
        "tests/data/sample.ged is out of date, or a checkout rewrote its line endings; "
        "run `python -m tests.make_fixtures`"
    )


def test_gedcom_fixture_still_uses_crlf(tmp_path):
    """CRLF is what GEDCOM specifies, and the fixture's bare LF is the anomaly."""
    raw = GEDCOM_SAMPLE.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf0 HEAD\r\n"), "byte-order mark and CRLF expected"
    # Exactly one line ends with a bare LF: the defect repair_bare_newlines exists for.
    assert raw.count(b"\n") - raw.count(b"\r\n") == 1


def test_ftb_fixture_matches_its_generator(tmp_path):
    rebuilt = build_ftb(tmp_path / "sample.ftb")
    assert _content(rebuilt) == _content(SAMPLE), (
        "tests/data/sample.ftb is out of date; run `python -m tests.make_fixtures`"
    )


def test_generator_is_deterministic(tmp_path):
    first = build_gedcom(tmp_path / "first.ged").read_bytes()
    second = build_gedcom(tmp_path / "second.ged").read_bytes()
    assert first == second

    assert _content(build_ftb(tmp_path / "first.ftb")) == _content(
        build_ftb(tmp_path / "second.ftb")
    )
