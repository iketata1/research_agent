"""Generation et export du rapport hebdomadaire au format Markdown.

Orchestre les etapes finales de la Phase 5 :
- agregation des items retenus de la periode (aggregator) ;
- mise en forme en Markdown (renderer) ;
- ajout d'un pied de page de metadonnees de run (date, total d'items, tokens et
  cout LLM cumules) ;
- ecriture du fichier sous un nom normalise base sur la periode.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from research_agent.config import PROJECT_ROOT
from research_agent.llm.client import Usage
from research_agent.logging_config import get_logger
from research_agent.reporting.aggregator import WeeklyReportData, aggregate_week
from research_agent.reporting.renderer import render_markdown
from research_agent.storage.database import Database

logger = get_logger(__name__)


def _footer(data: WeeklyReportData, usage: Optional[Usage]) -> str:
    """Construit le pied de page de metadonnees du run."""
    generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "",
        "---",
        f"_Genere le {generated}_",
        f"_Items retenus : {data.total}_",
    ]
    if usage is not None:
        lines.append(
            f"_LLM : {usage.requests} requete(s), "
            f"{usage.input_tokens + usage.output_tokens} tokens, "
            f"cout estime {usage.cost:.4f}_"
        )
    return "\n".join(lines) + "\n"


def build_report(
    data: WeeklyReportData,
    usage: Optional[Usage] = None,
    title: str = "INTRA-AIR RESEARCH INTELLIGENCE",
) -> str:
    """Assemble le rapport Markdown complet (corps + pied de page)."""
    body = render_markdown(data, title=title)
    return body + _footer(data, usage)


def report_filename(data: WeeklyReportData) -> str:
    """Nom de fichier normalise base sur la fin de periode (ex. weekly_report_2026-09-10.md)."""
    return f"weekly_report_{data.period_end}.md"


def save_weekly_report(
    output_dir: str = "reports/",
    db: Optional[Database] = None,
    days: int = 7,
    threshold: Optional[float] = None,
    usage: Optional[Usage] = None,
    now: Optional[datetime] = None,
    data: Optional[WeeklyReportData] = None,
) -> str:
    """Genere le rapport hebdomadaire et l'ecrit dans un fichier Markdown.

    Args:
        output_dir: repertoire de sortie (cree si absent). Relatif a la racine
            du projet s'il n'est pas absolu.
        db: base a interroger (ignore si `data` est fourni).
        days: taille de la fenetre glissante (jours).
        threshold: seuil de pertinence [0, 1] ; par defaut celui de la config.
        usage: suivi des tokens/couts LLM a inclure dans le pied de page.
        now: instant de reference (pour les tests).
        data: donnees deja agregees (evite une requete ; surtout pour les tests).

    Returns:
        Le chemin du fichier de rapport ecrit.
    """
    if data is None:
        if db is None:
            db = Database.from_config()
            db.initialize()
        data = aggregate_week(db, days=days, threshold=threshold, now=now)

    content = build_report(data, usage=usage)

    out_path = Path(output_dir)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    out_path.mkdir(parents=True, exist_ok=True)

    file_path = out_path / report_filename(data)
    file_path.write_text(content, encoding="utf-8")
    logger.info("Rapport hebdomadaire ecrit : %s (%d item(s)).", file_path, data.total)
    return str(file_path)
