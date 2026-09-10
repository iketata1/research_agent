"""DAO (Data Access Object) pour la table `items`.

Regroupe les operations CRUD sur les `RawItem` et la logique de deduplication.

Deduplication : l'`id` d'un `RawItem` est un hash stable derive du DOI, sinon de
l'URL, sinon de (source + titre). L'insertion utilise `INSERT OR IGNORE` sur cet
`id` (PRIMARY KEY), double par l'index UNIQUE sur `url`. Un meme article/contrat
collecte plusieurs fois (meme d'une semaine a l'autre) n'est donc stocke qu'une fois.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from research_agent.models import RawItem, Theme
from research_agent.storage.database import Database


class ItemStatus(str, Enum):
    """Cycle de vie d'un item dans le pipeline."""

    COLLECTED = "collected"  # tout juste collecte
    FILTERED = "filtered"    # passe le pre-filtrage mots-cles
    KEPT = "kept"            # juge pertinent par le LLM (dans le rapport)
    DROPPED = "dropped"      # juge non pertinent, ecarte


class UpsertResult(str, Enum):
    """Issue d'un upsert, pour le logging et les statistiques de run."""

    INSERTED = "inserted"    # nouvelle ressource
    UNCHANGED = "unchanged"  # ressource connue, contenu identique (vrai doublon)
    UPDATED = "updated"      # ressource connue, contenu modifie -> rafraichie


