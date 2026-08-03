"""SQL against the FTB schema. The only module that knows table and column names.

Three conventions run through every query here:

* ``delete_flag = 0`` -- FTB soft-deletes. Soft-deleted family connections would
  otherwise show up as phantom relatives.
* Soft deletion is two levels deep, so an aggregate over facts also joins back to the
  owning individual or family and checks *its* flag. Deleting a person leaves their
  facts with ``delete_flag = 0`` of their own, so filtering the fact table alone counts
  events belonging to people who are no longer in the tree. See LIVE_INDIVIDUAL_JOIN.
* Language ranking -- text lives in ``*_lang_data`` tables keyed by ``data_language``.
  Rows are ranked by the caller's preferred language, then Czech, then English, then
  anything, and the winner is taken with ROW_NUMBER(). Every text result reports the
  language it actually came from so callers can tell when a fallback happened.
"""

from __future__ import annotations

from typing import Any

from .db import FtbDatabase
from .decode import clean_text, norm_date, pb_text, year_of
from .schema import (
    CHILD_ROLES,
    FACT_LABELS,
    FAMILY_STATUS,
    FIELD_QUALIFIER_TYPES,
    GENDER,
    ITEM_TYPE_FAMILY_FACT,
    ITEM_TYPE_INDIVIDUAL,
    ITEM_TYPE_INDIVIDUAL_FACT,
    LANGUAGE_CODES,
    LANGUAGE_NAMES,
    LIVING_STATUS,
    PROTOBUF_HEADER_TOKENS,
    ROLE_TYPE,
    SPOUSE_ROLES,
    fact_label,
    label_for,
)

# Ranks a *_lang_data row: caller's language first, then Czech, then English.
LANG_RANK = "CASE {col} WHEN :lang THEN 0 WHEN 20 THEN 1 WHEN 0 THEN 2 ELSE 3 END"

# Best-language place name per place_id. Some place_ids carry a row in the preferred
# language whose text is blank while another language has the real name, so non-empty
# text outranks language preference.
PLACES_CTE = f"""
places AS (
    SELECT place_id, place, data_language,
           ROW_NUMBER() OVER (
               PARTITION BY place_id
               ORDER BY CASE WHEN TRIM(COALESCE(place, '')) = '' THEN 1 ELSE 0 END,
                        {LANG_RANK.format(col="data_language")}
           ) AS rn
    FROM places_lang_data
)
"""

# Soft deletion is two levels deep. FTB deletes a person by flagging their
# individual_main_data row and leaves the facts hanging off them with delete_flag = 0 of
# their own, so `WHERE delete_flag = 0` on the fact table alone still counts the events
# of people the author removed. Every aggregate over facts joins back to the owner with
# one of these, which expect the fact table to be aliased `f`.
#
# kafkova.ftb has 360 soft-deleted individuals holding 866 facts between them. Counting
# those inflated the completeness metric past 100% of the tree, and pinned the reported
# earliest event year on a person who is no longer in it.
LIVE_INDIVIDUAL_JOIN = (
    "JOIN individual_main_data owner "
    "ON owner.individual_id = f.individual_id AND owner.delete_flag = 0"
)
LIVE_FAMILY_JOIN = (
    "JOIN family_main_data owner ON owner.family_id = f.family_id AND owner.delete_flag = 0"
)

# Earliest birth-ish and death-ish dates per individual, ignoring unknown sentinels.
VITALS_CTE = f"""
vitals AS (
    SELECT f.individual_id,
           MIN(CASE WHEN f.token IN ('BIRT','CHR','BAPM') AND f.sorted_date <> 999999999
                    THEN f.sorted_date END) AS birth_sd,
           MIN(CASE WHEN f.token IN ('DEAT','BURI') AND f.sorted_date <> 999999999
                    THEN f.sorted_date END) AS death_sd
    FROM individual_fact_main_data f
    {LIVE_INDIVIDUAL_JOIN}
    WHERE f.delete_flag = 0
    GROUP BY f.individual_id
)
"""


