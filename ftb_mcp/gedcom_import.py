"""Load a GEDCOM file into an in-memory database shaped like an .ftb file.

The FTB schema is this server's internal representation, so a GEDCOM file is imported
into a database of that shape rather than given its own query layer. Everything in
:mod:`ftb_mcp.queries` and :mod:`ftb_mcp.graph` -- language ranking, fact shaping,
pedigrees, statistics -- then works against either source unchanged.

Only the columns those modules actually read are created; the FTB tables carry many
more (photo file ids, sync timestamps, privacy flags) that no query touches.

Parsing is delegated to ged4py, which matters for two reasons visible in
kafkova.ged:

* MyHeritage splits ``CONC`` records mid-character, leaving a multi-byte UTF-8
  sequence straddling a line break. ged4py concatenates ``CONC`` values as bytes and
  decodes only once a record is complete, so those characters survive. Parsers that
  decode line by line raise UnicodeDecodeError on this file.
* ged4py reads the ``CHAR`` header and the byte-order mark, so ANSEL and UTF-16 files
  work without special handling here.

What ged4py cannot accept is a value containing a bare newline, which MyHeritage also
emits: the continuation arrives as a line with no level number at all. Those lines are
repaired by :func:`repair_bare_newlines`, applied only after a first parse attempt has
failed so that well-formed files are never rewritten.
"""

from __future__ import annotations

import io
import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ged4py.date import DateValue, DateValueTypes
from ged4py.model import Record
from ged4py.parser import GedcomReader, IntegrityError, ParserError

from .db import FtbDatabase, FtbDatabaseError
from .decode import UNKNOWN_DATE
from .schema import (
    FACT_LABELS,
    ITEM_TYPE_FAMILY_FACT,
    ITEM_TYPE_INDIVIDUAL,
    ITEM_TYPE_INDIVIDUAL_FACT,
    PEDIGREE_ROLES,
    ROLE_HUSBAND,
    ROLE_NATURAL_CHILD,
    ROLE_WIFE,
    SUPPORTED_DB_MAJOR_VERSION,
    register_language,
)

log = logging.getLogger(__name__)

# FTB's marker for "no lower bound", i.e. a BEF date. Mirrors decode._OPEN_LOWER_BOUND.
OPEN_LOWER_BOUND = -99999999

# A GEDCOM line: level, optional xref, tag. Matched as bytes so the pre-pass never has
# to decode text in an encoding it has not established yet.
_GEDCOM_LINE = re.compile(rb"^(\d+)\s+(?:@[^@]+@\s+)?([A-Za-z0-9_]+)")

_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")

# INDI/FAM sub-tags that carry structure rather than an event, so a tag outside both
# this set and FACT_LABELS is data this importer is dropping and worth reporting.
_STRUCTURAL_TAGS = frozenset(
    # Kept as one string because a list literal reformats to one tag per line.
    "ADDR ALIA ANCI ASSO CHAN CHIL DESI FAMC FAMS HUSB NAME NCHI NOTE OBJE REFN "  # noqa: SIM905
    "RESN RFN RIN SEX SLGC SLGS SOUR SUBM WIFE".split()
)

# Address parts folded into a fact's detail line, in the order GEDCOM defines them.
_ADDRESS_TAGS = ("ADR1", "ADR2", "ADR3", "CITY", "STAE", "POST", "CTRY")

# GEDCOM tags whose presence means the person is dead.
_DEATH_TAGS = ("DEAT", "BURI", "CREM")

