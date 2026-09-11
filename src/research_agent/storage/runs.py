"""Journalisation des executions (table `runs`) pour l'audit et l'anti-doublon.

Fournit :
- `run_tracker(...)` : un gestionnaire de contexte qui ouvre un run, expose un
  objet `RunMetrics` a mettre a jour pendant l'execution, et clot le run
  proprement (statut success/failed) meme en cas d'exception ;
- `already_processed(...)` : verification d'existence d'un item en base pour
  eviter de retraiter (et refacturer au LLM) un document deja vu lors d'un run
  precedent.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from research_agent.llm.client import Usage
from research_agent.logging_config import get_logger
from research_agent.storage.database import Database

logger = get_logger(__name__)


@dataclass
class RunMetrics:
    """Metriques d'un run, mises a jour en temps reel pendant l'execution."""

    items_collected: int = 0
    items_filtered: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cost: float = 0.0
    error: Optional[str] = None

    def apply_usage(self, usage: Usage) -> None:
        """Renseigne les metriques LLM a partir de l'usage d'un client LLM."""
        self.llm_input_tokens = usage.input_tokens
        self.llm_output_tokens = usage.output_tokens
        self.llm_cost = usage.cost


def _open_run(db: Database, run_type: str) -> int:
    """Cree une ligne de run 'running' et retourne son id."""
    with db.connection() as conn:
        cursor = conn.execute(
            "INSERT INTO runs (task_type, run_type, status) VALUES (?, ?, 'running');",
            (run_type, run_type),
        )
        return int(cursor.lastrowid)


def _close_run(db: Database, run_id: int, status: str, metrics: RunMetrics) -> None:
    """Clot un run avec son statut final et ses metriques."""
    with db.connection() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = datetime('now'), "
            "items_collected = ?, items_filtered = ?, "
            "llm_input_tokens = ?, llm_output_tokens = ?, llm_cost = ?, error = ? "
            "WHERE id = ?;",
            (
                status,
                metrics.items_collected,
                metrics.items_filtered,
                metrics.llm_input_tokens,
                metrics.llm_output_tokens,
                metrics.llm_cost,
                metrics.error,
                run_id,
            ),
        )


@contextmanager
def run_tracker(db: Database, run_type: str) -> Iterator[RunMetrics]:
    """Gestionnaire de contexte qui journalise un run de bout en bout.

    Ouvre un run ('running'), fournit un `RunMetrics` a renseigner, puis clot le
    run en 'success' en sortie normale, ou en 'failed' (avec le message d'erreur)
    si une exception survient. L'exception est ensuite propagee.

    Args:
        db: base cible.
        run_type: type de run ("daily" ou "weekly").

    Yields:
        Un `RunMetrics` a mettre a jour pendant l'execution.
    """
    run_id = _open_run(db, run_type)
    metrics = RunMetrics()
    logger.info("Run '%s' ouvert (id=%d).", run_type, run_id)
    try:
        yield metrics
    except Exception as exc:
        metrics.error = str(exc)
        _close_run(db, run_id, "failed", metrics)
        logger.exception("Run '%s' (id=%d) en echec.", run_type, run_id)
        raise
    else:
        _close_run(db, run_id, "success", metrics)
        logger.info(
            "Run '%s' (id=%d) termine | collectes=%d, filtres=%d, cout LLM=%.4f",
            run_type,
            run_id,
            metrics.items_collected,
            metrics.items_filtered,
            metrics.llm_cost,
        )


def already_processed(item_id: str, db: Database) -> bool:
    """Indique si un item a deja ete traite lors d'un run precedent.

    Sert de garde anti-doublon inter-semaines : un item deja present en base ne
    doit pas etre re-soumis au LLM (evite la refacturation inutile).

    Args:
        item_id: identifiant stable de l'item.
        db: base a interroger.

    Returns:
        True si l'item existe deja en base.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM items WHERE id = ? LIMIT 1;", (item_id,)
        ).fetchone()
    return row is not None
