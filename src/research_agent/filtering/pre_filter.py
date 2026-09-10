"""Pre-filtre deterministe par mots-cles (gratuit, en amont du LLM).

Objectif : ecarter instantanement le bruit evident AVANT tout appel LLM, pour
limiter le cout. Le pre-filtre applique trois regles configurables :

1. exclusions : la presence d'un terme exclu provoque un rejet immediat ;
2. requis : si des termes requis sont definis, au moins un doit etre present ;
3. optionnels : si aucun terme requis n'est defini, la presence d'au moins un
   terme optionnel suffit a passer (sinon, l'absence d'optionnels est toleree).

Le resultat est attache a l'item : `metadata["passed_prefilter"]` (bool) et,
en cas de rejet, `metadata["prefilter_reason"]` (motif, pour la tracabilite).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from research_agent.config import PreFilterConfig
from research_agent.logging_config import get_logger
from research_agent.models import RawItem

logger = get_logger(__name__)


@dataclass
class PreFilterResult:
    """Issue du pre-filtrage d'un item."""

    passed: bool
    reason: Optional[str] = None          # motif de rejet (si passed=False)
    matched: Optional[str] = None         # terme ayant permis de passer (si passed=True)


def _searchable_text(item: RawItem) -> str:
    """Concatene le texte examinable d'un item (titre + contenu), en minuscules."""
    return f"{item.title}\n{item.raw_text}".lower()


def _first_match(text: str, terms: List[str]) -> Optional[str]:
    """Retourne le premier terme present dans `text`, ou None."""
    for term in terms:
        if term and term.lower() in text:
            return term
    return None


def evaluate(item: RawItem, config: PreFilterConfig) -> PreFilterResult:
    """Evalue un item selon les regles de pre-filtrage (sans le modifier).

    Args:
        item: element a evaluer.
        config: regles required / optional / excluded.

    Returns:
        Le resultat du pre-filtrage.
    """
    text = _searchable_text(item)

    # 1. Exclusions : rejet immediat.
    excluded = _first_match(text, config.excluded)
    if excluded:
        return PreFilterResult(passed=False, reason=f"terme exclu : '{excluded}'")

    # 2. Requis : au moins un doit etre present (si la liste est non vide).
    if config.required:
        matched = _first_match(text, config.required)
        if matched is None:
            return PreFilterResult(
                passed=False, reason="aucun terme requis present"
            )
        return PreFilterResult(passed=True, matched=matched)

    # 3. Optionnels : si des optionnels sont definis, il en faut au moins un.
    if config.optional:
        matched = _first_match(text, config.optional)
        if matched is None:
            return PreFilterResult(
                passed=False, reason="aucun terme pertinent present"
            )
        return PreFilterResult(passed=True, matched=matched)

    # Aucune regle definie : on laisse passer (le LLM tranchera).
    return PreFilterResult(passed=True)


def pre_filter_item(item: RawItem, config: PreFilterConfig) -> bool:
    """Evalue un item et annote ses metadonnees avec le resultat.

    Args:
        item: element a pre-filtrer (modifie en place : metadonnees annotees).
        config: regles de pre-filtrage.

    Returns:
        True si l'item passe le pre-filtre, False sinon.
    """
    result = evaluate(item, config)
    item.metadata["passed_prefilter"] = result.passed
    if result.passed:
        if result.matched:
            item.metadata["prefilter_matched"] = result.matched
    else:
        item.metadata["prefilter_reason"] = result.reason
    return result.passed


def pre_filter(items: List[RawItem], config: PreFilterConfig) -> List[RawItem]:
    """Applique le pre-filtre a une liste et retourne uniquement ceux qui passent.

    Tous les items sont annotes (passants comme rejetes) ; seuls les passants
    sont retournes, prets a etre envoyes au LLM.

    Args:
        items: items collectes.
        config: regles de pre-filtrage.

    Returns:
        La liste des items ayant passe le pre-filtre.
    """
    kept: List[RawItem] = []
    for item in items:
        if pre_filter_item(item, config):
            kept.append(item)
    logger.info(
        "Pre-filtre : %d/%d item(s) conserve(s).", len(kept), len(items)
    )
    return kept
