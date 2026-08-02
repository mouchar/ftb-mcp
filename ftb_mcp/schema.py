"""Decoded FTB enumerations and constants.

FTB stores everything as bare integers with no lookup tables in the file. The mappings
below were derived by correlating values against independent evidence in kafkova.ftb --
role types against gender, family status against co-occurring marriage/divorce facts --
rather than by guessing from the terse schema comments.

Values that do not occur in the sample file are marked UNVERIFIED. Unknown values are
rendered as "unknown (N)" so a different .ftb file degrades legibly instead of lying.
"""

from __future__ import annotations

# Supported db_major_version. A different major version may move columns around.
SUPPORTED_DB_MAJOR_VERSION = 1

# individual_main_data.gender
GENDER = {"M": "male", "F": "female", "U": "unknown"}

# individual_main_data.is_alive -- despite the name this is not a boolean.
# Verified: 1655 rows = 2 (deceased, all with DEAT facts), 209 rows = 3.
LIVING_STATUS = {2: "deceased", 3: "living"}

# token_on_item.item_type -- the discriminator for polymorphic references.
# Verified by joining each entity table back through token_on_item_id.
ITEM_TYPE_INDIVIDUAL = 1
ITEM_TYPE_FAMILY = 2
ITEM_TYPE_INDIVIDUAL_FACT = 3
ITEM_TYPE_FAMILY_FACT = 4

ITEM_TYPE = {
    ITEM_TYPE_INDIVIDUAL: "individual",
    ITEM_TYPE_FAMILY: "family",
    ITEM_TYPE_INDIVIDUAL_FACT: "individual_fact",
    ITEM_TYPE_FAMILY_FACT: "family_fact",
}

# family_individual_connection.individual_role_type
# Verified against gender: every role 2 is male (540), every role 3 is female (532).
ROLE_HUSBAND = 2
ROLE_WIFE = 3
ROLE_NATURAL_CHILD = 5
ROLE_FOSTER_CHILD = 6
ROLE_ADOPTED_CHILD = 7

ROLE_TYPE = {
    ROLE_HUSBAND: "husband",
    ROLE_WIFE: "wife",
    ROLE_NATURAL_CHILD: "natural child",
    ROLE_FOSTER_CHILD: "foster child",
    ROLE_ADOPTED_CHILD: "adopted child",
}

SPOUSE_ROLES = (ROLE_HUSBAND, ROLE_WIFE)
CHILD_ROLES = (ROLE_NATURAL_CHILD, ROLE_FOSTER_CHILD, ROLE_ADOPTED_CHILD)
NATURAL_CHILD_ROLES = (ROLE_NATURAL_CHILD,)

# family_main_data.status
# Verified: every status 3 family carries a MARR fact, every status 5 a DIV fact,
# every status 8 a "Death of Spouse" event, every status 9 a REL_UNKNOWN event.
FAMILY_STATUS = {
    0: "unspecified",
    1: "engaged",  # UNVERIFIED - absent from kafkova.ftb
    2: "separated",  # UNVERIFIED - absent from kafkova.ftb
    3: "married",
    5: "divorced",
    8: "widowed",
    9: "life partners",
}

# project_parameters project_languages = [0, 20] for this file.
LANGUAGE_CODES = {0: "en", 20: "cs"}
LANGUAGE_NAMES = {0: "English", 20: "Czech"}
LANG_ENGLISH = 0
LANG_CZECH = 20

# Order in which to look for text when the requested language has no row.
DEFAULT_LANGUAGE_PREFERENCE = (LANG_CZECH, LANG_ENGLISH)

# ISO 639-1 codes for the language names GEDCOM's HEAD.LANG may carry. Only the two
# above have a known FTB number; the rest get an importer-assigned one (see
# register_language) so a Polish GEDCOM is not mislabelled as English.
GEDCOM_LANGUAGE_ISO = {
    "czech": "cs",
    "english": "en",
    "danish": "da",
    "dutch": "nl",
    "finnish": "fi",
    "french": "fr",
    "german": "de",
    "hebrew": "he",
    "hungarian": "hu",
    "italian": "it",
    "norwegian": "no",
    "polish": "pl",
    "portuguese": "pt",
    "romanian": "ro",
    "russian": "ru",
    "slovak": "sk",
    "spanish": "es",
    "swedish": "sv",
    "turkish": "tr",
    "ukrainian": "uk",
}

