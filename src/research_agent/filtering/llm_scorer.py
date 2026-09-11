"""Scoring de pertinence par le LLM (score 0-100), par lot.

Prend des `RawItem` ayant passe le pre-filtre deterministe et demande au LLM
d'evaluer chacun par rapport aux axes strategiques d'Intra-Air. Le traitement
se fait par lot : plusieurs items sont envoyes en une requete, le LLM renvoie un
JSON structure (une entree par item, avec score et justification).

Le parsing est defensif : JSON mal forme -> `LLMError` ; scores hors bornes
clampes a [0, 100] ; items absents de la reponse -> score de securite 0.

Le score 0-100 est attache a l'item :
- `item.relevance` = score / 100 (normalise en [0, 1], coherent avec le modele) ;
- `item.metadata["llm_score"]` = score brut 0-100 ;
- `item.metadata["llm_justification"]` = courte justification.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from research_agent.exceptions import LLMError
from research_agent.llm.client import LLMClient
from research_agent.logging_config import get_logger
from research_agent.models import RawItem

logger = get_logger(__name__)

# Prompt systeme expert : ancre l'evaluation sur le domaine metier d'Intra-Air.
SYSTEM_PROMPT = (
    "Tu es un analyste commercial pour Intra-Air, entreprise neerlandaise qui "
    "realise des INSPECTIONS de ventilation, d'humidite/moisissure (schimmel) et "
    "d'odeurs (geur) dans le logement. L'objectif est de trouver des CLIENTS "
    "POTENTIELS : proprietaires, bailleurs et woningcorporaties qui ont un besoin "
    "d'inspection MAINTENANT.\n\n"
    "Note haut (80-100) un document qui revele une DEMANDE ou un BESOIN concret :\n"
    "- un appel d'offres / tender pour une inspection ou un diagnostic ;\n"
    "- un bailleur confronte a des plaintes de moisissure / humidite / odeurs ;\n"
    "- une decision de justice obligeant un bailleur a agir ;\n"
    "- une obligation reglementaire creant un besoin d'inspection.\n"
    "Note bas (0-30) la veille technologique, la recherche academique, et tout ce "
    "qui n'indique pas un client a demarcher pour une INSPECTION. "
    "Privilegie fortement les besoins situes aux PAYS-BAS.\n\n"
    "Pour chaque document, attribue un score entier de 0 a 100 et une "
    "justification courte (une phrase). Reponds UNIQUEMENT avec un objet JSON :\n"
    '{"scores": [{"index": <int>, "score": <int 0-100>, '
    '"justification": "<texte>"}]}\n'
    "Inclus une entree par document, en reprenant son index."
)


class ScoreEntry(BaseModel):
    """Une entree de score renvoyee par le LLM pour un document."""

    index: int
    score: int
    justification: str = ""

    @field_validator("score")
    @classmethod
    def _clamp(cls, value: int) -> int:
        return max(0, min(100, value))


class ScoreResponse(BaseModel):
    """Structure attendue de la reponse LLM."""

    scores: List[ScoreEntry] = Field(default_factory=list)


def build_batch_prompt(items: List[RawItem]) -> str:
    """Construit le prompt utilisateur listant les documents a evaluer."""
    lines = ["Voici les documents a evaluer :\n"]
    for idx, item in enumerate(items):
        excerpt = (item.raw_text or "").strip().replace("\n", " ")
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + "..."
        lines.append(
            f"[{idx}] source={item.source}\n"
            f"titre: {item.title}\n"
            f"extrait: {excerpt}\n"
        )
    return "\n".join(lines)


def _parse_scores(raw: str) -> Dict[int, ScoreEntry]:
    """Parse la reponse JSON du LLM en un mapping index -> ScoreEntry.

    Raises:
        LLMError: si le JSON est absent ou invalide.
    """
    text = raw.strip()
    # Tolerance : certains modeles entourent le JSON de balises ```json ... ```.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise LLMError(f"reponse de scoring illisible (JSON) : {exc}") from exc
    try:
        parsed = ScoreResponse.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"structure de scoring invalide : {exc}") from exc
    return {entry.index: entry for entry in parsed.scores}


def score_items(
    items: List[RawItem],
    client: Optional[LLMClient] = None,
    batch_size: int = 5,
) -> List[RawItem]:
    """Score une liste d'items via le LLM, par lots, et annote chaque item.

    Args:
        items: items ayant passe le pre-filtre.
        client: client LLM ; instancie par defaut si absent.
        batch_size: nombre d'items evalues par requete LLM.

    Returns:
        La liste des items, chacun annote (relevance + metadonnees de score).
        Un item absent de la reponse LLM recoit un score de securite 0.
    """
    if not items:
        return []
    llm = client or LLMClient()

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        prompt = build_batch_prompt(batch)
        raw = llm.generate(prompt, system_prompt=SYSTEM_PROMPT)
        scores = _parse_scores(raw)
        for idx, item in enumerate(batch):
            entry = scores.get(idx)
            score = entry.score if entry is not None else 0
            justification = entry.justification if entry is not None else "non evalue"
            item.relevance = score / 100.0
            item.metadata["llm_score"] = score
            item.metadata["llm_justification"] = justification
        logger.info(
            "Scoring LLM : lot %d-%d evalue (%d item(s)).",
            start,
            start + len(batch) - 1,
            len(batch),
        )
    return items
