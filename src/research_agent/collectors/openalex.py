"""Connecteur OpenAlex (recherche scientifique).

Interroge l'endpoint `works` de l'API OpenAlex (https://api.openalex.org/works),
filtre par mots-cles et par date, et normalise chaque publication en `RawItem`.

Specificites OpenAlex prises en charge :
- l'abstract est fourni sous forme d'`abstract_inverted_index` (dictionnaire
  mot -> positions) ; on le reconstruit en texte lisible ;
- le DOI et l'URL pereenne sont extraits proprement ;
- le "polite pool" est utilise si un email de contact est configure (mailto),
  ce qui offre des quotas plus stables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from research_agent.collectors.base import BaseConnector
from research_agent.config import Secrets
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """Reconstruit un abstract a partir de l'`abstract_inverted_index` d'OpenAlex.

    L'index associe chaque mot a la liste de ses positions dans le texte. On
    replace donc chaque mot a ses positions puis on recompose la phrase.

    Args:
        inverted_index: dictionnaire mot -> positions, ou None.

    Returns:
        Le texte de l'abstract, ou une chaine vide si indisponible.
    """
    if not inverted_index:
        return ""
    positions: List[tuple[int, str]] = []
    for word, indexes in inverted_index.items():
        for idx in indexes:
            positions.append((idx, word))
    positions.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positions)


class OpenAlexConnector(BaseConnector):
    """Connecteur pour l'API OpenAlex (publications scientifiques)."""

    name = "openalex"

    def __init__(
        self,
        query: str,
        max_results: int = 50,
        mailto: Optional[str] = None,
        timeout: float = 20.0,
    ) -> None:
        """Initialise le connecteur.

        Args:
            query: termes de recherche (ex. "mould OR humidity OR indoor air quality").
            max_results: nombre maximum de resultats a collecter.
            mailto: email de contact pour le "polite pool" (recommande).
            timeout: timeout reseau en secondes.
        """
        super().__init__(timeout=timeout)
        self.query = query
        self.max_results = max_results
        self.mailto = mailto or Secrets().openalex_mailto

    def _build_params(self, since: datetime) -> Dict[str, Any]:
        """Construit les parametres de requete OpenAlex."""
        params: Dict[str, Any] = {
            "search": self.query,
            "filter": f"from_publication_date:{since.date().isoformat()}",
            "per-page": min(self.max_results, 200),  # OpenAlex plafonne a 200/page
            "sort": "publication_date:desc",
        }
        if self.mailto:
            params["mailto"] = self.mailto
        return params

    def fetch(self, since: datetime) -> List[RawItem]:
        """Collecte les publications OpenAlex pertinentes depuis `since`."""
        params = self._build_params(since)
        try:
            response = self.client.get(OPENALEX_WORKS_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorError(f"echec requete OpenAlex : {exc}", source=self.name) from exc
        except ValueError as exc:  # JSON invalide
            raise ConnectorError(f"reponse OpenAlex illisible : {exc}", source=self.name) from exc

        works = payload.get("results", [])
        items: List[RawItem] = []
        for work in works[: self.max_results]:
            item = self._to_raw_item(work)
            if item is not None:
                items.append(item)
        return items

    def _to_raw_item(self, work: Dict[str, Any]) -> Optional[RawItem]:
        """Normalise un `work` OpenAlex en `RawItem` (ou None si inexploitable)."""
        title = work.get("title") or work.get("display_name")
        if not title:
            return None

        # URL pereenne : l'id OpenAlex est deja une URL stable (ex. https://openalex.org/W...).
        url = work.get("id")
        doi = work.get("doi")  # ex. "https://doi.org/10.xxxx/yyy"
        if not url:
            url = doi
        if not url:
            return None

        published = work.get("publication_date")
        published_at: Optional[datetime] = None
        if published:
            try:
                published_at = datetime.fromisoformat(published)
            except ValueError:
                published_at = None

        abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

        metadata: Dict[str, Any] = {
            "openalex_id": work.get("id"),
            "type": work.get("type"),
            "cited_by_count": work.get("cited_by_count"),
        }
        # Le DOI (sans prefixe URL) sert de cle prioritaire pour l'id stable.
        if doi:
            metadata["doi"] = doi.replace("https://doi.org/", "")

        return RawItem(
            source=self.name,
            title=title,
            url=url,
            published_at=published_at,
            raw_text=abstract,
            language=work.get("language"),
            metadata=metadata,
        )