# Language numbers at or above this value were assigned by the GEDCOM importer, not
# read out of an FTB file. They exist only to give non-FTB languages a stable key.
# Kept under 256 because FTB declares every data_language column TINYINT UNSIGNED,
# and project_languages is a protobuf byte string.
SYNTHETIC_LANGUAGE_BASE = 200


def register_language(name: str | None) -> int:
    """Return a language number for a GEDCOM ``HEAD.LANG`` name.

    Reuses the real FTB number when one is known, so a Czech GEDCOM and a Czech .ftb
    file both report language 20. Anything else is assigned a synthetic number and
    registered in the lookup tables, so ``language_code`` and ``tree_info`` report the
    file's actual language rather than a plausible-looking wrong one.
    """
    if not name or not name.strip():
        return LANG_ENGLISH

    label = name.strip()
    lowered = label.lower()

    for number, known in LANGUAGE_NAMES.items():
        if known.lower() == lowered:
            return number

    number = SYNTHETIC_LANGUAGE_BASE + len(
        [n for n in LANGUAGE_CODES if n >= SYNTHETIC_LANGUAGE_BASE]
    )
    LANGUAGE_CODES[number] = GEDCOM_LANGUAGE_ISO.get(lowered, lowered)
    LANGUAGE_NAMES[number] = label
    return number


# GEDCOM FAMC.PEDI values, which say how a child joined the family. Absent PEDI means
# a natural child, which is why ROLE_NATURAL_CHILD is the default in the importer.
PEDIGREE_ROLES = {
    "birth": ROLE_NATURAL_CHILD,
    "natural": ROLE_NATURAL_CHILD,
    "adopted": ROLE_ADOPTED_CHILD,
    "foster": ROLE_FOSTER_CHILD,
}

# GEDCOM 5.5.1 tags observed in individual_fact_main_data.token, plus the custom
# EVEN subtypes MyHeritage writes into fact_type.
FACT_LABELS = {
    "BIRT": "Birth",
    "DEAT": "Death",
    "BURI": "Burial",
    "CHR": "Christening",
    "BAPM": "Baptism",
    "OCCU": "Occupation",
    "CENS": "Census",
    "RESI": "Residence",
    "EDUC": "Education",
    "RELI": "Religion",
    "IMMI": "Immigration",
    "EMIG": "Emigration",
    "NATI": "Nationality",
    "NATU": "Naturalization",
    "PROP": "Property",
    "DSCR": "Physical description",
    "TITL": "Title",
    "CAST": "Caste",
    "MARR": "Marriage",
    "DIV": "Divorce",
    "ENGA": "Engagement",
    "ANUL": "Annulment",
    "EVEN": "Event",
}

# Facts whose header column holds a protobuf address message rather than plain text.
PROTOBUF_HEADER_TOKENS = frozenset({"RESI"})

# GEDCOM sub-tags that FTB stores in fact_type even though they name a *field* of the
# fact, not the fact itself. A RESI fact with fact_type ADDR is still a Residence; it
# would be misleading to label it "ADDR". These are surfaced as `detail_kind` instead.
FIELD_QUALIFIER_TYPES = frozenset({"ADDR", "EMAIL", "PHON", "FAX", "WWW"})

# Tags that anchor a person in time, used for search filters and summaries.
BIRTH_TOKENS = ("BIRT", "CHR", "BAPM")
DEATH_TOKENS = ("DEAT", "BURI")


def label_for(mapping: dict, value, fallback: str = "unknown") -> str:
    """Look up an enum value, degrading to 'unknown (N)' for unmapped values."""
    if value is None:
        return fallback
    return mapping.get(value, f"{fallback} ({value})")


def fact_label(token: str | None, fact_type: str | None) -> str:
    """Human label for a fact.

    Custom events store their real name in fact_type ("Settlement", "Hobbies"), so that
    wins over the generic EVEN tag. MyHeritage also writes namespaced values such as
    ``MYHERITAGE:REL_PARTNERS``, which are tidied to a readable form. Field sub-tags
    like ADDR never win, since they describe a field of the fact rather than the event.
    """
    token = (token or "").strip()
    fact_type = (fact_type or "").strip()

    if fact_type in FIELD_QUALIFIER_TYPES and token in FACT_LABELS:
        return FACT_LABELS[token]

    if fact_type and fact_type != token:
        if ":" in fact_type:
            fact_type = fact_type.split(":", 1)[1]
        if fact_type in FACT_LABELS:
            return FACT_LABELS[fact_type]
        if fact_type.isupper() and "_" in fact_type:
            return fact_type.replace("_", " ").capitalize()
        return fact_type

    return FACT_LABELS.get(token, token or "Unknown")
