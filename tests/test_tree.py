"""Golden-value tests against the static fixture in tests/data/sample.ftb.

The fixture is built by ``tests/make_fixtures.py`` on the real FTB schema, with
protobuf column values copied from genuine rows, so a regression in the query or decode
layers shows up here as a changed value. Counts are exact because the fixture is fixed;
see ``test_live_files.py`` for the checks that run against the author's live tree.
"""

from __future__ import annotations

from ftb_mcp import queries
from ftb_mcp.graph import TreeIndex, fold

# Sizes of the fixture. Change these only alongside tests/make_fixtures.py.
PEOPLE = 15
FAMILIES = 5
INDIVIDUAL_FACTS = 30  # 31 rows, one of them soft-deleted
FAMILY_FACTS = 5
CONNECTIONS = 18  # 19 rows, one of them soft-deleted

# Fixture ids used by name, so a test failure names a person rather than a number.
SIMON, ANNA, ZBYNEK, MARIE, JOSEF, EVA = 1, 2, 3, 4, 5, 6
VIT, MAGDALENA, PETR, ANEZKA, TOMAS, NIKDO = 7, 8, 9, 10, 11, 12
ZAHADA, ONDREJ, BARBORA = 13, 14, 15
DELETED = 99


class TestTreeInfo:
    def test_identifies_source_application(self, db):
        info = queries.tree_info(db)
        assert info["source_application"] == "MyHeritage Family Tree Builder"
        assert info["gedcom_format"] == "FTBDB"
        assert info["db_version"] == "1.7"

    def test_entity_counts_match_the_fixture(self, db):
        counts = queries.tree_info(db)["counts"]
        assert counts["individuals"] == PEOPLE
        assert counts["families"] == FAMILIES
        assert counts["individual_facts"] == INDIVIDUAL_FACTS
        assert counts["family_facts"] == FAMILY_FACTS
        assert counts["sources"] == 3
        assert counts["citations"] == 4

    def test_declares_english_and_czech(self, db):
        codes = [lang["code"] for lang in queries.tree_info(db)["languages"]]
        assert codes == ["en", "cs"]

    def test_year_span_covers_the_oldest_and_newest_events(self, db):
        info = queries.tree_info(db)
        assert info["earliest_event_year"] == 1695
        assert info["latest_event_year"] == 2020


class TestIndex:
    def test_loads_every_live_individual(self, index: TreeIndex):
        assert len(index.people) == PEOPLE

    def test_soft_deleted_individual_is_absent(self, db, index: TreeIndex):
        assert db.scalar("SELECT COUNT(*) FROM individual_main_data") == PEOPLE + 1
        assert DELETED not in index.people

    def test_connection_roles_match_the_fixture(self, index: TreeIndex):
        spouses = sum(len(v) for v in index.family_spouses.values())
        children = sum(len(v) for v in index.family_children.values())
        assert spouses == 10  # five families, two spouses each
        assert children == 8  # six natural, one foster, one adopted
        assert spouses + children == CONNECTIONS

    def test_soft_deleted_connections_are_excluded(self, db, index: TreeIndex):
        raw = db.scalar("SELECT COUNT(*) FROM family_individual_connection")
        live = sum(len(v) for v in index.family_spouses.values()) + sum(
            len(v) for v in index.family_children.values()
        )
        assert raw == CONNECTIONS + 1
        assert live == raw - 1

    def test_soft_deleted_fact_is_excluded(self, db):
        assert db.scalar("SELECT COUNT(*) FROM individual_fact_main_data") == INDIVIDUAL_FACTS + 1
        tokens = {fact["gedcom_tag"] for fact in queries.person_facts(db, [SIMON], lang=20)[SIMON]}
        assert "CENS" not in tokens, "the soft-deleted census fact must not appear"

    def test_undocumented_living_status_degrades_legibly(self, index: TreeIndex):
        assert index.people[ZAHADA].summary()["living_status"] == "unknown (7)"