SCHEMA_SQL = """
CREATE TABLE project_parameters (
    project_parameter_id INTEGER PRIMARY KEY,
    category TEXT, name TEXT, value TEXT);

CREATE TABLE individual_main_data (
    individual_id INTEGER PRIMARY KEY,
    gender TEXT, is_alive INTEGER, guid TEXT, delete_flag INTEGER DEFAULT 0);

CREATE TABLE individual_data_set (
    individual_data_set_id INTEGER PRIMARY KEY,
    individual_id INTEGER, delete_flag INTEGER DEFAULT 0);

CREATE TABLE individual_lang_data (
    individual_lang_data_id INTEGER PRIMARY KEY,
    individual_data_set_id INTEGER, data_language INTEGER,
    first_name TEXT, last_name TEXT, prefix TEXT, suffix TEXT, nickname TEXT,
    religious_name TEXT, former_name TEXT, married_surname TEXT,
    alias_name TEXT, aka TEXT);

CREATE TABLE individual_fact_main_data (
    individual_fact_id INTEGER PRIMARY KEY,
    individual_id INTEGER, token TEXT, fact_type TEXT, age TEXT,
    date TEXT, sorted_date INTEGER,
    lower_bound_search_date INTEGER, upper_bound_search_date INTEGER,
    is_current INTEGER DEFAULT 1, place_id INTEGER, delete_flag INTEGER DEFAULT 0);

CREATE TABLE individual_fact_lang_data (
    individual_fact_lang_id INTEGER PRIMARY KEY,
    individual_fact_id INTEGER, data_language INTEGER,
    header TEXT, cause_of_death TEXT);

CREATE TABLE family_main_data (
    family_id INTEGER PRIMARY KEY,
    status INTEGER, guid TEXT, delete_flag INTEGER DEFAULT 0);

CREATE TABLE family_individual_connection (
    family_individual_connection_id INTEGER PRIMARY KEY,
    family_id INTEGER, individual_id INTEGER,
    individual_role_type INTEGER, child_order_in_family INTEGER,
    delete_flag INTEGER DEFAULT 0);

CREATE TABLE family_fact_main_data (
    family_fact_id INTEGER PRIMARY KEY,
    family_id INTEGER, token TEXT, fact_type TEXT, spouse_age TEXT,
    date TEXT, sorted_date INTEGER,
    lower_bound_search_date INTEGER, upper_bound_search_date INTEGER,
    is_current INTEGER DEFAULT 1, place_id INTEGER, delete_flag INTEGER DEFAULT 0);

CREATE TABLE family_fact_lang_data (
    family_fact_lang_id INTEGER PRIMARY KEY,
    family_fact_id INTEGER, data_language INTEGER, header TEXT);

CREATE TABLE places_main_data (place_id INTEGER PRIMARY KEY);

CREATE TABLE places_lang_data (
    place_lang_data_id INTEGER PRIMARY KEY,
    place_id INTEGER, data_language INTEGER, place TEXT);

CREATE TABLE token_on_item (
    token_on_item_id INTEGER PRIMARY KEY, entity_id INTEGER, item_type INTEGER);

CREATE TABLE note_main_data (
    note_id INTEGER PRIMARY KEY,
    guid TEXT, special_note_key TEXT, delete_flag INTEGER DEFAULT 0);

CREATE TABLE note_lang_data (
    note_lang_data_id INTEGER PRIMARY KEY,
    note_id INTEGER, data_language INTEGER, note_text TEXT);

CREATE TABLE note_to_item_connection (
    note_to_item_connection_id INTEGER PRIMARY KEY,
    note_id INTEGER, external_token_on_item_id INTEGER, delete_flag INTEGER DEFAULT 0);

CREATE TABLE source_main_data (
    source_id INTEGER PRIMARY KEY, delete_flag INTEGER DEFAULT 0);

CREATE TABLE source_lang_data (
    source_lang_data_id INTEGER PRIMARY KEY,
    source_id INTEGER, data_language INTEGER,
    title TEXT, abbreviation TEXT, author TEXT, publisher TEXT, agency TEXT,
    text TEXT, type TEXT, media TEXT);

CREATE TABLE citation_main_data (
    citation_id INTEGER PRIMARY KEY,
    source_id INTEGER, page TEXT, confidence INTEGER,
    event_type TEXT, event_role TEXT,
    external_token_on_item_id INTEGER, delete_flag INTEGER DEFAULT 0);

CREATE TABLE citation_lang_data (
    citation_lang_data_id INTEGER PRIMARY KEY,
    citation_id INTEGER, data_language INTEGER, description TEXT);

CREATE TABLE media_item_main_data (
    media_item_id INTEGER PRIMARY KEY,
    place_id INTEGER, date TEXT, sorted_date INTEGER,
    lower_bound_search_date INTEGER, upper_bound_search_date INTEGER,
    delete_flag INTEGER DEFAULT 0);

CREATE TABLE media_item_lang_data (
    media_item_lang_data_id INTEGER PRIMARY KEY,
    media_item_id INTEGER, data_language INTEGER, title TEXT, description TEXT);

CREATE TABLE media_item_to_item_connection (
    media_item_to_item_connection_id INTEGER PRIMARY KEY,
    media_item_id INTEGER, external_token_on_item_id INTEGER,
    delete_flag INTEGER DEFAULT 0);

CREATE INDEX ix_fact_individual ON individual_fact_main_data (individual_id);
CREATE INDEX ix_fact_lang ON individual_fact_lang_data (individual_fact_id);
CREATE INDEX ix_family_fact ON family_fact_main_data (family_id);
CREATE INDEX ix_connection_family ON family_individual_connection (family_id);
CREATE INDEX ix_connection_individual ON family_individual_connection (individual_id);
CREATE INDEX ix_data_set_individual ON individual_data_set (individual_id);
CREATE INDEX ix_token_entity ON token_on_item (item_type, entity_id);
CREATE INDEX ix_note_item ON note_to_item_connection (note_id);
CREATE INDEX ix_citation_token ON citation_main_data (external_token_on_item_id);
CREATE INDEX ix_place_lang ON places_lang_data (place_id);
"""


class GedcomImportError(FtbDatabaseError):
    """Raised when a GEDCOM file cannot be read at all."""


# ged4py raises IntegrityError for bad level nesting, which is a sibling of ParserError
# rather than a subclass, so both have to be named wherever a parse is attempted.
GEDCOM_ERRORS = (ParserError, IntegrityError)


