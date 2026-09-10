"""Schema pivot : modele de donnees normalise commun a toutes les sources.

Chaque connecteur (OpenAlex, TenderNed, TED, Google News, Aedes, Rechtspraak)
convertit ses items bruts en `RawItem`. Le reste du pipeline (deduplication,
filtrage, stockage, rapport) reste ainsi agnostique de la source.

Choix cles :
- Pydantic v2 pour la validation et le typage.
- `id` stable et deterministe (hash), genere automatiquement s'il n'est pas
  fourni, a partir de la meilleure cle disponible : DOI > URL > source+titre.
  Cela rend la deduplication fiable d'une execution a l'autre.
- `metadata` : dictionnaire libre pour les champs specifiques a une source.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class Theme(str, Enum):
    """Themes de regroupement dans le rapport hebdomadaire."""

    RESEARCH = "research"
    OPPORTUNITY = "opportunity"
    LEGAL = "legal"
    TECHNOLOGY = "technology"
    UNKNOWN = "unknown"


def compute_stable_id(
    *,
    doi: Optional[str] = None,
    url: Optional[str] = None,
    source: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    """Calcule un identifiant stable (SHA-256 tronque) pour la deduplication.

    On privilegie la cle la plus fiable disponible, dans l'ordre :
    DOI, puis URL, puis la combinaison source + titre.

    Args:
        doi: DOI de la ressource (le plus fiable pour la recherche).
        url: URL de la ressource.
        source: identifiant de la source.
        title: titre de la ressource.

    Returns:
        Empreinte hexadecimale de 16 caracteres.

    Raises:
        ValueError: si aucune cle exploitable n'est fournie.
    """
    if doi:
        key = f"doi:{doi.strip().lower()}"
    elif url:
        key = f"url:{str(url).strip().lower()}"
    elif source and title:
        key = f"src:{source.strip().lower()}|title:{title.strip().lower()}"
    else:
        raise ValueError(
            "Impossible de generer un id : fournir au moins un DOI, une URL, "
            "ou le couple (source, title)."
        )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class RawItem(BaseModel):
    """Element normalise, commun a toutes les sources.

    Attributes:
        id: identifiant unique stable (genere automatiquement si absent).
        source: nom de la source (ex. "openalex", "tenderned").
        title: titre de l'article / contrat / decision.
        url: lien vers la ressource d'origine.
        published_at: date de publication.
        raw_text: contenu brut ou resume extrait de la source.
        language: code langue si disponible (ex. "nl", "en", "de").
        metadata: metadonnees specifiques a la source (DOI, auteurs, montant...).
        theme: theme attribue lors du filtrage LLM (en aval).
        relevance: score de pertinence [0..1] attribue lors du filtrage (en aval).
    """

    model_config = {"extra": "forbid"}

    id: Optional[str] = None
    source: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl
    published_at: Optional[datetime] = None
    raw_text: str = ""
    language: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Champs enrichis en aval du pipeline.
    theme: Theme = Theme.UNKNOWN
    relevance: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("language")
    @classmethod
    def _normalize_language(cls, value: Optional[str]) -> Optional[str]:
        """Normalise le code langue en minuscules (ex. 'EN' -> 'en')."""
        if value is None:
            return None
        value = value.strip().lower()
        return value or None

    @model_validator(mode="after")
    def _ensure_id(self) -> "RawItem":
        """Genere l'`id` s'il n'a pas ete fourni, a partir de DOI/URL/titre."""
        if not self.id:
            self.id = compute_stable_id(
                doi=self.metadata.get("doi"),
                url=str(self.url),
                source=self.source,
                title=self.title,
            )
        return self

    def content_hash(self) -> str:
        """Empreinte du contenu porteur de sens (titre + texte).

        Sert a distinguer un vrai doublon d'une ressource dont le contenu a
        change. La `metadata` volatile est volontairement exclue pour eviter
        de fausses detections de modification.

        Returns:
            Empreinte SHA-256 hexadecimale (16 caracteres).
        """
        normalized = f"{self.title.strip().lower()}\n{self.raw_text.strip().lower()}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
