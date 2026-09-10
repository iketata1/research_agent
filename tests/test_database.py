"""Tests de la connexion SQLite et du module d'acces aux donnees."""

import pytest

from research_agent.config import DatabaseConfig
from research_agent.exceptions import DatabaseError
from research_agent.storage.database import Database, initialize_database


def test_initialize_creates_dir_and_file(tmp_path):
    db_path = tmp_path / "nested" / "kb.db"
    db = Database(db_path)
    db.initialize()
    assert db_path.exists()
    assert db_path.parent.is_dir()


def test_connection_executes_query(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        row = conn.execute("SELECT 1 AS value;").fetchone()
        assert row["value"] == 1


def test_connection_commits_on_success(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")
        conn.execute("INSERT INTO t (v) VALUES ('a');")
    # Nouvelle connexion : la donnee doit avoir ete validee (commit).
    with db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM t;").fetchone()["n"]
        assert count == 1


def test_connection_rolls_back_on_error(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")
        conn.execute("INSERT INTO t (v) VALUES ('committed');")

    # Une erreur SQL dans le bloc doit declencher un rollback + DatabaseError.
    with pytest.raises(DatabaseError):
        with db.connection() as conn:
            conn.execute("INSERT INTO t (v) VALUES ('will-rollback');")
            conn.execute("INSERT INTO nonexistent_table VALUES (1);")

    # La table d'origine ne doit contenir que la ligne validee precedemment.
    with db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM t;").fetchone()["n"]
        assert count == 1


def test_wal_mode_enabled(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"


def test_from_config_resolves_relative_path(tmp_path):
    cfg = DatabaseConfig(path=str(tmp_path / "kb.db"))
    db = initialize_database(cfg)
    assert db.path.exists()
