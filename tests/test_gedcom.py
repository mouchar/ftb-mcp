"""Tests for the GEDCOM importer.

Two layers: the pre-pass and date encoder are pinned with synthetic input, and the
imported database is checked against tests/data/sample.ged, which reproduces the two
defects MyHeritage's own exports contain.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ftb_mcp import queries, server
from ftb_mcp.db import FtbDatabase
from ftb_mcp.decode import OPEN_LOWER_BOUND, OPEN_UPPER_BOUND, UNKNOWN_DATE, norm_date
from ftb_mcp.gedcom_import import (
    GedcomImportError,
    encode_date,
    open_gedcom,
    repair_bare_newlines,
)
from ftb_mcp.graph import TreeIndex
from ftb_mcp.schema import register_language
from tests.conftest import GEDCOM_SAMPLE

# ------------------------------------------------------------------------- pre-pass


def test_well_formed_file_is_left_alone():
    data = b"0 HEAD\r\n1 CHAR UTF-8\r\n0 @I1@ INDI\r\n1 NAME A /B/\r\n0 TRLR\r\n"
    repaired, count = repair_bare_newlines(data)
    assert count == 0
    assert repaired.rstrip(b"\r\n") == data.rstrip(b"\r\n")


def test_bare_newline_becomes_a_cont_line():
    data = b"0 @I1@ INDI\r\n1 NOTE first\nsecond\r\n0 TRLR\r\n"
    repaired, count = repair_bare_newlines(data)
    assert count == 1
    assert b"2 CONT second" in repaired


def test_consecutive_bare_newlines_stay_at_one_level():
    """A run of continuations must not nest deeper with each line."""
    data = b"0 @I1@ INDI\r\n1 NOTE a\nb\nc\r\n0 TRLR\r\n"
    repaired, count = repair_bare_newlines(data)
    assert count == 2
    assert b"2 CONT b" in repaired
    assert b"2 CONT c" in repaired
    assert b"3 CONT" not in repaired


def test_continuation_after_a_cont_keeps_its_level():
    data = b"0 @I1@ INDI\r\n1 NOTE a\r\n2 CONT b\nc\r\n0 TRLR\r\n"
    repaired, count = repair_bare_newlines(data)
    assert count == 1
    assert b"2 CONT c" in repaired


def test_byte_order_mark_survives_the_pre_pass():
    data = b"\xef\xbb\xbf0 HEAD\r\n1 NOTE a\nb\r\n0 TRLR\r\n"
    repaired, count = repair_bare_newlines(data)
    assert count == 1
    assert repaired.startswith(b"\xef\xbb\xbf0 HEAD")


def test_fixture_reproduces_the_defect_the_pre_pass_exists_for():
    _, count = repair_bare_newlines(GEDCOM_SAMPLE.read_bytes())
    assert count == 1


# ---------------------------------------------------------------------------- dates


def test_exact_date_has_no_range():
    display, sorted_date, lower, upper = encode_date("7 OCT 1735")
    assert display == "7 OCT 1735"
    assert sorted_date == lower == upper == 17351007


def test_year_only_date():
    assert encode_date("1891") == ("1891", 18910000, 18910000, 18910000)


def test_month_and_year_date():
    assert encode_date("MAR 1848") == ("MAR 1848", 18480300, 18480300, 18480300)


def test_about_date_keeps_its_qualifier_but_not_a_range():
    display, sorted_date, lower, upper = encode_date("ABT 1762")
    assert "1762" in display
    assert sorted_date == lower == upper == 17620000


def test_before_date_has_an_open_lower_bound():
    """FTB sorts a BEF date just before its bound, with no lower bound at all."""
    display, sorted_date, lower, upper = encode_date("BEF 1856")
    assert "1856" in display
    assert lower == OPEN_LOWER_BOUND
    assert upper == 18560000
    assert sorted_date == 18559999


def test_after_date_has_an_open_upper_bound():
    """FTB's own marker for "no upper bound", so both backends read the same."""
    _, sorted_date, lower, upper = encode_date("AFT 1904")
    assert lower == 19040000
    assert upper == OPEN_UPPER_BOUND
    assert sorted_date == 19040001
    assert norm_date("AFT 1904", sorted_date, lower, upper)["year_to"] is None


