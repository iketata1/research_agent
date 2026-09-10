"""Tests de la persistance des enrichissements LLM (score, theme, statut)."""

import pytest

from research_agent.models import RawItem, Theme
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    yield database
    database.close()


def _insert(db, url="https://example.org/a", metadata=None):
    item = RawItem(
        source="openalex",
        title="Mould study",
        url=url,
        raw_text="humidity research",
        metadata=metadata or {},
    )
    dao.insert_item(item, db)
    return item


# --- Persistance de base -----------------------------------------------------


def test_save_enriched_item_persists_all_fields(db):
    item = _insert(db)
    ok = dao.save_enriched_item(
        item.id,
        db,
        score=82,
        theme=Theme.RESEARCH,
        status=ItemStatus.KEPT,
        justification="central pour le modele moisissure",
        theme_confidence=0.91,
    )
    assert ok is True

    stored = dao.get_item(item.id, db)
    # relevance normalisee a partir du score 0-100.
    assert stored["relevance"] == pytest.approx(0.82)
    assert stored["theme"] == "research"
    assert stored["status"] == "kept"
    assert stored["metadata"]["llm_score"] == 82
    assert stored["metadata"]["llm_justification"] == "central pour le modele moisissure"
    assert stored["metadata"]["theme_confidence"] == pytest.approx(0.91)


def test_save_enriched_item_persists_across_connection(db):
    """Les valeurs doivent etre committees et relisibles via une nouvelle lecture."""
    item = _insert(db)
    dao.save_enriched_item(item.id, db, score=40, theme=Theme.LEGAL, status=ItemStatus.DROPPED)
    # Nouvelle lecture (nouvelle requete) : la valeur a bien ete persistee.
    stored = dao.get_item(item.id, db)
    assert stored["status"] == "dropped"
    assert stored["theme"] == "legal"


# --- Tracabilite : fusion des metadonnees ------------------------------------


def test_existing_metadata_is_preserved(db):
    # Metadonnees issues de la collecte + pre-filtre.
    item = _insert(
        db,
        metadata={"doi": "10.1/x", "passed_prefilter": True},
    )
    dao.save_enriched_item(item.id, db, score=70, theme=Theme.RESEARCH, status=ItemStatus.KEPT)
    stored = dao.get_item(item.id, db)
    # L'enrichissement LLM n'ecrase pas la tracabilite anterieure.
    assert stored["metadata"]["doi"] == "10.1/x"
    assert stored["metadata"]["passed_prefilter"] is True
    assert stored["metadata"]["llm_score"] == 70


# --- Cas limites -------------------------------------------------------------


def test_save_enriched_item_missing_returns_false(db):
    assert dao.save_enriched_item(
        "inexistant", db, score=50, theme=Theme.RESEARCH, status=ItemStatus.KEPT
    ) is False


def test_score_out_of_range_raises(db):
    item = _insert(db)
    with pytest.raises(ValueError):
        dao.save_enriched_item(item.id, db, score=150, theme=Theme.RESEARCH, status=ItemStatus.KEPT)


def test_optional_fields_can_be_omitted(db):
    item = _insert(db)
    dao.save_enriched_item(item.id, db, score=55, theme=Theme.TECHNOLOGY, status=ItemStatus.KEPT)
    stored = dao.get_item(item.id, db)
    assert stored["metadata"]["llm_score"] == 55
    # justification / confidence non fournies -> absentes, sans erreur.
    assert "llm_justification" not in stored["metadata"]


def test_kept_and_dropped_status_flow(db):
    kept = _insert(db, url="https://e.org/keep")
    dropped = _insert(db, url="https://e.org/drop")
    dao.save_enriched_item(kept.id, db, score=90, theme=Theme.OPPORTUNITY, status=ItemStatus.KEPT)
    dao.save_enriched_item(dropped.id, db, score=5, theme=Theme.UNKNOWN, status=ItemStatus.DROPPED)

    kept_items = dao.list_items(db, status=ItemStatus.KEPT)
    dropped_items = dao.list_items(db, status=ItemStatus.DROPPED)
    assert [i["id"] for i in kept_items] == [kept.id]
    assert [i["id"] for i in dropped_items] == [dropped.id]
