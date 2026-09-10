"""Tests du schema et des migrations initiales."""

import pytest

from research_agent.exceptions import DatabaseError
from research_agent.storage.database import Database
from research_agent.storage.migrations import run_migrations


def _tables(conn) -> set:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table';"
    ).fetchall()
    return {r["name"] for r in rows}


def test_initialize_creates_all_tables(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        tables = _tables(conn)
    assert {"items", "summaries", "runs", "schema_version"} <= tables


def test_schema_version_recorded(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        version = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version;"
        ).fetchone()["v"]
    assert version == 3


def test_migrations_are_idempotent(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    # Relancer les migrations ne doit rien casser ni re-enregistrer la version.
    with db.connection() as conn:
        final = run_migrations(conn)
        count = conn.execute("SELECT COUNT(*) AS n FROM schema_version;").fetchone()["n"]
    # Trois migrations appliquees (v1 + v2 + v3), version finale = 3.
    assert final == 3
    assert count == 3


def test_unique_url_prevents_duplicates(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO items (id, source, title, url) VALUES (?,?,?,?);",
            ("id1", "aedes", "T1", "https://example.org/a"),
        )
    # Meme URL avec un id different -> viole l'index unique sur url.
    # Le context manager encapsule l'IntegrityError dans DatabaseError.
    with pytest.raises(DatabaseError):
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO items (id, source, title, url) VALUES (?,?,?,?);",
                ("id2", "ted", "T2", "https://example.org/a"),
            )


def test_primary_key_prevents_duplicate_id(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO items (id, source, title, url) VALUES (?,?,?,?);",
            ("same", "aedes", "T1", "https://example.org/a"),
        )
    with pytest.raises(DatabaseError):
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO items (id, source, title, url) VALUES (?,?,?,?);",
                ("same", "ted", "T2", "https://example.org/b"),
            )


def test_relevance_check_constraint(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with pytest.raises(DatabaseError):
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO items (id, source, title, url, relevance) "
                "VALUES (?,?,?,?,?);",
                ("id1", "aedes", "T", "https://example.org/a", 1.5),
            )


def test_summary_foreign_key_cascade(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO items (id, source, title, url) VALUES (?,?,?,?);",
            ("item1", "openalex", "Paper", "https://example.org/p"),
        )
        conn.execute(
            "INSERT INTO summaries (item_id, summary) VALUES (?,?);",
            ("item1", "resume en 3 lignes"),
        )
        conn.execute("DELETE FROM items WHERE id = 'item1';")
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM summaries;"
        ).fetchone()["n"]
    assert remaining == 0  # cascade a supprime le resume lie


def test_runs_defaults(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO runs (task_type) VALUES ('collect');")
        row = conn.execute(
            "SELECT status, items_collected FROM runs;"
        ).fetchone()
    assert row["status"] == "running"
    assert row["items_collected"] == 0


def test_indexes_created(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index';"
        ).fetchall()
        names = {r["name"] for r in rows}
    assert "idx_items_url" in names
    assert "idx_items_theme_status" in names
    assert "idx_runs_started_at" in names
