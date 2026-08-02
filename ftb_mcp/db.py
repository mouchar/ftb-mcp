"""Read-only SQLite access to an .ftb file."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .schema import SUPPORTED_DB_MAJOR_VERSION

log = logging.getLogger(__name__)

REQUIRED_TABLES = frozenset(
    {
        "individual_main_data",
        "individual_data_set",
        "individual_lang_data",
        "family_main_data",
        "family_individual_connection",
        "individual_fact_main_data",
    }
)


class FtbDatabaseError(RuntimeError):
    """Raised when the file cannot be opened or is not a Family Tree Builder file."""


class FtbDatabase:
    """A read-only connection to a Family Tree Builder database.

    A file is opened with SQLite's ``mode=ro`` URI so it cannot be modified even by a
    bug in this server. Text columns are decoded with ``surrogateescape`` because
    several nominally-TEXT columns hold protobuf bytes that are not valid UTF-8; the
    original bytes stay recoverable via :func:`ftb_mcp.decode.to_bytes`.

    Passing ``connection`` adopts an already-open database instead of opening a file.
    :mod:`ftb_mcp.gedcom_import` uses that to serve a GEDCOM file it has loaded into
    an in-memory database of this same shape, so every query in
    :mod:`ftb_mcp.queries` works against either source.
    """

    def __init__(self, path: str | Path, connection: sqlite3.Connection | None = None) -> None:
        self.path = Path(path).expanduser()

        if connection is None:
            self.path = self.path.resolve()
            if not self.path.is_file():
                raise FtbDatabaseError(f"No such .ftb file: {self.path}")
            try:
                connection = sqlite3.connect(
                    f"file:{self.path}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                )
            except sqlite3.Error as exc:
                raise FtbDatabaseError(f"Cannot open {self.path}: {exc}") from exc

        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._conn.text_factory = lambda raw: raw.decode("utf-8", "surrogateescape")

        self._verify()

    def _verify(self) -> None:
        """Confirm this really is an FTB file and warn on unexpected versions."""
        try:
            present = {
                row["name"]
                for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        except sqlite3.DatabaseError as exc:
            raise FtbDatabaseError(f"{self.path} is not a readable SQLite database: {exc}") from exc

        missing = REQUIRED_TABLES - present
        if missing:
            raise FtbDatabaseError(
                f"{self.path} does not look like a Family Tree Builder file "
                f"(missing tables: {', '.join(sorted(missing))})"
            )

        major = self.parameter("db_major_version")
        if major and major.isdigit() and int(major) != SUPPORTED_DB_MAJOR_VERSION:
            log.warning(
                "%s has db_major_version %s; this server was built against version %s. "
                "Column meanings may differ.",
                self.path.name,
                major,
                SUPPORTED_DB_MAJOR_VERSION,
            )

    # ------------------------------------------------------------------ query helpers

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        """Run a parameterised SELECT and return all rows."""
        return list(self._conn.execute(sql, params))

    def query_one(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = ()
    ) -> sqlite3.Row | None:
        """Run a parameterised SELECT and return the first row, or None."""
        return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        """Run a parameterised SELECT and return the first column of the first row."""
        row = self.query_one(sql, params)
        return row[0] if row is not None else None

    def count(self, table: str) -> int:
        """Count live (non-soft-deleted) rows in a table."""
        if not table.isidentifier():
            raise ValueError(f"Unsafe table name: {table!r}")
        where = " WHERE delete_flag = 0" if self.has_column(table, "delete_flag") else ""
        return int(self.scalar(f"SELECT COUNT(*) FROM {table}{where}") or 0)

    def has_column(self, table: str, column: str) -> bool:
        """True when a table exposes the named column."""
        if not table.isidentifier():
            raise ValueError(f"Unsafe table name: {table!r}")
        return any(row["name"] == column for row in self.query(f"PRAGMA table_info({table})"))

    def parameter(self, name: str, category: str | None = None) -> str | None:
        """Read a single value out of project_parameters."""
        sql = "SELECT value FROM project_parameters WHERE name = ?"
        params: list[Any] = [name]
        if category:
            sql += " AND category = ?"
            params.append(category)
        return self.scalar(sql, params)

    def project_languages(self) -> list[int]:
        """Language codes declared by the project, decoded from its protobuf blob."""
        from .decode import pb_fields, to_bytes

        raw = to_bytes(self.parameter("project_languages"))
        if not raw:
            return []
        try:
            for value in pb_fields(raw).get(1, []):
                if isinstance(value, bytes):
                    return sorted(value)
        except ValueError:
            log.warning("Could not decode project_languages; falling back to defaults")
        return []

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> FtbDatabase:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def placeholders(values: Iterable[Any]) -> str:
    """Build a ``?, ?, ?`` placeholder list for an IN clause."""
    return ", ".join("?" for _ in values)
