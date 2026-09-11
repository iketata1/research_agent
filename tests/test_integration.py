"""Tests d'integration globaux : parcours d'un article de la source a Telegram.

Les frontieres externes (connecteurs, client LLM, client Telegram) sont simulees ;
le reste (pre-filtre, scoring, classification, persistance, agregation, rendu)
s'execute reellement sur une base SQLite en memoire.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from research_agent import pipeline
from research_agent.collectors.mock import MockConnector
from research_agent.config import AppConfig, PreFilterConfig
from research_agent.models import RawItem, Theme
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus

SINCE = datetime(2026, 1, 1)


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    yield database
    database.close()


def _config():
    """Config minimale : pre-filtre ancre moisissure, seuil a 0.5."""
    return AppConfig(
        prefilter=PreFilterConfig(required=["mould", "schimmel", "humidity"]),
        relevance_threshold=0.5,
        sources={},  # les connecteurs sont injectes via un mock
    )


def _connector_returning(items):
    class _C(MockConnector):
        name = "openalex"

        def fetch(self, since):
            return items

    return _C()


def _llm_returning(score_json, classify_json):
    """Client LLM factice : premiere reponse = scoring, seconde = classification."""
    from research_agent.llm.client import Usage

    client = MagicMock()
    client.generate.side_effect = [score_json, classify_json]
    client.usage = Usage()  # usage reel (valeurs numeriques) pour le run tracker
    return client


# --- Parcours nominal --------------------------------------------------------


def test_relevant_item_flows_to_persistence_and_alert(db):
    relevant = RawItem(
        source="openalex",
        title="Improved mould prediction model",
        url="https://openalex.org/W1",
        raw_text="A study on humidity and mould in social housing.",
    )
    connector = _connector_returning([relevant])
    # Le LLM attribue un score critique (95) et le theme research.
    llm = _llm_returning(
        json.dumps({"scores": [{"index": 0, "score": 95, "justification": "central"}]}),
        json.dumps({"classifications": [{"index": 0, "category": "research", "confidence": 0.9}]}),
    )
    telegram = MagicMock()
    telegram.send_message.return_value = 1

    with patch.object(pipeline, "build_connectors", return_value=[connector]), \
         patch("research_agent.delivery.alerts.TelegramClient", return_value=telegram):
        report = pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=llm)

    # L'item a traverse tout le parcours.
    assert report["collected"] == 1
    assert report["prefiltered"] == 1
    assert report["persisted"] == 1
    assert report["alerts_sent"] == 1  # score 95 >= 90 -> alerte

    # Persistance verifiee en base.
    stored = dao.get_item(relevant.id, db)
    assert stored["status"] == "kept"
    assert stored["theme"] == "research"
    assert stored["relevance"] == pytest.approx(0.95)

    # Notification Telegram declenchee.
    telegram.send_message.assert_called_once()


def test_irrelevant_item_dropped_by_prefilter(db):
    """Un article hors sujet est ecarte par le pre-filtre, sans appel LLM."""
    noise = RawItem(
        source="openalex",
        title="Road construction asphalt tender",
        url="https://openalex.org/W2",
        raw_text="About highways and asphalt.",
    )
    connector = _connector_returning([noise])
    from research_agent.llm.client import Usage

    llm = MagicMock()  # ne doit jamais etre appele (generate)
    llm.usage = Usage()  # usage reel pour le run tracker

    with patch.object(pipeline, "build_connectors", return_value=[connector]):
        report = pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=llm)

    assert report["collected"] == 1
    assert report["prefiltered"] == 0
    llm.generate.assert_not_called()  # pas de cout LLM sur le bruit


def test_relevant_but_below_threshold_is_dropped(db):
    """Un item pertinent mais mal note (< seuil) est stocke en statut DROPPED."""
    item = RawItem(
        source="openalex",
        title="Minor note on humidity",
        url="https://openalex.org/W3",
        raw_text="humidity mentioned briefly",
    )
    connector = _connector_returning([item])
    llm = _llm_returning(
        json.dumps({"scores": [{"index": 0, "score": 20, "justification": "marginal"}]}),
        json.dumps({"classifications": [{"index": 0, "category": "research", "confidence": 0.4}]}),
    )

    with patch.object(pipeline, "build_connectors", return_value=[connector]), \
         patch("research_agent.delivery.alerts.TelegramClient", return_value=MagicMock()):
        report = pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=llm)

    assert report["alerts_sent"] == 0  # score 20 < 90
    stored = dao.get_item(item.id, db)
    assert stored["status"] == "dropped"


# --- Cycle hebdomadaire -> Telegram ------------------------------------------


def test_weekly_report_delivered(db):
    """Un item retenu apparait dans le rapport hebdomadaire livre sur Telegram."""
    item = RawItem(source="openalex", title="Mould model", url="https://openalex.org/W1")
    dao.insert_item(item, db)
    dao.save_enriched_item(
        item.id, db, score=88, theme=Theme.RESEARCH, status=ItemStatus.KEPT,
        justification="pertinent",
    )

    telegram = MagicMock()
    telegram.send_message.return_value = 1
    with patch("research_agent.delivery.formatter.TelegramClient", return_value=telegram):
        report = pipeline.run_weekly(config=_config(), db=db)

    assert report["items"] == 1
    assert report["delivered"] is True
    sent_message = telegram.send_message.call_args[0][0]
    assert "Mould model" in sent_message


# --- Planification -----------------------------------------------------------


def test_scheduler_registers_daily_and_weekly():
    import schedule as schedule_lib

    from research_agent import scheduler

    sched = schedule_lib.Scheduler()
    scheduler.build_schedule(sched)
    # Deux taches enregistrees : une quotidienne, une hebdomadaire.
    assert len(sched.jobs) == 2
    units = {job.unit for job in sched.jobs}
    assert "days" in units
    assert "weeks" in units  # une tache "monday" est une tache hebdomadaire