def test_between_date_spans_both_bounds():
    _, sorted_date, lower, upper = encode_date("BET 1943 AND 1944")
    assert (lower, upper) == (19430000, 19440000)
    assert sorted_date == 19430001


def test_period_date_spans_both_bounds():
    _, _, lower, upper = encode_date("FROM 2012 TO 2020")
    assert (lower, upper) == (20120000, 20200000)


def test_open_ended_period():
    _, _, lower, upper = encode_date("FROM 1900")
    assert lower == 19000000
    assert upper == OPEN_UPPER_BOUND


def test_unparseable_date_keeps_its_text_and_reports_no_bounds():
    display, sorted_date, lower, upper = encode_date("sometime in the war")
    assert display == "sometime in the war"
    assert sorted_date == lower == upper == UNKNOWN_DATE


def test_missing_date_is_unknown():
    assert encode_date(None) == ("", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE)
    assert encode_date("") == ("", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE)


def test_bounds_reach_norm_date_as_year_from_and_year_to():
    """The encoder's output has to make sense to the shared date normaliser."""
    display, sorted_date, lower, upper = encode_date("BET 1943 AND 1944")
    date = norm_date(display, sorted_date, lower, upper)
    assert date["year_from"] == 1943
    assert date["year_to"] == 1944
    assert date["is_range"] is True

    display, sorted_date, lower, upper = encode_date("7 OCT 1735")
    date = norm_date(display, sorted_date, lower, upper)
    assert (date["year"], date["month"], date["day"]) == (1735, 10, 7)
    assert date["is_range"] is False


# -------------------------------------------------------------------------- language


def test_known_language_reuses_the_ftb_number():
    assert register_language("Czech") == 20
    assert register_language("English") == 0


def test_unknown_language_is_registered_rather_than_guessed():
    from ftb_mcp.schema import LANGUAGE_CODES, LANGUAGE_NAMES, SYNTHETIC_LANGUAGE_BASE

    number = register_language("Polish")
    assert number >= SYNTHETIC_LANGUAGE_BASE
    assert LANGUAGE_CODES[number] == "pl"
    assert LANGUAGE_NAMES[number] == "Polish"
    # Stays under 256: data_language is a TINYINT and project_languages is a byte string.
    assert number < 256
    assert register_language("Polish") == number


# ------------------------------------------------------------------------- importer


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(GedcomImportError, match="No such GEDCOM file"):
        open_gedcom(tmp_path / "absent.ged")


def test_a_file_that_is_not_gedcom_is_rejected_cleanly(tmp_path):
    """Nothing the pre-pass can fix, so the underlying error is reported as our own."""
    path = tmp_path / "notes.txt"
    path.write_bytes(b"this is not a GEDCOM file\n")
    with pytest.raises(GedcomImportError, match="notes.txt"):
        open_gedcom(path)


def test_a_level_less_line_is_recovered_rather_than_aborting(tmp_path):
    """The whole point of the pre-pass: one bad line must not lose the whole file."""
    path = tmp_path / "recovered.ged"
    path.write_bytes(
        b"0 HEAD\r\n1 CHAR UTF-8\r\n1 LANG English\r\n"
        b"0 @I1@ INDI\r\n1 NAME Ada /Lovelace/\r\n1 NOTE first\nsecond line\r\n0 TRLR\r\n"
    )
    with open_gedcom(path) as database:
        assert database.count("individual_main_data") == 1
        note = database.query_one("SELECT note_text FROM note_lang_data")
        assert note["note_text"] == "first\nsecond line"


def test_illegal_level_nesting_is_reported_not_raised_raw(tmp_path):
    """ged4py signals bad nesting with IntegrityError, which is not a ParserError."""
    path = tmp_path / "nested.ged"
    path.write_bytes(b"0 HEAD\r\n1 CHAR UTF-8\r\n0 @I1@ INDI\r\n3 NAME too deep\r\n0 TRLR\r\n")
    with pytest.raises(GedcomImportError):
        open_gedcom(path)


