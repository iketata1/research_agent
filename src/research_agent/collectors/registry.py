"""Registre des connecteurs : construit les instances a partir de la configuration.

Chaque source declaree dans `config.yaml` (et activee) est associee ici a une
fabrique qui instancie le connecteur correspondant avec ses parametres (query,
feed_url, max_results) et les mots-cles globaux de veille.

`build_connectors()` retourne la liste des connecteurs actifs, prets a etre
declenches par l'orchestrateur.
"""

from __future__ import annotations

from typing import Callable, Dict, List

from research_agent.collectors.aedes import AedesConnector
from research_agent.collectors.base import BaseConnector
from research_agent.collectors.googlenews import GoogleNewsConnector
from research_agent.collectors.openalex import OpenAlexConnector
from research_agent.collectors.rechtspraak import RechtspraakConnector
from research_agent.collectors.ted import TEDConnector
from research_agent.collectors.tenderned import TenderNedConnector
from research_agent.config import AppConfig, SourceConfig, Secrets
from research_agent.logging_config import get_logger

logger = get_logger(__name__)


def _build_openalex(cfg: SourceConfig, keywords: List[str]) -> BaseConnector:
    return OpenAlexConnector(
        query=cfg.query or " OR ".join(keywords),
        max_results=cfg.max_results,
        mailto=Secrets().openalex_mailto,
    )


def _build_via_google_news(name: str, cfg: SourceConfig, keywords: List[str]) -> BaseConnector:
    """Construit un connecteur Google News avec un nom de source specifique.

    Utilise pour les sources dont le flux RSS officiel n'est plus exploitable
    (TenderNed, Aedes) : on capte leur actualite via Google News (requete `site:`).
    """
    connector = GoogleNewsConnector(
        query=cfg.query or " OR ".join(keywords),
        keywords=None,
        max_results=cfg.max_results,
    )
    connector.name = name  # tag des items avec la vraie source
    return connector


def _build_tenderned(cfg: SourceConfig, keywords: List[str]) -> BaseConnector:
    # Si un feed_url RSS est fourni, on l'utilise ; sinon, via Google News.
    if cfg.feed_url:
        return TenderNedConnector(
            feed_url=cfg.feed_url, keywords=keywords, max_results=cfg.max_results
        )
    return _build_via_google_news("tenderned", cfg, keywords)


def _build_aedes(cfg: SourceConfig, keywords: List[str]) -> BaseConnector:
    if cfg.feed_url:
        return AedesConnector(
            feed_url=cfg.feed_url, keywords=keywords, max_results=cfg.max_results
        )
    return _build_via_google_news("aedes", cfg, keywords)


def _build_google_news(cfg: SourceConfig, keywords: List[str]) -> BaseConnector:
    return GoogleNewsConnector(
        query=cfg.query or " OR ".join(keywords),
        keywords=None,  # la requete fait deja le filtrage cote serveur
        max_results=cfg.max_results,
    )


def _build_ted(cfg: SourceConfig, keywords: List[str]) -> BaseConnector:
    # Filtre Pays-Bas (buyer-country=NLD) : priorite au marche neerlandais.
    return TEDConnector(
        query=cfg.query or " OR ".join(keywords),
        max_results=cfg.max_results,
        country="NLD",
    )


def _build_rechtspraak(cfg: SourceConfig, keywords: List[str]) -> BaseConnector:
    # Si une query de recherche web est fournie, on capte via Google News
    # (plus fiable pour trouver les decisions pertinentes que le filtre titre).
    # Sinon, on interroge directement l'API Open Data Rechtspraak.
    if cfg.query and cfg.query.startswith("site:"):
        return _build_via_google_news("rechtspraak", cfg, keywords)
    return RechtspraakConnector(keywords=keywords, max_results=cfg.max_results)


# Association nom de source -> fabrique de connecteur.
CONNECTOR_FACTORIES: Dict[str, Callable[[SourceConfig, List[str]], BaseConnector]] = {
    "openalex": _build_openalex,
    "tenderned": _build_tenderned,
    "aedes": _build_aedes,
    "google_news": _build_google_news,
    "ted": _build_ted,
    "rechtspraak": _build_rechtspraak,
}


def build_connectors(app_config: AppConfig) -> List[BaseConnector]:
    """Instancie les connecteurs des sources activees dans la configuration.

    Args:
        app_config: configuration fonctionnelle du projet.

    Returns:
        La liste des connecteurs actifs. Les sources inconnues ou desactivees
        sont ignorees (avec un avertissement pour les inconnues).
    """
    keywords = app_config.keywords.all()
    connectors: List[BaseConnector] = []
    for name, source_cfg in app_config.sources.items():
        if not source_cfg.enabled:
            continue
        factory = CONNECTOR_FACTORIES.get(name)
        if factory is None:
            logger.warning("Source inconnue ignoree : '%s'.", name)
            continue
        connectors.append(factory(source_cfg, keywords))
    return connectors
