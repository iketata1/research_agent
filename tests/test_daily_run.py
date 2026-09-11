"""Tests d'integration du cycle quotidien et du point d'entree CLI."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from research_agent import main as cli
from research_agent import pipeline
from research_agent.collectors.mock import MockConnector
from research_agent.config import AppConfig, PreFilterConfig
from research_agent.llm.client import LLMClient
from research_agent.models import RawItem
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database

SINCE = datetime(2026, 1, 1)


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    yield database
    database.close()


def _config():
    return AppConfig(
        prefilter=PreFilterConfig(required=["mould", "humidity"]),
        relevance_threshold=0.5,
        sources={},
    )


def _connector(items):
    class _C(MockConnector):
        name = "openalex"

        def fetch(self, since):
            return items

    return _C()


def _llm(score, category="research", usage_cost=0.0012):
    """Client LLM factice avec suivi d'usage simule."""
    client = MagicMock(spec=LLMClient)
    client.generate.side_effect = [
        json.dumps({"scores": [{"index": 0, "score": score, "justification": "j"}]}),
        json.dumps({"classifications": [{"index": 0, "category": category, "confidence": 0.8}]}),
    ]
    usage = MagicMock()
    usage.requests = 2
    usage.input_tokens = 1000
    usage.output_tokens = 500
    usage.cost = usage_cost
    client.usage = usage
    return client


# --- Enchaînement de bout en bout --------------------------------------------


def test_daily_run_full_chain(db):
    item = RawItem(
        source="openalex",
        title="Mould and humidity study",
        url="https://openalex.org/W1",
        raw_text="research on mould",
    )
    llm = _llm(score=80)
    with patch.object(pipeline, "build_connectors", return_value=[_connector([item])]), \
         patch("research_agent.delivery.alerts.TelegramClient", return_value=MagicMock()):
        report = pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=llm)

    assert report["collected"] == 1
    assert report["prefiltered"] == 1
    assert report["persisted"] == 1
    # Item persiste et enrichi.
    stored = dao.get_item(item.id, db)
    assert stored["status"] == "kept"
    assert stored["theme"] == "research"


def test_daily_run_reports_llm_cost(db):
    """L'observabilite doit inclure le cout et les tokens LLM cumules."""
    item = RawItem(source="openalex", title="humidity", url="https://openalex.org/W1",
                   raw_text="mould data")
    llm = _llm(score=70, usage_cost=0.0034)
    with patch.object(pipeline, "build_connectors", return_value=[_connector([item])]), \
         patch("research_agent.delivery.alerts.TelegramClient", return_value=MagicMock()):
        report = pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=llm)
    assert report["llm_requests"] == 2
    assert report["llm_tokens"] == 1500
    assert report["llm_cost"] == pytest.approx(0.0034)


def test_daily_run_noise_no_llm_no_persist(db):
    """Le bruit est ecarte par le pre-filtre : pas d'appel LLM, rien de persiste."""
    noise = RawItem(source="openalex", title="asphalt roads", url="https://openalex.org/W9",
                    raw_text="highway construction")
    llm = MagicMock(spec=LLMClient)
    llm.usage = MagicMock(requests=0, input_tokens=0, output_tokens=0, cost=0.0)
    with patch.object(pipeline, "build_connectors", return_value=[_connector([noise])]):
        report = pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=llm)
    assert report["prefiltered"] == 0
    llm.generate.assert_not_called()


def test_daily_run_deduplicates_across_runs(db):
    item = RawItem(source="openalex", title="mould humidity", url="https://openalex.org/W1",
                   raw_text="mould")
    with patch.object(pipeline, "build_connectors", return_value=[_connector([item])]), \
         patch("research_agent.delivery.alerts.TelegramClient", return_value=MagicMock()):
        pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=_llm(80))
        pipeline.run_daily(since=SINCE, config=_config(), db=db, llm=_llm(80))
    # Un seul enregistrement malgre deux runs.
    rows = dao.list_items(db)
    assert len([r for r in rows if r["url"] == "https://openalex.org/W1"]) == 1


# --- Point d'entree CLI ------------------------------------------------------


def test_cli_daily_invokes_run_daily():
    with patch("research_agent.main.run_daily") as run_daily_mock:
        code = cli.main(["daily"])
    assert code == 0
    run_daily_mock.assert_called_once()


def test_cli_weekly_executive_flag():
    with patch("research_agent.main.run_weekly") as run_weekly_mock:
        cli.main(["weekly", "--executive"])
    _, kwargs = run_weekly_mock.call_args
    assert kwargs.get("executive") is True


def test_cli_daily_since_parsed():
    with patch("research_agent.main.run_daily") as run_daily_mock:
        cli.main(["daily", "--since", "2026-09-01"])
    _, kwargs = run_daily_mock.call_args
    assert kwargs["since"] == datetime(2026, 9, 1)


def test_cli_run_invokes_both():
    with patch("research_agent.main.run_daily") as d, \
         patch("research_agent.main.run_weekly") as w:
        cli.main(["run"])
    d.assert_called_once()
    w.assert_called_once()