def test_a_minimal_well_formed_file_imports(tmp_path):
    path = tmp_path / "tiny.ged"
    path.write_bytes(
        b"0 HEAD\r\n1 CHAR UTF-8\r\n1 LANG English\r\n"
        b"0 @I1@ INDI\r\n1 NAME Ada /Lovelace/\r\n1 SEX F\r\n"
        b"1 BIRT\r\n2 DATE 10 DEC 1815\r\n2 PLAC London\r\n"
        b"1 DEAT\r\n2 DATE 27 NOV 1852\r\n"
        b"1 FAMS @F1@\r\n"
        b"0 @I2@ INDI\r\n1 NAME William /King/\r\n1 SEX M\r\n1 FAMS @F1@\r\n"
        b"0 @F1@ FAM\r\n1 HUSB @I2@\r\n1 WIFE @I1@\r\n1 MARR\r\n2 DATE 8 JUL 1835\r\n"
        b"0 TRLR\r\n"
    )
    with open_gedcom(path) as database:
        assert database.count("individual_main_data") == 2
        assert database.count("family_main_data") == 1
        assert database.project_languages() == [0]

        index = TreeIndex(database, lang=0)
        ada = index.search(last_name="Lovelace")[0]
        assert ada.first_name == "Ada"
        assert ada.birth_year == 1815
        assert ada.living_status == 2
        spouses = index.spouses(ada.person_id)
        assert len(spouses) == 1
        assert index.people[spouses[0][0]].full_name == "William King"


def test_record_counts_match_the_fixture(gedcom_db: FtbDatabase):
    assert gedcom_db.count("individual_main_data") == 14
    assert gedcom_db.count("family_main_data") == 4
    assert gedcom_db.count("source_main_data") == 2


def test_header_is_imported(gedcom_db: FtbDatabase):
    info = queries.tree_info(gedcom_db)
    assert info["source_application"] == "MyHeritage Family Tree Builder"
    assert info["application_version"] == "8.0.0.8640"
    assert info["gedcom_version"] == "5.5.1"
    assert info["character_set"] == "UTF-8"
    assert info["primary_language"] == "Czech"
    # HEAD.FILE is an export description, so the filename names the tree instead.
    assert info["tree_name"] == "sample"


def test_project_language_round_trips(gedcom_db: FtbDatabase):
    assert gedcom_db.project_languages() == [20]
    assert queries.resolve_language(gedcom_db, None) == 20


def test_concatenated_multibyte_character_survives(gedcom_db: FtbDatabase):
    """MyHeritage split "Matějů" across a CONC boundary mid-character."""
    rows = gedcom_db.query(
        "SELECT description FROM citation_lang_data WHERE description LIKE '%Jarmila Mat%'"
    )
    assert rows
    assert any("Jarmila Matějů" in row["description"] for row in rows)


def test_no_text_was_mangled_into_replacement_characters(gedcom_db: FtbDatabase):
    for table, column in (
        ("citation_lang_data", "description"),
        ("note_lang_data", "note_text"),
        ("individual_lang_data", "first_name"),
        ("individual_lang_data", "last_name"),
        ("places_lang_data", "place"),
        ("source_lang_data", "text"),
    ):
        broken = gedcom_db.query(f"SELECT {column} AS value FROM {table} WHERE {column} LIKE '%�%'")
        assert not broken, f"{table}.{column} lost characters: {broken[0]['value'][:60]!r}"


def test_bare_newline_continuation_keeps_its_line_break(gedcom_db: FtbDatabase):
    """The repaired line becomes a real line break with its first character intact."""
    row = gedcom_db.query_one(
        "SELECT note_text FROM note_lang_data WHERE note_text LIKE 'Poznamka%'"
    )
    assert row is not None, "expected a note whose text was split by a raw newline"
    # A parser that mishandles the continuation drops its leading character.
    assert row["note_text"] == "Poznamka radek jeden\nPoznamka radek dva"


