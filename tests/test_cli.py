"""Entry point and file-opening behaviour.

The transport is never actually served: ``mcp.run`` is replaced so ``main`` returns after
it has resolved arguments, opened the tree and logged what it loaded.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3

import pytest

from ftb_mcp import server
from ftb_mcp.db import FtbDatabase, FtbDatabaseError
from tests.conftest import GEDCOM_SAMPLE, SAMPLE


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    """Stop main() short of serving, and record how it would have served."""
    calls: list[dict] = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.append(kwargs))
    yield calls
    if server.state.db is not None:
        server.state.db.close()
        server.state.db = None
    server.state._indexes.clear()


class TestArguments:
    def test_no_source_is_an_error(self, capsys, monkeypatch):
        monkeypatch.delenv("FTB_DB_PATH", raising=False)
        monkeypatch.delenv("FTB_GEDCOM_PATH", raising=False)
        assert server.main([]) == 2
        assert "one of --db-path or --gedcom-path is required" in capsys.readouterr().err

    def test_two_sources_are_an_error(self, capsys):
        assert server.main(["--db-path", str(SAMPLE), "--gedcom-path", str(GEDCOM_SAMPLE)]) == 2
        assert "not both" in capsys.readouterr().err

    def test_missing_ftb_file_is_reported(self, capsys, tmp_path):
        assert server.main(["--db-path", str(tmp_path / "absent.ftb")]) == 1
        assert "No such .ftb file" in capsys.readouterr().err

    def test_missing_gedcom_file_is_reported(self, capsys, tmp_path):
        assert server.main(["--gedcom-path", str(tmp_path / "absent.ged")]) == 1
        assert "No such GEDCOM file" in capsys.readouterr().err

    def test_env_var_supplies_the_path(self, monkeypatch, no_transport):
        monkeypatch.setenv("FTB_DB_PATH", str(SAMPLE))
        assert server.main([]) == 0
        assert len(server.state.index(server.state.default_lang).people) == 15


class TestSourceSelection:
    def test_ftb_path_opens_the_sqlite_file(self, no_transport):
        assert server.main(["--db-path", str(SAMPLE)]) == 0
        assert server.state.db.path.name == "sample.ftb"
        assert len(server.state.index(server.state.default_lang).people) == 15

    def test_gedcom_path_uses_the_importer(self, no_transport):
        assert server.main(["--gedcom-path", str(GEDCOM_SAMPLE)]) == 0
        assert server.state.db.path.name == "sample.ged"
        assert len(server.state.index(server.state.default_lang).people) == 14

    def test_gedcom_extension_is_detected_on_db_path(self, no_transport):
        assert server.main(["--db-path", str(GEDCOM_SAMPLE)]) == 0
        assert len(server.state.index(server.state.default_lang).people) == 14

    def test_gedcom_path_wins_even_without_the_extension(self, tmp_path, no_transport):
        renamed = tmp_path / "tree.txt"
        renamed.write_bytes(GEDCOM_SAMPLE.read_bytes())
        assert server.main(["--gedcom-path", str(renamed)]) == 0
        assert len(server.state.index(server.state.default_lang).people) == 14

    def test_language_option_selects_the_indexed_language(self, no_transport):
        assert server.main(["--db-path", str(SAMPLE), "--language", "en"]) == 0
        assert server.state.default_lang == 0
        index = server.state.index(server.state.default_lang)
        assert index.people[1].first_name == "Simon"


class TestTransport:
    def test_streamable_http_is_the_default(self, no_transport):
        assert server.main(["--db-path", str(SAMPLE), "--port", "9999", "--path", "/x"]) == 0
        assert no_transport == [
            {
                "transport": "streamable-http",
                "host": "127.0.0.1",
                "port": 9999,
                "streamable_http_path": "/x",
            }
        ]

    def test_stdio_takes_no_host_or_port(self, no_transport):
        assert server.main(["--db-path", str(SAMPLE), "--transport", "stdio"]) == 0
        assert no_transport == [{"transport": "stdio"}]

    def test_sse_is_served_on_host_and_port(self, no_transport):
        assert server.main(["--db-path", str(SAMPLE), "--transport", "sse", "--port", "8081"]) == 0
        assert no_transport == [{"transport": "sse", "host": "127.0.0.1", "port": 8081}]


class TestDatabaseValidation:
    def test_a_file_that_is_not_sqlite_is_rejected(self, tmp_path):
        path = tmp_path / "notes.ftb"
        path.write_text("just some text")
        with pytest.raises(FtbDatabaseError, match="not a readable SQLite database"):
            FtbDatabase(path)

    def test_a_sqlite_file_without_ftb_tables_is_rejected(self, tmp_path):
        path = tmp_path / "other.ftb"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with pytest.raises(FtbDatabaseError, match="does not look like a Family Tree Builder"):
            FtbDatabase(path)

    def test_missing_tables_are_named(self, tmp_path):
        path = tmp_path / "partial.ftb"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE individual_main_data (individual_id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with pytest.raises(FtbDatabaseError, match="family_main_data"):
            FtbDatabase(path)

    def test_an_unexpected_major_version_warns_but_still_opens(self, tmp_path, caplog):
        """A newer FTB release may move columns, so the mismatch is worth saying out loud."""
        path = tmp_path / "future.ftb"
        shutil.copy(SAMPLE, path)

        conn = sqlite3.connect(path)
        conn.execute("UPDATE project_parameters SET value = '2' WHERE name = 'db_major_version'")
        conn.commit()
        conn.close()

        with caplog.at_level(logging.WARNING), FtbDatabase(path) as database:
            assert database.count("individual_main_data") == 15
        assert "db_major_version 2" in caplog.text

    def test_a_directory_is_not_a_file(self, tmp_path):
        with pytest.raises(FtbDatabaseError, match="No such .ftb file"):
            FtbDatabase(tmp_path)

    def test_unsafe_table_names_are_refused(self, db):
        for method in (db.count, lambda t: db.has_column(t, "id")):
            with pytest.raises(ValueError, match="Unsafe table name"):
                method("individual_main_data; DROP TABLE x")

    def test_parameter_lookup_can_be_scoped_by_category(self, db):
        assert db.parameter("db_minor_version") == "7"
        assert db.parameter("GedcomFormat", category="Header") == "FTBDB"
        assert db.parameter("GedcomFormat", category="Project") is None
        assert db.parameter("nonexistent") is None

    def test_has_column_reports_what_the_schema_declares(self, db):
        assert db.has_column("individual_main_data", "delete_flag") is True
        assert db.has_column("places_main_data", "delete_flag") is False
