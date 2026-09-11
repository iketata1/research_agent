"""Tests d'integration du cycle hebdomadaire (Organize -> Summarize -> Export -> Deliver)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research_agent import pipeline
from research_agent.config import AppConfig
from research_agent.llm.client import LLMClient
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


def _config():
    return AppConfig(relevance_threshold=0.5, sources={})


def _seed_kept_item(db, url, title, score=88, theme=Theme.RESEARCH, summary=None):
    item = RawItem(source="openalex", title=title, url=url, raw_text="contenu")
    dao.insert_item(item, db)
    dao.save_enriched_item(
        item.id, db, score=score, theme=theme, status=ItemStatus.KEPT,
        justification="pertinent",
    )
    if summary is not None:
        # Injecte un resume deja present dans la metadata.
        stored = dao.get_item(item.id, db)
        meta = stored["metadata"]
        meta["summary"] = summary
        with db.connection() as conn:
            conn.execute("UPDATE items SET metadata = ? WHERE id = ?;",
                         (json.dumps(meta), item.id))
    return item


def _llm_summary(*lines):
    client = MagicMock(spec=LLMClient)
    client.generate.return_value = json.dumps({"lines": list(lines)})
    client.usage = MagicMock(requests=1, input_tokens=100, output_tokens=50, cost=0.0003)
    return client


# --- Flux complet ------------------------------------------------------------


def test_weekly_run_full_flow(tmp_path, db):
    _seed_kept_item(db, "https://openalex.org/W1", "Mould model")
    telegram = MagicMock()
    telegram.send_message.return_value = 1
    llm = _llm_summary("impact 1", "impact 2", "impact 3")

    with patch("research_agent.delivery.formatter.TelegramClient", return_value=telegram):
        report = pipeline.run_weekly(
            config=_config(), db=db, llm=llm, output_dir=str(tmp_path)
        )

    # Organize
    assert report["items"] == 1
    # Summarize : l'item n'avait pas de resume -> genere.
    assert report["summaries_generated"] == 1
    # Export : fichier Markdown ecrit.
    assert Path(report["file"]).exists()
    content = Path(report["file"]).read_text(encoding="utf-8")
    assert "Mould model" in content
    assert "impact 1" in content
    # Deliver : envoi Telegram.
    assert report["delivered"] is True
    telegram.send_message.assert_called_once()


def test_weekly_run_skips_existing_summaries(tmp_path, db):
    """Un item disposant deja d'un resume ne redeclenche pas d'appel LLM."""
    _seed_kept_item(
        db, "https://openalex.org/W1", "Deja resume",
        summary=["deja", "trois", "lignes"],
    )
    llm = _llm_summary("ne", "doit", "pas")
    with patch("research_agent.delivery.formatter.TelegramClient", return_value=MagicMock()):
        report = pipeline.run_weekly(
            config=_config(), db=db, llm=llm, output_dir=str(tmp_path)
        )
    assert report["summaries_generated"] == 0
    llm.generate.assert_not_called()


def test_weekly_run_persists_generated_summary(tmp_path, db):
    """Le resume genere est persiste en base (tracabilite)."""
    item = _seed_kept_item(db, "https://openalex.org/W1", "A resumer")
    llm = _llm_summary("l1", "l2", "l3")
    with patch("research_agent.delivery.formatter.TelegramClient", return_value=MagicMock()):
        pipeline.run_weekly(config=_config(), db=db, llm=llm, output_dir=str(tmp_path))
    stored = dao.get_item(item.id, db)
    assert stored["metadata"]["summary"] == ["l1", "l2", "l3"]


def test_weekly_run_empty_still_exports(tmp_path, db):
    """Sans item, le rapport est tout de meme genere et exporte."""
    telegram = MagicMock()
    telegram.send_message.return_value = 1
    llm = _llm_summary("x", "y", "z")
    with patch("research_agent.delivery.formatter.TelegramClient", return_value=telegram):
        report = pipeline.run_weekly(
            config=_config(), db=db, llm=llm, output_dir=str(tmp_path)
        )
    assert report["items"] == 0
    assert Path(report["file"]).exists()
    content = Path(report["file"]).read_text(encoding="utf-8")
    assert "Aucun element pertinent" in content


def test_weekly_run_grouped_by_theme(tmp_path, db):
    _seed_kept_item(db, "https://e.org/1", "Etude", theme=Theme.RESEARCH,
                    summary=["a", "b", "c"])
    _seed_kept_item(db, "https://e.org/2", "Tender", theme=Theme.OPPORTUNITY, score=92,
                    summary=["a", "b", "c"])
    telegram = MagicMock()
    telegram.send_message.return_value = 1
    llm = _llm_summary("x", "y", "z")
    with patch("research_agent.delivery.formatter.TelegramClient", return_value=telegram):
        pipeline.run_weekly(config=_config(), db=db, llm=llm, output_dir=str(tmp_path))
    sent = telegram.send_message.call_args[0][0]
    assert "RESEARCH" in sent
    assert "OPPORTUNITY" in sent