def _to_row(item: RawItem, status: ItemStatus) -> Dict[str, Any]:
    """Convertit un RawItem en dictionnaire de colonnes pour la table `items`."""
    return {
        "id": item.id,
        "source": item.source,
        "title": item.title,
        "url": str(item.url),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "raw_text": item.raw_text,
        "language": item.language,
        "metadata": json.dumps(item.metadata, ensure_ascii=False),
        "theme": item.theme.value,
        "relevance": item.relevance,
        "status": status.value,
        "content_hash": item.content_hash(),
    }


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convertit une ligne SQLite en dict, avec `metadata` re-parse en JSON."""
    data = dict(row)
    if isinstance(data.get("metadata"), str):
        try:
            data["metadata"] = json.loads(data["metadata"])
        except (ValueError, TypeError):
            data["metadata"] = {}
    return data


# --- Create ------------------------------------------------------------------


def insert_item(
    item: RawItem,
    db: Database,
    status: ItemStatus = ItemStatus.COLLECTED,
) -> bool:
    """Insere un item, en ignorant les doublons (meme id OU meme url).

    Args:
        item: element normalise a stocker.
        db: base cible.
        status: statut initial (par defaut COLLECTED).

    Returns:
        True si l'item a ete insere, False s'il existait deja (doublon ignore).
    """
    row = _to_row(item, status)
    columns = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    sql = f"INSERT OR IGNORE INTO items ({columns}) VALUES ({placeholders});"
    with db.connection() as conn:
        cursor = conn.execute(sql, row)
        inserted = cursor.rowcount > 0
    return inserted


def insert_items(
    items: List[RawItem],
    db: Database,
    status: ItemStatus = ItemStatus.COLLECTED,
) -> int:
    """Insere une liste d'items en ignorant les doublons.

    Returns:
        Le nombre d'items reellement inseres (hors doublons).
    """
    inserted = 0
    for item in items:
        if insert_item(item, db, status=status):
            inserted += 1
    return inserted


def upsert_item(
    item: RawItem,
    db: Database,
    status: ItemStatus = ItemStatus.COLLECTED,
) -> UpsertResult:
    """Insere ou rafraichit un item selon l'evolution de son contenu.

    Trois cas :
    - ressource inconnue -> insertion (INSERTED) ;
    - ressource connue, meme content_hash -> rien a faire (UNCHANGED) ;
    - ressource connue, content_hash different -> le contenu est rafraichi et le
      statut repasse a COLLECTED pour re-filtrage. Les champs enrichis par le LLM
      (relevance, theme) sont preserves (UPDATED).

    La ressource est reperee par son `id` puis, a defaut, par son `url`.

    Args:
        item: element normalise.
        db: base cible.
        status: statut applique lors d'une insertion neuve.

    Returns:
        Le resultat de l'operation (voir `UpsertResult`).
    """
    row = _to_row(item, status)
    new_hash = row["content_hash"]

    with db.connection() as conn:
        existing = conn.execute(
            "SELECT id, content_hash FROM items WHERE id = ? OR url = ? LIMIT 1;",
            (item.id, str(item.url)),
        ).fetchone()

        if existing is None:
            columns = ", ".join(row.keys())
            placeholders = ", ".join(f":{k}" for k in row)
            conn.execute(
                f"INSERT INTO items ({columns}) VALUES ({placeholders});", row
            )
            return UpsertResult.INSERTED

        if existing["content_hash"] == new_hash:
            return UpsertResult.UNCHANGED

        # Contenu modifie : on rafraichit le contenu et on repasse a COLLECTED.
        # On preserve relevance/theme (travail LLM deja effectue).
        conn.execute(
            "UPDATE items SET "
            "  title = :title,"
            "  raw_text = :raw_text,"
            "  language = :language,"
            "  metadata = :metadata,"
            "  published_at = :published_at,"
            "  content_hash = :content_hash,"
            "  status = :status,"
            "  updated_at = datetime('now') "
            "WHERE id = :existing_id;",
            {
                "title": row["title"],
                "raw_text": row["raw_text"],
                "language": row["language"],
                "metadata": row["metadata"],
                "published_at": row["published_at"],
                "content_hash": new_hash,
                "status": ItemStatus.COLLECTED.value,
                "existing_id": existing["id"],
            },
        )
        return UpsertResult.UPDATED


def upsert_items(
    items: List[RawItem],
    db: Database,
    status: ItemStatus = ItemStatus.COLLECTED,
) -> Dict[str, int]:
    """Upsert une liste d'items et retourne le compte par type de resultat.

    Returns:
        Un dict {"inserted": n, "unchanged": n, "updated": n}.
    """
    counts = {r.value: 0 for r in UpsertResult}
    for item in items:
        result = upsert_item(item, db, status=status)
        counts[result.value] += 1
    return counts


# --- Read --------------------------------------------------------------------


def get_item(item_id: str, db: Database) -> Optional[Dict[str, Any]]:
    """Recupere un item par son identifiant, ou None s'il n'existe pas."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?;", (item_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_by_url(url: str, db: Database) -> Optional[Dict[str, Any]]:
    """Recupere un item par son URL, ou None."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE url = ?;", (url,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_items(
    db: Database,
    source: Optional[str] = None,
    status: Optional[ItemStatus] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Liste les items, filtrables par source et/ou statut.

    Args:
        db: base a interroger.
        source: si fourni, ne retourne que les items de cette source.
        status: si fourni, ne retourne que les items dans ce statut.
        limit: nombre maximum de resultats.

    Returns:
        Liste d'items (les plus recemment collectes d'abord).
    """
    clauses: List[str] = []
    params: List[Any] = []
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if status is not None:
        clauses.append("status = ?")
        params.append(status.value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    sql = f"SELECT * FROM items {where} ORDER BY collected_at DESC LIMIT ?;"
    with db.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def exists(item_id: str, db: Database) -> bool:
    """Indique si un item avec cet identifiant existe deja."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM items WHERE id = ? LIMIT 1;", (item_id,)
        ).fetchone()
    return row is not None


# --- Update ------------------------------------------------------------------


def update_status(item_id: str, status: ItemStatus, db: Database) -> bool:
    """Met a jour le statut d'un item. Retourne True si une ligne a change."""
    with db.connection() as conn:
        cursor = conn.execute(
            "UPDATE items SET status = ? WHERE id = ?;",
            (status.value, item_id),
        )
        return cursor.rowcount > 0


def update_relevance(
    item_id: str,
    relevance: float,
    db: Database,
    theme: Optional[Theme] = None,
    status: Optional[ItemStatus] = None,
) -> bool:
    """Enregistre le resultat du filtrage LLM (score, et optionnellement theme/statut).

    Args:
        item_id: identifiant de l'item.
        relevance: score de pertinence [0..1] calcule par le LLM.
        db: base cible.
        theme: theme attribue par le LLM (optionnel).
        status: nouveau statut (optionnel, ex. KEPT ou DROPPED).

    Returns:
        True si une ligne a ete mise a jour.

    Raises:
        ValueError: si `relevance` est hors de [0, 1].
    """
    if not 0.0 <= relevance <= 1.0:
        raise ValueError("relevance doit etre dans l'intervalle [0, 1].")

    fields = ["relevance = ?"]
    params: List[Any] = [relevance]
    if theme is not None:
        fields.append("theme = ?")
        params.append(theme.value)
    if status is not None:
        fields.append("status = ?")
        params.append(status.value)
    params.append(item_id)
    sql = f"UPDATE items SET {', '.join(fields)} WHERE id = ?;"
    with db.connection() as conn:
        cursor = conn.execute(sql, params)
        return cursor.rowcount > 0


# --- Delete ------------------------------------------------------------------


def delete_item(item_id: str, db: Database) -> bool:
    """Supprime un item (et ses resumes lies via cascade). Retourne True si supprime."""
    with db.connection() as conn:
        cursor = conn.execute("DELETE FROM items WHERE id = ?;", (item_id,))
        return cursor.rowcount > 0


def save_enriched_item(
    item_id: str,
    db: Database,
    score: int,
    theme: Theme,
    status: ItemStatus,
    justification: Optional[str] = None,
    theme_confidence: Optional[float] = None,
) -> bool:
    """Persiste de maniere transactionnelle l'enrichissement LLM d'un item.

    Enregistre en une seule operation :
    - `relevance` (score normalise [0, 1] a partir du score 0-100) ;
    - `theme` (categorie) et `status` (statut final de traitement) ;
    - les details LLM dans `metadata` (score brut 0-100, justification, confiance),
      en preservant les metadonnees existantes (tracabilite collecte + pre-filtre).

    Args:
        item_id: identifiant de l'item a enrichir.
        db: base cible.
        score: score de pertinence 0-100 calcule par le LLM.
        theme: categorie attribuee.
        status: statut final (ex. KEPT / DROPPED).
        justification: justification courte du score (optionnel).
        theme_confidence: confiance de la classification [0, 1] (optionnel).

    Returns:
        True si l'item existait et a ete mis a jour, False sinon.

    Raises:
        ValueError: si `score` est hors de [0, 100].
    """
    if not 0 <= score <= 100:
        raise ValueError("score doit etre dans l'intervalle [0, 100].")

    relevance = score / 100.0
    with db.connection() as conn:
        row = conn.execute(
            "SELECT metadata FROM items WHERE id = ?;", (item_id,)
        ).fetchone()
        if row is None:
            return False

        # Fusion des metadonnees existantes avec les details LLM (tracabilite).
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except (ValueError, TypeError):
            metadata = {}
        metadata["llm_score"] = score
        if justification is not None:
            metadata["llm_justification"] = justification
        if theme_confidence is not None:
            metadata["theme_confidence"] = theme_confidence

        conn.execute(
            "UPDATE items SET relevance = ?, theme = ?, status = ?, metadata = ? "
            "WHERE id = ?;",
            (
                relevance,
                theme.value,
                status.value,
                json.dumps(metadata, ensure_ascii=False),
                item_id,
            ),
        )
    return True
