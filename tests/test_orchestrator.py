"""Tests de l'orchestration de collecte (isolation des pannes par source)."""

from datetime import datetime
from unittest.mock import patch

import pytest

from research_agent.collectors.base import BaseConnector
from research_agent.collectors.mock import MockConnector
from research_agent.collectors import orchestrator
from research_agent.config import AppConfig
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


class _NamedMock(MockConnector):
    """MockConnector dont on peut fixer le nom et les items."""

    def __init__(self, name, items=None, fail=False):
        super().__init__(items=items, fail=fail)
        self.name = name
        # Recree le logger avec le bon nom.
        from research_agent.logging_config import get_logger

        self.logger = get_logger(f"collectors.{name}")


def _item(source, url):
    return RawItem(source=source, title=f"Titre {url}", url=url)


def test_report_structure(db):
    connectors = [
        _NamedMock("s1", items=[_item("s1", "https://e.org/1")]),
        _NamedMock("s2", items=[_item("s2", "https://e.org/2")]),
    ]
    with patch.object(orchestrator, "build_connectors", return_value=connectors):
        report = orchestrator.run_all_ingestions(SINCE, app_config=AppConfig(), db=db)
    assert report["total_collected"] == 2
    assert report["total_inserted"] == 2
    assert report["failed_sources"] == []
    assert len(report["sources"]) == 2


def test_failing_connector_does_not_stop_others(db):
    """Le coeur de la tache : une source qui plante n'affecte pas les autres."""
    connectors = [
        _NamedMock("ok1", items=[_item("ok1", "https://e.org/1")]),
        _NamedMock("boom", fail=True),  # collect() renverra [] (panne isolee)
        _NamedMock("ok2", items=[_item("ok2", "https://e.org/2")]),
    ]
    with patch.object(orchestrator, "build_connectors", return_value=connectors):
        report = orchestrator.run_all_ingestions(SINCE, app_config=AppConfig(), db=db)

    by_source = {s["source"]: s for s in report["sources"]}
    # Les deux sources saines ont bien collecte et stocke.
    assert by_source["ok1"]["inserted"] == 1
    assert by_source["ok2"]["inserted"] == 1
    # La source en panne renvoie 0 (collect() a isole l'erreur), sans casser le run.
    assert by_source["boom"]["collected"] == 0
    assert report["total_inserted"] == 2


def test_storage_error_isolated(db):
    """Une erreur de STOCKAGE sur une source ne doit pas affecter les autres."""
    connectors = [
        _NamedMock("good", items=[_item("good", "https://e.org/ok")]),
        _NamedMock("bad", items=[_item("bad", "https://e.org/bad")]),
    ]

    real_upsert = dao.upsert_items

    def flaky_upsert(items, database, **kw):
        if items and items[0].source == "bad":
            raise RuntimeError("stockage casse")
        return real_upsert(items, database, **kw)

    with patch.object(orchestrator, "build_connectors", return_value=connectors), \
         patch.object(orchestrator.dao, "upsert_items", side_effect=flaky_upsert):
        report = orchestrator.run_all_ingestions(SINCE, app_config=AppConfig(), db=db)

    by_source = {s["source"]: s for s in report["sources"]}
    assert by_source["good"]["status"] == "success"
    assert by_source["good"]["inserted"] == 1
    assert by_source["bad"]["status"] == "failed"
    assert by_source["bad"]["error"] is not None


def test_items_are_persisted(db):
    connectors = [_NamedMock("s1", items=[_item("s1", "https://e.org/persist")])]
    with patch.object(orchestrator, "build_connectors", return_value=connectors):
        orchestrator.run_all_ingestions(SINCE, app_config=AppConfig(), db=db)
    stored = dao.get_by_url("https://e.org/persist", db)
    assert stored is not None
    assert stored["source"] == "s1"


def test_runs_are_recorded(db):
    connectors = [
        _NamedMock("s1", items=[_item("s1", "https://e.org/1")]),
        _NamedMock("boom", fail=True),
    ]
    with patch.object(orchestrator, "build_connectors", return_value=connectors):
        orchestrator.run_all_ingestions(SINCE, app_config=AppConfig(), db=db)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT task_type, status FROM runs ORDER BY task_type;"
        ).fetchall()
    task_types = {r["task_type"] for r in rows}
    assert "collect:s1" in task_types
    assert "collect:boom" in task_types


def test_deduplication_across_run(db):
    """Relancer la meme collecte ne cree pas de doublons."""
    connectors = [_NamedMock("s1", items=[_item("s1", "https://e.org/dup")])]
    with patch.object(orchestrator, "build_connectors", return_value=connectors):
        first = orchestrator.run_all_ingestions(SINCE, app_config=AppConfig(), db=db)
        second = orchestrator.run_all_ingestions(SINCE, app_config=AppConfig(), db=db)
    assert first["total_inserted"] == 1
    assert second["total_inserted"] == 0  # deja present -> unchanged
