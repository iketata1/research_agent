"""Monitoring leger : logs fichier rotatifs + alerte Telegram sur crash.

Fournit :
- `setup_monitoring()` : configure le logging centralise vers un fichier rotatif
  (logs/agent.log) en plus de la console ;
- `notify_failure()` : envoie une alerte Telegram d'urgence contenant le type de
  run, un identifiant et une stacktrace tronquee ;
- `guard()` : gestionnaire de contexte qui enveloppe un point d'entree, journalise
  toute exception fatale, declenche l'alerte, puis relaie l'erreur.
"""

from __future__ import annotations

import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from research_agent.config import PROJECT_ROOT
from research_agent.delivery.telegram_client import TelegramClient
from research_agent.exceptions import DeliveryError
from research_agent.logging_config import setup_logging, get_logger

logger = get_logger(__name__)

DEFAULT_LOG_FILE = PROJECT_ROOT / "logs" / "agent.log"

# Longueur maximale de la stacktrace incluse dans l'alerte Telegram.
_MAX_TRACE_CHARS = 1500


def setup_monitoring(log_file: Optional[Path] = None, level: str = "INFO") -> None:
    """Initialise le logging centralise : console + fichier rotatif.

    Args:
        log_file: chemin du fichier de log (par defaut logs/agent.log).
        level: niveau de log minimal.
    """
    path = log_file or DEFAULT_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(level=level, log_file=path)


def _truncate_trace(exc: BaseException, limit: int = _MAX_TRACE_CHARS) -> str:
    """Retourne la stacktrace de `exc`, tronquee a `limit` caracteres."""
    trace = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    if len(trace) > limit:
        return trace[-limit:]  # on garde la fin (la plus utile au diagnostic)
    return trace


def notify_failure(
    run_type: str,
    exc: BaseException,
    run_id: Optional[str] = None,
    client: Optional[TelegramClient] = None,
) -> bool:
    """Envoie une alerte Telegram signalant l'echec critique d'un run.

    Args:
        run_type: type de run concerne ("daily" / "weekly").
        exc: exception fatale interceptee.
        run_id: identifiant du run (optionnel).
        client: client Telegram ; instancie par defaut si absent.

    Returns:
        True si l'alerte a ete envoyee, False sinon (echec journalise, non propage).
    """
    identifier = f" (run {run_id})" if run_id else ""
    message = (
        f"\U0001F6A8 *ECHEC CRITIQUE* — run {run_type}{identifier}\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"```\n{_truncate_trace(exc)}\n```"
    )
    telegram = client or TelegramClient()
    try:
        telegram.send_message(message, parse_mode="Markdown")
        return True
    except DeliveryError:
        logger.exception("Impossible d'envoyer l'alerte d'echec Telegram.")
        return False
    finally:
        if client is None:
            telegram.close()


@contextmanager
def guard(
    run_type: str,
    run_id: Optional[str] = None,
    client: Optional[TelegramClient] = None,
) -> Iterator[None]:
    """Enveloppe un point d'entree : journalise et alerte sur exception fatale.

    Toute exception qui remonte est journalisee (avec stacktrace), declenche une
    alerte Telegram, puis est propagee (le code de sortie reste non nul).

    Args:
        run_type: libelle du run protege (ex. "daily", "weekly").
        run_id: identifiant du run (optionnel).
        client: client Telegram (optionnel).
    """
    try:
        yield
    except Exception as exc:  # crash critique du pipeline
        logger.exception("Echec critique du run '%s'.", run_type)
        notify_failure(run_type, exc, run_id=run_id, client=client)
        raise
