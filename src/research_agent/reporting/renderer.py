"""Rendu du rapport hebdomadaire en Markdown structure.

Transforme les donnees agregees (`WeeklyReportData`) en un rapport lisible,
organise par theme (RESEARCH / OPPORTUNITY / LEGAL / TECHNOLOGY), a la maniere
de la maquette du lundi. Chaque item affiche son resume, son score, un lien
direct vers la source et un badge de priorite.

Niveaux de priorite :
- ACT  : action recommandee pour l'equipe (opportunites a fort score : un tender
         pertinent est a traiter rapidement) ;
- HIGH : haute pertinence / a lire en priorite (score eleve, autres themes) ;
- (aucun badge) pour les items de pertinence moderee.
"""

from __future__ import annotations

from typing import List

from research_agent.models import Theme
from research_agent.reporting.aggregator import ReportItem, WeeklyReportData

# Seuils de score (0-100) pour l'attribution des badges de priorite.
ACT_THRESHOLD = 75    # opportunites au-dessus de ce score -> ACT
HIGH_THRESHOLD = 75   # autres themes au-dessus de ce score -> HIGH

# Titre et emoji par theme (ordre d'affichage gere par l'agregateur).
THEME_HEADERS = {
    Theme.RESEARCH.value: ("RESEARCH", "\U0001F52C"),      # microscope
    Theme.OPPORTUNITY.value: ("OPPORTUNITY", "\U0001F3E2"),  # batiment
    Theme.LEGAL.value: ("LEGAL", "\u2696\uFE0F"),           # balance
    Theme.TECHNOLOGY.value: ("TECHNOLOGY", "\U0001F4E1"),   # antenne
    Theme.UNKNOWN.value: ("AUTRES", "\U0001F4CC"),          # punaise
}


def priority_label(item: ReportItem) -> str:
    """Determine le badge de priorite d'un item selon son theme et son score."""
    if item.theme is Theme.OPPORTUNITY and item.score >= ACT_THRESHOLD:
        return "ACT"
    if item.score >= HIGH_THRESHOLD:
        return "HIGH"
    return ""


def _render_item(item: ReportItem) -> str:
    """Rend un item en Markdown : titre, resume, score, lien, badge."""
    badge = priority_label(item)
    badge_str = f" · **{badge}**" if badge else ""
    lines: List[str] = [f"- **{item.title}** (score {item.score}){badge_str}"]
    for summary_line in item.summary:
        if summary_line:
            lines.append(f"  - {summary_line}")
    lines.append(f"  - [source]({item.url})")
    return "\n".join(lines)


def render_markdown(data: WeeklyReportData, title: str = "INTRA-AIR RESEARCH INTELLIGENCE") -> str:
    """Rend le rapport hebdomadaire complet en Markdown.

    Args:
        data: donnees agregees et triees par theme.
        title: titre du rapport.

    Returns:
        Le rapport formate en Markdown.
    """
    parts: List[str] = [
        f"# 📊 {title}",
        f"_Periode : {data.period_start} → {data.period_end}_",
        "",
    ]

    if data.total == 0:
        parts.append("Aucun element pertinent cette semaine.")
        return "\n".join(parts)

    for theme_key, items in data.groups.items():
        if not items:
            continue
        header, emoji = THEME_HEADERS.get(
            theme_key, THEME_HEADERS[Theme.UNKNOWN.value]
        )
        parts.append(f"## {emoji} {header}")
        for item in items:
            parts.append(_render_item(item))
        parts.append("")  # ligne vide entre sections

    return "\n".join(parts).rstrip() + "\n"
