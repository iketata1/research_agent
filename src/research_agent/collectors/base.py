"""Interface commune a tous les connecteurs de sources.

Chaque source (OpenAlex, TenderNed, TED, Google News, Aedes, Rechtspraak) implemente
un connecteur derivant de `BaseConnector`. Le contrat central est :

    fetch(since: datetime) -> list[RawItem]

qui renvoie une liste homogene de `RawItem` normalises, quelle que soit la source.

Deux niveaux d'appel :
- `fetch()` : methode abstraite a implementer ; elle PEUT lever une exception.
- `collect()` : enveloppe sure de `fetch()` ; elle isole les pannes (journalise
  et renvoie une liste vide) afin qu'une source defaillante n'interrompe jamais
  le pipeline global. C'est le point d'entree recommande pour l'orchestrateur.

Mecanismes communs fournis :
- un `name` par connecteur (tracabilite) et un logger dedie ;
- une session HTTP `httpx` partagee avec timeout configurable (creee a la demande).
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import List, Optional

import httpx

from research_agent.exceptions import ConnectorError
from research_agent.logging_config import get_logger
from research_agent.models import RawItem

# Timeout par defaut (secondes) pour les appels reseau d'un connecteur.
DEFAULT_TIMEOUT = 20.0


class BaseConnector(abc.ABC):
    """Classe de base abstraite pour un connecteur de source.

    Attributes:
        name: identifiant court de la source (ex. "openalex"), utilise dans les
            logs et pour tracer l'origine des items et des erreurs.
    """

    #: Nom de la source. Les sous-classes DOIVENT le definir.
    name: str = "base"

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self.logger = get_logger(f"collectors.{self.name}")
        self._client: Optional[httpx.Client] = None

    # --- Contrat obligatoire -------------------------------------------------

    @abc.abstractmethod
    def fetch(self, since: datetime) -> List[RawItem]:
        """Collecte les items publies/mis a jour depuis `since`.

        Args:
            since: borne temporelle basse (ne recuperer que ce qui est plus recent).

        Returns:
            Une liste de `RawItem` normalises.

        Raises:
            ConnectorError: en cas d'echec de collecte (reseau, parsing...).
                Les sous-classes devraient encapsuler leurs erreurs ici.
        """
        raise NotImplementedError

    # --- Enveloppe sure -------------------------------------------------------

    def collect(self, since: datetime) -> List[RawItem]:
        """Appelle `fetch()` en isolant toute panne.

        Une exception dans `fetch()` est journalisee et convertie en liste vide,
        pour qu'une source defaillante n'interrompe pas la collecte des autres.

        Args:
            since: borne temporelle basse.

        Returns:
            Les items collectes, ou une liste vide en cas d'echec.
        """
        try:
            self.logger.info("Collecte depuis %s...", since.isoformat())
            items = self.fetch(since)
            self.logger.info("Collecte terminee : %d item(s).", len(items))
            return items
        except ConnectorError:
            self.logger.exception("Echec du connecteur '%s' (ignore).", self.name)
            return []
        except Exception:  # filet de securite : aucune source ne doit casser le pipeline
            self.logger.exception(
                "Erreur inattendue du connecteur '%s' (ignore).", self.name
            )
            return []
        finally:
            self.close()

    # --- Session HTTP partagee ------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        """Client HTTP `httpx` partage, cree a la demande, avec timeout."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        """Ferme la session HTTP si elle a ete ouverte."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "BaseConnector":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
