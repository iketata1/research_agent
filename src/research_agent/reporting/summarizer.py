"""Resume LLM en 3 lignes, axe "pertinence Intra-Air".

Pour chaque item ayant passe les filtres de la Phase 4, produit un resume
ultra-concis de STRICTEMENT 3 lignes, oriente valeur metier : impact direct ou
opportunite commerciale concrete pour Intra-Air (besoin d'inspection humidite/
moisissure/ventilation/odeurs, marches publics, obligations des bailleurs).

La sortie est validee par un modele Pydantic (`Summary`) qui garantit la
contrainte des 3 lignes : la reponse du LLM est normalisee (lignes vides
retirees) puis ajustee a exactement 3 lignes.

Le resume est attache a l'item : `item.metadata["summary"]` (liste de 3 lignes)
et `item.metadata["summary_text"]` (texte joint).
"""

from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, field_validator

from research_agent.exceptions import LLMError
from research_agent.llm.client import LLMClient
from research_agent.logging_config import get_logger
from research_agent.models import RawItem

logger = get_logger(__name__)

SUMMARY_LINES = 3

SYSTEM_PROMPT = (
    "Tu es un analyste pour Intra-Air, entreprise neerlandaise qui realise des "
    "INSPECTIONS d'humidite/moisissure, de ventilation et d'odeurs dans le "
    "logement. Pour le document fourni, redige un resume de STRICTEMENT 3 lignes, "
    "chacune tres courte. Chaque ligne doit mettre en avant l'impact direct ou "
    "l'opportunite commerciale concrete pour Intra-Air (besoin d'inspection, "
    "marches publics, obligations legales des bailleurs). Sois factuel "
    "et oriente action. Reponds UNIQUEMENT avec un objet JSON de la forme :\n"
    '{"lines": ["ligne 1", "ligne 2", "ligne 3"]}'
)


class Summary(BaseModel):
    """Resume en exactement 3 lignes, oriente valeur metier."""

    lines: List[str]

    @field_validator("lines", mode="before")
    @classmethod
    def _normalize(cls, value):
        """Normalise en exactement 3 lignes non vides.

        - accepte une chaine (decoupee sur les retours a la ligne) ou une liste ;
        - retire les lignes vides ;
        - tronque a 3 lignes si trop, complete par "" si trop peu.
        """
        if isinstance(value, str):
            raw_lines = value.splitlines()
        elif isinstance(value, list):
            raw_lines = [str(v) for v in value]
        else:
            raise ValueError("format de resume invalide")

        cleaned = [line.strip() for line in raw_lines if line and line.strip()]
        if not cleaned:
            raise ValueError("resume vide")
        # Ajustement a exactement 3 lignes.
        cleaned = cleaned[:SUMMARY_LINES]
        while len(cleaned) < SUMMARY_LINES:
            cleaned.append("")
        return cleaned

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _parse_summary(raw: str) -> Summary:
    """Parse la reponse JSON du LLM en `Summary`.

    Raises:
        LLMError: si le JSON est absent, illisible ou de structure invalide.
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
        raise LLMError(f"resume illisible (JSON) : {exc}") from exc
    try:
        return Summary.model_validate(data)
    except Exception as exc:  # ValidationError et erreurs de normalisation
        raise LLMError(f"structure de resume invalide : {exc}") from exc


def _build_prompt(item: RawItem) -> str:
    excerpt = (item.raw_text or "").strip().replace("\n", " ")
    if len(excerpt) > 800:
        excerpt = excerpt[:800] + "..."
    return (
        f"source: {item.source}\n"
        f"titre: {item.title}\n"
        f"contenu: {excerpt}"
    )


def summarize_item(item: RawItem, client: Optional[LLMClient] = None) -> Summary:
    """Genere le resume 3 lignes d'un item et l'attache a ses metadonnees.

    Args:
        item: item a resumer.
        client: client LLM ; instancie par defaut si absent.

    Returns:
        Le `Summary` (3 lignes).

    Raises:
        LLMError: si la generation ou le parsing echoue.
    """
    llm = client or LLMClient()
    raw = llm.generate(_build_prompt(item), system_prompt=SYSTEM_PROMPT)
    summary = _parse_summary(raw)
    item.metadata["summary"] = summary.lines
    item.metadata["summary_text"] = summary.text
    return summary


def summarize_items(
    items: List[RawItem],
    client: Optional[LLMClient] = None,
) -> List[RawItem]:
    """Resume une liste d'items, en isolant les echecs individuels.

    Un item dont le resume echoue est journalise et laisse sans resume (le
    pipeline global n'est pas interrompu).

    Args:
        items: items a resumer.
        client: client LLM ; instancie par defaut si absent.

    Returns:
        La liste des items (ceux resumes avec succes sont annotes).
    """
    if not items:
        return []
    llm = client or LLMClient()
    for item in items:
        try:
            summarize_item(item, client=llm)
        except LLMError:
            logger.exception("Echec du resume pour '%s' (ignore).", item.id)
    return items
