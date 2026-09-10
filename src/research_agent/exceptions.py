"""Exceptions personnalisees du Research Intelligence Agent.

Une hierarchie unique enracinee sur `ResearchAgentError` permet au pipeline de
capturer proprement les pannes par categorie (un connecteur qui tombe, un appel
LLM qui echoue, une erreur de base) sans faire tomber toute l'execution.

Convention :
- `ResearchAgentError` : racine, a attraper pour toute erreur "attendue" du projet.
- Les sous-classes ajoutent du contexte (source, provider) pour la tracabilite.
"""

from __future__ import annotations

from typing import Optional


class ResearchAgentError(Exception):
    """Exception de base pour toutes les erreurs du projet.

    Alias historique : `ResearchAgentBaseException` (voir plus bas).
    """


class ConfigError(ResearchAgentError):
    """Configuration manquante, invalide ou incoherente."""


class ConnectorError(ResearchAgentError):
    """Echec lors de la collecte depuis une source (API ou RSS).

    Attributes:
        source: identifiant de la source concernee (ex. "openalex", "aedes").
    """

    def __init__(self, message: str, source: Optional[str] = None) -> None:
        self.source = source
        prefix = f"[source={source}] " if source else ""
        super().__init__(f"{prefix}{message}")


class LLMError(ResearchAgentError):
    """Echec lors d'un appel au modele de langage (filtrage / resume).

    Attributes:
        provider: fournisseur LLM concerne (ex. "openai", "mistral").
    """

    def __init__(self, message: str, provider: Optional[str] = None) -> None:
        self.provider = provider
        prefix = f"[provider={provider}] " if provider else ""
        super().__init__(f"{prefix}{message}")


class DatabaseError(ResearchAgentError):
    """Echec lors d'une operation de stockage (lecture / ecriture)."""


class DeliveryError(ResearchAgentError):
    """Echec lors de l'envoi du rapport (Telegram / email)."""


# Alias retro-compatible avec le nom mentionne dans la specification de tache.
ResearchAgentBaseException = ResearchAgentError
