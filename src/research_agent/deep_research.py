"""Deep research : analyse approfondie d'un item a partir de la page web reelle.

Contrairement au scoring/resume qui ne voient que le titre + un extrait, la deep
research recupere le contenu COMPLET de la page (article, tender, decision), puis
demande au LLM une analyse detaillee orientee opportunite commerciale pour Intra-Air.

Flux :
1. `fetch_page_text(url)` telecharge la page et en extrait le texte lisible ;
2. `deep_analyze(...)` envoie ce texte au LLM avec un prompt specialise et
   renvoie une fiche structuree (resume, organisation, besoin, action) ;
3. le resultat peut etre persiste dans la metadata de l'item (evite de refaire
   l'analyse et donc de repayer).

A la demande uniquement (bouton dans le dashboard) : le contenu complet consomme
plus de tokens, on ne l'execute donc pas en masse.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx
from bs4 import BeautifulSoup

from research_agent.exceptions import ConnectorError, LLMError
from research_agent.llm.client import LLMClient
from research_agent.logging_config import get_logger

logger = get_logger(__name__)

# Longueur max de texte de page envoyee au LLM (maitrise du cout).
_MAX_PAGE_CHARS = 6000

SYSTEM_PROMPT = (
    "Tu es un Analyste Senior en Intelligence Economique et Technique pour "
    "Intra-Air, une entreprise specialisee dans la qualite de l'air interieur "
    "(QAI), la ventilation, la sante des batiments et le monitoring IoT.\n\n"
    "A partir du texte complet de l'article ou du document fourni, realise une "
    "analyse approfondie et pragmatique orientee business et technique.\n\n"
    "Fournis ta reponse EXCLUSIVEMENT sous forme d'un objet JSON valide "
    "respectant scrupuleusement la structure suivante :\n"
    "{\n"
    '  "titre_analyse": "Titre explicite et synthetique de l enjeu",\n'
    '  "organisation": "Nom de l entreprise, institution publique ou organisme '
    "concerne (ou 'Non specifie')\",\n"
    '  "contexte_resume": "Resume en 3 lignes percutantes expliquant de quoi il '
    "s agit reellement et pourquoi c est important dans le secteur QAI / "
    "ventilation / reglementation.\",\n"
    '  "besoin_ou_opportunite": "Description precise de l opportunite '
    "commerciale, du marche vise ou de l exigence reglementaire pour Intra-Air.\",\n"
    '  "niveau": "Choisis strictement entre HIGH (haute pertinence, urgence '
    "critique ou gros contrat) ou ACT (action recommandee a moyen terme ou "
    "veille active).\",\n"
    '  "action": "Action concrete et immediate recommandee pour l equipe '
    "Intra-Air (ex: repondre a l appel d offres, contacter l organisme, ajuster "
    "la configuration des capteurs SEN66, auditer la conformite legale).\"\n"
    "}"
)


def _extract_google_news_target(html: str) -> Optional[str]:
    """Extrait l'URL de l'article reel depuis une page de redirection Google News."""
    import re

    # Google News encode souvent l'URL cible dans un lien ou un attribut data.
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a", href=True)
    if a and a["href"].startswith("http") and "google.com" not in a["href"]:
        return a["href"]
    # Repli : chercher une URL http(s) externe dans le HTML.
    matches = re.findall(r'https?://[^"\'<>\s]+', html)
    for m in matches:
        if "google.com" not in m and "gstatic.com" not in m:
            return m
    return None


def fetch_page_text(url: str, timeout: float = 20.0) -> str:
    """Telecharge une page web et en extrait le texte lisible.

    Args:
        url: URL de la page.
        timeout: timeout reseau.

    Returns:
        Le texte principal de la page (tronque a une longueur raisonnable).

    Raises:
        ConnectorError: si la page est injoignable ou illisible.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            response.raise_for_status()
            html = response.text
            # Les URLs Google News pointent vers une page de redirection : on
            # tente d'extraire l'URL de l'article reel et de la suivre.
            if "news.google.com" in url:
                real = _extract_google_news_target(html)
                if real:
                    r2 = client.get(real)
                    r2.raise_for_status()
                    html = r2.text
    except httpx.HTTPError as exc:
        raise ConnectorError(f"page injoignable : {exc}", source="deep_research") from exc

    soup = BeautifulSoup(html, "html.parser")
    # On retire les elements non informatifs.
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:_MAX_PAGE_CHARS]


def deep_analyze(
    url: str,
    title: str = "",
    client: Optional[LLMClient] = None,
) -> Dict[str, Any]:
    """Effectue l'analyse approfondie d'une page et renvoie une fiche structuree.

    Args:
        url: URL de la ressource a analyser.
        title: titre connu (contexte pour le LLM).
        client: client LLM ; instancie par defaut si absent.

    Returns:
        Un dict : resume, organisation, besoin, opportunite, action, niveau.

    Raises:
        ConnectorError: si la page ne peut etre recuperee.
        LLMError: si l'analyse LLM echoue ou est illisible.
    """
    page_text = fetch_page_text(url)
    if not page_text:
        raise ConnectorError("contenu de page vide", source="deep_research")

    llm = client or LLMClient()
    prompt = (
        f"Titre : {title}\nURL : {url}\n\nContenu de la page :\n{page_text}"
    )
    raw = llm.generate(prompt, system_prompt=SYSTEM_PROMPT, max_tokens=800)

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise LLMError(f"analyse deep research illisible : {exc}") from exc

    # Normalisation des cles attendues (structure analyste Intra-Air).
    niveau = str(data.get("niveau", "ACT")).upper()
    if niveau not in {"HIGH", "ACT"}:
        niveau = "ACT"
    return {
        "titre_analyse": data.get("titre_analyse", ""),
        "organisation": data.get("organisation", "Non specifie"),
        "contexte_resume": data.get("contexte_resume", ""),
        "besoin_ou_opportunite": data.get("besoin_ou_opportunite", ""),
        "niveau": niveau,
        "action": data.get("action", ""),
    }