def language_code(value: int | None) -> str | None:
    """Map an FTB language number to an ISO-ish code."""
    if value is None:
        return None
    return LANGUAGE_CODES.get(value, f"lang{value}")


def resolve_language(db: FtbDatabase, requested: str | int | None) -> int:
    """Turn a caller's language hint into an FTB language number.

    Accepts a code ("cs"), a name ("Czech") or a raw number. Unrecognised values fall
    back to the project's own primary language rather than failing the call.
    """
    if isinstance(requested, int):
        return requested
    if isinstance(requested, str) and requested.strip():
        wanted = requested.strip().lower()
        for number, code in LANGUAGE_CODES.items():
            if wanted in (code, LANGUAGE_NAMES.get(number, "").lower()):
                return number
        if wanted.isdigit():
            return int(wanted)

    declared = db.project_languages()
    return declared[-1] if declared else 20


# ------------------------------------------------------------------------- tree level


def tree_info(db: FtbDatabase) -> dict[str, Any]:
    """Project metadata and entity counts."""
    header = {
        row["name"]: row["value"]
        for row in db.query("SELECT name, value FROM project_parameters WHERE category = 'Header'")
    }
    languages = db.project_languages()

    span = db.query_one(
        f"""
        SELECT MIN(NULLIF(f.lower_bound_search_date, 999999999)) AS earliest,
               MAX(NULLIF(f.upper_bound_search_date, 999999999)) AS latest
        FROM individual_fact_main_data f
        {LIVE_INDIVIDUAL_JOIN}
        WHERE f.delete_flag = 0 AND f.lower_bound_search_date > 0
        """
    )

    return {
        "tree_name": (header.get("File") or "").strip() or db.path.stem,
        "file": db.path.name,
        "source_application": header.get("ProductName") or header.get("Source"),
        "application_version": header.get("Version"),
        "gedcom_version": header.get("GedcomVersion"),
        "gedcom_format": header.get("GedcomFormat"),
        "character_set": header.get("CharacterSet"),
        "primary_language": header.get("Language"),
        "db_version": f"{db.parameter('db_major_version')}.{db.parameter('db_minor_version')}",
        "languages": [
            {"code": language_code(number), "name": LANGUAGE_NAMES.get(number), "ftb_code": number}
            for number in languages
        ],
        "counts": {
            "individuals": db.count("individual_main_data"),
            "families": db.count("family_main_data"),
            # Facts of a deleted person are not part of the tree any more than the
            # person is, so they are excluded here as everywhere else.
            "individual_facts": db.scalar(
                f"SELECT COUNT(*) FROM individual_fact_main_data f "
                f"{LIVE_INDIVIDUAL_JOIN} WHERE f.delete_flag = 0"
            ),
            "family_facts": db.scalar(
                f"SELECT COUNT(*) FROM family_fact_main_data f "
                f"{LIVE_FAMILY_JOIN} WHERE f.delete_flag = 0"
            ),
            "places": db.scalar("SELECT COUNT(*) FROM places_main_data"),
            "notes": db.count("note_main_data"),
            "sources": db.count("source_main_data"),
            "citations": db.count("citation_main_data"),
            "media_items": db.count("media_item_main_data"),
        },
        "earliest_event_year": year_of(span["earliest"]) if span else None,
        "latest_event_year": year_of(span["latest"]) if span else None,
    }


# ----------------------------------------------------------------------------- facts


