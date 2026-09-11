"""Orchestration du pipeline de bout en bout.

Deux cycles :

- `run_daily(since)` : Ingestion -> Pre-filtre -> Scoring LLM -> Classification
  -> Persistance -> Alertes instantanees (items critiques).
- `run_weekly()` : Agregation de la semaine -> Rapport -> Livraison Telegram.

Chaque etape est protegee : une panne de source ou de livraison est journalisee
sans interrompre le cycle global.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from research_agent.config import AppConfig, load_settings
from research_agent.delivery.alerts import process_alerts
from research_agent.delivery.formatter import send_weekly_report
from research_agent.filtering.classifier import classify_items
from research_agent.filtering.llm_scorer import score_items
from research_agent.filtering.pre_filter import pre_filter
from research_agent.llm.client import LLMClient
from research_agent.logging_config import get_logger, setup_logging
from research_agent.reporting.aggregator import aggregate_week
from research_agent.reporting.generator import save_weekly_report
from research_agent.reporting.summarizer import summarize_item
from research_agent.storage import items_dao as dao
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus
from research_agent.collectors.registry import build_connectors
from research_agent.models import RawItem

logger = get_logger(__name__)


def run_daily(
    since: Optional[datetime] = None,
    config: Optional[AppConfig] = None,
    db: Optional[Database] = None,
    llm: Optional[LLMClient] = None,
) -> Dict[str, Any]:
    """Cycle quotidien : collecte, filtrage, enrichissement, persistance, alertes.

    Args:
        since: borne temporelle basse (par defaut : 24h en arriere).
        config: configuration ; chargee depuis le YAML si absente.
        db: base cible ; construite depuis la config si absente.
        llm: client LLM ; instancie par defaut si absent.

    Returns:
        Un rapport de cycle (compteurs par etape).
    """
    app = config or load_settings().app
    database = db or Database.from_config(app.database)
    database.initialize()
    reference = since or (datetime.utcnow() - timedelta(days=1))
    client = llm or LLMClient()

    # 1. Ingestion (chaque connecteur isole ses pannes via collect()).
    collected = []
    for connector in build_connectors(app):
        collected.extend(connector.collect(reference))
    logger.info("Cycle quotidien : %d item(s) collecte(s).", len(collected))

    # 2. Pre-filtre deterministe (gratuit).
    kept = pre_filter(collected, app.prefilter)

    # 3. Enrichissement LLM : scoring puis classification.
    if kept:
        score_items(kept, client=client)
        classify_items(kept, client=client)

    # 4. Persistance : upsert des items, puis enrichissement (score/theme/statut).
    dao.upsert_items(collected, database)
    threshold_score = int(app.relevance_threshold * 100)
    persisted = 0
    for item in kept:
        score = int(item.metadata.get("llm_score", 0))
        status = ItemStatus.KEPT if score >= threshold_score else ItemStatus.DROPPED
        if dao.save_enriched_item(
            item.id,
            database,
            score=score,
            theme=item.theme,
            status=status,
            justification=item.metadata.get("llm_justification"),
            theme_confidence=item.metadata.get("theme_confidence"),
        ):
            persisted += 1

    # 5. Alertes instantanees pour les items critiques.
    alerts_sent = process_alerts(kept)

    dropped = len(collected) - persisted
    report = {
        "collected": len(collected),
        "prefiltered": len(kept),
        "persisted": persisted,
        "dropped": dropped,
        "alerts_sent": alerts_sent,
        "llm_requests": client.usage.requests,
        "llm_tokens": client.usage.input_tokens + client.usage.output_tokens,
        "llm_cost": round(client.usage.cost, 6),
    }
    # Observabilite : synthese du run quotidien.
    logger.info(
        "Cycle quotidien termine | collectes=%d, pre-filtre=%d, valides=%d, "
        "ecartes=%d, alertes=%d | LLM: %d requete(s), %d tokens, cout=%.4f",
        report["collected"],
        report["prefiltered"],
        report["persisted"],
        report["dropped"],
        report["alerts_sent"],
        report["llm_requests"],
        report["llm_tokens"],
        report["llm_cost"],
    )
    return report


def _ensure_summaries(
    data,
    database: Database,
    llm: LLMClient,
) -> int:
    """Genere le resume 3 lignes des items retenus qui n'en ont pas encore.

    Le resume est persiste dans la metadata de l'item (tracabilite) et injecte
    dans l'objet du rapport. Un echec de resume est isole (item laisse tel quel).

    Returns:
        Le nombre de resumes generes.
    """
    generated = 0
    for items in data.groups.values():
        for report_item in items:
            if report_item.summary:
                continue
            proxy = RawItem(
                source=report_item.source,
                title=report_item.title,
                url=report_item.url,
                raw_text=report_item.title,
            )
            try:
                summary = summarize_item(proxy, client=llm)
            except Exception:
                logger.exception("Resume echoue pour '%s' (ignore).", report_item.id)
                continue
            report_item.summary = summary.lines
            # Persistance du resume dans la metadata de l'item stocke.
            stored = dao.get_item(report_item.id, database)
            if stored is not None:
                metadata = stored.get("metadata") or {}
                metadata["summary"] = summary.lines
                metadata["summary_text"] = summary.text
                with database.connection() as conn:
                    import json as _json

                    conn.execute(
                        "UPDATE items SET metadata = ? WHERE id = ?;",
                        (_json.dumps(metadata, ensure_ascii=False), report_item.id),
                    )
            generated += 1
    return generated


def run_weekly(
    config: Optional[AppConfig] = None,
    db: Optional[Database] = None,
    llm: Optional[LLMClient] = None,
    executive: bool = False,
    output_dir: str = "reports/",
) -> Dict[str, Any]:
    """Cycle hebdomadaire : Organize -> Summarize -> Export -> Deliver.

    Etapes :
    1. agrege les items valides des 7 derniers jours par categorie ;
    2. genere les resumes 3 lignes manquants (angle valeur metier) ;
    3. exporte le rapport Markdown dans le repertoire des rapports ;
    4. envoie le rapport via Telegram (avec decoupage si necessaire).

    Args:
        config: configuration ; chargee depuis le YAML si absente.
        db: base a interroger ; construite depuis la config si absente.
        llm: client LLM ; instancie par defaut si absent.
        executive: si True, envoie le resume executif sur Telegram.
        output_dir: repertoire d'export du fichier Markdown.

    Returns:
        Un rapport de cycle (items, resumes generes, chemin du fichier, livraison).
    """
    app = config or load_settings().app
    database = db or Database.from_config(app.database)
    database.initialize()
    client = llm or LLMClient()

    # 1. Organize : agregation par theme, triee par score.
    data = aggregate_week(database, threshold=app.relevance_threshold)

    # 2. Summarize : resumes manquants pour les items retenus.
    summaries_generated = _ensure_summaries(data, database, client)

    # 3. Export : ecriture du fichier Markdown.
    file_path = save_weekly_report(
        output_dir=output_dir, data=data, usage=client.usage
    )

    # 4. Deliver : envoi Telegram.
    delivered = send_weekly_report(data, executive=executive)

    report = {
        "items": data.total,
        "summaries_generated": summaries_generated,
        "file": file_path,
        "delivered": delivered,
    }
    logger.info(
        "Cycle hebdomadaire termine | items=%d, resumes=%d, fichier=%s, livre=%s",
        report["items"],
        report["summaries_generated"],
        report["file"],
        report["delivered"],
    )
    return report


def run() -> None:
    """Point d'entree ponctuel : un cycle quotidien suivi du rapport hebdo."""
    setup_logging()
    logger.info("Research Intelligence Agent — execution ponctuelle.")
    run_daily()
    run_weekly()


if __name__ == "__main__":
    run()
