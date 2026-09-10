"""Tests du DAO items : CRUD + deduplication."""

from datetime import datetime

import pytest

from research_agent.models import RawItem, Theme
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus


def _make_db(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    return db


def _item(**kwargs):
    base = dict(source="openalex", title="Titre", url="https://example.org/a")
    base.update(kwargs)
    return RawItem(**base)


# --- Create + deduplication --------------------------------------------------


def test_insert_returns_true_then_stored(tmp_path):
    db = _make_db(tmp_path)
    item = _item()
    assert dao.insert_item(item, db) is True
    assert dao.exists(item.id, db) is True


def test_duplicate_same_id_ignored(tmp_path):
    db = _make_db(tmp_path)
    item = _item()
    assert dao.insert_item(item, db) is True
    # Meme item (meme id) -> ignore, pas d'erreur.
    assert dao.insert_item(item, db) is False


def test_duplicate_same_url_different_id_ignored(tmp_path):
    db = _make_db(tmp_path)
    a = _item(id="idA", url="https://example.org/same")
    b = _item(id="idB", url="https://example.org/same", title="Autre titre")
    assert dao.insert_item(a, db) is True
    # Meme URL -> l'index UNIQUE bloque, INSERT OR IGNORE l'ignore sans erreur.
    assert dao.insert_item(b, db) is False
    # Un seul enregistrement pour cette URL.
    assert dao.get_by_url("https://example.org/same", db)["id"] == "idA"


def test_dedup_by_doi(tmp_path):
    db = _make_db(tmp_path)
    # Deux items avec le meme DOI mais des URLs differentes -> meme id -> dedup.
    a = _item(url="https://a.org/1", metadata={"doi": "10.1/x"})
    b = _item(url="https://b.org/2", metadata={"doi": "10.1/x"})
    assert a.id == b.id
    assert dao.insert_item(a, db) is True
    assert dao.insert_item(b, db) is False


def test_insert_items_counts_only_new(tmp_path):
    db = _make_db(tmp_path)
    items = [
        _item(url="https://example.org/1"),
        _item(url="https://example.org/2"),
        _item(url="https://example.org/1"),  # doublon
    ]
    assert dao.insert_items(items, db) == 2


# --- Read --------------------------------------------------------------------


def test_get_item_deserializes_metadata(tmp_path):
    db = _make_db(tmp_path)
    item = _item(
        published_at=datetime(2026, 9, 7),
        metadata={"doi": "10.1/x", "authors": ["A", "B"]},
    )
    dao.insert_item(item, db)
    stored = dao.get_item(item.id, db)
    assert stored["metadata"]["authors"] == ["A", "B"]
    assert stored["status"] == "collected"


def test_get_item_missing_returns_none(tmp_path):
    db = _make_db(tmp_path)
    assert dao.get_item("nope", db) is None


def test_list_by_source_and_status(tmp_path):
    db = _make_db(tmp_path)
    dao.insert_item(_item(source="openalex", url="https://e.org/1"), db)
    dao.insert_item(_item(source="ted", url="https://e.org/2"), db)
    openalex = dao.list_items(db, source="openalex")
    assert len(openalex) == 1
    assert openalex[0]["source"] == "openalex"
    collected = dao.list_items(db, status=ItemStatus.COLLECTED)
    assert len(collected) == 2


# --- Update ------------------------------------------------------------------


def test_update_status(tmp_path):
    db = _make_db(tmp_path)
    item = _item()
    dao.insert_item(item, db)
    assert dao.update_status(item.id, ItemStatus.FILTERED, db) is True
    assert dao.get_item(item.id, db)["status"] == "filtered"


def test_update_relevance_with_theme_and_status(tmp_path):
    db = _make_db(tmp_path)
    item = _item()
    dao.insert_item(item, db)
    ok = dao.update_relevance(
        item.id, 0.87, db, theme=Theme.RESEARCH, status=ItemStatus.KEPT
    )
    assert ok is True
    stored = dao.get_item(item.id, db)
    assert stored["relevance"] == 0.87
    assert stored["theme"] == "research"
    assert stored["status"] == "kept"


def test_update_relevance_out_of_range_raises(tmp_path):
    db = _make_db(tmp_path)
    item = _item()
    dao.insert_item(item, db)
    with pytest.raises(ValueError):
        dao.update_relevance(item.id, 1.5, db)


# --- Delete ------------------------------------------------------------------


def test_delete_item(tmp_path):
    db = _make_db(tmp_path)
    item = _item()
    dao.insert_item(item, db)
    assert dao.delete_item(item.id, db) is True
    assert dao.get_item(item.id, db) is None
    assert dao.delete_item(item.id, db) is False  # deja supprime