# ------------------------------------------------------------------- spot checks


@pytest.fixture(scope="module")
def simon(gedcom_index: TreeIndex):
    """@I1@ Šimon Herda, the first record in the file."""
    matches = gedcom_index.search(first_name="Šimon", last_name="Herda")
    assert matches, "expected Šimon Herda in the sample"
    return matches[0]


def test_names_come_from_givn_and_surn(simon):
    assert simon.first_name == "Šimon"
    assert simon.last_name == "Herda"
    assert simon.prefix == "starý"
    assert simon.gender == "M"


def test_typed_name_records_fill_their_own_columns(gedcom_index: TreeIndex):
    anna = gedcom_index.search(first_name="Anna", last_name="Kafková")[0]
    assert anna.married_surname == "Herdová"  # from NAME._MARNM
    assert anna.aka == "Anička Kafková"  # from a second NAME with TYPE AKA


def test_nickname_is_imported(gedcom_index: TreeIndex):
    jakub = gedcom_index.search(first_name="Zbyněk")[0]
    assert jakub.nickname == "Kuba"


def test_death_implies_deceased(simon):
    assert simon.living_status == 2
    assert simon.birth_year == 1735
    assert simon.death_year == 1791


def test_facts_carry_place_detail_and_age(gedcom_db: FtbDatabase, simon):
    facts = queries.person_facts(gedcom_db, [simon.person_id], 20)[simon.person_id]
    by_tag = {fact["gedcom_tag"]: fact for fact in facts}

    assert by_tag["BIRT"]["date"]["display"] == "7 OCT 1735"
    assert by_tag["BIRT"]["place"] == "Branná"
    assert by_tag["DEAT"]["age"] == "55"
    assert by_tag["DEAT"]["place"] == "Branná 13"
    # An event's own value is its detail; "Y" on DEAT is an assertion, not a detail.
    assert by_tag["DEAT"]["detail"] is None
    assert by_tag["DEAT"]["cause_of_death"] == "stářím"
    assert by_tag["OCCU"]["detail"] == "sedlák"
    assert by_tag["RELI"]["detail"] == "římskokatolické"
    assert by_tag["BIRT"]["type"] == "Birth"
    # A RESI whose detail is an address is still a Residence, tagged detail_kind addr.
    assert by_tag["RESI"]["type"] == "Residence"
    assert by_tag["RESI"]["detail"] == "Branná 13, Czechia"
    assert by_tag["RESI"]["detail_kind"] == "addr"
    # An EVEN takes its name from TYPE.
    assert by_tag["EVEN"]["type"] == "Settlement"


def test_event_notes_are_attached_to_the_fact_not_the_person(gedcom_db: FtbDatabase, simon):
    from ftb_mcp.schema import ITEM_TYPE_INDIVIDUAL, ITEM_TYPE_INDIVIDUAL_FACT

    assert queries.notes_for(gedcom_db, [simon.person_id], ITEM_TYPE_INDIVIDUAL, 20) == {
        simon.person_id: []
    }
    fact_ids = [
        row["individual_fact_id"]
        for row in gedcom_db.query(
            "SELECT individual_fact_id FROM individual_fact_main_data WHERE individual_id = ?",
            [simon.person_id],
        )
    ]
    notes = queries.notes_for(gedcom_db, fact_ids, ITEM_TYPE_INDIVIDUAL_FACT, 20)
    assert "8017/200" in [note["text"] for group in notes.values() for note in group]


def test_citations_split_page_from_url(gedcom_db: FtbDatabase, simon):
    from ftb_mcp.schema import ITEM_TYPE_INDIVIDUAL

    citations = queries.citations_for(gedcom_db, [simon.person_id], ITEM_TYPE_INDIVIDUAL, 20)[
        simon.person_id
    ]
    assert len(citations) == 2
    assert all(citation["confidence"] == 3 for citation in citations)
    assert any(citation["page"] == "8017/200" for citation in citations)
    assert any((citation["url"] or "").startswith("https://") for citation in citations)
    assert all(citation["source_title"] for citation in citations)
    # DATA.TEXT becomes the citation's transcribed text.
    assert any(c["text"] == "Matrika Branna, kniha 8017" for c in citations)