# --------------------------------------------------------------------------- pre-pass


def repair_bare_newlines(data: bytes) -> tuple[bytes, int]:
    """Convert value-internal bare newlines into proper ``CONT`` lines.

    GEDCOM forbids a raw newline inside a value; a multi-line note must be split into
    ``CONT`` records. MyHeritage emits the raw newline anyway, which leaves a line with
    no level number and stops any conforming parser. Such a line is re-tagged as a
    ``CONT`` continuing the record above it, which is what it was meant to be, so the
    line break is preserved in the text rather than dropped.

    Returns the repaired bytes and how many lines were rewritten. Operates purely on
    bytes: every GEDCOM encoding has ASCII digits and spaces as a single-byte subset,
    so the level and tag can be matched without knowing the encoding.
    """
    bom = next((b for b in _BOMS if data.startswith(b)), b"")
    body = data[len(bom) :]

    out: list[bytes] = []
    level, tag = 0, ""
    repaired = 0
    for raw_line in body.split(b"\n"):
        line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
        match = _GEDCOM_LINE.match(line)
        if match:
            level = int(match.group(1))
            tag = match.group(2).decode("ascii", "replace").upper()
        elif line.strip():
            # A continuation of the value above: keep CONT chains at their own level,
            # otherwise nest one level below the record being continued.
            level = level if tag in ("CONT", "CONC") else level + 1
            tag = "CONT"
            line = b"%d CONT %s" % (level, line)
            repaired += 1
        out.append(line)

    return bom + b"\r\n".join(out), repaired


def _open_reader(path: Path) -> tuple[GedcomReader, io.BytesIO | None]:
    """Open a GEDCOM file, repairing bare newlines only if the file needs it."""
    reader: GedcomReader | None = None
    try:
        reader = GedcomReader(str(path))
        # Reading the header forces the record index to be built, which is where a
        # malformed line surfaces. Doing it here keeps the fallback in one place.
        _ = reader.header
        return reader, None
    except GEDCOM_ERRORS as exc:
        log.debug("%s needs repair before parsing: %s", path.name, exc)
        first_error: Exception = exc
    except OSError as exc:
        raise GedcomImportError(f"Cannot read {path}: {exc}") from exc

    if reader is not None:
        # Release the file handle the failed attempt opened; the retry reads the bytes
        # itself. GedcomReader exposes no close(), only the context-manager exit.
        reader.__exit__(None, None, None)

    repaired, count = repair_bare_newlines(path.read_bytes())
    if not count:
        raise GedcomImportError(f"Cannot parse {path}: {first_error}") from first_error

    log.warning(
        "%s has %d value(s) containing a raw newline, which GEDCOM does not allow; "
        "treating each as a CONT continuation",
        path.name,
        count,
    )
    stream = io.BytesIO(repaired)
    try:
        reader = GedcomReader(stream)
        _ = reader.header
    except (*GEDCOM_ERRORS, OSError) as exc:
        stream.close()
        raise GedcomImportError(f"Cannot parse {path}: {exc}") from exc
    return reader, stream


# ------------------------------------------------------------------------------ dates


def _calendar_to_int(date: Any) -> int:
    """Encode a ged4py calendar date as FTB's YYYYMMDD integer, negative for B.C."""
    year = abs(getattr(date, "year", 0) or 0)
    month = getattr(date, "month_num", None) or 0
    day = getattr(date, "day", None) or 0
    value = year * 10000 + month * 100 + day
    return -value if getattr(date, "bc", False) else value


def encode_date(value: DateValue | str | None) -> tuple[str, int, int, int]:
    """Turn a GEDCOM date into FTB's ``(display, sorted, lower, upper)`` quadruple.

    FTB stores a display string alongside three integers that carry the range
    semantics, and offsets ``sorted_date`` by one so an open-ended date sorts just
    outside its own bound -- "BEF 1856" sorts at 18559999, "AFT 1904" at 19040001.
    Those conventions are reproduced here so a fact reads the same whichever file it
    came from.

    Unparseable dates keep their text and report no bounds, which is how FTB stores a
    date phrase too.
    """
    unknown = ("", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE)
    if value is None:
        return unknown
    if not isinstance(value, DateValue):
        text = str(value).strip()
        if not text:
            return unknown
        value = DateValue.parse(text)

    kind = value.kind
    display = str(value)

    if kind is DateValueTypes.PHRASE:
        phrase = getattr(value, "phrase", None)
        return (phrase or "", UNKNOWN_DATE, UNKNOWN_DATE, UNKNOWN_DATE)

    if kind in (DateValueTypes.RANGE, DateValueTypes.PERIOD):
        lower = _calendar_to_int(value.date1)
        upper = _calendar_to_int(value.date2)
        return display, lower + 1, lower, upper

    if kind in (DateValueTypes.BEFORE, DateValueTypes.TO):
        upper = _calendar_to_int(value.date)
        return display, upper - 1, OPEN_LOWER_BOUND, upper

    if kind in (DateValueTypes.AFTER, DateValueTypes.FROM):
        lower = _calendar_to_int(value.date)
        return display, lower + 1, lower, UNKNOWN_DATE

    # SIMPLE, ABOUT, CALCULATED, ESTIMATED, INTERPRETED: one date, no range.
    exact = _calendar_to_int(value.date)
    return display, exact, exact, exact


