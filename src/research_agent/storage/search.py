"""Recherche plein texte sur la base de connaissances (FTS5).

`search_items()` interroge la table virtuelle `items_fts`, joint la table `items`
pour retourner les colonnes completes, et classe les resultats par pertinence
grace au classement BM25 integre a FTS5 (colonne implicite `rank`).

La requete utilisateur est transformee en une requete FTS5 sure : chaque terme
est place entre guillemets (evite les erreurs de syntaxe sur les caracteres
speciaux de FTS5) et les termes sont combines en ET implicite.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from research_agent.exceptions import DatabaseError
from research_agent.logging_config import get_logger
from research_agent.storage.database import Database

logger = get_logger(__name__)

# Colonnes de `items` renvoyees pour chaque resultat (prefixees par l'alias i).
_ITEM_COLUMNS = ", ".join(
    f"i.{col}"
    for col in (
        "id",
        "source",
        "title",
        "url",
        "published_at",
        "raw_text",
        "language",
        "metadata",
        "theme",
        "relevance",
        "status",
    )
)


def build_fts_query(user_query: str) -> str:
    """Convertit une requete utilisateur en requete FTS5 sure.

    Chaque mot est extrait, echappe (guillemets doubles) et les mots sont
    combines en ET implicite. Retourne une chaine vide si aucun terme exploitable.

    Args:
        user_query: texte libre saisi par l'utilisateur.

    Returns:
        Une expression de recherche FTS5 (ex. '"mould" "housing"').
    """
    # On conserve les caracteres alphanumeriques (dont accentues) comme separateurs de mots.
    tokens = re.findall(r"\w+", user_query, flags=re.UNICODE)
    # Les guillemets internes sont doubles pour l'echappement FTS5.
    quoted = [f'"{tok}"' for tok in tokens if tok]
    return " ".join(quoted)


def search_items(
    query: str,
    limit: int = 20,
    db: Database | None = None,
) -> List[Dict[str, Any]]:
    """Recherche des items par mots-cles, classes par pertinence.

    Args:
        query: mots-cles de recherche (texte libre).
        limit: nombre maximum de resultats.
        db: base a interroger ; chargee depuis la config si absente.

    Returns:
        Liste de dictionnaires (un par item), du plus pertinent au moins pertinent.
        Liste vide si la requete ne contient aucun terme exploitable.

    Raises:
        DatabaseError: en cas d'erreur SQLite.
    """
    fts_query = build_fts_query(query)
    if not fts_query:
        logger.debug("Requete de recherche vide apres nettoyage : %r", query)
        return []

    database = db or Database.from_config()
    sql = (
        f"SELECT {_ITEM_COLUMNS} "
        "FROM items_fts f "
        "JOIN items i ON i.rowid = f.rowid "
        "WHERE items_fts MATCH ? "
        "ORDER BY f.rank "
        "LIMIT ?;"
    )
    try:
        with database.connection() as conn:
            rows = conn.execute(sql, (fts_query, limit)).fetchall()
    except DatabaseError:
        raise
    logger.debug("Recherche '%s' -> %d resultat(s).", fts_query, len(rows))
    return [dict(row) for row in rows]
