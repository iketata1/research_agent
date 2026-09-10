"""Connecteur Google News (RSS de recherche par mots-cles).

Google News expose un flux RSS de recherche : on construit l'URL du flux a partir
d'une requete de mots-cles cibles (ex. "schimmel woning OR mould housing"). Le
flux renvoie les articles recents du web entier correspondant a la requete.

Specificites Google News prises en charge :
- l'URL du flux est derivee de la requete (pas de feed_url fixe) ;
- le titre inclut souvent le nom du media en suffixe (" - NOS") ;
- une balise `source` donne l'editeur d'origine, place dans les metadonnees.

Le filtrage par date (`since`), le parsing et la normalisation reposent sur la
base commune `RSSConnector`.
"""

from __future__ import annotations

from typing import Any, List, Optional
from urllib.parse import quote_plus

from research_agent.collectors.rss import RSSConnector
from research_agent.models import RawItem

# Modele d'URL du flux de recherche Google News.
# hl/gl/ceid ciblent la langue/pays (Pays-Bas par defaut, adapte au domaine metier).
GOOGLE_NEWS_SEARCH_URL = (
    "https://news.google.com/rss/search?q={query}&hl=nl&gl=NL&ceid=NL:nl"
)


def build_feed_url(query: str) -> str:
    """Construit l'URL du flux RSS Google News pour une requete donnee."""
    return GOOGLE_NEWS_SEARCH_URL.format(query=quote_plus(query))


class GoogleNewsConnector(RSSConnector):
    """Connecteur pour le flux RSS de recherche Google News."""

    name = "google_news"
    default_language = "nl"

    def __init__(
        self,
        query: str,
        keywords: Optional[List[str]] = None,
        max_results: int = 50,
        timeout: float = 20.0,
    ) -> None:
        """Initialise le connecteur.

        Args:
            query: requete de recherche envoyee a Google News.
            keywords: mots-cles de filtrage cote client (optionnel ; la requete
                Google News fait deja l'essentiel du filtrage cote serveur).
            max_results: nombre maximum d'articles a retourner.
            timeout: timeout reseau en secondes.
        """
        super().__init__(
            feed_url=build_feed_url(query),
            keywords=keywords,
            max_results=max_results,
            timeout=timeout,
        )
        self.query = query

    def _to_raw_item(self, entry: Any, since) -> Optional[RawItem]:
        """Normalise une entree Google News en `RawItem`.

        Reutilise la logique de base puis enrichit les metadonnees avec le media
        d'origine et nettoie le suffixe " - <media>" du titre.
        """
        item = super()._to_raw_item(entry, since)
        if item is None:
            return item

        # Nom du media d'origine (balise <source> du flux Google News).
        source_title = None
        source_obj = getattr(entry, "source", None)
        if source_obj is not None:
            source_title = getattr(source_obj, "title", None) or (
                source_obj.get("title") if isinstance(source_obj, dict) else None
            )

        if source_title:
            item.metadata["publisher"] = source_title
            # Google News suffixe souvent le titre par " - <media>".
            suffix = f" - {source_title}"
            if item.title.endswith(suffix):
                item.title = item.title[: -len(suffix)].strip()

        return item