class TestSearch:
    def test_matches_ignoring_diacritics(self, index: TreeIndex):
        with_marks = index.search(last_name="Kafková")
        without = index.search(last_name="Kafkova")
        assert with_marks and [p.person_id for p in with_marks] == [p.person_id for p in without]

    def test_matches_ignoring_case(self, index: TreeIndex):
        assert [p.person_id for p in index.search(last_name="HERDA")] == [
            p.person_id for p in index.search(last_name="herda")
        ]

    def test_gender_filter(self, index: TreeIndex):
        assert len(index.search(gender="M")) == 8
        assert len(index.search(gender="F")) == 7

    def test_birth_year_range_filter(self, index: TreeIndex):
        results = index.search(birth_year_from=1700, birth_year_to=1770)
        assert results
        assert all(1700 <= p.birth_year <= 1770 for p in results)

    def test_death_year_range_filter(self, index: TreeIndex):
        results = index.search(death_year_from=1780, death_year_to=1830)
        assert {p.person_id for p in results} == {SIMON, ANNA, ZBYNEK, PETR}
        # A person with no recorded death is excluded rather than treated as year zero.
        assert all(p.death_year is not None for p in results)
        assert EVA not in {p.person_id for p in results}

    def test_death_year_filters_are_independent(self, index: TreeIndex):
        assert {p.person_id for p in index.search(death_year_to=1750)} == {VIT}
        assert {p.person_id for p in index.search(death_year_from=1880)} == {MARIE}

    def test_surname_prefix_filter(self, index: TreeIndex):
        names = {s["surname"] for s in index.surnames("Herd", min_count=1)}
        assert names == {"Herda", "Herdová"}
        # Diacritic-insensitive, like every other name match.
        assert {s["surname"] for s in index.surnames("mat", 1)} == {"Matějů"}
        assert index.surnames("zzz", 1) == []

    def test_living_filter_uses_status_three(self, index: TreeIndex):
        living = index.search(is_living=True)
        assert {p.person_id for p in living} == {EVA, TOMAS}
        # Anything that is not status 3 counts as not living, including the unknown 7.
        assert len(index.search(is_living=False)) == PEOPLE - 2

    def test_married_surname_is_searchable(self, index: TreeIndex):
        found = index.search(last_name="Herdová")
        assert ANNA in {p.person_id for p in found}, "married surname must match"

    def test_surnames_are_ranked_by_frequency(self, index: TreeIndex):
        surnames = index.surnames(None, min_count=2)
        assert surnames
        assert surnames[0]["count"] >= surnames[-1]["count"]
        assert surnames[0]["surname"] == "Herda"

    def test_surname_span_covers_recorded_births(self, index: TreeIndex):
        herda = next(s for s in index.surnames(None, 1) if s["surname"] == "Herda")
        assert herda["earliest_birth"] == 1695
        assert herda["latest_birth"] == 1801

    def test_fold_strips_czech_diacritics(self):
        assert fold("Kafková") == "kafkova"
        assert fold("Matějů") == "mateju"


class TestRelatives:
    def test_parent_and_child_links_are_reciprocal(self, index: TreeIndex):
        for person_id in index.people:
            for parent in index.parents(person_id):
                assert person_id in index.children(parent)

    def test_spouse_links_are_symmetric(self, index: TreeIndex):
        for person_id in index.people:
            for spouse, _ in index.spouses(person_id):
                assert person_id in [s for s, _ in index.spouses(spouse)]

    def test_relatives_groups_all_kinds(self, index: TreeIndex):
        groups = index.relatives(SIMON, ["parents", "siblings", "spouses", "children"])
        assert set(groups) == {"parents", "siblings", "spouses", "children"}
        assert {p["person_id"] for p in groups["parents"]} == {VIT, MAGDALENA}
        assert {p["person_id"] for p in groups["siblings"]} == {PETR}
        assert {p["person_id"] for p in groups["spouses"]} == {ANNA}
        assert {p["person_id"] for p in groups["children"]} == {ZBYNEK, ANEZKA, TOMAS}

    def test_child_roles_distinguish_foster_and_adopted(self, index: TreeIndex):
        children = index.relatives(SIMON, ["children"])["children"]
        roles = {entry["person_id"]: entry["relationship"] for entry in children}
        assert roles[ZBYNEK] == "natural child"
        assert roles[ANEZKA] == "foster child"
        assert roles[TOMAS] == "adopted child"

    def test_natural_only_children_exclude_foster_and_adopted(self, index: TreeIndex):
        assert set(index.children(SIMON, natural_only=True)) == {ZBYNEK}

    def test_half_siblings_can_be_excluded(self, index: TreeIndex):
        assert set(index.siblings(ZBYNEK, include_half=False)) == {ANEZKA, TOMAS}


