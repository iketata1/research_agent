"""Connecteur TenderNed (marches publics neerlandais, flux RSS).

Recupere le flux RSS de TenderNed, filtre les appels d'offres par date (`since`)
et par mots-cles (qualite de l'air, ventilation, humidite...), puis normalise
chaque entree en `RawItem`.

Le RSS TenderNed n'offre pas de recherche serveur fiable : le filtrage par
mots-cles se fait donc cote client (sur le titre et la description), via la
logique commune de `RSSConnector`.
"""

from __future__ import annotations

from research_agent.collectors.rss import RSSConnector


class TenderNedConnector(RSSConnector):
    """Connecteur pour le flux RSS des marches publics TenderNed."""

    name = "tenderned"
    default_language = "nl"
