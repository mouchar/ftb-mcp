"""Build the static test fixtures in ``tests/data``.

``kafkova.ftb`` and ``kafkova.ged`` are live files: the tree is edited in Family Tree
Builder and re-exported, so any test asserting a row count against them breaks the next
time the author records a birth. The fixtures built here are small, invented and fixed,
so counts can be asserted exactly.

Two files are produced:

* ``sample.ftb`` — a SQLite database on the real FTB schema (``ftb_schema.sql``, taken
  verbatim from an actual file), holding a three-generation family designed to exercise
  every quirk the query layer handles: protobuf date columns, protobuf ``RESI`` headers,
  soft-deleted rows, per-language text with gaps, every role and family status, notes
  with doubly-escaped HTML, a citation whose "page" is really a URL, and a place whose
  name is blank in the preferred language.
* ``sample.ged`` — the same family as GEDCOM, plus the two defects MyHeritage's own
  exports contain (a ``CONC`` record split mid-character and a value continued by a bare
  newline) and one-sided ``FAMC`` links for the adopted and fostered children.

Binary column values are copied from real rows, so the fixture pins behaviour against
bytes FTB actually wrote; only names, places and dates are invented.

Regenerate with ``python -m tests.make_fixtures``. The generated files are checked in,
so the tests never depend on this script having been run.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
SCHEMA = DATA / "ftb_schema.sql"
FTB = DATA / "sample.ftb"
GEDCOM = DATA / "sample.ged"

# ---------------------------------------------------------------- real captured bytes

# individual_fact_main_data.date is a protobuf message whose field 1 is the display
# string; field 4 is a nested message. Captured from real rows.
DATE_1735 = bytes.fromhex(
    "0A0A37204F43542031373335222D0801100018002007280A30C70D3800400048005000"
    "58BF843D60006800700078008001008801019001B69BDE52"
)
DATE_1791 = bytes.fromhex(
    "0A0B3139204D41522031373931222D0801100018002013280330FF0D3800400048005000"
    "58BF843D60006800700078008001008801019001D6CBB355"
)
DATE_BETWEEN = bytes.fromhex(
    "0A11424554203230313220414E442032303230222C0805100018022000280030DC0F3800"
    "400248005000 58E40F6000680070007800800100880101900180A3F85F".replace(" ", "")
)
DATE_BEFORE = bytes.fromhex(
    "0A084245462031383536222D0801100118022000280030C00E3800400048005000"
    "58BF843D60006800700078008001008801019001FC8FC058"
)
DATE_AFTER = bytes.fromhex(
    "0A084146542031393034222D0801100218022000280030F00E3800400048005000"
    "58BF843D600068007000780080010088010190019B99EB5A"
)

# A row that lost its display string but kept the nested date: field 1 is present and
# empty, while field 4 still holds 4 MAY 1772. Every integer column reads as unknown, so
# the blob is the only surviving copy. Captured from kafkova.ftb.
DATE_LOST_DISPLAY = bytes.fromhex(
    "0A00222D0801100018002004280530EC0D3800400048005000"
    "58BF843D60006800700078008001008801019001B0DEBF54"
)

# individual_fact_lang_data.header for RESI facts -- protobuf, not text.
ADDR_SHORT = bytes.fromhex("0A0D4272616E6EC3A120C48D2E3133")  # "Branná č.13"
ADDR_LONG = bytes.fromhex(
    "125E43616E62792C2043616E656D61682C204D61706C65204C616E652C20616E64204E65"
    "77204572612050726563696E6374732043616E627920746F776E2C20436C61636B616D61"
    "732C204F7265676F6E2C20556E6974656420537461746573"
)

# project_parameters.project_languages -- protobuf field 1 = bytes [0, 20].
PROJECT_LANGUAGES = bytes.fromhex("0A020014")

UNKNOWN_DATE = 999999999
OPEN_LOWER = -99999999
# FTB's "no upper bound", written for AFT and FROM dates. Larger than any real date, so
# anything that treats it as one reports the year 9999 and wins every MAX.
OPEN_UPPER = 99999999

CS, EN = 20, 0

# Documented token_on_item.item_type values. Entities outside this set leave their own
# token_on_item_id NULL rather than inventing a type number.
ITEM_INDIVIDUAL, ITEM_FAMILY, ITEM_INDIVIDUAL_FACT, ITEM_FAMILY_FACT = 1, 2, 3, 4

# ------------------------------------------------------------------------ the fixture

# id, gender, is_alive, first, last, married_surname, extra name columns.
# is_alive: 2 deceased, 3 living. Person 13 carries an undocumented 7, which the labels
# must render as "unknown (7)" instead of guessing.
PEOPLE = [
    (1, "M", 2, "Šimon", "Herda", "", {"prefix": "starý"}),
    (2, "F", 2, "Anna", "Kafková", "Herdová", {"aka": "Anička"}),
    (3, "M", 2, "Zbyněk", "Herda", "", {"nickname": "Kuba"}),
    (4, "F", 2, "Marie", "Matějů", "Herdová", {}),
    (5, "M", 2, "Josef", "Herda", "", {}),
    (6, "F", 3, "Eva", "Herdová", "", {}),
    (7, "M", 2, "Vít", "Herda", "", {}),
    (8, "F", 2, "Magdalena", "Herdová", "", {}),
    (9, "M", 2, "Petr", "Herda", "", {}),
    (10, "F", 2, "Anežka", "Herdová", "", {}),
    (11, "M", 3, "Tomáš", "Herda", "", {}),
    (12, "M", 2, "Nikdo", "Sám", "", {}),
    (13, "F", 7, "Záhada", "Neznámá", "", {}),
    (14, "M", 2, "Ondřej", "Herda", "", {}),
    (15, "F", 2, "Barbora", "Kafková", "Herdová", {}),
]

# An English name row as well, so the language fallback has something to fall back to.
ENGLISH_NAMES = {1: ("Simon", "Herda"), 2: ("Anna", "Kafkova")}

# Soft-deleted, so every query must ignore them. Facts and a citation hang off this
# person below: FTB leaves those rows with delete_flag = 0 of their own, so an aggregate
# that filters only the fact table still counts them. Without a deleted person who owns
# data, that whole class of bug is invisible to the suite.
DELETED_PEOPLE = [(99, "M", 2, "Smazaný", "Duch", "", {})]
DELETED_PERSON = 99

# id, status, husband, wife, [(child, role)]
# Roles: 2 husband, 3 wife, 5 natural child, 6 foster child, 7 adopted child.
FAMILIES = [
    (1, 3, 7, 8, [(1, 5), (9, 5)]),
    (2, 3, 1, 2, [(3, 5), (10, 6), (11, 7)]),
    (3, 5, 3, 4, [(5, 5), (6, 5)]),
    (4, 0, 9, 15, [(14, 5)]),
    (5, 9, 5, 13, []),
]

PLACES = {
    1: {CS: "Branná", EN: "Branna"},
    2: {CS: "Branná 13", EN: ""},
    3: {CS: "Praha", EN: "Prague"},
    # Blank in the preferred language: places_lang_data must rank non-empty text first.
    4: {CS: "", EN: "Vienna"},
}

# id, individual, token, fact_type, age, date, sorted, lower, upper, place, header,
# cause_of_death, delete_flag
FACTS = [
    (1, 1, "BIRT", "", "", DATE_1735, 17351007, 17351007, 17351007, 1, "", "", 0),
    (2, 1, "DEAT", "", "55", DATE_1791, 17910319, 17910319, 17910319, 2, "", "stářím", 0),
    (3, 1, "OCCU", "", "", "1759", 17590000, 17590000, 17590000, 1, "sedlák", "", 0),
    (
        4,
        1,
        "RELI",
        "",
        "",
        "",
        UNKNOWN_DATE,
        UNKNOWN_DATE,
        UNKNOWN_DATE,
        None,
        "římskokatolické",
        "",
        0,
    ),
    (5, 1, "RESI", "ADDR", "", "", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE, 2, ADDR_SHORT, "", 0),
    (6, 1, "EVEN", "Settlement", "", "", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE, 1, "", "", 0),
    # Soft-deleted fact: must not appear anywhere.
    (7, 1, "CENS", "", "", "", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE, 1, "", "", 1),
    (8, 2, "BIRT", "", "", "1738", 17380000, 17380000, 17380000, 1, "", "", 0),
    (9, 2, "DEAT", "", "", "1799", 17990000, 17990000, 17990000, 1, "", "", 0),
    (10, 3, "BIRT", "", "", "1760", 17600000, 17600000, 17600000, 1, "", "", 0),
    (11, 3, "DEAT", "", "", "1822", 18220000, 18220000, 18220000, 3, "", "", 0),
    (12, 3, "RESI", "ADDR", "", "", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE, 4, ADDR_LONG, "", 0),
    # Open lower bound: a BEF date sorts just below its own upper bound.
    (13, 4, "BIRT", "", "", DATE_BEFORE, 18559999, OPEN_LOWER, 18560000, 3, "", "", 0),
    (14, 4, "DEAT", "", "", "1890", 18900000, 18900000, 18900000, 3, "", "", 0),
    (15, 5, "BIRT", "", "", "1801", 18010000, 18010000, 18010000, 1, "", "", 0),
    (16, 5, "DEAT", "", "", "1860", 18600000, 18600000, 18600000, 1, "", "", 0),
    # A genuine range, and a dateless fact that must sort last.
    (17, 5, "CENS", "", "12", DATE_BETWEEN, 20120001, 20120000, 20200000, 3, "", "", 0),
    (18, 5, "BURI", "", "", "", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE, None, "", "", 0),
    (19, 6, "BIRT", "", "", "1950", 19500000, 19500000, 19500000, 3, "", "", 0),
    # An open upper bound: "AFT 1904" has a lower year and no upper one at all. The
    # sentinel is larger than every real date in the fixture, so anything that reads it
    # as a date drags the tree's latest_event_year out to 9999.
    (35, 6, "RESI", "", "", DATE_AFTER, 19040001, 19040000, OPEN_UPPER, None, "", "", 0),
    (20, 7, "BIRT", "", "", "1695", 16950000, 16950000, 16950000, 1, "", "", 0),
    (21, 7, "DEAT", "", "", "1742", 17420000, 17420000, 17420000, 1, "", "", 0),
    (22, 8, "BIRT", "", "", "1704", 17040000, 17040000, 17040000, 1, "", "", 0),
    (23, 8, "DEAT", "", "", "1772", 17720000, 17720000, 17720000, 1, "", "", 0),
    (24, 9, "BIRT", "", "", "1737", 17370000, 17370000, 17370000, 1, "", "", 0),
    (25, 9, "DEAT", "", "", "1801", 18010000, 18010000, 18010000, 1, "", "", 0),
    (26, 10, "BIRT", "", "", "1765", 17650000, 17650000, 17650000, 1, "", "", 0),
    (27, 11, "BIRT", "", "", "1768", 17680000, 17680000, 17680000, 1, "", "", 0),
    # Death date survives only inside the blob: sorted_date and both bounds are the
    # unknown sentinel, so any reader that trusts the columns alone reports no date.
    # fmt: off
    (36, 11, "DEAT", "", "", DATE_LOST_DISPLAY,
     UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE, 1, "", "", 0),
    # fmt: on
    (28, 12, "BIRT", "", "", "1800", 18000000, 18000000, 18000000, 3, "", "", 0),
    (29, 14, "BIRT", "", "", "1770", 17700000, 17700000, 17700000, 1, "", "", 0),
    (30, 15, "BIRT", "", "", "1740", 17400000, 17400000, 17400000, 1, "", "", 0),
    (31, 13, "BIRT", "", "", "1980", 19800000, 19800000, 19800000, 3, "", "", 0),
    # Facts of the soft-deleted person. The fact rows are live; only their owner is
    # deleted, which is exactly how FTB leaves them.
    #
    # The birth predates and the census postdates every real person, so counting these
    # visibly widens the tree's reported span. Birth and death are also a *plausible*
    # 60 years apart, so they would pass the lifespan plausibility guard and land in the
    # average -- an implausible span would be filtered out and prove nothing.
    (32, 99, "BIRT", "", "", "1600", 16000000, 16000000, 16000000, 3, "", "", 0),
    (33, 99, "DEAT", "", "", "1660", 16600000, 16600000, 16600000, 3, "", "", 0),
    (34, 99, "CENS", "", "", "2099", 20990000, 20990000, 20990000, 3, "", "", 0),
]

# id, family, token, fact_type, spouse_age, date, sorted, lower, upper, place
FAMILY_FACTS = [
    (1, 1, "MARR", "MARR", "", "1726", 17260000, 17260000, 17260000, 1),
    (2, 2, "MARR", "MARR", "22", "13 FEB 1757", 17570213, 17570213, 17570213, 1),
    (3, 3, "MARR", "MARR", "", "1790", 17900000, 17900000, 17900000, 3),
    (4, 3, "DIV", "DIV", "", "1795", 17950000, 17950000, 17950000, 3),
    (
        5,
        5,
        "EVEN",
        "MYHERITAGE:REL_PARTNERS",
        "",
        "",
        UNKNOWN_DATE,
        UNKNOWN_DATE,
        UNKNOWN_DATE,
        None,
    ),
]

# id, special_note_key, text, item_type it hangs off, entity id
NOTES = [
    (1, "", "<p>bydli&scaron;tě: Majdalena, Plzeň</p>", ITEM_INDIVIDUAL, 1),
    (2, "", "Anna Sophia Herdova&amp;lt;br&amp;gt;Narození: 1738", ITEM_INDIVIDUAL, 2),
    (3, "N1", "<p>matriční z&aacute;pis 8017/200</p>", ITEM_INDIVIDUAL_FACT, 1),
    (4, "", "<p>rozvod potvrzen</p>", ITEM_FAMILY_FACT, 4),
]

# id, title, author, publisher, type, media, text
SOURCES = [
    (
        1,
        "Matrika Branná",
        "Farní úřad Branná",
        "SOA Plzeň",
        "Collection",
        "10147",
        "<p>kniha 8017</p>",
    ),
    (2, "Papež Web Site", "Petr Papež", "", "Smart Matching", "209185631-1", "Rodinný strom"),
    (3, "BillionGraves", "", "MyHeritage", "Collection", "40001", ""),
]

# id, source, page, confidence, event_type, description, item_type, entity id
CITATIONS = [
    (1, 1, "8017/200", 3, "BIRT", "Přidáno potvrzením Smart Match", ITEM_INDIVIDUAL, 1),
    # FTB overloads `page` with a record URL for online collections.
    (2, 2, "https://www.myheritage.cz/profile-ABC/simon-herda", 3, "", "", ITEM_INDIVIDUAL, 1),
    (3, 1, "8053/131", -1, "DEAT", "z&aacute;pis o úmrtí", ITEM_INDIVIDUAL, 1),
    (4, 3, "hrob 12", 4, "", "", ITEM_INDIVIDUAL, 3),
    # Cites the soft-deleted person, so "share of people with a source" has to reach
    # past the citation to its subject before counting it.
    (5, 3, "hrob 99", 3, "", "", ITEM_INDIVIDUAL, DELETED_PERSON),
]

# id, title, description, date, sorted_date, place, linked individual
MEDIA = [
    (1, "Portrét Šimona Herdy", "olej na plátně", "1780", 17800000, 1, 1),
    (2, "Matriční zápis", "<p>strana 200</p>", "", UNKNOWN_DATE, None, 1),
    # Neither title nor description: media_for must drop it.
    (3, "", "", "", UNKNOWN_DATE, None, None),
]

HEADER_PARAMETERS = {
    "Source": "MYHERITAGE",
    "ProductName": "MyHeritage Family Tree Builder",
    "Version": "8.0.0.8640",
    "GedcomVersion": "5.5.1",
    "GedcomFormat": "FTBDB",
    "CharacterSet": "UTF-8",
    "Language": "Czech",
    "File": "sample",
}


def _raw(value: bytes | str) -> bytes:
    """Bytes for a column that FTB declares TEXT but fills with protobuf.

    Bound with ``CAST(? AS TEXT)`` so SQLite stores TEXT-typed bytes that are not valid
    UTF-8, exactly as the real file does. Binding the str form is impossible: sqlite3
    refuses to encode the lone surrogates that decoding such bytes produces.
    """
    return value if isinstance(value, bytes) else value.encode()


class _Builder:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tokens: dict[tuple[int, int], int] = {}

    def token(self, item_type: int, entity_id: int) -> int:
        """token_on_item is FTB's polymorphic reference; one row per (type, entity)."""
        key = (item_type, entity_id)
        if key not in self.tokens:
            token_id = len(self.tokens) + 1
            self.tokens[key] = token_id
            self.conn.execute(
                "INSERT INTO token_on_item (token_on_item_id, entity_id, item_type) "
                "VALUES (?, ?, ?)",
                (token_id, entity_id, item_type),
            )
        return self.tokens[key]

    def build(self) -> None:
        self.conn.executescript(SCHEMA.read_text())
        self._parameters()
        self._people()
        self._places()
        self._facts()
        self._families()
        self._notes()
        self._sources()
        self._media()
        self.conn.commit()

    def _parameters(self) -> None:
        rows = [("Header", name, value) for name, value in HEADER_PARAMETERS.items()]
        rows += [("Project", "db_major_version", "1"), ("Project", "db_minor_version", "7")]
        self.conn.executemany(
            "INSERT INTO project_parameters (category, name, value) VALUES (?, ?, ?)", rows
        )
        self.conn.execute(
            "INSERT INTO project_parameters (category, name, value) "
            "VALUES ('Project', 'project_languages', CAST(? AS TEXT))",
            (_raw(PROJECT_LANGUAGES),),
        )

    def _people(self) -> None:
        for row in PEOPLE:
            self._person(*row, delete_flag=0)
        for row in DELETED_PEOPLE:
            self._person(*row, delete_flag=1)

    def _person(
        self,
        person_id: int,
        gender: str,
        is_alive: int,
        first: str,
        last: str,
        married: str,
        extras: dict[str, str],
        delete_flag: int,
    ) -> None:
        self.conn.execute(
            "INSERT INTO individual_main_data "
            "(individual_id, gender, is_alive, guid, delete_flag, token_on_item_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                person_id,
                gender,
                is_alive,
                f"GUID{person_id:04d}",
                delete_flag,
                self.token(ITEM_INDIVIDUAL, person_id),
            ),
        )
        self.conn.execute(
            "INSERT INTO individual_data_set (individual_data_set_id, individual_id, delete_flag) "
            "VALUES (?, ?, ?)",
            (person_id, person_id, delete_flag),
        )
        self.conn.execute(
            "INSERT INTO individual_lang_data "
            "(individual_data_set_id, data_language, first_name, last_name, prefix, suffix, "
            " nickname, religious_name, former_name, married_surname, alias_name, aka) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                person_id,
                CS,
                first,
                last,
                extras.get("prefix", ""),
                extras.get("suffix", ""),
                extras.get("nickname", ""),
                extras.get("religious_name", ""),
                extras.get("former_name", ""),
                married,
                extras.get("alias_name", ""),
                extras.get("aka", ""),
            ),
        )
        if person_id in ENGLISH_NAMES:
            first_en, last_en = ENGLISH_NAMES[person_id]
            self.conn.execute(
                "INSERT INTO individual_lang_data "
                "(individual_data_set_id, data_language, first_name, last_name) "
                "VALUES (?, ?, ?, ?)",
                (person_id, EN, first_en, last_en),
            )

    def _places(self) -> None:
        for place_id, names in PLACES.items():
            self.conn.execute("INSERT INTO places_main_data (place_id) VALUES (?)", (place_id,))
            for language, name in names.items():
                self.conn.execute(
                    "INSERT INTO places_lang_data (place_id, data_language, place) "
                    "VALUES (?, ?, ?)",
                    (place_id, language, name),
                )

    def _facts(self) -> None:
        for row in FACTS:
            (
                fact_id,
                person,
                token,
                fact_type,
                age,
                date,
                sorted_date,
                lower,
                upper,
                place,
                header,
                cause,
                delete_flag,
            ) = row
            self.conn.execute(
                "INSERT INTO individual_fact_main_data "
                "(individual_fact_id, individual_id, token, fact_type, age, sorted_date, "
                " lower_bound_search_date, upper_bound_search_date, date, is_current, "
                " place_id, delete_flag, token_on_item_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS TEXT), 1, ?, ?, ?)",
                (
                    fact_id,
                    person,
                    token,
                    fact_type,
                    age,
                    sorted_date,
                    lower,
                    upper,
                    _raw(date),
                    place,
                    delete_flag,
                    self.token(ITEM_INDIVIDUAL_FACT, fact_id),
                ),
            )
            self.conn.execute(
                "INSERT INTO individual_fact_lang_data "
                "(individual_fact_id, data_language, header, cause_of_death) "
                "VALUES (?, ?, CAST(? AS TEXT), ?)",
                (fact_id, CS, _raw(header), cause),
            )

    def _families(self) -> None:
        for family_id, status, husband, wife, children in FAMILIES:
            self.conn.execute(
                "INSERT INTO family_main_data "
                "(family_id, status, guid, delete_flag, token_on_item_id) VALUES (?, ?, ?, 0, ?)",
                (family_id, status, f"FAM{family_id:04d}", self.token(ITEM_FAMILY, family_id)),
            )
            self._connect(family_id, husband, 2, -1)
            self._connect(family_id, wife, 3, -1)
            for order, (child, role) in enumerate(children):
                self._connect(family_id, child, role, order)

        # A soft-deleted connection. Without the delete_flag filter this shows up as a
        # phantom relative, which is exactly what the real file contains.
        self._connect(4, 12, 5, 1, delete_flag=1)

        for row in FAMILY_FACTS:
            fact_id, family_id, token, fact_type, age, date, sorted_date, lower, upper, place = row
            self.conn.execute(
                "INSERT INTO family_fact_main_data "
                "(family_fact_id, family_id, token, fact_type, spouse_age, sorted_date, "
                " lower_bound_search_date, upper_bound_search_date, date, is_current, "
                " place_id, delete_flag, token_on_item_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS TEXT), 1, ?, 0, ?)",
                (
                    fact_id,
                    family_id,
                    token,
                    fact_type,
                    age,
                    sorted_date,
                    lower,
                    upper,
                    _raw(date),
                    place,
                    self.token(ITEM_FAMILY_FACT, fact_id),
                ),
            )
            self.conn.execute(
                "INSERT INTO family_fact_lang_data (family_fact_id, data_language, header) "
                "VALUES (?, ?, '')",
                (fact_id, CS),
            )

    def _connect(
        self, family_id: int, person_id: int, role: int, order: int, delete_flag: int = 0
    ) -> None:
        self.conn.execute(
            "INSERT INTO family_individual_connection "
            "(delete_flag, family_id, individual_id, individual_role_type, child_order_in_family) "
            "VALUES (?, ?, ?, ?, ?)",
            (delete_flag, family_id, person_id, role, order),
        )

    def _notes(self) -> None:
        for note_id, key, text, item_type, entity_id in NOTES:
            self.conn.execute(
                "INSERT INTO note_main_data (note_id, guid, special_note_key, delete_flag) "
                "VALUES (?, ?, ?, 0)",
                (note_id, f"NOTE{note_id:04d}", key),
            )
            self.conn.execute(
                "INSERT INTO note_lang_data (note_id, data_language, note_text) VALUES (?, ?, ?)",
                (note_id, CS, text),
            )
            self.conn.execute(
                "INSERT INTO note_to_item_connection "
                "(note_id, delete_flag, external_token_on_item_id) VALUES (?, 0, ?)",
                (note_id, self.token(item_type, entity_id)),
            )

    def _sources(self) -> None:
        for source_id, title, author, publisher, kind, media, text in SOURCES:
            self.conn.execute(
                "INSERT INTO source_main_data (source_id, delete_flag) VALUES (?, 0)", (source_id,)
            )
            self.conn.execute(
                "INSERT INTO source_lang_data "
                "(source_id, data_language, title, abbreviation, author, publisher, agency, "
                " text, type, media) VALUES (?, ?, ?, '', ?, ?, '', ?, ?, ?)",
                (source_id, CS, title, author, publisher, text, kind, media),
            )

        for row in CITATIONS:
            citation_id, source_id, page, confidence, event_type, description, item, entity = row
            self.conn.execute(
                "INSERT INTO citation_main_data "
                "(citation_id, source_id, page, confidence, event_type, event_role, date, "
                " sorted_date, lower_bound_search_date, upper_bound_search_date, delete_flag, "
                " external_token_on_item_id) VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, 0, ?)",
                (
                    citation_id,
                    source_id,
                    page,
                    confidence,
                    event_type,
                    UNKNOWN_DATE,
                    UNKNOWN_DATE,
                    UNKNOWN_DATE,
                    self.token(item, entity),
                ),
            )
            self.conn.execute(
                "INSERT INTO citation_lang_data (citation_id, data_language, description) "
                "VALUES (?, ?, ?)",
                (citation_id, CS, description),
            )

    def _media(self) -> None:
        for media_id, title, description, date, sorted_date, place, person in MEDIA:
            self.conn.execute(
                "INSERT INTO media_item_main_data "
                "(media_item_id, place_id, guid, date, sorted_date, lower_bound_search_date, "
                " upper_bound_search_date, delete_flag) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    media_id,
                    place,
                    f"MEDIA{media_id:04d}",
                    date,
                    sorted_date,
                    sorted_date,
                    sorted_date,
                ),
            )
            self.conn.execute(
                "INSERT INTO media_item_lang_data "
                "(media_item_id, data_language, title, description) VALUES (?, ?, ?, ?)",
                (media_id, CS, title, description),
            )
            if person is not None:
                self.conn.execute(
                    "INSERT INTO media_item_to_item_connection "
                    "(media_item_id, guid, delete_flag, external_token_on_item_id) "
                    "VALUES (?, ?, 0, ?)",
                    (media_id, f"MLINK{media_id:04d}", self.token(ITEM_INDIVIDUAL, person)),
                )


