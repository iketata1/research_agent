"""Connecteur TED (Tenders Electronic Daily, marches publics europeens).

Interroge l'API de recherche TED (v3, endpoint `notices/search`, en POST) avec
une "expert query" combinant des mots-cles et un filtre de date de publication,
puis normalise chaque avis en `RawItem`.

Les notices TED sont multilingues et leurs champs peuvent varier : la
normalisation est donc defensive (titre, date, lien extraits avec repli).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from research_agent.collectors.base import BaseConnector
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"

# Champs demandes a l'API (limite le volume de reponse).
_TED_FIELDS = [
    "publication-number",
    "notice-title",
    "publication-date",
    "links",
    "buyer-name",
    "notice-type",
]


def _first_text(value: Any) -> Optional[str]:
    """Extrait un texte lisible d'un champ TED (str, liste, ou dict multilingue)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for element in value:
            text = _first_text(element)
            if text:
                return text
        return None
    if isinstance(value, dict):
        # Champs multilingues : on privilegie EN, sinon la premiere valeur.
        for key in ("eng", "en", "ENG", "EN"):
            if key in value and value[key]:
                return _first_text(value[key])
        for element in value.values():
            text = _first_text(element)
            if text:
                return text
    return None


class TEDConnector(BaseConnector):
    """Connecteur pour l'API de recherche des marches publics europeens (TED)."""

    name = "ted"

    def __init__(
        self,
        query: str,
        max_results: int = 100,
        timeout: float = 20.0,
    ) -> None:
        """Initialise le connecteur.

        Args:
            query: mots-cles (ex. "ventilation OR indoor air quality").
            max_results: nombre maximum d'avis a collecter.
            timeout: timeout reseau en secondes.
        """
        super().__init__(timeout=timeout)
        self.query = query
        self.max_results = max_results

    def _build_expert_query(self, since: datetime) -> str:
        """Construit l'expert query TED : mots-cles + filtre de date de publication."""
        date_str = since.date().isoformat().replace("-", "")
        return f"({self.query}) AND publication-date>={date_str}"

    def _build_payload(self, since: datetime) -> Dict[str, Any]:
        return {
            "query": self._build_expert_query(since),
            "fields": _TED_FIELDS,
            "limit": min(self.max_results, 250),
            "scope": "ACTIVE",
        }

    def fetch(self, since: datetime) -> List[RawItem]:
        """Collecte les avis TED pertinents publies depuis `since`."""
        payload = self._build_payload(since)
        try:
            response = self.client.post(TED_SEARCH_URL, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorError(f"echec requete TED : {exc}", source=self.name) from exc
        except ValueError as exc:  # JSON invalide
            raise ConnectorError(f"reponse TED illisible : {exc}", source=self.name) from exc

        notices = data.get("notices") or data.get("results") or []
        items: List[RawItem] = []
        for notice in notices[: self.max_results]:
            item = self._to_raw_item(notice)
            if item is not None:
                items.append(item)
        return items

    def _to_raw_item(self, notice: Dict[str, Any]) -> Optional[RawItem]:
        """Normalise une notice TED en `RawItem` (ou None si inexploitable)."""
        title = _first_text(notice.get("notice-title"))
        if not title:
            return None

        pub_number = _first_text(notice.get("publication-number"))
        url = self._extract_url(notice, pub_number)
        if not url:
            return None

        published_at = self._parse_date(_first_text(notice.get("publication-date")))

        metadata: Dict[str, Any] = {
            "publication_number": pub_number,
            "buyer": _first_text(notice.get("buyer-name")),
            "notice_type": _first_text(notice.get("notice-type")),
        }

        return RawItem(
            source=self.name,
            title=title,
            url=url,
            published_at=published_at,
            raw_text=title,  # la recherche renvoie surtout le titre ; enrichi en aval
            language="en",
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(pub_date: Optional[str]) -> Optional[datetime]:
        """Parse une date de publication TED (jour), fuseau ignore.

        Les dates de publication sont a granularite journaliere ; on normalise
        donc a minuit, en ignorant l'eventuel decalage horaire.
        """
        if not pub_date:
            return None
        try:
            parsed = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
        except ValueError:
            return None
        return datetime(parsed.year, parsed.month, parsed.day)

    @staticmethod
    def _extract_url(notice: Dict[str, Any], pub_number: Optional[str]) -> Optional[str]:
        """Determine l'URL officielle de l'avis (lien fourni, sinon URL derivee)."""
        links = notice.get("links")
        link = _first_text(links)
        if link and link.startswith("http"):
            return link
        if pub_number:
            # URL pereenne construite a partir du numero de publication.
            return f"https://ted.europa.eu/udl?uri=TED:NOTICE:{pub_number}:TEXT:EN:HTML"
        return None