def test_marriage_makes_a_family_married(gedcom_db: FtbDatabase, simon):
    families = queries.families_of(gedcom_db, simon.person_id)
    assert len(families["as_spouse"]) == 1
    assert len(families["as_child"]) == 1

    family_id = families["as_spouse"][0]
    record = queries.family_records(gedcom_db, [family_id])[family_id]
    assert record["status"] == "married"

    facts = queries.family_facts(gedcom_db, [family_id], 20)[family_id]
    assert facts[0]["gedcom_tag"] == "MARR"
    assert facts[0]["date"]["display"] == "13 FEB 1757"
    assert facts[0]["place"] == "Branná"


def test_divorce_outranks_marriage_in_family_status(gedcom_db: FtbDatabase):
    """FTB reports a divorced family as divorced even though it also has a MARR fact."""
    divorced = gedcom_db.query(
        "SELECT family_id FROM family_main_data WHERE status = 5 AND delete_flag = 0"
    )
    assert divorced
    for row in divorced:
        tokens = {
            fact["gedcom_tag"]
            for fact in queries.family_facts(gedcom_db, [row["family_id"]], 20)[row["family_id"]]
        }
        assert "DIV" in tokens


def test_children_keep_their_recorded_order(gedcom_db: FtbDatabase, gedcom_index: TreeIndex):
    """Order comes from the FAM record's CHIL sequence."""
    divorced = gedcom_db.query_one("SELECT family_id FROM family_main_data WHERE status = 5")
    family_id = divorced["family_id"]
    members = queries.family_members(gedcom_db, [family_id])[family_id]
    assert [member["role"] for member in members] == [
        "husband",
        "wife",
        "natural child",
        "natural child",
    ]
    names = [gedcom_index.people[m["person_id"]].first_name for m in members[2:]]
    assert names == ["Josef", "Eva"]

    orders = [
        row["child_order_in_family"]
        for row in gedcom_db.query(
            "SELECT child_order_in_family FROM family_individual_connection "
            "WHERE family_id = ? AND individual_role_type = 5 ORDER BY child_order_in_family",
            [family_id],
        )
    ]
    assert orders == [0, 1]


def test_membership_declared_only_on_the_individual_is_recovered(
    gedcom_db: FtbDatabase, gedcom_index: TreeIndex
):
    """The fostered and adopted children's family never lists them as CHIL."""
    simon = gedcom_index.search(first_name="Šimon")[0]
    family_id = queries.families_of(gedcom_db, simon.person_id)["as_spouse"][0]
    children = gedcom_index.relatives(simon.person_id, ["children"])["children"]
    by_name = {entry["first_name"]: entry for entry in children}

    assert set(by_name) == {"Zbyněk", "Anežka", "Tomáš"}
    assert by_name["Zbyněk"]["relationship"] == "natural child"
    assert by_name["Anežka"]["relationship"] == "foster child"
    assert by_name["Tomáš"]["relationship"] == "adopted child"
    assert all(entry["family_id"] == family_id for entry in children)

    # A family that never listed them never ordered them either.
    orders = {
        row["individual_role_type"]: row["child_order_in_family"]
        for row in gedcom_db.query(
            "SELECT individual_role_type, child_order_in_family "
            "FROM family_individual_connection WHERE family_id = ?",
            [family_id],
        )
    }
    assert orders[6] == -1
    assert orders[7] == -1


def test_pedigree_gives_non_natural_children_their_role(gedcom_db: FtbDatabase):
    """The file records one adopted and one foster child via FAMC.PEDI."""
    roles = {
        row["individual_role_type"]: row["n"]
        for row in gedcom_db.query(
            "SELECT individual_role_type, COUNT(*) n FROM family_individual_connection "
            "GROUP BY individual_role_type"
        )
    }
    assert roles.get(6) == 1  # foster
    assert roles.get(7) == 1  # adopted


