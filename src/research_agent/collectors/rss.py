"""Base commune aux connecteurs de flux RSS.

Plusieurs sources (TenderNed, Aedes, Google News) sont exposees via RSS et
partagent la meme mecanique : telecharger le flux, le parser (feedparser),
filtrer par date (`since`) et par mots-cles, puis normaliser en `RawItem`.

`RSSConnector` factorise cette logique. Une source RSS concrete se resume alors
a fixer `name`, la langue par defaut, et a instancier avec son `feed_url`.
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


def entry_datetime(entry: Any) -> Optional[datetime]:
    """Extrait la date de publication d'une entree RSS (UTC naive, ou None)."""
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed is None:
        return None
    return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc).replace(tzinfo=None)


def matches_keywords(text: str, keywords: List[str]) -> bool:
    """True si `text` contient au moins un mot-cle (insensible a la casse).

    Si `keywords` est vide, aucun filtrage n'est applique (tout passe).
    """
    if not keywords:
        return True
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


class RSSConnector(BaseConnector):
    """Connecteur generique pour une source exposee en RSS.

    Attributes:
        default_language: code langue attribue aux items (les flux RSS ne le
            precisent generalement pas de maniere fiable).
    """

    name = "rss"
    default_language: Optional[str] = None

    def __init__(
        self,
        feed_url: str,
        keywords: Optional[List[str]] = None,
        max_results: int = 100,
        timeout: float = 20.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.feed_url = feed_url
        self.keywords = keywords or []
        self.max_results = max_results

    def fetch(self, since: datetime) -> List[RawItem]:
        """Collecte les entrees publiees depuis `since` correspondant aux mots-cles."""
        try:
            response = self.client.get(self.feed_url)
            response.raise_for_status()
            content = response.text
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"echec requete {self.name} : {exc}", source=self.name
            ) from exc

        feed = feedparser.parse(content)
        if getattr(feed, "bozo", 0) and not feed.entries:
            raise ConnectorError(
                f"flux RSS {self.name} illisible ou vide", source=self.name
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

        if not matches_keywords(f"{title} {description}", self.keywords):
            return None

        published_at = entry_datetime(entry)
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
            language=self.default_language,
            metadata=metadata,
        )