class TestPaths:
    def test_self_path_is_same_person(self, index: TreeIndex):
        assert index.relationship_path(SIMON, SIMON)["same_person"] is True

    def test_parent_child_path_is_labelled(self, index: TreeIndex):
        assert index.relationship_path(SIMON, ZBYNEK)["relationship"] == "child"
        assert index.relationship_path(ZBYNEK, SIMON)["relationship"] == "parent"

    def test_path_is_symmetric_in_length(self, index: TreeIndex):
        forward = index.relationship_path(SIMON, ZBYNEK)
        backward = index.relationship_path(ZBYNEK, SIMON)
        assert forward["degrees_of_separation"] == backward["degrees_of_separation"] == 1

    def test_siblings_are_labelled(self, index: TreeIndex):
        assert index.relationship_path(SIMON, PETR)["relationship"] == "sibling"

    def test_grandparent_is_labelled(self, index: TreeIndex):
        assert index.relationship_path(ZBYNEK, VIT)["relationship"] == "grandparent"

    def test_first_cousins_are_labelled(self, index: TreeIndex):
        assert index.relationship_path(ZBYNEK, ONDREJ)["relationship"] == "first cousin"

    def test_relationship_by_marriage_is_not_called_kinship(self, index: TreeIndex):
        result = index.relationship_path(ANNA, VIT)
        assert "marriage" in result["relationship"]

    def test_grandparent_label(self):
        assert TreeIndex._label_path(["parent", "parent"]) == "grandparent"
        assert TreeIndex._label_path(["parent", "parent", "parent"]) == "great-grandparent"

    def test_cousin_labels(self):
        assert TreeIndex._label_path(["parent", "parent", "child", "child"]) == "first cousin"
        assert (
            TreeIndex._label_path(["parent", "parent", "child", "child", "child"])
            == "first cousin once removed"
        )

    def test_uncle_and_nephew_labels(self):
        assert TreeIndex._label_path(["parent", "parent", "child"]) == "uncle/aunt"
        assert TreeIndex._label_path(["parent", "child", "child"]) == "nephew/niece"

    def test_unreachable_person_reports_not_found(self, index: TreeIndex):
        assert not index.parents(NIKDO)
        assert not index.children(NIKDO)
        assert index.relationship_path(NIKDO, SIMON)["found"] is False