# --------------------------------------------------------------------------- importer


def _text(record: Record | None) -> str:
    """A record's value as stripped text, tolerating ged4py's parsed value types."""
    if record is None:
        return ""
    value = record.value
    if value is None:
        return ""
    if isinstance(value, tuple):
        return " ".join(str(part) for part in value if part).strip()
    return str(value).strip()


def _child_text(record: Record, tag: str) -> str:
    return _text(record.sub_tag(tag, follow=False))


class _Importer:
    """Walks GEDCOM records and writes FTB-shaped rows."""

    def __init__(self, reader: GedcomReader, conn: sqlite3.Connection) -> None:
        self.reader = reader
        self.conn = conn
        self.lang = 0
        self.header: dict[str, str] = {}

        self.individuals: dict[str, int] = {}
        self.families: dict[str, int] = {}
        self.sources: dict[str, int] = {}
        self.media: dict[str, int] = {}
        self.places: dict[str, int] = {}
        # (child xref, family xref) -> role, from FAMC.PEDI on the child's record.
        self.pedigree: dict[tuple[str, str], int] = {}
        # FAMC/FAMS links as the individuals declare them, used to recover
        # relationships the family record fails to mirror.
        self.child_links: list[tuple[str, str]] = []
        self.spouse_links: list[tuple[str, str]] = []
        self.genders: dict[str, str] = {}
        self.connected: set[tuple[int, int]] = set()
        self.defined_families: set[int] = set()
        self.skipped_tags: Counter[str] = Counter()

        self._next_id: Counter[str] = Counter()

    # ------------------------------------------------------------------- id handling

    def _id(self, kind: str) -> int:
        self._next_id[kind] += 1
        return self._next_id[kind]

    def _entity_id(self, table: dict[str, int], xref: str | None, kind: str) -> int | None:
        """Map a GEDCOM xref to an integer id, allocating on first sight.

        Records may be referenced before they are read -- an individual cites a source
        defined later in the file -- so ids are handed out on demand and the record's
        own row is filled in when it is reached.
        """
        if not xref:
            return None
        if xref not in table:
            table[xref] = self._id(kind)
        return table[xref]

    # ------------------------------------------------------------------------ insert

    def _insert(self, table: str, **columns: Any) -> None:
        names = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)
        self.conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(columns.values()))

    def _token(self, entity_id: int, item_type: int) -> int:
        """Create a token_on_item row, FTB's polymorphic reference to any entity."""
        token_id = self._id("token")
        self._insert(
            "token_on_item", token_on_item_id=token_id, entity_id=entity_id, item_type=item_type
        )
        return token_id

    def _place_id(self, place: str) -> int | None:
        """Intern a place name, mirroring FTB's separate place tables."""
        name = place.strip()
        if not name:
            return None
        if name not in self.places:
            place_id = self._id("place")
            self.places[name] = place_id
            self._insert("places_main_data", place_id=place_id)
            self._insert(
                "places_lang_data",
                place_lang_data_id=self._id("place_lang"),
                place_id=place_id,
                data_language=self.lang,
                place=name,
            )
        return self.places[name]

    # ------------------------------------------------------------------------- header

    def _import_header(self) -> None:
        header = self.reader.header
        values: dict[str, str] = {}
        if header is not None:
            source = header.sub_tag("SOUR", follow=False)

            def of(record: Record | None, tag: str) -> str:
                return _child_text(record, tag) if record is not None else ""

            values = {
                "Source": _text(source),
                "ProductName": of(source, "NAME"),
                "Version": of(source, "VERS"),
                "CorporateName": of(source, "CORP"),
                "Destination": _text(header.sub_tag("DEST", follow=False)),
                "Data": _text(header.sub_tag("DATE", follow=False)),
                "GedcomVersion": _text(header.sub_tag("GEDC/VERS", follow=False)),
                "GedcomFormat": _text(header.sub_tag("GEDC/FORM", follow=False)),
                "CharacterSet": _text(header.sub_tag("CHAR", follow=False)),
                "Language": _text(header.sub_tag("LANG", follow=False)),
                # Not stored as "File": tree_info reads that as the tree's name, and a
                # GEDCOM's HEAD.FILE is an export description ("Exported by ... on
                # Sun, 02 Aug 2026"), not a name. The filename is used instead.
                "ExportDescription": _text(header.sub_tag("FILE", follow=False)),
            }

        self.lang = register_language(values.get("Language"))
        self.header = {name: value for name, value in values.items() if value}

        for name, value in self.header.items():
            self._insert(
                "project_parameters",
                project_parameter_id=self._id("param"),
                category="Header",
                name=name,
                value=value,
            )

        # project_languages is a protobuf message whose field 1 holds one byte per
        # language; FtbDatabase.project_languages decodes exactly this shape. Written
        # as a BLOB so a language number above 0x7F stays byte-exact.
        for name, value in (
            ("project_languages", bytes([0x0A, 1, self.lang])),
            ("db_major_version", str(SUPPORTED_DB_MAJOR_VERSION)),
            ("db_minor_version", "0"),
        ):
            self._insert(
                "project_parameters",
                project_parameter_id=self._id("param"),
                category="Project",
                name=name,
                value=value,
            )

    # -------------------------------------------------------------------- individuals

    def _import_individuals(self) -> None:
        for record in self.reader.records0("INDI"):
            individual_id = self._entity_id(self.individuals, record.xref_id, "individual")
            if individual_id is None:
                continue

            deceased = any(record.sub_tag(tag, follow=False) for tag in _DEATH_TAGS)
            gender = (_child_text(record, "SEX") or "U")[:1].upper()
            self.genders[record.xref_id or ""] = gender
            self._insert(
                "individual_main_data",
                individual_id=individual_id,
                gender=gender,
                is_alive=2 if deceased else 3,
                guid=_child_text(record, "_UID") or None,
            )
            self._import_names(individual_id, record)

            for link in record.sub_tags("FAMC", follow=False):
                target = _text(link)
                if not target:
                    continue
                pedi = _child_text(link, "PEDI").lower()
                if pedi:
                    self.pedigree[(record.xref_id or "", target)] = PEDIGREE_ROLES.get(
                        pedi, ROLE_NATURAL_CHILD
                    )
                self.child_links.append((record.xref_id or "", target))
                self._entity_id(self.families, target, "family")
            for link in record.sub_tags("FAMS", follow=False):
                target = _text(link)
                if not target:
                    continue
                self.spouse_links.append((record.xref_id or "", target))
                self._entity_id(self.families, target, "family")

            token = self._token(individual_id, ITEM_TYPE_INDIVIDUAL)
            self._import_notes(token, record)
            self._import_citations(token, record)
            self._import_media_links(token, record)
            self._import_facts(individual_id, record)

    def _import_names(self, individual_id: int, record: Record) -> None:
        """Collapse a person's NAME records into the single row FTB keeps per language.

        FTB stores one name row with a column per variant, so the primary NAME fills
        the main columns and typed variants (AKA, married, former) fill their own.
        """
        names = record.sub_tags("NAME", follow=False)
        primary: Record | None = None
        variants: dict[str, str] = {}

        for name in names:
            kind = _child_text(name, "TYPE").lower()
            if not kind and primary is None:
                primary = name
                continue
            text = _text(name)
            if not text:
                continue
            if kind in ("aka", "nickname"):
                variants.setdefault("aka", text)
            elif kind in ("married", "marriedname"):
                variants.setdefault("married_surname", text)
            elif kind in ("birth", "maiden", "former"):
                variants.setdefault("former_name", text)
            elif kind in ("religious", "religiousname"):
                variants.setdefault("religious_name", text)
            else:
                variants.setdefault("alias_name", text)

        if primary is None and names:
            primary = names[0]

        def part(tag: str) -> str:
            return _child_text(primary, tag) if primary is not None else ""

        first, last, suffix = part("GIVN"), part("SURN"), part("NSFX")
        if primary is not None and not (first or last):
            # No GIVN/SURN sub-records: fall back to the "Given /Surname/ Suffix" form
            # of the NAME value, which ged4py has already split into a tuple.
            value = primary.value
            parts = value if isinstance(value, tuple) else (str(value or ""), "", "")
            padded = (*parts, "", "")
            first = (padded[0] or "").strip()
            last = (padded[1] or "").strip()
            suffix = suffix or (padded[2] or "").strip()

        data_set_id = self._id("data_set")
        self._insert(
            "individual_data_set", individual_data_set_id=data_set_id, individual_id=individual_id
        )
        self._insert(
            "individual_lang_data",
            individual_lang_data_id=self._id("name_lang"),
            individual_data_set_id=data_set_id,
            data_language=self.lang,
            first_name=first,
            last_name=last,
            prefix=part("NPFX"),
            suffix=suffix,
            nickname=part("NICK"),
            married_surname=variants.get("married_surname", part("_MARNM")),
            religious_name=variants.get("religious_name", ""),
            former_name=variants.get("former_name", ""),
            alias_name=variants.get("alias_name", ""),
            aka=variants.get("aka", ""),
        )

    # -------------------------------------------------------------------------- facts

    def _import_facts(self, individual_id: int, record: Record) -> None:
        for sub in record.sub_records:
            tag = sub.tag.upper()
            if tag not in FACT_LABELS:
                if tag not in _STRUCTURAL_TAGS and not tag.startswith("_"):
                    self.skipped_tags[tag] += 1
                continue

            fact_id = self._id("fact")
            display, sorted_date, lower, upper = encode_date(
                sub.sub_tag_value("DATE", follow=False)
            )
            detail, fact_type = self._fact_detail(sub, tag)
            self._insert(
                "individual_fact_main_data",
                individual_fact_id=fact_id,
                individual_id=individual_id,
                token=tag,
                fact_type=fact_type,
                age=_child_text(sub, "AGE"),
                date=display,
                sorted_date=sorted_date,
                lower_bound_search_date=lower,
                upper_bound_search_date=upper,
                is_current=1,
                place_id=self._place_id(_child_text(sub, "PLAC")),
            )
            self._insert(
                "individual_fact_lang_data",
                individual_fact_lang_id=self._id("fact_lang"),
                individual_fact_id=fact_id,
                data_language=self.lang,
                header=detail,
                cause_of_death=_child_text(sub, "CAUS"),
            )

            token = self._token(fact_id, ITEM_TYPE_INDIVIDUAL_FACT)
            self._import_notes(token, sub)
            self._import_citations(token, sub)

    def _fact_detail(self, fact: Record, tag: str) -> tuple[str, str]:
        """The fact's free-text detail and FTB's fact_type discriminator.

        FTB records a sub-tag name in fact_type when the detail describes a *field* of
        the event rather than the event itself -- a residence whose detail is an
        address is stored as token RESI, fact_type ADDR -- and the custom event name
        for EVEN. Everything else leaves fact_type empty, as FTB does.
        """
        if tag == "EVEN":
            return _text(fact), _child_text(fact, "TYPE")

        address = fact.sub_tag("ADDR", follow=False)
        if address is not None:
            lines = [_text(address)]
            lines += [_child_text(address, part) for part in _ADDRESS_TAGS]
            joined = ", ".join(line for line in lines if line)
            if joined:
                return joined, "ADDR"

        for field in ("EMAIL", "PHON", "FAX", "WWW"):
            value = _child_text(fact, field)
            if value:
                return value, field

        # "Y" only asserts that the event happened; it is not a detail worth showing.
        detail = _text(fact)
        return ("" if detail.upper() == "Y" else detail), ""

    # ------------------------------------------------------------------------ notes

    def _import_notes(self, token_id: int, record: Record) -> None:
        for note in record.sub_tags("NOTE", follow=True):
            text = _text(note)
            if not text:
                continue
            note_id = self._id("note")
            self._insert(
                "note_main_data",
                note_id=note_id,
                guid=_child_text(note, "_UID") or None,
                special_note_key=None,
            )
            self._insert(
                "note_lang_data",
                note_lang_data_id=self._id("note_lang"),
                note_id=note_id,
                data_language=self.lang,
                note_text=text,
            )
            self._insert(
                "note_to_item_connection",
                note_to_item_connection_id=self._id("note_link"),
                note_id=note_id,
                external_token_on_item_id=token_id,
            )

    # -------------------------------------------------------------------- citations

    def _source_ref(self, source: Record) -> int | None:
        """Resolve a citation's SOUR value, which may be a pointer or inline text.

        GEDCOM allows a citation to name its source inline instead of pointing at a
        record. FTB has no such form, so inline text becomes a source record with that
        text as its title, deduplicated so a repeated citation reuses one source.
        """
        value = _text(source)
        if not value:
            return None
        if value.startswith("@") and value.endswith("@"):
            return self._entity_id(self.sources, value, "source")

        key = f"inline:{value}"
        if key not in self.sources:
            source_id = self._entity_id(self.sources, key, "source")
            self._insert("source_main_data", source_id=source_id)
            self._insert(
                "source_lang_data",
                source_lang_data_id=self._id("source_lang"),
                source_id=source_id,
                data_language=self.lang,
                title=value,
            )
        return self.sources[key]

    def _import_citations(self, token_id: int, record: Record) -> None:
        for source in record.sub_tags("SOUR", follow=False):
            source_id = self._source_ref(source)
            citation_id = self._id("citation")
            quay = _child_text(source, "QUAY")
            event = source.sub_tag("EVEN", follow=False)
            self._insert(
                "citation_main_data",
                citation_id=citation_id,
                source_id=source_id,
                page=_child_text(source, "PAGE"),
                # FTB uses -1 for "unstated"; queries.citations_for hides negatives.
                confidence=int(quay) if quay.isdigit() else -1,
                event_type=_text(event),
                event_role=_child_text(event, "ROLE") if event else "",
                external_token_on_item_id=token_id,
            )
            description = _text(source.sub_tag("DATA/TEXT", follow=False)) or _text(
                source.sub_tag("TEXT", follow=False)
            )
            self._insert(
                "citation_lang_data",
                citation_lang_data_id=self._id("citation_lang"),
                citation_id=citation_id,
                data_language=self.lang,
                description=description,
            )

    def _import_sources(self) -> None:
        for record in self.reader.records0("SOUR"):
            source_id = self._entity_id(self.sources, record.xref_id, "source")
            if source_id is None:
                continue
            self._insert("source_main_data", source_id=source_id)
            self._insert(
                "source_lang_data",
                source_lang_data_id=self._id("source_lang"),
                source_id=source_id,
                data_language=self.lang,
                title=_child_text(record, "TITL"),
                abbreviation=_child_text(record, "ABBR"),
                author=_child_text(record, "AUTH"),
                publisher=_child_text(record, "PUBL"),
                agency=_child_text(record, "AGNC"),
                text=_child_text(record, "TEXT"),
                type=_child_text(record, "_TYPE"),
                media=_child_text(record, "_MEDI"),
            )

        # A citation may point at a source the file never defines; give those a row so
        # search_sources still reports the id rather than dropping the citation.
        defined = {row[0] for row in self.conn.execute("SELECT source_id FROM source_main_data")}
        for source_id in sorted(set(self.sources.values()) - defined):
            self._insert("source_main_data", source_id=source_id)

    # ----------------------------------------------------------------------- media

    def _import_media(self) -> None:
        for record in self.reader.records0("OBJE"):
            media_id = self._entity_id(self.media, record.xref_id, "media")
            if media_id is not None:
                self._insert_media(media_id, record)

    def _insert_media(self, media_id: int, record: Record) -> None:
        """Write a media item's textual metadata.

        ``FILE`` is deliberately never read: the media tools expose descriptions only,
        never a path or the bytes behind one.
        """
        display, sorted_date, lower, upper = encode_date(record.sub_tag_value("DATE", follow=False))
        self._insert(
            "media_item_main_data",
            media_item_id=media_id,
            place_id=self._place_id(_child_text(record, "PLAC")),
            date=display,
            sorted_date=sorted_date,
            lower_bound_search_date=lower,
            upper_bound_search_date=upper,
        )
        self._insert(
            "media_item_lang_data",
            media_item_lang_data_id=self._id("media_lang"),
            media_item_id=media_id,
            data_language=self.lang,
            title=_child_text(record, "TITL"),
            description=_child_text(record, "NOTE"),
        )

    def _import_media_links(self, token_id: int, record: Record) -> None:
        for index, obje in enumerate(record.sub_tags("OBJE", follow=False)):
            value = _text(obje)
            if value.startswith("@") and value.endswith("@"):
                media_id = self._entity_id(self.media, value, "media")
            else:
                # An inline OBJE carries its own metadata instead of pointing at a
                # record. Keyed by the owning token so two inline items never merge.
                key = f"inline:{token_id}:{index}"
                media_id = self._entity_id(self.media, key, "media")
                if media_id is not None:
                    self._insert_media(media_id, obje)
            if media_id is None:
                continue
            self._insert(
                "media_item_to_item_connection",
                media_item_to_item_connection_id=self._id("media_link"),
                media_item_id=media_id,
                external_token_on_item_id=token_id,
            )

    # --------------------------------------------------------------------- families

    def _import_families(self) -> None:
        for record in self.reader.records0("FAM"):
            family_id = self._entity_id(self.families, record.xref_id, "family")
            if family_id is None:
                continue

            self.defined_families.add(family_id)
            self._insert(
                "family_main_data",
                family_id=family_id,
                status=self._family_status(record),
                guid=_child_text(record, "_UID") or None,
            )

            for tag, role in (("HUSB", ROLE_HUSBAND), ("WIFE", ROLE_WIFE)):
                for spouse in record.sub_tags(tag, follow=False):
                    self._connect(family_id, _text(spouse), role, -1)

            for order, child in enumerate(record.sub_tags("CHIL", follow=False)):
                xref = _text(child)
                role = self.pedigree.get((xref, record.xref_id or ""), ROLE_NATURAL_CHILD)
                self._connect(family_id, xref, role, order)

            self._import_family_facts(family_id, record)

    def _import_unmirrored_links(self) -> None:
        """Add memberships only the individual's own FAMC/FAMS records assert.

        GEDCOM states each membership twice -- once on the family as CHIL/HUSB/WIFE,
        once on the individual as FAMC/FAMS -- and a file can carry only one side.
        kafkova.ged does exactly that for two children whose FAMC also names them as
        adopted and fostered; reading only the family side would drop both
        relationships. Order is left unset (-1), since a family that never listed the
        child never ordered them either.
        """
        added = 0
        for child_xref, family_xref in self.child_links:
            role = self.pedigree.get((child_xref, family_xref), ROLE_NATURAL_CHILD)
            added += self._connect_late(family_xref, child_xref, role)

        for individual_xref, family_xref in self.spouse_links:
            gender = self.genders.get(individual_xref, "U")
            role = ROLE_WIFE if gender == "F" else ROLE_HUSBAND
            added += self._connect_late(family_xref, individual_xref, role)

        if added:
            log.info(
                "Added %d family membership(s) that only the individual's own record declared",
                added,
            )

    def _connect_late(self, family_xref: str, individual_xref: str, role: int) -> int:
        family_id = self.families.get(family_xref)
        individual_id = self.individuals.get(individual_xref)
        if family_id is None or individual_id is None:
            return 0
        if (family_id, individual_id) in self.connected:
            return 0
        if family_id not in self.defined_families:
            # The file points at a family it never defines. Give it a row so the
            # membership resolves instead of dangling.
            self.defined_families.add(family_id)
            self._insert("family_main_data", family_id=family_id, status=0, guid=None)
        self._connect(family_id, individual_xref, role, -1)
        return 1

    def _connect(self, family_id: int, xref: str, role: int, order: int) -> None:
        individual_id = self.individuals.get(xref)
        if individual_id is None:
            # A pointer to an individual the file never defines. FTB has no way to
            # represent that, and TreeIndex would drop it anyway.
            log.debug("Family %d references unknown individual %s", family_id, xref)
            return
        if (family_id, individual_id) in self.connected:
            return
        self.connected.add((family_id, individual_id))
        self._insert(
            "family_individual_connection",
            family_individual_connection_id=self._id("connection"),
            family_id=family_id,
            individual_id=individual_id,
            individual_role_type=role,
            child_order_in_family=order,
        )

    @staticmethod
    def _family_status(record: Record) -> int:
        """FTB's family status, using the same evidence its own files show.

        In kafkova.ftb every status 5 family carries a DIV fact and every status 3 a
        MARR fact, so a divorce outranks the marriage that preceded it.
        """
        tags = {sub.tag.upper() for sub in record.sub_records}
        types = {_child_text(sub, "TYPE").upper() for sub in record.sub_tags("EVEN", follow=False)}
        if "DIV" in tags or "ANUL" in tags:
            return 5
        if "MARR" in tags:
            return 3
        if "DEATH OF SPOUSE" in types:
            return 8
        if any(t.endswith("REL_UNKNOWN") or t.endswith("REL_PARTNERS") for t in types):
            return 9
        if "ENGA" in tags:
            return 1
        return 0

    def _import_family_facts(self, family_id: int, record: Record) -> None:
        for sub in record.sub_records:
            tag = sub.tag.upper()
            if tag not in FACT_LABELS:
                if tag not in _STRUCTURAL_TAGS and not tag.startswith("_"):
                    self.skipped_tags[tag] += 1
                continue

            fact_id = self._id("family_fact")
            display, sorted_date, lower, upper = encode_date(
                sub.sub_tag_value("DATE", follow=False)
            )
            detail, fact_type = self._fact_detail(sub, tag)
            self._insert(
                "family_fact_main_data",
                family_fact_id=fact_id,
                family_id=family_id,
                token=tag,
                fact_type=fact_type,
                spouse_age=_child_text(sub, "AGE"),
                date=display,
                sorted_date=sorted_date,
                lower_bound_search_date=lower,
                upper_bound_search_date=upper,
                is_current=1,
                place_id=self._place_id(_child_text(sub, "PLAC")),
            )
            self._insert(
                "family_fact_lang_data",
                family_fact_lang_id=self._id("family_fact_lang"),
                family_fact_id=fact_id,
                data_language=self.lang,
                header=detail,
            )

            token = self._token(fact_id, ITEM_TYPE_FAMILY_FACT)
            self._import_notes(token, sub)
            self._import_citations(token, sub)

    # ------------------------------------------------------------------------- run

    def run(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self._import_header()
        # Individuals first: families and citations resolve pointers against the maps
        # this pass fills in.
        self._import_individuals()
        self._import_families()
        self._import_unmirrored_links()
        self._import_sources()
        self._import_media()
        self.conn.commit()

        if self.skipped_tags:
            log.warning(
                "Ignored %d record(s) with tags this importer does not map: %s",
                sum(self.skipped_tags.values()),
                ", ".join(f"{tag} x{n}" for tag, n in self.skipped_tags.most_common(10)),
            )


def open_gedcom(path: str | Path) -> FtbDatabase:
    """Read a GEDCOM file and return it as an :class:`~ftb_mcp.db.FtbDatabase`.

    The result is backed by an in-memory database, so nothing is written to disk and
    the source file is only ever read.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise GedcomImportError(f"No such GEDCOM file: {source}")

    reader, stream = _open_reader(source)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        with reader:
            _Importer(reader, conn).run()
        return FtbDatabase(source, connection=conn)
    except (*GEDCOM_ERRORS, OSError, sqlite3.Error) as exc:
        conn.close()
        raise GedcomImportError(f"Cannot import {source}: {exc}") from exc
    except BaseException:
        # Anything else is a bug here, but the connection still has to go.
        conn.close()
        raise
    finally:
        if stream is not None:
            stream.close()
