"""Tests unitaires d'ensemble du module de stockage (base SQLite in-memory).

Ce fichier valide le module de stockage de bout en bout sur une base SQLite
EN MEMOIRE (":memory:"), afin de ne jamais toucher la vraie base du projet.
Il couvre : initialisation du schema/migrations, insertion + lecture par ID,
deduplication (id / url / DOI), recherche FTS5, et mises a jour (score/statut LLM).
"""

from datetime import datetime

import pytest

from research_agent.models import RawItem, Theme
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus, UpsertResult
from research_agent.storage.search import search_items


@pytest.fixture()
def db():
    """Base SQLite en memoire, initialisee (schema + migrations), puis fermee."""
    database = Database(":memory:")
    database.initialize()
    yield database
    database.close()


def _item(**kwargs):
    base = dict(
        source="openalex",
        title="Improved mould-prediction model",
        url="https://example.org/a",
        raw_text="humidity and ventilation in social housing",
    )
    base.update(kwargs)
    return RawItem(**base)


# --- Base en memoire ---------------------------------------------------------


def test_in_memory_database_persists_across_connections(db):
    """La base en memoire doit conserver les donnees entre deux connexions."""
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO items (id, source, title, url) VALUES ('x','s','t','https://e.org/x');"
        )
    with db.connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM items;").fetchone()["n"]
    assert n == 1


# --- Initialisation du schema et des migrations ------------------------------


def test_schema_and_migrations_applied(db):
    with db.connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }
        version = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version;"
        ).fetchone()["v"]
    assert {"items", "summaries", "runs", "items_fts"} <= tables
    assert version == 4  # v1 schema + v2 FTS5 + v3 content_hash + v4 metriques runs


# --- Insertion + recuperation par ID -----------------------------------------


def test_insert_and_get_by_id(db):
    item = _item(
        published_at=datetime(2026, 9, 7),
        metadata={"doi": "10.1/x", "authors": ["Univ. Tampere"]},
    )
    assert dao.insert_item(item, db) is True
    stored = dao.get_item(item.id, db)
    assert stored is not None
    assert stored["title"] == item.title
    assert stored["metadata"]["authors"] == ["Univ. Tampere"]


# --- Deduplication -----------------------------------------------------------


def test_dedup_same_id_no_error(db):
    item = _item()
    assert dao.insert_item(item, db) is True
    # Deuxieme insertion du meme item : ignoree, sans lever d'erreur.
    assert dao.insert_item(item, db) is False


def test_dedup_same_url_different_id(db):
    a = _item(id="A", url="https://example.org/same")
    b = _item(id="B", url="https://example.org/same", title="Autre")
    assert dao.insert_item(a, db) is True
    assert dao.insert_item(b, db) is False
    assert dao.get_by_url("https://example.org/same", db)["id"] == "A"


def test_dedup_same_doi(db):
    a = _item(url="https://a.org/1", metadata={"doi": "10.1/dup"})
    b = _item(url="https://b.org/2", metadata={"doi": "10.1/dup"})
    assert a.id == b.id  # meme DOI -> meme id
    assert dao.insert_item(a, db) is True
    assert dao.insert_item(b, db) is False


def test_upsert_updates_changed_content(db):
    dao.upsert_item(_item(raw_text="budget 100k"), db)
    result = dao.upsert_item(_item(raw_text="budget 250k"), db)
    assert result is UpsertResult.UPDATED
    assert dao.get_item(_item().id, db)["raw_text"] == "budget 250k"


# --- Recherche FTS5 ----------------------------------------------------------


def test_fts_search_returns_matching_item(db):
    dao.insert_item(_item(url="https://e.org/1"), db)  # mould / humidity / housing
    dao.insert_item(
        _item(id="road", url="https://e.org/2", title="Road works", raw_text="asphalt"),
        db,
    )
    results = search_items("mould housing", db=db)
    assert len(results) == 1
    assert results[0]["url"] == "https://e.org/1"


def test_fts_search_ranks_by_relevance(db):
    dao.insert_item(
        _item(id="hi", url="https://e.org/1", title="mould mould", raw_text="mould mould"),
        db,
    )
    dao.insert_item(
        _item(id="lo", url="https://e.org/2", title="report", raw_text="a bit of mould"),
        db,
    )
    ids = [r["id"] for r in search_items("mould", db=db)]
    assert ids[0] == "hi"


# --- Mises a jour (scores et statuts LLM) ------------------------------------


def test_update_status(db):
    item = _item()
    dao.insert_item(item, db)
    assert dao.update_status(item.id, ItemStatus.FILTERED, db) is True
    assert dao.get_item(item.id, db)["status"] == "filtered"


def test_update_relevance_theme_and_status(db):
    item = _item()
    dao.insert_item(item, db)
    dao.update_relevance(
        item.id, 0.92, db, theme=Theme.RESEARCH, status=ItemStatus.KEPT
    )
    stored = dao.get_item(item.id, db)
    assert stored["relevance"] == 0.92
    assert stored["theme"] == "research"
    assert stored["status"] == "kept"


def test_update_relevance_rejects_out_of_range(db):
    item = _item()
    dao.insert_item(item, db)
    with pytest.raises(ValueError):
        dao.update_relevance(item.id, 2.0, db)