def _fact_row(row: Any, lang: int) -> dict[str, Any]:
    """Shape one fact row, decoding its date, header and place.

    Serves both individual and family facts, whose result sets differ slightly, so
    optional columns are probed against the row's own column list. Note that
    ``sqlite3.Row`` has no ``__contains__``: ``"age" in row`` would search the row's
    *values*, so the column names must be listed explicitly.
    """
    columns = row.keys()
    token = row["token"]
    header_raw = row["header"] if "header" in columns else None

    # RESI facts hide their address in a protobuf message; everything else is plain.
    header = pb_text(header_raw) if token in PROTOBUF_HEADER_TOKENS else clean_text(header_raw)

    fact: dict[str, Any] = {
        "fact_id": row["fact_id"],
        "type": fact_label(token, row["fact_type"]),
        "gedcom_tag": token,
        "date": norm_date(
            row["date"],
            row["sorted_date"],
            row["lower_bound_search_date"],
            row["upper_bound_search_date"],
        ),
        "place": row["place"] or None,
        "detail": header or None,
        "is_current": bool(row["is_current"]),
    }
    # ADDR/EMAIL name the kind of detail carried, not the event; keep them alongside
    # the event label rather than letting them replace it.
    if row["fact_type"] in FIELD_QUALIFIER_TYPES:
        fact["detail_kind"] = row["fact_type"].lower()
    if "age" in columns and row["age"]:
        fact["age"] = row["age"]
    if "cause_of_death" in columns and row["cause_of_death"]:
        fact["cause_of_death"] = clean_text(row["cause_of_death"])
    if row["text_language"] is not None and row["text_language"] != lang:
        fact["text_language"] = language_code(row["text_language"])
    return fact


_PERSON_FACTS_SQL = f"""
WITH {PLACES_CTE},
lang AS (
    SELECT individual_fact_id, header, cause_of_death, data_language,
           ROW_NUMBER() OVER (
               PARTITION BY individual_fact_id
               ORDER BY {LANG_RANK.format(col="data_language")}
           ) AS rn
    FROM individual_fact_lang_data
)
SELECT f.individual_fact_id AS fact_id, f.individual_id, f.token, f.fact_type, f.age,
       f.date, f.sorted_date, f.lower_bound_search_date, f.upper_bound_search_date,
       f.is_current, p.place AS place,
       l.header, l.cause_of_death, l.data_language AS text_language
FROM individual_fact_main_data f
LEFT JOIN places p ON p.place_id = f.place_id AND p.rn = 1
LEFT JOIN lang l ON l.individual_fact_id = f.individual_fact_id AND l.rn = 1
WHERE f.delete_flag = 0 AND f.individual_id IN ({{ids}})
ORDER BY f.individual_id,
         CASE WHEN f.sorted_date = 999999999 THEN 1 ELSE 0 END, f.sorted_date
"""


def person_facts(
    db: FtbDatabase, person_ids: list[int], lang: int, tags: list[str] | None = None
) -> dict[int, list[dict[str, Any]]]:
    """Facts for one or more people, grouped by person id and sorted chronologically."""
    if not person_ids:
        return {}

    sql = _PERSON_FACTS_SQL.format(ids=", ".join(f":id{i}" for i in range(len(person_ids))))
    params: dict[str, Any] = {"lang": lang}
    params.update({f"id{i}": pid for i, pid in enumerate(person_ids)})

    wanted = {tag.upper() for tag in tags} if tags else None
    grouped: dict[int, list[dict[str, Any]]] = {pid: [] for pid in person_ids}
    for row in db.query(sql, params):
        if wanted and (row["token"] or "").upper() not in wanted:
            continue
        grouped[row["individual_id"]].append(_fact_row(row, lang))
    return grouped


_FAMILY_FACTS_SQL = f"""
WITH {PLACES_CTE},
lang AS (
    SELECT family_fact_id, header, data_language,
           ROW_NUMBER() OVER (
               PARTITION BY family_fact_id ORDER BY {LANG_RANK.format(col="data_language")}
           ) AS rn
    FROM family_fact_lang_data
)
SELECT f.family_fact_id AS fact_id, f.family_id, f.token, f.fact_type, f.spouse_age AS age,
       f.date, f.sorted_date, f.lower_bound_search_date, f.upper_bound_search_date,
       f.is_current, p.place AS place, l.header, l.data_language AS text_language
FROM family_fact_main_data f
LEFT JOIN places p ON p.place_id = f.place_id AND p.rn = 1
LEFT JOIN lang l ON l.family_fact_id = f.family_fact_id AND l.rn = 1
WHERE f.delete_flag = 0 AND f.family_id IN ({{ids}})
ORDER BY f.family_id,
         CASE WHEN f.sorted_date = 999999999 THEN 1 ELSE 0 END, f.sorted_date
"""


