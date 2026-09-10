"""Tests de la recherche plein texte FTS5."""

import pytest

from research_agent.storage.database import Database
from research_agent.storage.search import build_fts_query, search_items


def _insert(db, id_, title, url, raw_text=""):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO items (id, source, title, url, raw_text) "
            "VALUES (?,?,?,?,?);",
            (id_, "test", title, url, raw_text),
        )


def _make_db(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    return db


def test_fts_table_and_triggers_exist(tmp_path):
    db = _make_db(tmp_path)
    with db.connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            )
        }
        triggers = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger';"
            )
        }
    assert "items_fts" in tables
    assert {"items_ai", "items_ad", "items_au"} <= triggers


def test_build_fts_query_escapes_terms():
    assert build_fts_query("mould housing") == '"mould" "housing"'
    # Les caracteres speciaux FTS5 sont neutralises (pas d'erreur de syntaxe).
    assert build_fts_query('mould AND (housing)') == '"mould" "AND" "housing"'
    assert build_fts_query("   ") == ""


def test_search_finds_inserted_item(tmp_path):
    db = _make_db(tmp_path)
    _insert(
        db, "i1", "Improved mould-prediction model", "https://e.org/1",
        raw_text="A study about humidity in social housing.",
    )
    results = search_items("mould", db=db)
    assert len(results) == 1
    assert results[0]["id"] == "i1"


def test_search_matches_raw_text(tmp_path):
    db = _make_db(tmp_path)
    _insert(db, "i1", "Titre neutre", "https://e.org/1", raw_text="ventilation systems")
    results = search_items("ventilation", db=db)
    assert len(results) == 1


def test_search_ranks_by_relevance(tmp_path):
    db = _make_db(tmp_path)
    # i1 mentionne 'mould' dans le titre ET le texte ; i2 une seule fois.
    _insert(db, "i1", "mould mould report", "https://e.org/1", raw_text="mould everywhere")
    _insert(db, "i2", "housing report", "https://e.org/2", raw_text="a bit of mould")
    results = search_items("mould", db=db)
    ids = [r["id"] for r in results]
    assert ids[0] == "i1"  # plus pertinent en tete


def test_trigger_keeps_index_in_sync_on_delete(tmp_path):
    db = _make_db(tmp_path)
    _insert(db, "i1", "mould report", "https://e.org/1")
    assert len(search_items("mould", db=db)) == 1
    with db.connection() as conn:
        conn.execute("DELETE FROM items WHERE id='i1';")
    assert search_items("mould", db=db) == []


def test_trigger_keeps_index_in_sync_on_update(tmp_path):
    db = _make_db(tmp_path)
    _insert(db, "i1", "humidity report", "https://e.org/1")
    with db.connection() as conn:
        conn.execute("UPDATE items SET title='ventilation report' WHERE id='i1';")
    # L'ancien terme ne matche plus, le nouveau oui.
    assert search_items("humidity", db=db) == []
    assert len(search_items("ventilation", db=db)) == 1


def test_empty_query_returns_empty(tmp_path):
    db = _make_db(tmp_path)
    _insert(db, "i1", "mould report", "https://e.org/1")
    assert search_items("   ", db=db) == []
