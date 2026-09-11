"""Tests du suivi des runs (metriques, statuts, anti-doublon)."""

import pytest

from research_agent.llm.client import Usage
from research_agent.models import RawItem
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database
from research_agent.storage.runs import (
    RunMetrics,
    already_processed,
    run_tracker,
)


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    yield database
    database.close()


def _runs(db):
    with db.connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id;")]


# --- Schema ------------------------------------------------------------------


def test_runs_has_llm_columns(db):
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs);")}
    assert {"run_type", "items_filtered", "llm_input_tokens",
            "llm_output_tokens", "llm_cost"} <= cols


# --- Cycle de vie d'un run ---------------------------------------------------


def test_run_tracker_records_success(db):
    with run_tracker(db, "daily") as metrics:
        metrics.items_collected = 10
        metrics.items_filtered = 3
        metrics.apply_usage(Usage(input_tokens=500, output_tokens=200, requests=2, cost=0.0012))
    runs = _runs(db)
    assert len(runs) == 1
    r = runs[0]
    assert r["run_type"] == "daily"
    assert r["status"] == "success"
    assert r["items_collected"] == 10
    assert r["items_filtered"] == 3
    assert r["llm_input_tokens"] == 500
    assert r["llm_output_tokens"] == 200
    assert r["llm_cost"] == pytest.approx(0.0012)
    assert r["finished_at"] is not None


def test_run_tracker_records_failure_and_reraises(db):
    with pytest.raises(RuntimeError):
        with run_tracker(db, "daily") as metrics:
            metrics.items_collected = 5
            raise RuntimeError("boom")
    runs = _runs(db)
    assert runs[0]["status"] == "failed"
    assert "boom" in runs[0]["error"]
    # Les metriques partielles sont tout de meme enregistrees.
    assert runs[0]["items_collected"] == 5


def test_run_tracker_opens_running_then_closes(db):
    # Pendant l'execution, un run 'running' existe ; a la sortie il est clos.
    with run_tracker(db, "weekly"):
        during = _runs(db)
        assert during[0]["status"] == "running"
        assert during[0]["finished_at"] is None
    after = _runs(db)
    assert after[0]["status"] == "success"


def test_apply_usage_sets_metrics():
    m = RunMetrics()
    m.apply_usage(Usage(input_tokens=100, output_tokens=50, requests=1, cost=0.0005))
    assert m.llm_input_tokens == 100
    assert m.llm_output_tokens == 50
    assert m.llm_cost == pytest.approx(0.0005)


# --- Anti-doublon ------------------------------------------------------------


def test_already_processed_false_then_true(db):
    item = RawItem(source="openalex", title="T", url="https://e.org/a")
    assert already_processed(item.id, db) is False
    dao.insert_item(item, db)
    assert already_processed(item.id, db) is True


def test_already_processed_distinguishes_items(db):
    a = RawItem(source="s", title="A", url="https://e.org/a")
    b = RawItem(source="s", title="B", url="https://e.org/b")
    dao.insert_item(a, db)
    assert already_processed(a.id, db) is True
    assert already_processed(b.id, db) is False