def family_facts(
    db: FtbDatabase, family_ids: list[int], lang: int
) -> dict[int, list[dict[str, Any]]]:
    """Marriage, divorce and other family-level facts, grouped by family id."""
    if not family_ids:
        return {}
    sql = _FAMILY_FACTS_SQL.format(ids=", ".join(f":id{i}" for i in range(len(family_ids))))
    params: dict[str, Any] = {"lang": lang}
    params.update({f"id{i}": fid for i, fid in enumerate(family_ids)})

    grouped: dict[int, list[dict[str, Any]]] = {fid: [] for fid in family_ids}
    for row in db.query(sql, params):
        grouped[row["family_id"]].append(_fact_row(row, lang))
    return grouped


# --------------------------------------------------------------------------- families


def families_of(db: FtbDatabase, person_id: int) -> dict[str, list[int]]:
    """Split a person's families into those they parent in and those they were born into."""
    rows = db.query(
        """
        SELECT family_id, individual_role_type
        FROM family_individual_connection
        WHERE delete_flag = 0 AND individual_id = :pid
        """,
        {"pid": person_id},
    )
    return {
        "as_spouse": [r["family_id"] for r in rows if r["individual_role_type"] in SPOUSE_ROLES],
        "as_child": [r["family_id"] for r in rows if r["individual_role_type"] in CHILD_ROLES],
    }


