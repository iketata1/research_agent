"""Planification de l'execution automatisee du Research Intelligence Agent.

Utilise le planificateur leger `schedule` :
- un cycle quotidien (collecte + filtrage + alertes) ;
- un cycle hebdomadaire le lundi (rapport complet + livraison Telegram).

Lancement : `python -m research_agent.scheduler` (processus long, a garder actif,
par ex. via systemd ou un conteneur). En alternative, les fonctions `run_daily`
et `run_weekly` peuvent etre appelees directement par une tache Cron.
"""

from __future__ import annotations

import time

import schedule

from research_agent.logging_config import get_logger, setup_logging
from research_agent.pipeline import run_daily, run_weekly

logger = get_logger(__name__)

# Heures d'execution (format 24h, heure locale du serveur).
DAILY_TIME = "07:00"
WEEKLY_DAY_TIME = "08:00"  # le lundi


def _safe_daily() -> None:
    """Execute le cycle quotidien en isolant toute erreur (le scheduler survit)."""
    try:
        run_daily()
    except Exception:
        logger.exception("Cycle quotidien en echec (le planificateur continue).")


def _safe_weekly() -> None:
    """Execute le cycle hebdomadaire en isolant toute erreur."""
    try:
        run_weekly()
    except Exception:
        logger.exception("Cycle hebdomadaire en echec (le planificateur continue).")


def build_schedule(scheduler: schedule.Scheduler | None = None) -> schedule.Scheduler:
    """Enregistre les taches planifiees et retourne le planificateur.

    Args:
        scheduler: planificateur a configurer ; l'instance globale par defaut.

    Returns:
        Le planificateur configure (2 taches : quotidienne et hebdomadaire).
    """
    sched = scheduler or schedule.default_scheduler
    sched.every().day.at(DAILY_TIME).do(_safe_daily)
    sched.every().monday.at(WEEKLY_DAY_TIME).do(_safe_weekly)
    logger.info(
        "Planification : quotidien a %s, hebdo (lundi) a %s.",
        DAILY_TIME,
        WEEKLY_DAY_TIME,
    )
    return sched


def main(poll_interval: int = 60) -> None:  # pragma: no cover (boucle infinie)
    """Demarre la boucle de planification (processus long).

    Args:
        poll_interval: intervalle (secondes) entre deux verifications des taches.
    """
    setup_logging()
    build_schedule()
    logger.info("Planificateur demarre. En attente des echeances...")
    while True:
        schedule.run_pending()
        time.sleep(poll_interval)


if __name__ == "__main__":  # pragma: no cover
    main()
