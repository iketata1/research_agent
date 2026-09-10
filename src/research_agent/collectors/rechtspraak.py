"""Connecteur Rechtspraak (Open Data des tribunaux neerlandais).

Interroge l'API Open Data de Rechtspraak (endpoint de recherche `zoeken`), qui
renvoie un flux Atom (XML) d'uitspraken (decisions de justice). Chaque entree
porte un ECLI (identifiant unique europeen de jurisprudence), un titre, un lien
officiel (deeplink) et une date.

Specificites prises en charge :
- reponse au format Atom XML (parsee avec la lib standard ElementTree) ;
- filtrage temporel via le parametre `date` (borne basse) ;
- filtrage par mots-cles cote client (moisissure, ventilation, humidite...),
  la recherche plein texte serveur n'etant pas fiable ;
- l'ECLI sert d'identifiant stable et de cle metier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import httpx

from research_agent.collectors.base import BaseConnector
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

RECHTSPRAAK_SEARCH_URL = "https://data.rechtspraak.nl/uitspraken/zoeken"

# Espaces de noms du flux Atom renvoye par Rechtspraak.
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _matches_keywords(text: str, keywords: List[str]) -> bool:
    """True si `text` contient au moins un mot-cle (insensible a la casse)."""
    if not keywords:
        return True
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


class RechtspraakConnector(BaseConnector):
    """Connecteur pour l'API Open Data de la jurisprudence neerlandaise."""

    name = "rechtspraak"

    def __init__(
        self,
        keywords: Optional[List[str]] = None,
        max_results: int = 50,
        timeout: float = 20.0,
    ) -> None:
        """Initialise le connecteur.

        Args:
            keywords: mots-cles de filtrage cote client (sur le titre de la decision).
            max_results: nombre maximum de decisions a retourner.
            timeout: timeout reseau en secondes.
        """
        super().__init__(timeout=timeout)
        self.keywords = keywords or []
        self.max_results = max_results

    def _build_params(self, since: datetime) -> Dict[str, Any]:
        """Parametres de recherche : borne de date basse et taille de page."""
        return {
            "date": since.date().isoformat(),
            "max": min(self.max_results, 1000),
            "return": "DOC",  # ne retourner que les decisions avec document
        }

    def fetch(self, since: datetime) -> List[RawItem]:
        """Collecte les decisions publiees depuis `since` correspondant aux mots-cles."""
        params = self._build_params(since)
        try:
            response = self.client.get(RECHTSPRAAK_SEARCH_URL, params=params)
            response.raise_for_status()
            content = response.text
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"echec requete Rechtspraak : {exc}", source=self.name
            ) from exc

        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise ConnectorError(
                f"reponse Rechtspraak illisible (XML) : {exc}", source=self.name
            ) from exc

        entries = root.findall("atom:entry", _ATOM_NS)
        items: List[RawItem] = []
        for entry in entries:
            item = self._to_raw_item(entry)
            if item is not None:
                items.append(item)
            if len(items) >= self.max_results:
                break
        return items

    def _to_raw_item(self, entry: ElementTree.Element) -> Optional[RawItem]:
        """Normalise une entree Atom en `RawItem`, avec filtre mots-cles."""
        ecli = self._text(entry, "atom:id")
        title = self._text(entry, "atom:title")
        if not ecli or not title:
            return None

        # Filtre mots-cles sur le titre (souvent le resume de la decision).
        summary = self._text(entry, "atom:summary") or ""
        if not _matches_keywords(f"{title} {summary}", self.keywords):
            return None

        link = self._link(entry) or self._deeplink_from_ecli(ecli)
        published_at = self._parse_date(self._text(entry, "atom:updated"))

        metadata: Dict[str, Any] = {"ecli": ecli}

        return RawItem(
            source=self.name,
            title=title,
            url=link,
            published_at=published_at,
            raw_text=summary or title,
            language="nl",
            metadata=metadata,
        )

    # --- Helpers d'extraction XML --------------------------------------------

    @staticmethod
    def _text(entry: ElementTree.Element, tag: str) -> Optional[str]:
        element = entry.find(tag, _ATOM_NS)
        if element is None or element.text is None:
            return None
        text = element.text.strip()
        return text or None

    @staticmethod
    def _link(entry: ElementTree.Element) -> Optional[str]:
        link = entry.find("atom:link", _ATOM_NS)
        if link is not None:
            href = link.get("href")
            if href and href.startswith("http"):
                return href
        return None

    @staticmethod
    def _deeplink_from_ecli(ecli: str) -> str:
        """URL officielle d'une decision a partir de son ECLI."""
        return f"https://uitspraken.rechtspraak.nl/details?id={ecli}"

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return datetime(parsed.year, parsed.month, parsed.day)
