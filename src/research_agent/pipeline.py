"""Orchestration du pipeline de bout en bout.

Enchaine les etapes : collect -> normalize -> pre-filter -> llm enrich ->
store -> report. Les etapes sont volontairement laissees en squelette pour la
Phase 1 ; elles seront implementees dans les phases suivantes.

Ce module illustre aussi l'usage du logging centralise et de la gestion d'erreurs
isolee : la panne d'un connecteur (ConnectorError) est journalisee sans
interrompre la collecte des autres sources.
"""

from __future__ import annotations

from research_agent.exceptions import ConnectorError, ResearchAgentError
from research_agent.logging_config import get_logger, setup_logging

logger = get_logger(__name__)


def _collect_all(sources: list[str]) -> list[dict]:
    """Collecte chaque source en isolant les pannes.

    Une erreur sur une source est journalisee et n'empeche pas les autres.
    """
    collected: list[dict] = []
    for source in sources:
        try:
            logger.info("Collecte de la source '%s'...", source)
            # TODO Phase 2 : appeler le connecteur reel de la source.
            # Ici, squelette : on ne collecte rien.
            items: list[dict] = []
            collected.extend(items)
            logger.debug("Source '%s' : %d item(s).", source, len(items))
        except ConnectorError:
            # Panne isolee : on journalise et on continue avec les autres sources.
            logger.exception("Echec du connecteur '%s' (ignore).", source)
    return collected


def run() -> None:
    """Point d'entree du pipeline (squelette Phase 1)."""
    setup_logging()
    logger.info("Research Intelligence Agent — pipeline demarre.")
    try:
        sources = ["openalex", "tenderned", "ted", "google_news", "aedes", "rechtspraak"]
        items = _collect_all(sources)
        logger.info("Collecte terminee : %d item(s) au total.", len(items))
        # TODO Phase 3+ : normalize -> filter -> store -> report
    except ResearchAgentError:
        # Filet de securite pour toute erreur "attendue" du projet.
        logger.exception("Erreur du pipeline.")
        raise
    logger.info("Pipeline termine.")


if __name__ == "__main__":
    run()
