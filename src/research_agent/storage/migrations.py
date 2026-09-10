"""Schema relationnel et migrations initiales (SQLite).

Systeme de migrations versionnees minimaliste :
- une table `schema_version` trace les migrations deja appliquees ;
- `MIGRATIONS` est une liste ordonnee (version, description, SQL) ;
- `run_migrations()` applique, dans une seule transaction, toutes les migrations
  dont la version est superieure a la version courante de la base.

Cela rend le schema idempotent (relancable sans risque) et extensible (ajouter
une migration = ajouter une entree a la liste, sans toucher aux precedentes).

Tables principales :
- items     : RawItem normalises (source de verite, anti-doublon sur id / url) ;
- summaries : syntheses generees par le LLM (par item et/ou par semaine) ;
- runs      : historique des executions (monitoring + anti-doublon inter-semaines).
"""

from __future__ import annotations

import sqlite3
from typing import List, Tuple

from research_agent.logging_config import get_logger

logger = get_logger(__name__)


# --- Definition du schema (migration v1) -------------------------------------

_V1_SQL = """
-- Items normalises collectes depuis toutes les sources.
CREATE TABLE IF NOT EXISTS items (
    id            TEXT    PRIMARY KEY,          -- id stable (hash) du RawItem
    source        TEXT    NOT NULL,             -- openalex, tenderned, ted...
    title         TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    published_at  TEXT,                         -- ISO 8601 (UTC de preference)
    raw_text      TEXT    NOT NULL DEFAULT '',
    language      TEXT,
    metadata      TEXT    NOT NULL DEFAULT '{}',-- JSON specifique a la source
    theme         TEXT    NOT NULL DEFAULT 'unknown',
    relevance     REAL,                         -- score LLM [0..1]
    status        TEXT    NOT NULL DEFAULT 'collected',
                                                -- collected|filtered|kept|dropped
    collected_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    CONSTRAINT relevance_range CHECK (relevance IS NULL OR (relevance >= 0 AND relevance <= 1))
);

-- Anti-doublon : une meme URL ne doit exister qu'une fois.
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_url ON items (url);
-- Index de performance pour les requetes du rapport hebdomadaire.
CREATE INDEX IF NOT EXISTS idx_items_source       ON items (source);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items (published_at);
CREATE INDEX IF NOT EXISTS idx_items_theme_status ON items (theme, status);

-- Syntheses generees par le LLM.
CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT,                           -- resume d'un item precis (ou NULL)
    week        TEXT,                           -- ex. "2026-W35" pour un resume hebdo
    theme       TEXT    NOT NULL DEFAULT 'unknown',
    summary     TEXT    NOT NULL,
    priority    TEXT,                           -- ACT|HIGH|... (cf. maquette)
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_summaries_item_id ON summaries (item_id);
CREATE INDEX IF NOT EXISTS idx_summaries_week    ON summaries (week);

-- Historique des executions du pipeline (monitoring + anti-doublon inter-semaines).
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type       TEXT    NOT NULL,           -- collect|filter|report...
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT    NOT NULL DEFAULT 'running',  -- running|success|failed
    items_collected INTEGER NOT NULL DEFAULT 0,
    error           TEXT                        -- message d'erreur si echec
);

CREATE INDEX IF NOT EXISTS idx_runs_task_type  ON runs (task_type);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs (started_at);
"""


# --- Recherche plein texte FTS5 (migration v2) -------------------------------

# Table virtuelle FTS5 en mode "contenu externe" : le texte n'est pas duplique,
# FTS5 lit directement dans `items` via le rowid. Des triggers maintiennent
# l'index synchronise a chaque insertion / mise a jour / suppression.
_V2_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title,
    raw_text,
    content='items',
    content_rowid='rowid'
);

-- Synchronisation : nouvel item -> indexe.
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts (rowid, title, raw_text)
    VALUES (new.rowid, new.title, new.raw_text);
END;

-- Synchronisation : item supprime -> retire de l'index.
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, raw_text)
    VALUES ('delete', old.rowid, old.title, old.raw_text);
END;

-- Synchronisation : item modifie -> reindexe (delete puis insert).
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, raw_text)
    VALUES ('delete', old.rowid, old.title, old.raw_text);
    INSERT INTO items_fts (rowid, title, raw_text)
    VALUES (new.rowid, new.title, new.raw_text);
END;

-- Reindexation des items eventuellement deja presents avant la creation de l'index.
INSERT INTO items_fts (rowid, title, raw_text)
SELECT rowid, title, raw_text FROM items;
"""


# --- Suivi des mises a jour de contenu (migration v3) ------------------------

# `content_hash` : empreinte du contenu (title + raw_text) pour distinguer un
# vrai doublon d'une ressource dont le contenu a change (upsert).
# `updated_at` : horodatage de la derniere modification de contenu.
_V3_SQL = """
ALTER TABLE items ADD COLUMN content_hash TEXT;
ALTER TABLE items ADD COLUMN updated_at TEXT;
CREATE INDEX IF NOT EXISTS idx_items_content_hash ON items (content_hash);
"""


# Liste ordonnee des migrations : (version, description, SQL).
MIGRATIONS: List[Tuple[int, str, str]] = [
    (1, "schema initial : items, summaries, runs", _V1_SQL),
    (2, "recherche plein texte FTS5 (items_fts + triggers)", _V2_SQL),
    (3, "suivi des mises a jour de contenu (content_hash, updated_at)", _V3_SQL),
]


def _current_version(conn: sqlite3.Connection) -> int:
    """Retourne la version de schema actuellement appliquee (0 si aucune)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version    INTEGER NOT NULL,"
        "  applied_at TEXT    NOT NULL DEFAULT (datetime('now'))"
        ");"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version;").fetchone()
    return row["v"] if row and row["v"] is not None else 0


def run_migrations(conn: sqlite3.Connection) -> int:
    """Applique les migrations manquantes sur la connexion fournie.

    Args:
        conn: connexion SQLite ouverte (le commit est gere par l'appelant, via
            le context manager `Database.connection()`).

    Returns:
        La version de schema finale apres migration.
    """
    current = _current_version(conn)
    applied = current
    for version, description, sql in MIGRATIONS:
        if version <= current:
            continue
        logger.info("Migration v%d : %s", version, description)
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?);", (version,)
        )
        applied = version
    if applied == current:
        logger.debug("Schema deja a jour (v%d).", current)
    else:
        logger.info("Schema migre : v%d -> v%d.", current, applied)
    return applied
