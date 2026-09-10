"""Agregation hebdomadaire des items retenus, regroupes par theme.

Interroge la base pour les items traites sur une fenetre glissante (7 jours par
defaut) dont le score depasse le seuil de pertinence, puis :
- les repartit par theme (research / opportunity / legal / technology) ;
- les trie par score decroissant a l'interieur de chaque theme (les plus
  critiques pour Intra-Air en tete) ;
- structure le tout dans un objet Pydantic pret pour le generateur de rapport.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from research_agent.config import load_settings
from research_agent.logging_config import get_logger
from research_agent.models import Theme
from research_agent.storage.database import Database
from research_agent.storage.items_dao import ItemStatus

logger = get_logger(__name__)

# Ordre d'affichage des themes dans le rapport (cf. maquette du lundi).
THEME_ORDER = [Theme.RESEARCH, Theme.OPPORTUNITY, Theme.LEGAL, Theme.TECHNOLOGY]


class ReportItem(BaseModel):
    """Item pret pour le rapport (vue simplifiee et enrichie)."""

    id: str
    source: str
    title: str
    url: str
    score: int = 0                       # score 0-100
    theme: Theme = Theme.UNKNOWN
    summary: List[str] = Field(default_factory=list)
    published_at: Optional[str] = None


class WeeklyReportData(BaseModel):
    """Structure agregee prete a etre consommee par le generateur de rapport."""

    period_start: str
    period_end: str
    groups: Dict[str, List[ReportItem]] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(len(items) for items in self.groups.values())


def _row_to_report_item(row: Dict[str, Any]) -> ReportItem:
    """Convertit une ligne `items` (dict) en `ReportItem`."""
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    metadata = metadata or {}

    # Score 0-100 : depuis metadata (llm_score) sinon derive de relevance.
    score = metadata.get("llm_score")
    if score is None and row.get("relevance") is not None:
        score = round(float(row["relevance"]) * 100)
    score = int(score or 0)

    summary = metadata.get("summary") or []
    if not isinstance(summary, list):
        summary = [str(summary)]

    return ReportItem(
        id=row["id"],
        source=row["source"],
        title=row["title"],
        url=row["url"],
        score=score,
        theme=Theme(row["theme"]) if row.get("theme") else Theme.UNKNOWN,
        summary=summary,
        published_at=row.get("published_at"),
    )


def aggregate_week(
    db: Database,
    days: int = 7,
    threshold: Optional[float] = None,
    now: Optional[datetime] = None,
) -> WeeklyReportData:
    """Agrege les items retenus de la periode, regroupes et tries par theme.

    Args:
        db: base a interroger.
        days: taille de la fenetre glissante (jours).
        threshold: seuil de pertinence [0, 1] ; par defaut celui de la config.
        now: instant de reference (pour les tests) ; par defaut maintenant.

    Returns:
        Les donnees agregees, groupees par theme et triees par score decroissant.
    """
    reference = now or datetime.utcnow()
    period_start = reference - timedelta(days=days)
    if threshold is None:
        threshold = load_settings().app.relevance_threshold

    # Items traites (collectes) dans la fenetre, au-dessus du seuil, non ecartes.
    sql = (
        "SELECT * FROM items "
        "WHERE collected_at >= ? "
        "  AND relevance IS NOT NULL AND relevance >= ? "
        "  AND status != ? "
        "ORDER BY relevance DESC;"
    )
    with db.connection() as conn:
        rows = conn.execute(
            sql,
            (period_start.isoformat(sep=" "), threshold, ItemStatus.DROPPED.value),
        ).fetchall()

    # Regroupement par theme, en respectant l'ordre d'affichage du rapport.
    groups: Dict[str, List[ReportItem]] = {t.value: [] for t in THEME_ORDER}
    for row in rows:
        item = _row_to_report_item(dict(row))
        key = item.theme.value if item.theme in THEME_ORDER else Theme.UNKNOWN.value
        groups.setdefault(key, []).append(item)

    # Tri par score decroissant a l'interieur de chaque groupe.
    for items in groups.values():
        items.sort(key=lambda it: it.score, reverse=True)

    # On retire les groupes vides pour un rapport propre.
    groups = {k: v for k, v in groups.items() if v}

    logger.info(
        "Agregation : %d item(s) retenu(s) sur %d jour(s), %d theme(s).",
        sum(len(v) for v in groups.values()),
        days,
        len(groups),
    )
    return WeeklyReportData(
        period_start=period_start.date().isoformat(),
        period_end=reference.date().isoformat(),
        groups=groups,
    )
