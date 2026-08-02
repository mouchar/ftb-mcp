"""Shared fixtures.

Tests run against the static files in ``tests/data``, built by
``python -m tests.make_fixtures``. The trees in the repository root (``kafkova.ftb``,
``kafkova.ged``) are live working files that gain people whenever the author records
one, so nothing here asserts anything about them; see ``test_live_files.py`` for the
opt-in checks that use them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ftb_mcp.db import FtbDatabase
from ftb_mcp.gedcom_import import open_gedcom
from ftb_mcp.graph import TreeIndex

DATA = Path(__file__).resolve().parent / "data"
SAMPLE = DATA / "sample.ftb"
GEDCOM_SAMPLE = DATA / "sample.ged"

# The live trees, used only by test_live_files.py.
ROOT = Path(__file__).resolve().parent.parent
LIVE_FTB = ROOT / "kafkova.ftb"
LIVE_GEDCOM = ROOT / "kafkova.ged"


@pytest.fixture(scope="session")
def db() -> FtbDatabase:
    with FtbDatabase(SAMPLE) as database:
        yield database


@pytest.fixture(scope="session")
def index(db: FtbDatabase) -> TreeIndex:
    return TreeIndex(db, lang=20)


@pytest.fixture(scope="session")
def gedcom_db() -> FtbDatabase:
    with open_gedcom(GEDCOM_SAMPLE) as database:
        yield database


@pytest.fixture(scope="session")
def gedcom_index(gedcom_db: FtbDatabase) -> TreeIndex:
    return TreeIndex(gedcom_db, lang=20)