def test_parents_and_pedigree_traverse(gedcom_index: TreeIndex, simon):
    parents = [gedcom_index.people[pid].full_name for pid in gedcom_index.parents(simon.person_id)]
    assert set(parents) == {"Vít Herda", "Magdalena Herdová"}

    ancestors = gedcom_index.ancestors(simon.person_id, 2)
    assert ancestors["root"]["father"]["name"] == "Vít Herda"
    assert ancestors["root"]["mother"]["name"] == "Magdalena Herdová"
    assert ancestors["ancestors_found"] == 2


def test_diacritic_insensitive_search(gedcom_index: TreeIndex):
    with_diacritics = gedcom_index.search(name="Kafková")
    without = gedcom_index.search(name="Kafkova")
    assert without
    assert {p.person_id for p in with_diacritics} == {p.person_id for p in without}


def test_relationship_path_is_labelled(gedcom_index: TreeIndex, simon):
    parents = gedcom_index.parents(simon.person_id)
    path = gedcom_index.relationship_path(simon.person_id, parents[0])
    assert path["found"] is True
    assert path["relationship"] == "parent"


def test_places_are_deduplicated(gedcom_db: FtbDatabase):
    total = gedcom_db.scalar("SELECT COUNT(*) FROM places_main_data")
    distinct = gedcom_db.scalar("SELECT COUNT(DISTINCT place) FROM places_lang_data")
    assert total == distinct

    branna = queries.search_places(gedcom_db, 20, "Branná", 5)
    assert branna[0]["place"] == "Branná"
    assert branna[0]["event_count"] > 1


def test_statistics_agree_with_the_fixture(gedcom_db: FtbDatabase):
    stats = queries.statistics(gedcom_db, 20, ["demographics", "completeness", "facts"])
    genders = stats["demographics"]["by_gender"]
    assert genders == {"female": 6, "male": 8}
    assert sum(genders.values()) == 14
    # Only those with a DEAT, BURI or CREM record count as deceased.
    assert stats["demographics"]["by_living_status"] == {"deceased": 7, "living": 7}
    assert stats["fact_frequency"]["Birth"] == 14
    assert stats["fact_frequency"]["Death"] == 7
    assert stats["completeness"]["individuals"] == 14


def test_family_status_is_derived_from_the_facts(gedcom_db: FtbDatabase):
    statuses = queries.statistics(gedcom_db, 20, ["demographics"])["demographics"][
        "families_by_status"
    ]
    assert statuses == {"married": 2, "divorced": 1, "unspecified": 1}


def test_media_is_imported_from_the_fixture(gedcom_db: FtbDatabase):
    items = queries.media_for(gedcom_db, None, 20, 10)
    assert len(items) == 1
    assert items[0]["title"] == "Portrét Šimona Herdy"
    assert items[0]["description"] == "olej na plátně"
    assert items[0]["date"]["year"] == 1780
    # FILE is never imported, so no path can reach a caller.
    assert "simon.jpg" not in json.dumps(items)


def test_media_metadata_is_imported_without_file_paths(tmp_path):
    """Both pointer and inline OBJE forms, and never the FILE path."""
    path = tmp_path / "media.ged"
    path.write_bytes(
        b"0 HEAD\r\n1 CHAR UTF-8\r\n1 LANG English\r\n"
        b"0 @I1@ INDI\r\n1 NAME Ada /Lovelace/\r\n"
        b"1 OBJE @M1@\r\n"
        b"1 OBJE\r\n2 FILE /secret/scan-two.jpg\r\n2 TITL Parish register\r\n"
        b"2 NOTE Entry for 1815\r\n"
        b"0 @M1@ OBJE\r\n1 FILE /secret/scan-one.jpg\r\n1 TITL Portrait\r\n"
        b"1 NOTE Oil on canvas\r\n1 DATE 1840\r\n"
        b"0 TRLR\r\n"
    )
    with open_gedcom(path) as database:
        index = TreeIndex(database, lang=0)
        person_id = index.search(last_name="Lovelace")[0].person_id
        items = queries.media_for(database, [person_id], 0, 10)

        titles = {item["title"] for item in items}
        assert titles == {"Portrait", "Parish register"}
        portrait = next(item for item in items if item["title"] == "Portrait")
        assert portrait["description"] == "Oil on canvas"
        assert portrait["date"]["year"] == 1840

        blob = json.dumps(items)
        assert "scan-one.jpg" not in blob
        assert "scan-two.jpg" not in blob
        assert "/secret/" not in blob