class TestPedigrees:
    def test_ancestors_use_ahnentafel_numbering(self, index: TreeIndex):
        tree = index.ancestors(ZBYNEK, generations=2)
        assert tree["root"]["ahnentafel"] == 1
        assert tree["root"]["father"]["ahnentafel"] == 2
        assert tree["root"]["mother"]["ahnentafel"] == 3
        assert tree["root"]["father"]["father"]["person_id"] == VIT
        assert tree["root"]["father"]["father"]["ahnentafel"] == 4

    def test_ancestors_flag_lines_that_continue_past_the_limit(self, index: TreeIndex):
        tree = index.ancestors(ZBYNEK, generations=1)
        assert tree["root"]["father"]["has_more_ancestors"] is True

    def test_descendants_report_generation_breakdown(self, index: TreeIndex):
        tree = index.descendants(VIT, generations=3)
        # Šimon and Petr, then Šimon's three children and Petr's one, then Zbyněk's two.
        assert tree["descendants_found"] == 8
        assert tree["by_generation"] == {
            "generation_1": 2,
            "generation_2": 4,
            "generation_3": 2,
        }

    def test_descendants_flag_lines_that_continue(self, index: TreeIndex):
        tree = index.descendants(VIT, generations=1)
        assert any(child.get("has_more_descendants") for child in tree["root"]["children"])

    def test_same_sex_parents_still_fill_both_pedigree_slots(self, index: TreeIndex):
        """Ahnentafel has two slots; a tree without one male and one female must still fit."""
        import copy

        clone = copy.copy(index)
        clone.family_spouses = dict(index.family_spouses)
        # Family 1's spouses become two men, so the gender rule cannot assign a mother.
        family = clone.child_families[SIMON][0]
        clone.family_spouses[family] = [VIT, PETR]

        tree = clone.ancestors(SIMON, generations=1)
        assert tree["root"]["father"]["person_id"] == VIT
        assert tree["root"]["mother"]["person_id"] == PETR

    def test_parents_of_unknown_gender_fill_slots_in_order(self, index: TreeIndex):
        import copy

        clone = copy.copy(index)
        clone.people = dict(index.people)
        for person_id in (VIT, MAGDALENA):
            person = copy.copy(index.people[person_id])
            person.gender = "U"
            clone.people[person_id] = person

        tree = clone.ancestors(SIMON, generations=1)
        assert {tree["root"]["father"]["person_id"], tree["root"]["mother"]["person_id"]} == {
            VIT,
            MAGDALENA,
        }


class TestFacts:
    def test_facts_are_chronological_with_unknowns_last(self, db):
        facts = queries.person_facts(db, [JOSEF], lang=20)[JOSEF]
        keys = [f["date"]["sort_key"] if f["date"] else None for f in facts]
        known = [k for k in keys if k is not None]
        assert known == sorted(known)
        assert keys[-1] is None, "the dateless burial must sort last"

    def test_protobuf_date_column_yields_its_display_string(self, db):
        birth = queries.person_facts(db, [SIMON], lang=20, tags=["BIRT"])[SIMON][0]
        assert birth["date"]["display"] == "7 OCT 1735"
        assert (birth["date"]["year"], birth["date"]["month"], birth["date"]["day"]) == (
            1735,
            10,
            7,
        )

    def test_range_date_exposes_both_bounds(self, db):
        census = queries.person_facts(db, [JOSEF], lang=20, tags=["CENS"])[JOSEF][0]
        assert census["date"]["display"] == "BET 2012 AND 2020"
        assert (census["date"]["year_from"], census["date"]["year_to"]) == (2012, 2020)
        assert census["date"]["is_range"] is True

    def test_before_date_has_no_lower_bound(self, db):
        birth = queries.person_facts(db, [MARIE], lang=20, tags=["BIRT"])[MARIE][0]
        assert birth["date"]["display"] == "BEF 1856"
        assert birth["date"]["year_from"] is None
        assert birth["date"]["year_to"] == 1856

    def test_residence_addresses_are_decoded_not_raw_protobuf(self, db):
        facts = queries.person_facts(db, [SIMON, ZBYNEK], lang=20, tags=["RESI"])
        details = [f["detail"] for group in facts.values() for f in group if f["detail"]]
        assert details == [
            "Branná č.13",
            "Canby, Canemah, Maple Lane, and New Era Precincts "
            "Canby town, Clackamas, Oregon, United States",
        ]

    def test_plain_text_header_is_left_alone(self, db):
        facts = {
            f["gedcom_tag"]: f["detail"] for f in queries.person_facts(db, [SIMON], lang=20)[SIMON]
        }
        assert facts["OCCU"] == "sedlák"
        assert facts["RELI"] == "římskokatolické"

    def test_age_and_cause_of_death_are_carried(self, db):
        death = queries.person_facts(db, [SIMON], lang=20, tags=["DEAT"])[SIMON][0]
        assert death["age"] == "55"
        assert death["cause_of_death"] == "stářím"

    def test_tag_filter_narrows_results(self, db, index: TreeIndex):
        births = queries.person_facts(db, list(index.people), lang=20, tags=["BIRT"])
        assert any(births.values())
        assert all(f["gedcom_tag"] == "BIRT" for facts in births.values() for f in facts)


