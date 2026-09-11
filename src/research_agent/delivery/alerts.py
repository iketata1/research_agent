"""Alertes instantanees pour les items critiques (score eleve).

Se branche en aval du scoring/classification : des qu'un item depasse un seuil
critique (par defaut score >= 90), une alerte concise et percutante est envoyee
immediatement sur Telegram, sur un chat dedie aux urgences si configure (sinon
le chat par defaut).

Seuls les items reellement critiques declenchent un envoi : le reste attend le
rapport hebdomadaire. La livraison est protegee par un repli (echec journalise,
non propage).
"""

from __future__ import annotations

from typing import List, Optional

from research_agent.delivery.telegram_client import TelegramClient
from research_agent.exceptions import DeliveryError
from research_agent.logging_config import get_logger
from research_agent.models import RawItem, Theme

logger = get_logger(__name__)

CRITICAL_SCORE = 90


def item_score(item: RawItem) -> int:
    """Retourne le score 0-100 d'un item (metadata llm_score, sinon relevance*100)."""
    score = item.metadata.get("llm_score")
    if score is None and item.relevance is not None:
        score = round(item.relevance * 100)
    return int(score or 0)


def is_critical(item: RawItem, threshold: int = CRITICAL_SCORE) -> bool:
    """Indique si un item atteint le seuil critique."""
    return item_score(item) >= threshold


def _priority(item: RawItem) -> str:
    """Niveau d'alerte : ACT pour une opportunite critique, HIGH sinon."""
    if item.theme is Theme.OPPORTUNITY:
        return "ACT"
    return "HIGH"


def format_alert(item: RawItem) -> str:
    """Formate un message d'alerte concis et percutant pour Telegram."""
    score = item_score(item)
    priority = _priority(item)
    icon = "\u2757" if priority == "ACT" else "\U0001F534"  # ! ou rond rouge
    # Angle metier : la justification LLM si disponible, sinon la 1re ligne de resume.
    angle = item.metadata.get("llm_justification")
    if not angle:
        summary = item.metadata.get("summary") or []
        angle = summary[0] if summary else ""
    lines = [
        f"{icon} *ALERTE {priority}* (score {score})",
        f"*{item.title}*",
    ]
    if angle:
        lines.append(str(angle))
    lines.append(str(item.url))
    return "\n".join(lines)


def send_alert(
    item: RawItem,
    client: Optional[TelegramClient] = None,
) -> bool:
    """Envoie une alerte pour un item critique, avec repli en cas d'echec.

    Returns:
        True si l'alerte a ete envoyee, False en cas d'echec (journalise).
    """
    telegram = client or _alert_client()
    try:
        telegram.send_message(format_alert(item), parse_mode="Markdown")
        logger.info("Alerte critique envoyee : '%s' (score %d).", item.title, item_score(item))
        return True
    except DeliveryError:
        logger.exception("Echec d'envoi d'alerte (ignore, pipeline non bloque).")
        return False
    finally:
        if client is None:
            telegram.close()


def process_alerts(
    items: List[RawItem],
    client: Optional[TelegramClient] = None,
    threshold: int = CRITICAL_SCORE,
) -> int:
    """Parcourt des items et envoie une alerte pour chacun atteignant le seuil.

    Args:
        items: items scores/classes.
        client: client Telegram ; un client dedie aux alertes est cree si absent.
        threshold: seuil critique de score.

    Returns:
        Le nombre d'alertes effectivement envoyees.
    """
    critical = [it for it in items if is_critical(it, threshold)]
    if not critical:
        logger.info("Aucun item critique (seuil %d).", threshold)
        return 0

    telegram = client or _alert_client()
    sent = 0
    try:
        for item in critical:
            if send_alert(item, client=telegram):
                sent += 1
    finally:
        if client is None:
            telegram.close()
    logger.info("%d alerte(s) critique(s) envoyee(s).", sent)
    return sent


def _alert_client() -> TelegramClient:
    """Construit un client Telegram pour les alertes.

    Utilise un chat d'urgence dedie (TELEGRAM_ALERT_CHAT_ID) si configure, sinon
    le chat par defaut du client.
    """
    from research_agent.config import Secrets

    secrets = Secrets()
    alert_chat = getattr(secrets, "telegram_alert_chat_id", None)
    if alert_chat:
        return TelegramClient(chat_id=alert_chat)
    return TelegramClient()
