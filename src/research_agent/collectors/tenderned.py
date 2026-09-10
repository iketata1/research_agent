"""Connecteur TenderNed (marches publics neerlandais, flux RSS).

Recupere le flux RSS de TenderNed, filtre les appels d'offres par date (`since`)
et par mots-cles (qualite de l'air, ventilation, humidite...), puis normalise
chaque entree en `RawItem`.

Le RSS TenderNed n'offre pas de recherche serveur fiable : le filtrage par
mots-cles se fait donc cote client, sur le titre et la description. Les mots-cles
proviennent de la configuration (multilingues), ce qui permet de cibler les
tenders pertinents pour Intra-Air.
"""

from __future__ import annotations

from datetime import datetime, timezone
from time import mktime
from typing import Any, Dict, List, Optional

import feedparser
import httpx

from research_agent.collectors.base import BaseConnector
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem


def _entry_datetime(entry: Any) -> Optional[datetime]:
    """Extrait la date de publication d'une entree RSS (naive -> None si absente)."""
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed is None:
        return None
    return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc).replace(tzinfo=None)


def _matches_keywords(text: str, keywords: List[str]) -> bool:
    """Indique si le texte contient au moins un des mots-cles (insensible a la casse).

    Si aucun mot-cle n'est fourni, tout passe (pas de filtrage).
    """
    if not keywords:
        return True
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


class TenderNedConnector(BaseConnector):
    """Connecteur pour le flux RSS des marches publics TenderNed."""

    name = "tenderned"

    def __init__(
        self,
        feed_url: str,
        keywords: Optional[List[str]] = None,
        max_results: int = 100,
        timeout: float = 20.0,
    ) -> None:
        """Initialise le connecteur.

        Args:
            feed_url: URL du flux RSS TenderNed.
            keywords: mots-cles de filtrage cote client (titre + description).
            max_results: nombre maximum d'items a retourner.
            timeout: timeout reseau en secondes.
        """
        super().__init__(timeout=timeout)
        self.feed_url = feed_url
        self.keywords = keywords or []
        self.max_results = max_results

    def fetch(self, since: datetime) -> List[RawItem]:
        """Collecte les tenders publies depuis `since` et correspondant aux mots-cles."""
        try:
            response = self.client.get(self.feed_url)
            response.raise_for_status()
            content = response.text
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"echec requete TenderNed : {exc}", source=self.name
            ) from exc

        feed = feedparser.parse(content)
        # feedparser signale un flux mal forme via `bozo` ; on tolere mais on journalise.
        if getattr(feed, "bozo", 0) and not feed.entries:
            raise ConnectorError(
                "flux RSS TenderNed illisible ou vide", source=self.name
            )

        items: List[RawItem] = []
        for entry in feed.entries:
            item = self._to_raw_item(entry, since)
            if item is not None:
                items.append(item)
            if len(items) >= self.max_results:
                break
        return items

    def _to_raw_item(self, entry: Any, since: datetime) -> Optional[RawItem]:
        """Normalise une entree RSS en `RawItem`, avec filtres date + mots-cles."""
        title = getattr(entry, "title", None)
        link = getattr(entry, "link", None)
        if not title or not link:
            return None

        description = getattr(entry, "summary", "") or getattr(entry, "description", "")

        # Filtre mots-cles (titre + description).
        if not _matches_keywords(f"{title} {description}", self.keywords):
            return None

        # Filtre date : on ecarte ce qui est anterieur a `since` (si date connue).
        published_at = _entry_datetime(entry)
        if published_at is not None and published_at < since:
            return None

        metadata: Dict[str, Any] = {}
        if getattr(entry, "id", None):
            metadata["guid"] = entry.id

        return RawItem(
            source=self.name,
            title=title,
            url=link,
            published_at=published_at,
            raw_text=description,
            language="nl",
            metadata=metadata,
        )
