"""Configuration centralisee du logging.

Objectifs :
- des logs lisibles avec horodatage, niveau et nom du logger (tracabilite) ;
- une configuration unique et idempotente (pas de handlers dupliques) ;
- une sortie console, et une sortie fichier optionnelle avec rotation dans `data/`.

Chaque module obtient son logger nomme via `get_logger(__name__)`, ce qui rend
la source de chaque ligne de log identifiable (ex. connecteur openalex, pipeline).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union

# Nom du logger racine du projet. Tous les loggers "research_agent.*" en heritent.
ROOT_LOGGER_NAME = "research_agent"

# Format lisible : 2026-09-10 09:12:00 | INFO | research_agent.pipeline | message
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Empeche une double configuration si setup_logging() est appele plusieurs fois.
_CONFIGURED = False


def setup_logging(
    level: Union[int, str] = logging.INFO,
    log_file: Optional[Union[str, Path]] = None,
    max_bytes: int = 2_000_000,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure le logger racine du projet.

    A appeler une fois au demarrage (pipeline, script, tests). Les appels
    suivants sont sans effet (idempotent), sauf reconfiguration explicite.

    Args:
        level: niveau minimal (ex. logging.DEBUG, "INFO").
        log_file: chemin d'un fichier de log. Si fourni, active la rotation.
        max_bytes: taille max d'un fichier de log avant rotation.
        backup_count: nombre de fichiers de rotation conserves.

    Returns:
        Le logger racine du projet (`research_agent`).
    """
    global _CONFIGURED

    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level)
    # On gere nos propres handlers ; on evite la remontee au root logger global.
    logger.propagate = False

    if _CONFIGURED:
        logger.setLevel(level)
        return logger

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # Sortie console (stderr par defaut).
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Sortie fichier optionnelle avec rotation.
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _CONFIGURED = True
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Retourne un logger nomme, enfant du logger racine du projet.

    Args:
        name: nom du module appelant (typiquement `__name__`). Si absent ou
            deja prefixe, il est raccorde proprement sous `research_agent`.

    Returns:
        Un logger configure et pret a l'emploi.
    """
    if not name or name == ROOT_LOGGER_NAME:
        return logging.getLogger(ROOT_LOGGER_NAME)
    if name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