class TestFamilies:
    def test_family_status_is_decoded(self, db):
        records = queries.family_records(db, [1, 3, 4, 5])
        assert records[1]["status"] == "married"
        assert records[3]["status"] == "divorced"
        assert records[4]["status"] == "unspecified"
        assert records[5]["status"] == "life partners"

    def test_members_are_ordered_spouses_then_children(self, db):
        members = queries.family_members(db, [2])[2]
        assert [m["role"] for m in members] == [
            "husband",
            "wife",
            "natural child",
            "foster child",
            "adopted child",
        ]

    def test_family_facts_include_marriage_and_divorce(self, db):
        facts = queries.family_facts(db, [3], lang=20)[3]
        assert [f["gedcom_tag"] for f in facts] == ["MARR", "DIV"]
        assert facts[0]["type"] == "Marriage"
        assert facts[1]["type"] == "Divorce"

    def test_custom_family_event_is_named(self, db):
        facts = queries.family_facts(db, [5], lang=20)[5]
        assert facts[0]["type"] == "Rel partners"

    def test_spouse_age_is_reported(self, db):
        marriage = queries.family_facts(db, [2], lang=20)[2][0]
        assert marriage["age"] == "22"


class TestEvidence:
    def test_citations_split_urls_from_page_references(self, db):
        citations = queries.citations_for(db, [SIMON], queries.ITEM_TYPE_INDIVIDUAL, lang=20)[SIMON]
        assert len(citations) == 3
        for entry in citations:
            assert not (entry["page"] and entry["page"].startswith("http"))
        urls = [c["url"] for c in citations if c["url"]]
        assert urls == ["https://www.myheritage.cz/profile-ABC/simon-herda"]

    def test_unstated_confidence_becomes_null(self, db):
        citations = queries.citations_for(db, [SIMON], queries.ITEM_TYPE_INDIVIDUAL, lang=20)[SIMON]
        by_page = {c["page"]: c for c in citations}
        assert by_page["8017/200"]["confidence"] == 3
        assert by_page["8053/131"]["confidence"] is None

    def test_citation_text_is_html_free(self, db):
        citations = queries.citations_for(db, [SIMON], queries.ITEM_TYPE_INDIVIDUAL, lang=20)[SIMON]
        texts = [c["text"] for c in citations if c["text"]]
        assert "zápis o úmrtí" in texts

    def test_sources_carry_titles_and_citation_counts(self, db):
        sources = queries.search_sources(db, lang=20, source_id=None, query=None, limit=10)
        assert [s["title"] for s in sources][0] == "Matrika Branná"
        assert all(s["title"] for s in sources)
        assert sources[0]["citation_count"] == 2

    def test_sources_can_be_searched_by_text(self, db):
        found = queries.search_sources(db, lang=20, source_id=None, query="Papež", limit=10)
        assert [s["source_id"] for s in found] == [2]

    def test_a_single_source_can_be_fetched_by_id(self, db):
        found = queries.search_sources(db, lang=20, source_id=3, query=None, limit=10)
        assert [s["title"] for s in found] == ["BillionGraves"]

    def test_citations_can_be_listed_for_a_source(self, db):
        found = queries.citations_for(db, [ZBYNEK], queries.ITEM_TYPE_INDIVIDUAL, lang=20)[ZBYNEK]
        assert [c["source_id"] for c in found] == [3]
        assert found[0]["page"] == "hrob 12"

    def test_notes_are_html_free(self, db):
        notes = queries.search_notes(db, lang=20, query="", limit=50)
        assert len(notes) == 4
        assert all("<p>" not in n["text"] and "&iacute;" not in n["text"] for n in notes)
        assert any(n["text"] == "bydliště: Majdalena, Plzeň" for n in notes)

    def test_double_escaped_break_becomes_a_newline(self, db):
        notes = queries.search_notes(db, lang=20, query="Sophia", limit=5)
        assert notes[0]["text"] == "Anna Sophia Herdova\nNarození: 1738"

    def test_notes_report_what_they_hang_off(self, db):
        kinds = {
            n["attached_to"]["type"] for n in queries.search_notes(db, lang=20, query="", limit=50)
        }
        assert kinds == {"individual", "individual_fact", "family_fact"}

    def test_places_are_ranked_by_event_count(self, db):
        places = queries.search_places(db, lang=20, query=None, limit=10)
        assert places[0]["place"] == "Branná"
        assert places[0]["event_count"] >= places[-1]["event_count"]

    def test_place_blank_in_preferred_language_falls_back(self, db):
        places = queries.search_places(db, lang=20, query=None, limit=10)
        vienna = next(p for p in places if p["place_id"] == 4)
        assert vienna["place"] == "Vienna"
        assert vienna["language"] == "en"


