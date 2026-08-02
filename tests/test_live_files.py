"""Invariant checks against the author's live trees in the repository root.

These files are working data: people are added in Family Tree Builder and the GEDCOM is
re-exported, so nothing here may assert a count, a name or a date. What it asserts is
what must hold for *any* real file — that it opens, that every tool returns something
JSON-serialisable, that no text was mangled while decoding, and that the graph is
internally consistent. That is what catches a regression the small fixture cannot: real
files are larger, messier, and contain shapes nobody thought to invent.

Skipped when the files are absent, so a clone without them still has a green suite.
"""

from __future__ import annotations

import json

import pytest

from ftb_mcp import queries
from ftb_mcp.db import FtbDatabase
from ftb_mcp.gedcom_import import open_gedcom
from ftb_mcp.graph import TreeIndex
from tests.conftest import LIVE_FTB, LIVE_GEDCOM

# Text columns that must never come back with U+FFFD in them: a replacement character
# means bytes were decoded with the wrong codec somewhere.
TEXT_COLUMNS = (
    ("individual_lang_data", "first_name"),
    ("individual_lang_data", "last_name"),
    ("places_lang_data", "place"),
    ("note_lang_data", "note_text"),
    ("citation_lang_data", "description"),
    ("source_lang_data", "title"),
)

MEDIA_KEYS = {"media_item_id", "title", "description", "date", "place", "person_id"}


@pytest.fixture(scope="module")
def live_ftb():
    if not LIVE_FTB.exists():
        pytest.skip(f"{LIVE_FTB.name} not present")
    with FtbDatabase(LIVE_FTB) as database:
        yield database


@pytest.fixture(scope="module")
def live_ftb_index(live_ftb: FtbDatabase) -> TreeIndex:
    return TreeIndex(live_ftb, lang=queries.resolve_language(live_ftb, None))


@pytest.fixture(scope="module")
def live_gedcom():
    if not LIVE_GEDCOM.exists():
        pytest.skip(f"{LIVE_GEDCOM.name} not present")
    with open_gedcom(LIVE_GEDCOM) as database:
        yield database


def _assert_tree_is_usable(db: FtbDatabase, index: TreeIndex) -> None:
    assert index.people, "a real tree should contain people"
    info = queries.tree_info(db)
    json.dumps(info)
    assert info["counts"]["individuals"] == len(index.people)


def _assert_no_mangled_text(db: FtbDatabase) -> None:
    for table, column in TEXT_COLUMNS:
        broken = db.query(f"SELECT {column} AS value FROM {table} WHERE {column} LIKE '%�%'")
        assert not broken, f"{table}.{column} decoded to U+FFFD: {broken[0]['value'][:60]!r}"


def _assert_graph_is_consistent(index: TreeIndex) -> None:
    for person_id in index.people:
        for parent in index.parents(person_id):
            assert person_id in index.children(parent)
        for spouse, _ in index.spouses(person_id):
            assert person_id in [s for s, _ in index.spouses(spouse)]


def _assert_evidence_is_clean(db: FtbDatabase, lang: int) -> None:
    for note in queries.search_notes(db, lang, "", 200):
        assert "<p>" not in note["text"]
        assert "&amp;" not in note["text"]
    for item in queries.media_for(db, None, lang, 200):
        assert set(item) <= MEDIA_KEYS, "media must expose text only"


class TestLiveFtb:
    def test_opens_and_reports_a_usable_tree(self, live_ftb, live_ftb_index):
        _assert_tree_is_usable(live_ftb, live_ftb_index)

    def test_no_text_was_mangled_while_decoding(self, live_ftb):
        _assert_no_mangled_text(live_ftb)

    def test_relationship_graph_is_consistent(self, live_ftb_index):
        _assert_graph_is_consistent(live_ftb_index)

    def test_notes_and_media_are_clean(self, live_ftb):
        _assert_evidence_is_clean(live_ftb, queries.resolve_language(live_ftb, None))

    def test_residence_details_are_decoded_not_raw_protobuf(self, live_ftb):
        lang = queries.resolve_language(live_ftb, None)
        ids = [
            row["individual_id"]
            for row in live_ftb.query(
                "SELECT individual_id FROM individual_fact_main_data "
                "WHERE token = 'RESI' AND delete_flag = 0 LIMIT 50"
            )
        ]
        if not ids:
            pytest.skip("no residence facts in the live tree")
        grouped = queries.person_facts(live_ftb, ids, lang, tags=["RESI"])
        details = [f["detail"] for facts in grouped.values() for f in facts if f["detail"]]
        assert details
        for detail in details:
            assert detail[0].isprintable() and not detail[0].isspace()

    def test_every_fact_date_is_serialisable(self, live_ftb, live_ftb_index):
        lang = queries.resolve_language(live_ftb, None)
        ids = list(live_ftb_index.people)
        for start in range(0, len(ids), 250):
            batch = queries.person_facts(live_ftb, ids[start : start + 250], lang)
            json.dumps(batch)

    def test_places_are_ranked_descending(self, live_ftb):
        lang = queries.resolve_language(live_ftb, None)
        counts = [p["event_count"] for p in queries.search_places(live_ftb, lang, None, 50)]
        assert counts == sorted(counts, reverse=True)


class TestLiveGedcom:
    def test_imports_and_reports_a_usable_tree(self, live_gedcom):
        index = TreeIndex(live_gedcom, lang=queries.resolve_language(live_gedcom, None))
        _assert_tree_is_usable(live_gedcom, index)

    def test_no_text_was_mangled_while_decoding(self, live_gedcom):
        """Catches a CONC record split mid-character, which real exports contain."""
        _assert_no_mangled_text(live_gedcom)

    def test_relationship_graph_is_consistent(self, live_gedcom):
        _assert_graph_is_consistent(
            TreeIndex(live_gedcom, lang=queries.resolve_language(live_gedcom, None))
        )

    def test_notes_and_media_are_clean(self, live_gedcom):
        _assert_evidence_is_clean(live_gedcom, queries.resolve_language(live_gedcom, None))

    def test_every_family_membership_points_at_a_known_person(self, live_gedcom):
        dangling = live_gedcom.query(
            "SELECT c.individual_id FROM family_individual_connection c "
            "LEFT JOIN individual_main_data i ON i.individual_id = c.individual_id "
            "WHERE i.individual_id IS NULL"
        )
        assert not dangling

    def test_every_citation_resolves_to_a_source(self, live_gedcom):
        dangling = live_gedcom.query(
            "SELECT c.citation_id FROM citation_main_data c "
            "LEFT JOIN source_main_data s ON s.source_id = c.source_id "
            "WHERE c.source_id IS NOT NULL AND s.source_id IS NULL"
        )
        assert not dangling
