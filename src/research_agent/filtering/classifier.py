"""Classification thematique par le LLM (research / opportunity / legal / technology).

Categorise chaque item (deja score) dans l'une des quatre categories strategiques
d'Intra-Air, correspondant a l'enum `Theme` :

- research     : articles scientifiques, etudes ;
- opportunity  : appels d'offres, marches publics ;
- legal        : decisions de justice (ECLI), lois, decrets ;
- technology   : nouvelles methodes/solutions techniques, actualites du secteur.

Le LLM renvoie un JSON structure (label strict + niveau de confiance), valide par
Pydantic. Un label hors des quatre choix retombe sur `Theme.UNKNOWN`.

Le resultat enrichit l'item avant stockage :
- `item.theme` = categorie attribuee ;
- `item.metadata["theme_confidence"]` = confiance [0, 1].
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from research_agent.exceptions import LLMError
from research_agent.llm.client import LLMClient
from research_agent.logging_config import get_logger
from research_agent.models import RawItem, Theme

logger = get_logger(__name__)

_VALID_THEMES = {t.value for t in Theme if t is not Theme.UNKNOWN}

SYSTEM_PROMPT = (
    "Tu es un analyste pour Intra-Air. Classe chaque document dans EXACTEMENT "
    "une des quatre categories suivantes :\n"
    "- research : articles scientifiques, etudes, publications academiques ;\n"
    "- opportunity : appels d'offres, marches publics, tenders ;\n"
    "- legal : decisions de justice (ECLI), lois, decrets, reglementations ;\n"
    "- technology : nouvelles methodes/solutions techniques, actualites du secteur.\n\n"
    "Reponds UNIQUEMENT avec un objet JSON de la forme :\n"
    '{"classifications": [{"index": <int>, "category": '
    '"research|opportunity|legal|technology", "confidence": <float 0-1>}]}\n'
    "Inclus une entree par document, en reprenant son index."
)


class Classification(BaseModel):
    """Classification d'un document renvoyee par le LLM."""

    index: int
    category: Theme
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, value):
        """Normalise le label ; tout label hors des 4 choix devient UNKNOWN."""
        if isinstance(value, Theme):
            return value
        if isinstance(value, str) and value.strip().lower() in _VALID_THEMES:
            return value.strip().lower()
        return Theme.UNKNOWN


class ClassificationResponse(BaseModel):
    """Structure attendue de la reponse LLM."""

    classifications: List[Classification] = Field(default_factory=list)


def build_batch_prompt(items: List[RawItem]) -> str:
    """Construit le prompt listant les documents a classer."""
    lines = ["Voici les documents a classer :\n"]
    for idx, item in enumerate(items):
        excerpt = (item.raw_text or "").strip().replace("\n", " ")
        if len(excerpt) > 300:
            excerpt = excerpt[:300] + "..."
        lines.append(
            f"[{idx}] source={item.source}\ntitre: {item.title}\nextrait: {excerpt}\n"
        )
    return "\n".join(lines)


def _parse(raw: str) -> Dict[int, Classification]:
    """Parse la reponse JSON en mapping index -> Classification.

    Raises:
        LLMError: si le JSON est absent ou la structure invalide.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise LLMError(f"reponse de classification illisible (JSON) : {exc}") from exc
    try:
        parsed = ClassificationResponse.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"structure de classification invalide : {exc}") from exc
    return {c.index: c for c in parsed.classifications}


def classify_items(
    items: List[RawItem],
    client: Optional[LLMClient] = None,
    batch_size: int = 5,
) -> List[RawItem]:
    """Classe une liste d'items par lots et enrichit chacun avec son theme.

    Args:
        items: items a classer (typiquement deja scores).
        client: client LLM ; instancie par defaut si absent.
        batch_size: nombre d'items classes par requete.

    Returns:
        La liste des items, chacun annote (theme + confiance). Un item absent de
        la reponse recoit `Theme.UNKNOWN`.
    """
    if not items:
        return []
    llm = client or LLMClient()

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        prompt = build_batch_prompt(batch)
        raw = llm.generate(prompt, system_prompt=SYSTEM_PROMPT)
        results = _parse(raw)
        for idx, item in enumerate(batch):
            result = results.get(idx)
            if result is not None:
                item.theme = result.category
                item.metadata["theme_confidence"] = result.confidence
            else:
                item.theme = Theme.UNKNOWN
                item.metadata["theme_confidence"] = 0.0
        logger.info(
            "Classification LLM : lot %d-%d (%d item(s)).",
            start,
            start + len(batch) - 1,
            len(batch),
        )
    return items