class TestMedia:
    def test_returns_only_textual_metadata(self, db):
        items = queries.media_for(db, None, lang=20, limit=25)
        assert items
        allowed = {"media_item_id", "title", "description", "date", "place", "person_id"}
        for item in items:
            assert set(item) <= allowed

    def test_items_without_any_text_are_dropped(self, db):
        assert db.count("media_item_main_data") == 3
        assert [item["media_item_id"] for item in queries.media_for(db, None, 20, 25)] == [1, 2]

    def test_media_can_be_filtered_to_one_person(self, db):
        items = queries.media_for(db, [SIMON], lang=20, limit=25)
        assert {item["person_id"] for item in items} == {SIMON}


class TestStatistics:
    def test_completeness_percentages_are_bounded(self, db):
        stats = queries.statistics(db, lang=20, metrics=["completeness"])["completeness"]
        for key in ("with_known_birth_date", "with_any_place", "with_source_citation"):
            assert 0 <= stats[key]["percent"] <= 100

    def test_demographics_match_the_fixture(self, db):
        demo = queries.statistics(db, lang=20, metrics=["demographics"])["demographics"]
        assert demo["by_gender"] == {"male": 8, "female": 7}
        assert demo["by_living_status"] == {"deceased": 12, "living": 2, "unknown (7)": 1}

    def test_family_status_labels_are_decoded(self, db):
        demo = queries.statistics(db, lang=20, metrics=["demographics"])["demographics"]
        assert demo["families_by_status"] == {
            "married": 2,
            "divorced": 1,
            "unspecified": 1,
            "life partners": 1,
        }

    def test_lifespans_are_plausible(self, db):
        spans = queries.statistics(db, lang=20, metrics=["lifespans"])["lifespans"]
        assert 0 < spans["average_years"] < 120
        assert spans["sample_size"] == 8

    def test_fact_frequency_uses_labels_not_tags(self, db):
        frequency = queries.statistics(db, lang=20, metrics=["facts"])["fact_frequency"]
        assert frequency["Birth"] == PEOPLE
        assert frequency["Settlement"] == 1
        assert "EVEN" not in frequency


class TestLanguages:
    def test_requested_language_wins(self, db):
        english = TreeIndex(db, lang=0)
        assert english.people[SIMON].first_name == "Simon"
        assert english.people[SIMON].text_language == 0

    def test_missing_language_falls_back_to_czech(self, db):
        english = TreeIndex(db, lang=0)
        # Only Šimon and Anna have English rows; everyone else falls back.
        assert english.people[ZBYNEK].first_name == "Zbyněk"
        assert english.people[ZBYNEK].text_language == 20

    def test_language_note_reports_what_was_actually_used(self, db):
        english = TreeIndex(db, lang=0)
        assert english.language_note() == {"en": 2, "cs": PEOPLE - 2}


