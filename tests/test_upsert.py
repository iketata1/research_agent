"""Tests de l'upsert par hash de contenu (insertion / inchange / mise a jour)."""

from research_agent.models import RawItem, Theme
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus, UpsertResult


def _make_db(tmp_path):
    db = Database(tmp_path / "kb.db")
    db.initialize()
    return db


def _item(title="Titre", raw_text="contenu", url="https://example.org/a", **kw):
    return RawItem(source="tenderned", title=title, raw_text=raw_text, url=url, **kw)


def test_upsert_new_inserts(tmp_path):
    db = _make_db(tmp_path)
    assert dao.upsert_item(_item(), db) is UpsertResult.INSERTED
    assert dao.get_item(_item().id, db) is not None


def test_upsert_same_content_unchanged(tmp_path):
    db = _make_db(tmp_path)
    dao.upsert_item(_item(), db)
    # Meme URL, meme contenu -> vrai doublon.
    assert dao.upsert_item(_item(), db) is UpsertResult.UNCHANGED


def test_upsert_changed_content_updates(tmp_path):
    db = _make_db(tmp_path)
    dao.upsert_item(_item(raw_text="budget 100k"), db)
    # Meme URL, contenu different (budget mis a jour) -> rafraichi.
    result = dao.upsert_item(_item(raw_text="budget 250k"), db)
    assert result is UpsertResult.UPDATED
    stored = dao.get_item(_item().id, db)
    assert stored["raw_text"] == "budget 250k"
    assert stored["updated_at"] is not None


def test_upsert_update_resets_status_to_collected(tmp_path):
    db = _make_db(tmp_path)
    item = _item(raw_text="v1")
    dao.upsert_item(item, db)
    # L'item avait ete juge et garde par le LLM.
    dao.update_relevance(item.id, 0.9, db, theme=Theme.OPPORTUNITY, status=ItemStatus.KEPT)
    # Le contenu change -> statut repasse a collected pour re-filtrage.
    dao.upsert_item(_item(raw_text="v2 modifie"), db)
    stored = dao.get_item(item.id, db)
    assert stored["status"] == "collected"


def test_upsert_update_preserves_llm_fields(tmp_path):
    db = _make_db(tmp_path)
    item = _item(raw_text="v1")
    dao.upsert_item(item, db)
    dao.update_relevance(item.id, 0.75, db, theme=Theme.LEGAL, status=ItemStatus.KEPT)
    # Mise a jour du contenu : relevance et theme deja calcules sont conserves.
    dao.upsert_item(_item(raw_text="v2"), db)
    stored = dao.get_item(item.id, db)
    assert stored["relevance"] == 0.75
    assert stored["theme"] == "legal"


def test_upsert_items_counts_by_result(tmp_path):
    db = _make_db(tmp_path)
    dao.upsert_item(_item(url="https://e.org/1", raw_text="a"), db)
    counts = dao.upsert_items(
        [
            _item(url="https://e.org/1", raw_text="a"),      # unchanged
            _item(url="https://e.org/1", raw_text="a-modifie"),  # updated
            _item(url="https://e.org/2", raw_text="b"),      # inserted
        ],
        db,
    )
    assert counts["unchanged"] == 1
    assert counts["updated"] == 1
    assert counts["inserted"] == 1
