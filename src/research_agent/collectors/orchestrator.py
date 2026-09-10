"""Orchestration de la collecte : lance tous les connecteurs et stocke le resultat.

`run_all_ingestions(since)` :
- construit les connecteurs actifs (via le registre) ;
- declenche chacun via `collect()` (deja isole : une panne renvoie une liste vide) ;
- stocke les items collectes en base avec `upsert_item()` (dedup + mise a jour) ;
- enregistre un run par source dans la table `runs` (suivi/monitoring) ;
- retourne un rapport d'execution detaille par source.

L'isolation des pannes est garantie a deux niveaux : `collect()` capture les
erreurs internes du connecteur, et l'orchestrateur enveloppe en plus chaque
source dans son propre try/except, de sorte qu'aucune defaillance (collecte OU
stockage) n'interrompt les autres sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from research_agent.collectors.base import BaseConnector
from research_agent.collectors.registry import build_connectors
from research_agent.config import AppConfig, load_settings
from research_agent.logging_config import get_logger
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database

logger = get_logger(__name__)


@dataclass
class SourceReport:
    """Rapport d'execution pour une source."""

    source: str
    status: str = "success"          # success | failed
    collected: int = 0               # items renvoyes par le connecteur
    inserted: int = 0                # nouveaux items stockes
    updated: int = 0                 # items rafraichis (contenu modifie)
    unchanged: int = 0               # doublons ignores
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "collected": self.collected,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "error": self.error,
        }


@dataclass
class IngestionReport:
    """Rapport global d'une execution de collecte."""

    started_at: datetime
    finished_at: Optional[datetime] = None
    sources: List[SourceReport] = field(default_factory=list)

    @property
    def total_collected(self) -> int:
        return sum(s.collected for s in self.sources)

    @property
    def total_inserted(self) -> int:
        return sum(s.inserted for s in self.sources)

    @property
    def failed_sources(self) -> List[str]:
        return [s.source for s in self.sources if s.status == "failed"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_collected": self.total_collected,
            "total_inserted": self.total_inserted,
            "failed_sources": self.failed_sources,
            "sources": [s.as_dict() for s in self.sources],
        }


def _record_run(db: Database, report: SourceReport, since: datetime) -> None:
    """Enregistre le resultat d'une source dans la table `runs`."""
    try:
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO runs (task_type, finished_at, status, items_collected, error) "
                "VALUES (?, datetime('now'), ?, ?, ?);",
                (
                    f"collect:{report.source}",
                    report.status,
                    report.collected,
                    report.error,
                ),
            )
    except Exception:  # le suivi ne doit jamais faire echouer la collecte
        logger.exception("Impossible d'enregistrer le run pour '%s'.", report.source)


def _ingest_one(
    connector: BaseConnector,
    since: datetime,
    db: Database,
) -> SourceReport:
    """Collecte et stocke une source, en isolant toute panne."""
    report = SourceReport(source=connector.name)
    try:
        items = connector.collect(since)  # deja isole : [] en cas d'echec interne
        report.collected = len(items)
        counts = dao.upsert_items(items, db)
        report.inserted = counts.get("inserted", 0)
        report.updated = counts.get("updated", 0)
        report.unchanged = counts.get("unchanged", 0)
        logger.info(
            "Source '%s' : %d collectes (%d nouveaux, %d maj, %d inchanges).",
            connector.name,
            report.collected,
            report.inserted,
            report.updated,
            report.unchanged,
        )
    except Exception as exc:  # filet : le stockage d'une source ne casse pas les autres
        report.status = "failed"
        report.error = str(exc)
        logger.exception("Echec de l'ingestion pour '%s'.", connector.name)
    _record_run(db, report, since)
    return report


def run_all_ingestions(
    since: datetime,
    app_config: Optional[AppConfig] = None,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Lance la collecte de toutes les sources actives et stocke les resultats.

    Args:
        since: borne temporelle basse pour la collecte.
        app_config: configuration ; chargee depuis le YAML si absente.
        db: base cible ; construite/initialisee depuis la config si absente.

    Returns:
        Le rapport d'execution detaille (voir `IngestionReport.as_dict`).
    """
    config = app_config or load_settings().app
    database = db or Database.from_config(config.database)
    database.initialize()

    report = IngestionReport(started_at=datetime.utcnow())
    connectors = build_connectors(config)
    logger.info("Debut de la collecte : %d source(s) active(s).", len(connectors))

    for connector in connectors:
        report.sources.append(_ingest_one(connector, since, database))

    report.finished_at = datetime.utcnow()
    logger.info(
        "Collecte terminee : %d item(s) collecte(s), %d nouveau(x). Echecs : %s.",
        report.total_collected,
        report.total_inserted,
        report.failed_sources or "aucun",
    )
    return report.as_dict()