# ------------------------------------------------------------------- tools end to end


@pytest.fixture(scope="module")
def gedcom_server():
    """Point the shared server state at the GEDCOM file for this module only."""
    if not GEDCOM_SAMPLE.exists():
        pytest.skip("sample .ged not present")
    server.state.open(str(GEDCOM_SAMPLE), "cs", gedcom=True)
    yield server.state
    if server.state.db:
        server.state.db.close()
        server.state.db = None


async def call(name: str, **arguments):
    result = await server.mcp.call_tool(name, arguments)
    assert result.is_error is False, result.content
    return result.structured_content


@pytest.mark.anyio
async def test_opening_a_second_tree_replaces_index_and_closes_the_old_database(gedcom_server):
    """A reopen must not keep serving the previous tree, nor leak its connection."""
    import sqlite3

    first_index = gedcom_server.index(gedcom_server.default_lang)
    first_db = gedcom_server.db

    gedcom_server.open(str(GEDCOM_SAMPLE), "cs", gedcom=True)

    assert gedcom_server.index(gedcom_server.default_lang) is not first_index
    assert gedcom_server.db is not first_db
    with pytest.raises(sqlite3.ProgrammingError):
        first_db.scalar("SELECT 1")


@pytest.mark.anyio
async def test_every_tool_works_against_a_gedcom_file(gedcom_server):
    """Smoke-call all 17 tools and confirm each payload survives JSON round-tripping."""
    match = await call("search_persons", last_name="Herda", limit=1)
    person_id = match["results"][0]["person_id"]

    relatives = await call("get_relatives", person_id=person_id)
    family_id = None
    for group in ("parents", "spouses", "children"):
        for entry in relatives.get(group, []):
            family_id = entry.get("family_id", family_id)
    assert family_id is not None

    calls = [
        ("get_tree_info", {}),
        ("search_persons", {"limit": 3}),
        ("list_surnames", {"limit": 5}),
        ("search_places", {"limit": 5}),
        ("search_notes", {"limit": 5}),
        ("get_person", {"person_id": person_id}),
        ("get_person_facts", {"person_id": person_id}),
        ("get_person_timeline", {"person_id": person_id}),
        ("get_relatives", {"person_id": person_id}),
        ("get_ancestors", {"person_id": person_id, "generations": 2}),
        ("get_descendants", {"person_id": person_id, "generations": 2}),
        ("find_relationship_path", {"person_id_a": person_id, "person_id_b": person_id}),
        ("get_family", {"family_id": family_id}),
        ("get_sources", {"limit": 3}),
        ("get_citations", {"person_id": person_id}),
        ("get_media_metadata", {"limit": 3}),
        ("get_statistics", {}),
    ]
    assert len(calls) == len(await server.mcp.list_tools())

    for name, arguments in calls:
        json.dumps(await call(name, **arguments))


@pytest.mark.anyio
async def test_get_person_returns_families_and_event_notes(gedcom_server):
    match = await call("search_persons", first_name="Šimon", last_name="Herda", limit=1)
    profile = await call("get_person", person_id=match["results"][0]["person_id"])

    assert profile["families_as_spouse"][0]["status"] == "married"
    assert profile["families_as_child"]
    assert profile["immediate_relatives"]["parents"]
    assert profile["event_notes"]
    assert profile["citations"]


@pytest.mark.anyio
async def test_unknown_person_still_raises_clearly(gedcom_server):
    with pytest.raises(ToolError, match="999999"):
        await call("get_person", person_id=999999)
