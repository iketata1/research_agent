"""Tests de l'agregation hebdomadaire (fenetre, seuil, groupement, tri)."""

import json
from datetime import datetime, timedelta

import pytest

from research_agent.models import Theme
from research_agent.reporting.aggregator import aggregate_week, WeeklyReportData
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus

NOW = datetime(2026, 9, 10, 12, 0, 0)


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    yield database
    database.close()


def _insert(
    db,
    item_id,
    theme,
    relevance,
    collected_at,
    status=ItemStatus.KEPT,
    llm_score=None,
    summary=None,
    url=None,
):
    metadata = {}
    if llm_score is not None:
        metadata["llm_score"] = llm_score
    if summary is not None:
        metadata["summary"] = summary
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO items (id, source, title, url, relevance, theme, status, "
            "metadata, collected_at) VALUES (?,?,?,?,?,?,?,?,?);",
            (
                item_id,
                "test",
                f"Titre {item_id}",
                url or f"https://example.org/{item_id}",
                relevance,
                theme.value,
                status.value,
                json.dumps(metadata),
                collected_at.isoformat(sep=" "),
            ),
        )


# --- Groupement par theme ----------------------------------------------------


def test_groups_by_theme(db):
    _insert(db, "r1", Theme.RESEARCH, 0.9, NOW)
    _insert(db, "o1", Theme.OPPORTUNITY, 0.8, NOW)
    _insert(db, "l1", Theme.LEGAL, 0.7, NOW)
    data = aggregate_week(db, threshold=0.5, now=NOW)
    assert set(data.groups.keys()) == {"research", "opportunity", "legal"}
    assert data.total == 3


def test_empty_groups_omitted(db):
    _insert(db, "r1", Theme.RESEARCH, 0.9, NOW)
    data = aggregate_week(db, threshold=0.5, now=NOW)
    # Aucun item opportunity/legal/technology -> ces cles absentes.
    assert list(data.groups.keys()) == ["research"]


# --- Tri par score -----------------------------------------------------------


def test_sorted_by_score_descending(db):
    _insert(db, "low", Theme.RESEARCH, 0.6, NOW, llm_score=60)
    _insert(db, "high", Theme.RESEARCH, 0.95, NOW, llm_score=95)
    _insert(db, "mid", Theme.RESEARCH, 0.8, NOW, llm_score=80)
    data = aggregate_week(db, threshold=0.5, now=NOW)
    ids = [it.id for it in data.groups["research"]]
    assert ids == ["high", "mid", "low"]


def test_score_derived_from_relevance_when_no_llm_score(db):
    _insert(db, "r1", Theme.RESEARCH, 0.73, NOW)  # pas de llm_score en metadata
    data = aggregate_week(db, threshold=0.5, now=NOW)
    assert data.groups["research"][0].score == 73


# --- Filtrage par seuil ------------------------------------------------------


def test_threshold_excludes_low_score(db):
    _insert(db, "keep", Theme.RESEARCH, 0.8, NOW)
    _insert(db, "drop", Theme.RESEARCH, 0.3, NOW)  # sous le seuil
    data = aggregate_week(db, threshold=0.5, now=NOW)
    ids = [it.id for it in data.groups["research"]]
    assert ids == ["keep"]


def test_dropped_status_excluded(db):
    _insert(db, "kept", Theme.RESEARCH, 0.9, NOW, status=ItemStatus.KEPT)
    _insert(db, "dropped", Theme.RESEARCH, 0.9, NOW, status=ItemStatus.DROPPED)
    data = aggregate_week(db, threshold=0.5, now=NOW)
    ids = [it.id for it in data.groups["research"]]
    assert ids == ["kept"]


# --- Filtrage temporel -------------------------------------------------------


def test_old_items_excluded(db):
    _insert(db, "recent", Theme.RESEARCH, 0.9, NOW - timedelta(days=2))
    _insert(db, "old", Theme.RESEARCH, 0.9, NOW - timedelta(days=30))
    data = aggregate_week(db, days=7, threshold=0.5, now=NOW)
    ids = [it.id for it in data.groups["research"]]
    assert ids == ["recent"]


def test_custom_window(db):
    _insert(db, "d5", Theme.RESEARCH, 0.9, NOW - timedelta(days=5))
    data_7 = aggregate_week(db, days=7, threshold=0.5, now=NOW)
    data_3 = aggregate_week(db, days=3, threshold=0.5, now=NOW)
    assert data_7.total == 1
    assert data_3.total == 0  # hors fenetre de 3 jours


# --- Structure de sortie -----------------------------------------------------


def test_output_is_pydantic_with_period_and_summary(db):
    _insert(db, "r1", Theme.RESEARCH, 0.9, NOW, summary=["l1", "l2", "l3"])
    data = aggregate_week(db, threshold=0.5, now=NOW)
    assert isinstance(data, WeeklyReportData)
    assert data.period_end == "2026-09-10"
    assert data.groups["research"][0].summary == ["l1", "l2", "l3"]