def build_ftb(path: Path = FTB) -> Path:
    """Write the .ftb fixture, replacing any previous copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        _Builder(conn).build()
    finally:
        conn.close()
    return path


# ---------------------------------------------------------------------------- GEDCOM

# The same family as GEDCOM. Two markers are replaced with byte-level defects that real
# MyHeritage exports contain and that no conforming parser accepts as written.
SPLIT_MARKER = "<<CONC-SPLIT>>"
BARE_NEWLINE_MARKER = "<<BARE-NEWLINE>>"

# The name whose UTF-8 encoding gets cut in half across the CONC boundary.
SPLIT_TEXT = "Jarmila Matějů"

GEDCOM_SOURCE = f"""\
0 HEAD
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
1 LANG Czech
1 SOUR MYHERITAGE
2 NAME MyHeritage Family Tree Builder
2 VERS 8.0.0.8640
2 CORP MyHeritage.com
1 DEST MYHERITAGE
1 DATE 02 AUG 2026
1 FILE Exported by MyHeritage.com from Sample in Sample Web Site
0 @I1@ INDI
1 NAME Šimon /Herda/
2 GIVN Šimon
2 SURN Herda
2 NPFX starý
1 SEX M
1 BIRT
2 DATE 7 OCT 1735
2 PLAC Branná
2 NOTE 8017/200
1 DEAT Y
2 DATE 19 MAR 1791
2 PLAC Branná 13
2 AGE 55
2 CAUS stářím
1 OCCU sedlák
2 DATE ABT 1759
1 RELI římskokatolické
1 RESI
2 ADDR
3 ADR1 Branná 13
3 CTRY Czechia
1 EVEN
2 TYPE Settlement
2 PLAC Branná
1 FAMS @F2@
1 FAMC @F1@
1 SOUR @S1@
2 PAGE 8017/200
2 QUAY 3
2 DATA
3 TEXT Matrika Branna, kniha 8017
1 SOUR @S2@
2 PAGE https://www.myheritage.cz/profile-ABC/simon-herda
2 QUAY 3
1 OBJE @M1@
1 RIN MH:I1
1 _UID 5B2147A7736B21CC2FA163E23BFA34F0
0 @I2@ INDI
1 NAME Anna /Kafková/
2 GIVN Anna
2 SURN Kafková
2 _MARNM Herdová
1 NAME Anička /Kafková/
2 TYPE AKA
1 SEX F
1 BIRT
2 DATE 1738
1 DEAT
2 DATE BEF 1799
1 FAMS @F2@
0 @I3@ INDI
1 NAME Zbyněk /Herda/
2 GIVN Zbyněk
2 SURN Herda
2 NICK Kuba
1 SEX M
1 BIRT
2 DATE 1760
1 DEAT
2 DATE BET 1820 AND 1822
1 FAMC @F2@
1 FAMS @F3@
1 SOUR @S2@
2 QUAY 4
2 DATA
{SPLIT_MARKER}
0 @I4@ INDI
1 NAME Marie /Matějů/
2 GIVN Marie
2 SURN Matějů
1 SEX F
1 BIRT
2 DATE AFT 1765
1 FAMS @F3@
1 NOTE Poznamka radek jeden
{BARE_NEWLINE_MARKER}
0 @I5@ INDI
1 NAME Josef /Herda/
2 GIVN Josef
2 SURN Herda
1 SEX M
1 BIRT
2 DATE 1801
1 DEAT
2 DATE 1860
1 CENS
2 DATE FROM 2012 TO 2020
2 AGE 12
1 FAMC @F3@
0 @I6@ INDI
1 NAME Eva /Herdová/
2 GIVN Eva
2 SURN Herdová
1 SEX F
1 BIRT
2 DATE 1950
1 FAMC @F3@
0 @I7@ INDI
1 NAME Vít /Herda/
2 GIVN Vít
2 SURN Herda
1 SEX M
1 BIRT
2 DATE 1695
1 DEAT
2 DATE 1742
1 FAMS @F1@
0 @I8@ INDI
1 NAME Magdalena /Herdová/
2 GIVN Magdalena
2 SURN Herdová
1 SEX F
1 BIRT
2 DATE 1704
1 DEAT
2 DATE 1772
1 FAMS @F1@
0 @I9@ INDI
1 NAME Petr /Herda/
2 GIVN Petr
2 SURN Herda
1 SEX M
1 BIRT
2 DATE 1737
1 DEAT
2 DATE 1801
1 FAMC @F1@
1 FAMS @F4@
0 @I10@ INDI
1 NAME Anežka /Herdová/
2 GIVN Anežka
2 SURN Herdová
1 SEX F
1 BIRT
2 DATE 1765
1 FAMC @F2@
2 PEDI Foster
0 @I11@ INDI
1 NAME Tomáš /Herda/
2 GIVN Tomáš
2 SURN Herda
1 SEX M
1 BIRT
2 DATE 1768
1 FAMC @F2@
2 PEDI Adopted
0 @I12@ INDI
1 NAME Nikdo /Sám/
2 GIVN Nikdo
2 SURN Sám
1 SEX M
1 BIRT
2 DATE 1800
2 PLAC Praha
0 @I14@ INDI
1 NAME Ondřej /Herda/
2 GIVN Ondřej
2 SURN Herda
1 SEX M
1 BIRT
2 DATE 1770
1 FAMC @F4@
0 @I15@ INDI
1 NAME Barbora /Kafková/
2 GIVN Barbora
2 SURN Kafková
1 SEX F
1 BIRT
2 DATE 1740
1 FAMS @F4@
0 @F1@ FAM
1 HUSB @I7@
1 WIFE @I8@
1 CHIL @I1@
1 CHIL @I9@
1 MARR
2 DATE 1726
2 PLAC Branná
0 @F2@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
1 MARR
2 DATE 13 FEB 1757
2 PLAC Branná
2 AGE 22
2 NOTE 8041/111
0 @F3@ FAM
1 HUSB @I3@
1 WIFE @I4@
1 CHIL @I5@
1 CHIL @I6@
1 MARR
2 DATE 1790
1 DIV
2 DATE 1795
0 @F4@ FAM
1 HUSB @I9@
1 WIFE @I15@
1 CHIL @I14@
0 @S1@ SOUR
1 TITL Matrika Branna
1 AUTH Farni urad Branna
1 PUBL SOA Plzen
1 TEXT kniha 8017
1 _TYPE Collection
1 _MEDI 10147
0 @S2@ SOUR
1 TITL Papež Web Site
1 AUTH Petr Papež
1 _TYPE Smart Matching
0 @M1@ OBJE
1 FILE /pictures/simon.jpg
1 TITL Portrét Šimona Herdy
1 NOTE olej na plátně
1 DATE 1780
0 TRLR
"""


def _conc_split() -> bytes:
    """A TEXT value whose CONC continuation starts mid-character.

    MyHeritage counts the 255-character line limit in bytes, so a multi-byte character
    can straddle the boundary. Reproduced here by cutting "Matějů" between the two
    bytes of "ě".
    """
    encoded = SPLIT_TEXT.encode()
    cut = encoded.index(b"\xc4\x9b") + 1
    return b"3 TEXT " + encoded[:cut] + b"\r\n4 CONC " + encoded[cut:] + b" a Zdenka"


def build_gedcom(path: Path = GEDCOM) -> Path:
    """Write the .ged fixture, including the two defects real exports contain."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = GEDCOM_SOURCE.splitlines()
    out = bytearray(b"\xef\xbb\xbf")  # byte-order mark, as MyHeritage writes
    for line in lines:
        if line == SPLIT_MARKER:
            out += _conc_split() + b"\r\n"
        elif line == BARE_NEWLINE_MARKER:
            # A value continued by a bare newline: the continuation arrives with no
            # level number at all, which is what repair_bare_newlines recovers.
            out = out[: -len(b"\r\n")] + b"\nPoznamka radek dva\r\n"
        else:
            out += line.encode() + b"\r\n"
    path.write_bytes(bytes(out))
    return path


def main(argv: list[str] | None = None) -> int:
    directory = Path(argv[0]).resolve() if argv else DATA
    for path in (build_ftb(directory / FTB.name), build_gedcom(directory / GEDCOM.name)):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
