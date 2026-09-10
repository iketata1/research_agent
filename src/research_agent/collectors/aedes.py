"""Connecteur Aedes (federation neerlandaise des societes de logement, RSS).

Recupere les actualites et publications d'Aedes via son flux RSS. Aedes regroupe
les bailleurs sociaux : ses publications signalent les enjeux du secteur du
logement (qualite de l'air interieur, ventilation, durabilite des batiments),
utiles pour reperer les besoins de nos clients.

Le filtrage par date (`since`) et par mots-cles, ainsi que la normalisation en
`RawItem`, sont assures par la logique commune `RSSConnector`.
"""

from __future__ import annotations

from research_agent.collectors.rss import RSSConnector


class AedesConnector(RSSConnector):
    """Connecteur pour le flux RSS d'Aedes (secteur du logement social NL)."""

    name = "aedes"
    default_language = "nl"
