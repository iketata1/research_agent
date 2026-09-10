"""Mise en forme et diffusion du rapport hebdomadaire sur Telegram.

Convertit les donnees agregees (`WeeklyReportData`) en un message adapte au
mobile : emojis thematiques, gras cible (syntaxe Markdown de Telegram : *gras*),
listes compactes. Deux niveaux :

- `format_full_report`   : rapport complet, une entree par item (avec resume) ;
- `format_executive_summary` : resume executif (les tops items par categorie).

`send_weekly_report` diffuse le message via le `TelegramClient` avec un mecanisme
de repli : en cas d'echec, l'erreur est journalisee et la fonction renvoie False
sans propager, pour ne pas bloquer le pipeline global.
"""

from __future__ import annotations

from typing import Optional

from research_agent.delivery.telegram_client import TelegramClient
from research_agent.exceptions import DeliveryError
from research_agent.logging_config import get_logger
from research_agent.models import Theme
from research_agent.reporting.aggregator import ReportItem, WeeklyReportData

logger = get_logger(__name__)

# Emoji et libelle par theme (rendu mobile).
THEME_DISPLAY = {
    Theme.RESEARCH.value: ("\U0001F52C", "RESEARCH"),
    Theme.OPPORTUNITY.value: ("\U0001F3E2", "OPPORTUNITY"),
    Theme.LEGAL.value: ("\u2696\uFE0F", "LEGAL"),
    Theme.TECHNOLOGY.value: ("\U0001F4E1", "TECHNOLOGY"),
    Theme.UNKNOWN.value: ("\U0001F4CC", "AUTRES"),
}

# Seuils de badge (coherents avec le renderer).
_ACT_THRESHOLD = 75
_HIGH_THRESHOLD = 75


def _badge(item: ReportItem) -> str:
    if item.theme is Theme.OPPORTUNITY and item.score >= _ACT_THRESHOLD:
        return " \u2757ACT"  # exclamation
    if item.score >= _HIGH_THRESHOLD:
        return " \U0001F534HIGH"  # rond rouge
    return ""


def _header(data: WeeklyReportData) -> str:
    return (
        f"\U0001F4CA *INTRA-AIR RESEARCH INTELLIGENCE*\n"
        f"_{data.period_start} \u2192 {data.period_end}_"
    )


def format_full_report(data: WeeklyReportData) -> str:
    """Formate le rapport complet pour Telegram (item + resume 3 lignes)."""
    if data.total == 0:
        return _header(data) + "\n\nAucun element pertinent cette semaine."

    parts = [_header(data), ""]
    for theme_key, items in data.groups.items():
        if not items:
            continue
        emoji, label = THEME_DISPLAY.get(theme_key, THEME_DISPLAY[Theme.UNKNOWN.value])
        parts.append(f"{emoji} *{label}*")
        for item in items:
            parts.append(f"\u2022 *{item.title}* ({item.score}){_badge(item)}")
            for line in item.summary:
                if line:
                    parts.append(f"  {line}")
            parts.append(f"  {item.url}")
        parts.append("")
    return "\n".join(parts).rstrip()


def format_executive_summary(data: WeeklyReportData, top_n: int = 3) -> str:
    """Formate un resume executif : les `top_n` items par categorie (titre + score)."""
    if data.total == 0:
        return _header(data) + "\n\nAucun element pertinent cette semaine."

    parts = [_header(data), ""]
    for theme_key, items in data.groups.items():
        if not items:
            continue
        emoji, label = THEME_DISPLAY.get(theme_key, THEME_DISPLAY[Theme.UNKNOWN.value])
        parts.append(f"{emoji} *{label}*")
        for item in items[:top_n]:
            parts.append(f"\u2022 {item.title} ({item.score}){_badge(item)}")
        parts.append("")
    return "\n".join(parts).rstrip()


def send_weekly_report(
    data: WeeklyReportData,
    client: Optional[TelegramClient] = None,
    executive: bool = False,
) -> bool:
    """Diffuse le rapport hebdomadaire sur Telegram, avec repli en cas d'echec.

    Args:
        data: donnees agregees de la semaine.
        client: client Telegram ; instancie par defaut si absent.
        executive: si True, envoie le resume executif au lieu du rapport complet.

    Returns:
        True si l'envoi a reussi, False en cas d'echec (erreur journalisee,
        non propagee : le pipeline global n'est pas bloque).
    """
    message = (
        format_executive_summary(data) if executive else format_full_report(data)
    )
    telegram = client or TelegramClient()
    try:
        chunks = telegram.send_message(message, parse_mode="Markdown")
        logger.info("Rapport hebdomadaire envoye sur Telegram (%d morceau(x)).", chunks)
        return True
    except DeliveryError:
        logger.exception("Echec de la livraison Telegram (ignore, pipeline non bloque).")
        return False
    finally:
        if client is None:
            telegram.close()