class TestCycleSafety:
    """A corrupt tree must not make either pedigree direction loop or lie."""

    def _cyclic(self, index: TreeIndex):
        import copy

        clone = copy.copy(index)
        clone.child_families = dict(index.child_families)
        clone.family_spouses = dict(index.family_spouses)
        clone.family_children = dict(index.family_children)
        clone.spouse_families = dict(index.spouse_families)

        # Make Šimon a spouse in the family he is also a child of.
        family = clone.child_families[SIMON][0]
        clone.family_spouses[family] = [*clone.family_spouses.get(family, []), SIMON]
        clone.spouse_families[SIMON] = [*clone.spouse_families.get(SIMON, []), family]
        return clone

    def test_ancestors_terminate_on_a_cycle(self, index: TreeIndex):
        clone = self._cyclic(index)
        tree = clone.ancestors(SIMON, generations=8)
        assert tree["root"]["person_id"] == SIMON

        def ids(node):
            out = [node["person_id"]]
            for key in ("father", "mother"):
                if key in node:
                    out += ids(node[key])
            return out

        chain = ids(tree["root"])
        assert chain.count(SIMON) == 1, "person appears as their own ancestor"

    def test_descendants_terminate_on_a_cycle(self, index: TreeIndex):
        clone = self._cyclic(index)
        tree = clone.descendants(SIMON, generations=8)
        assert tree["root"]["person_id"] == SIMON


class TestEmptyInput:
    """Every batch query short-circuits on an empty id list instead of building bad SQL."""

    def test_batch_queries_accept_no_ids(self, db):
        assert queries.person_facts(db, [], lang=20) == {}
        assert queries.family_facts(db, [], lang=20) == {}
        assert queries.family_members(db, []) == {}
        assert queries.family_records(db, []) == {}
        assert queries.notes_for(db, [], queries.ITEM_TYPE_INDIVIDUAL, lang=20) == {}
        assert queries.citations_for(db, [], queries.ITEM_TYPE_INDIVIDUAL, lang=20) == {}

    def test_unknown_ids_yield_empty_groups_not_errors(self, db):
        assert queries.person_facts(db, [12345], lang=20) == {12345: []}
        assert queries.family_records(db, [12345]) == {}

    def test_language_hints_are_resolved_leniently(self, db):
        assert queries.resolve_language(db, "cs") == 20
        assert queries.resolve_language(db, "Czech") == 20
        assert queries.resolve_language(db, 20) == 20
        assert queries.resolve_language(db, "20") == 20
        # Unrecognised hints fall back to the project's own language rather than failing.
        assert queries.resolve_language(db, "klingon") == 20
        assert queries.resolve_language(db, "") == 20

    def test_unmapped_enum_values_are_labelled_not_guessed(self):
        from ftb_mcp.schema import FAMILY_STATUS, label_for

        assert label_for(FAMILY_STATUS, 3) == "married"
        assert label_for(FAMILY_STATUS, 42) == "unknown (42)"
        assert label_for(FAMILY_STATUS, None) == "unknown"


class TestFactLabels:
    """RESI facts carry ADDR/EMAIL in fact_type; that must not become the event name."""

    def test_residence_is_labelled_residence_not_addr(self, db):
        facts = [f for v in queries.person_facts(db, [SIMON], 20, ["RESI"]).values() for f in v]
        assert facts
        addr = [f for f in facts if f.get("detail_kind") == "addr"]
        assert addr
        assert all(f["type"] == "Residence" for f in addr)

    def test_custom_event_names_still_win(self):
        from ftb_mcp.schema import fact_label

        assert fact_label("EVEN", "Settlement") == "Settlement"
        assert fact_label("EVEN", "MYHERITAGE:REL_PARTNERS") == "Rel partners"
        assert fact_label("BIRT", "") == "Birth"