def family_members(db: FtbDatabase, family_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Members of each family with their role, children ordered as the user arranged them."""
    if not family_ids:
        return {}
    sql = f"""
        SELECT family_id, individual_id, individual_role_type, child_order_in_family
        FROM family_individual_connection
        WHERE delete_flag = 0
          AND family_id IN ({", ".join(f":id{i}" for i in range(len(family_ids)))})
        ORDER BY family_id,
                 CASE WHEN individual_role_type IN (2, 3) THEN 0 ELSE 1 END,
                 CASE WHEN child_order_in_family < 0 THEN 1 ELSE 0 END,
                 child_order_in_family
    """
    params = {f"id{i}": fid for i, fid in enumerate(family_ids)}

    grouped: dict[int, list[dict[str, Any]]] = {fid: [] for fid in family_ids}
    for row in db.query(sql, params):
        grouped[row["family_id"]].append(
            {
                "person_id": row["individual_id"],
                "role": label_for(ROLE_TYPE, row["individual_role_type"]),
                "role_code": row["individual_role_type"],
            }
        )
    return grouped


def family_records(db: FtbDatabase, family_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Core family rows keyed by family id."""
    if not family_ids:
        return {}
    sql = f"""
        SELECT family_id, status, guid FROM family_main_data
        WHERE delete_flag = 0
          AND family_id IN ({", ".join(f":id{i}" for i in range(len(family_ids)))})
    """
    params = {f"id{i}": fid for i, fid in enumerate(family_ids)}
    return {
        row["family_id"]: {
            "family_id": row["family_id"],
            "status": label_for(FAMILY_STATUS, row["status"]),
            "status_code": row["status"],
        }
        for row in db.query(sql, params)
    }


# ------------------------------------------------------------------- notes, citations


def _token_filter(item_type: int, entity_ids: list[int]) -> tuple[str, dict[str, Any]]:
    """Build a join condition selecting token_on_item rows for given entities."""
    names = ", ".join(f":e{i}" for i in range(len(entity_ids)))
    params: dict[str, Any] = {"itype": item_type}
    params.update({f"e{i}": eid for i, eid in enumerate(entity_ids)})
    return f"t.item_type = :itype AND t.entity_id IN ({names})", params


def notes_for(
    db: FtbDatabase, entity_ids: list[int], item_type: int, lang: int
) -> dict[int, list[dict[str, Any]]]:
    """Notes attached to individuals or facts, grouped by the entity they belong to."""
    if not entity_ids:
        return {}
    condition, params = _token_filter(item_type, entity_ids)
    params["lang"] = lang

    sql = f"""
    WITH lang AS (
        SELECT note_id, note_text, data_language,
               ROW_NUMBER() OVER (
                   PARTITION BY note_id ORDER BY {LANG_RANK.format(col="data_language")}
               ) AS rn
        FROM note_lang_data
    )
    SELECT t.entity_id, n.note_id, n.special_note_key,
           l.note_text, l.data_language AS text_language
    FROM note_main_data n
    JOIN note_to_item_connection c
      ON c.note_id = n.note_id AND c.delete_flag = 0
    JOIN token_on_item t ON t.token_on_item_id = c.external_token_on_item_id
    LEFT JOIN lang l ON l.note_id = n.note_id AND l.rn = 1
    WHERE n.delete_flag = 0 AND {condition}
    ORDER BY t.entity_id, n.note_id
    """

    grouped: dict[int, list[dict[str, Any]]] = {eid: [] for eid in entity_ids}
    for row in db.query(sql, params):
        text = clean_text(row["note_text"])
        if not text:
            continue
        grouped[row["entity_id"]].append(
            {
                "note_id": row["note_id"],
                "text": text,
                "gedcom_key": row["special_note_key"] or None,
                "language": language_code(row["text_language"]),
            }
        )
    return grouped


def citations_for(
    db: FtbDatabase, entity_ids: list[int], item_type: int, lang: int, limit: int = 200
) -> dict[int, list[dict[str, Any]]]:
    """Source citations attached to the given entities, grouped by entity id."""
    if not entity_ids:
        return {}
    condition, params = _token_filter(item_type, entity_ids)
    params.update({"lang": lang, "limit": limit})

    sql = f"""
    WITH cl AS (
        SELECT citation_id, description, data_language,
               ROW_NUMBER() OVER (
                   PARTITION BY citation_id ORDER BY {LANG_RANK.format(col="data_language")}
               ) AS rn
        FROM citation_lang_data
    ),
    sl AS (
        SELECT source_id, title, author, publisher, type, data_language,
               ROW_NUMBER() OVER (
                   PARTITION BY source_id ORDER BY {LANG_RANK.format(col="data_language")}
               ) AS rn
        FROM source_lang_data
    )
    SELECT t.entity_id, c.citation_id, c.source_id, c.page, c.confidence,
           c.event_type, c.event_role, cl.description,
           sl.title AS source_title, sl.author AS source_author,
           sl.publisher AS source_publisher, sl.type AS source_type
    FROM citation_main_data c
    JOIN token_on_item t ON t.token_on_item_id = c.external_token_on_item_id
    LEFT JOIN cl ON cl.citation_id = c.citation_id AND cl.rn = 1
    LEFT JOIN sl ON sl.source_id = c.source_id AND sl.rn = 1
    WHERE c.delete_flag = 0 AND {condition}
    ORDER BY t.entity_id, c.citation_id
    LIMIT :limit
    """

    grouped: dict[int, list[dict[str, Any]]] = {eid: [] for eid in entity_ids}
    for row in db.query(sql, params):
        page = row["page"] or ""
        grouped[row["entity_id"]].append(
            {
                "citation_id": row["citation_id"],
                "source_id": row["source_id"],
                "source_title": row["source_title"],
                "source_author": row["source_author"] or None,
                "source_publisher": row["source_publisher"] or None,
                "source_type": row["source_type"] or None,
                # FTB overloads `page` with a record URL for online collections.
                "url": page if page.startswith("http") else None,
                "page": None if page.startswith("http") else (page or None),
                "confidence": row["confidence"] if row["confidence"] >= 0 else None,
                "event_type": row["event_type"] or None,
                "text": clean_text(row["description"]),
            }
        )
    return grouped


def search_sources(
    db: FtbDatabase, lang: int, source_id: int | None, query: str | None, limit: int
) -> list[dict[str, Any]]:
    """Sources, optionally filtered by id or free-text match on title/author/text."""
    params: dict[str, Any] = {"lang": lang, "limit": limit}
    where = ["s.delete_flag = 0"]
    if source_id is not None:
        where.append("s.source_id = :sid")
        params["sid"] = source_id
    if query:
        where.append(
            "(sl.title LIKE :q OR sl.author LIKE :q OR sl.publisher LIKE :q OR sl.text LIKE :q)"
        )
        params["q"] = f"%{query}%"

    sql = f"""
    WITH sl AS (
        SELECT source_id, title, abbreviation, author, publisher, agency, text, type, media,
               data_language,
               ROW_NUMBER() OVER (
                   PARTITION BY source_id ORDER BY {LANG_RANK.format(col="data_language")}
               ) AS rn
        FROM source_lang_data
    )
    SELECT s.source_id, sl.title, sl.abbreviation, sl.author, sl.publisher, sl.agency,
           sl.text, sl.type, sl.media, sl.data_language AS text_language,
           (SELECT COUNT(*) FROM citation_main_data c
             WHERE c.source_id = s.source_id AND c.delete_flag = 0) AS citation_count
    FROM source_main_data s
    LEFT JOIN sl ON sl.source_id = s.source_id AND sl.rn = 1
    WHERE {" AND ".join(where)}
    ORDER BY citation_count DESC, s.source_id
    LIMIT :limit
    """
    return [
        {
            "source_id": row["source_id"],
            "title": row["title"] or None,
            "abbreviation": row["abbreviation"] or None,
            "author": row["author"] or None,
            "publisher": row["publisher"] or None,
            "agency": row["agency"] or None,
            "type": row["type"] or None,
            "media": row["media"] or None,
            "text": clean_text(row["text"]),
            "citation_count": row["citation_count"],
            "language": language_code(row["text_language"]),
        }
        for row in db.query(sql, params)
    ]


def search_notes(db: FtbDatabase, lang: int, query: str, limit: int) -> list[dict[str, Any]]:
    """Free-text search across note bodies.

    Matches the stored HTML, so a query can miss text that only appears once entities
    are resolved. Results always return cleaned text.
    """
    sql = f"""
    WITH lang AS (
        SELECT note_id, note_text, data_language,
               ROW_NUMBER() OVER (
                   PARTITION BY note_id ORDER BY {LANG_RANK.format(col="data_language")}
               ) AS rn
        FROM note_lang_data
    )
    SELECT n.note_id, l.note_text, l.data_language AS text_language,
           t.entity_id, t.item_type
    FROM note_main_data n
    JOIN lang l ON l.note_id = n.note_id AND l.rn = 1
    LEFT JOIN note_to_item_connection c
           ON c.note_id = n.note_id AND c.delete_flag = 0
    LEFT JOIN token_on_item t ON t.token_on_item_id = c.external_token_on_item_id
    WHERE n.delete_flag = 0 AND l.note_text LIKE :q
    ORDER BY n.note_id
    LIMIT :limit
    """
    results = []
    for row in db.query(sql, {"lang": lang, "q": f"%{query}%", "limit": limit}):
        results.append(
            {
                "note_id": row["note_id"],
                "text": clean_text(row["note_text"]),
                "attached_to": {
                    "type": "individual"
                    if row["item_type"] == ITEM_TYPE_INDIVIDUAL
                    else "individual_fact"
                    if row["item_type"] == ITEM_TYPE_INDIVIDUAL_FACT
                    else "family_fact"
                    if row["item_type"] == ITEM_TYPE_FAMILY_FACT
                    else None,
                    "id": row["entity_id"],
                }
                if row["entity_id"] is not None
                else None,
                "language": language_code(row["text_language"]),
            }
        )
    return results


# ---------------------------------------------------------------------------- places


def search_places(
    db: FtbDatabase, lang: int, query: str | None, limit: int
) -> list[dict[str, Any]]:
    """Places with a count of how many events happened there."""
    params: dict[str, Any] = {"lang": lang, "limit": limit}
    where = "p.rn = 1 AND TRIM(COALESCE(p.place, '')) <> ''"
    if query:
        where += " AND p.place LIKE :q"
        params["q"] = f"%{query}%"

    sql = f"""
    WITH {PLACES_CTE}
    SELECT p.place_id, p.place, p.data_language AS text_language,
           (SELECT COUNT(*) FROM individual_fact_main_data f
             {LIVE_INDIVIDUAL_JOIN}
             WHERE f.place_id = p.place_id AND f.delete_flag = 0) AS individual_event_count,
           (SELECT COUNT(*) FROM family_fact_main_data f
             {LIVE_FAMILY_JOIN}
             WHERE f.place_id = p.place_id AND f.delete_flag = 0) AS family_event_count
    FROM places p
    WHERE {where}
    ORDER BY (individual_event_count + family_event_count) DESC, p.place
    LIMIT :limit
    """
    return [
        {
            "place_id": row["place_id"],
            "place": row["place"],
            "event_count": row["individual_event_count"] + row["family_event_count"],
            "language": language_code(row["text_language"]),
        }
        for row in db.query(sql, params)
        if row["place"]
    ]


# ----------------------------------------------------------------------------- media


def media_for(
    db: FtbDatabase, person_ids: list[int] | None, lang: int, limit: int
) -> list[dict[str, Any]]:
    """Textual metadata for media items. Never returns binary data or image bytes."""
    params: dict[str, Any] = {"lang": lang, "limit": limit}
    joins = ""
    where = ["m.delete_flag = 0"]

    if person_ids:
        names = ", ".join(f":p{i}" for i in range(len(person_ids)))
        params.update({f"p{i}": pid for i, pid in enumerate(person_ids)})
        params["itype"] = ITEM_TYPE_INDIVIDUAL
        joins = """
        JOIN media_item_to_item_connection mc
          ON mc.media_item_id = m.media_item_id AND mc.delete_flag = 0
        JOIN token_on_item t ON t.token_on_item_id = mc.external_token_on_item_id
        """
        where.append(f"t.item_type = :itype AND t.entity_id IN ({names})")

    subject = "t.entity_id AS person_id," if person_ids else "NULL AS person_id,"
    sql = f"""
    WITH {PLACES_CTE},
    ml AS (
        SELECT media_item_id, title, description, data_language,
               ROW_NUMBER() OVER (
                   PARTITION BY media_item_id ORDER BY {LANG_RANK.format(col="data_language")}
               ) AS rn
        FROM media_item_lang_data
    )
    SELECT DISTINCT {subject} m.media_item_id, m.date, m.sorted_date,
           m.lower_bound_search_date, m.upper_bound_search_date,
           ml.title, ml.description, pl.place AS place
    FROM media_item_main_data m
    {joins}
    LEFT JOIN ml ON ml.media_item_id = m.media_item_id AND ml.rn = 1
    LEFT JOIN places pl ON pl.place_id = m.place_id AND pl.rn = 1
    WHERE {" AND ".join(where)}
    ORDER BY m.media_item_id
    LIMIT :limit
    """

    results = []
    for row in db.query(sql, params):
        title = clean_text(row["title"])
        description = clean_text(row["description"])
        if not title and not description:
            continue
        entry = {
            "media_item_id": row["media_item_id"],
            "title": title or None,
            "description": description or None,
            "date": norm_date(
                row["date"],
                row["sorted_date"],
                row["lower_bound_search_date"],
                row["upper_bound_search_date"],
            ),
            "place": row["place"] or None,
        }
        if row["person_id"] is not None:
            entry["person_id"] = row["person_id"]
        results.append(entry)
    return results


# ------------------------------------------------------------------------ statistics


def statistics(db: FtbDatabase, lang: int, metrics: list[str]) -> dict[str, Any]:
    """Aggregate statistics over the whole tree."""
    out: dict[str, Any] = {}

    if "demographics" in metrics:
        genders = {
            GENDER.get(row["gender"], row["gender"]): row["n"]
            for row in db.query(
                "SELECT gender, COUNT(*) n FROM individual_main_data "
                "WHERE delete_flag = 0 GROUP BY gender"
            )
        }
        living = {
            label_for(LIVING_STATUS, row["is_alive"]): row["n"]
            for row in db.query(
                "SELECT is_alive, COUNT(*) n FROM individual_main_data "
                "WHERE delete_flag = 0 GROUP BY is_alive"
            )
        }
        statuses = {
            label_for(FAMILY_STATUS, row["status"]): row["n"]
            for row in db.query(
                "SELECT status, COUNT(*) n FROM family_main_data "
                "WHERE delete_flag = 0 GROUP BY status"
            )
        }
        out["demographics"] = {
            "by_gender": genders,
            "by_living_status": living,
            "families_by_status": statuses,
        }

    if "lifespans" in metrics:
        rows = db.query(
            f"""
            WITH {VITALS_CTE}
            SELECT birth_sd / 10000 AS birth_year, death_sd / 10000 AS death_year
            FROM vitals
            WHERE birth_sd IS NOT NULL AND death_sd IS NOT NULL
            """
        )
        by_century: dict[str, list[int]] = {}
        for row in rows:
            span = row["death_year"] - row["birth_year"]
            if 0 <= span <= 120:
                century = f"{(row['birth_year'] // 100) + 1}th century"
                by_century.setdefault(century, []).append(span)
        out["lifespans"] = {
            "sample_size": sum(len(v) for v in by_century.values()),
            "average_years": round(
                sum(sum(v) for v in by_century.values())
                / max(1, sum(len(v) for v in by_century.values())),
                1,
            ),
            "by_birth_century": {
                century: {"count": len(spans), "average_years": round(sum(spans) / len(spans), 1)}
                for century, spans in sorted(by_century.items())
            },
        }

    if "places" in metrics:
        out["top_places"] = search_places(db, lang, None, 15)

    if "completeness" in metrics:
        # Every numerator counts the same population as the denominator: people who are
        # still in the tree. Counting a deleted person's birth record against a total
        # that excludes them is what let this report 112% of a tree as documented.
        total = db.count("individual_main_data")

        def documented(condition: str) -> int:
            return int(
                db.scalar(
                    f"SELECT COUNT(DISTINCT f.individual_id) FROM individual_fact_main_data f "
                    f"{LIVE_INDIVIDUAL_JOIN} WHERE f.delete_flag = 0 AND {condition}"
                )
                or 0
            )

        with_birth = documented("f.token IN ('BIRT','CHR','BAPM') AND f.sorted_date <> 999999999")
        with_death = documented("f.token IN ('DEAT','BURI') AND f.sorted_date <> 999999999")
        with_place = documented("f.place_id IS NOT NULL")
        cited = db.scalar(
            "SELECT COUNT(DISTINCT t.entity_id) FROM citation_main_data c "
            "JOIN token_on_item t ON t.token_on_item_id = c.external_token_on_item_id "
            "JOIN individual_main_data owner "
            "  ON owner.individual_id = t.entity_id AND owner.delete_flag = 0 "
            "WHERE c.delete_flag = 0 AND t.item_type = :itype",
            {"itype": ITEM_TYPE_INDIVIDUAL},
        )

        def pct(part: int) -> float:
            return round(100.0 * part / total, 1) if total else 0.0

        out["completeness"] = {
            "individuals": total,
            "with_known_birth_date": {"count": with_birth, "percent": pct(with_birth)},
            "with_known_death_date": {"count": with_death, "percent": pct(with_death)},
            "with_any_place": {"count": with_place, "percent": pct(with_place)},
            "with_source_citation": {"count": cited, "percent": pct(cited)},
        }

    if "facts" in metrics:
        out["fact_frequency"] = {
            fact_label(row["token"], row["fact_type"]): row["n"]
            for row in db.query(
                f"SELECT f.token, f.fact_type, COUNT(*) n FROM individual_fact_main_data f "
                f"{LIVE_INDIVIDUAL_JOIN} WHERE f.delete_flag = 0 "
                f"GROUP BY f.token, f.fact_type ORDER BY n DESC"
            )
        }

    return out


FACT_TAGS = sorted(FACT_LABELS)
